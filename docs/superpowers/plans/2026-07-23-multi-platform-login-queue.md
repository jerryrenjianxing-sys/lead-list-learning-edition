# Multi-Platform Login Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Search Mode select multiple platforms, log into every selected platform first, then crawl them sequentially with the existing single-platform process.

**Architecture:** Keep the existing single-platform API and crawler cores. Add an internal `login` crawler type, then let one WebUI hook submit two sequential phases and wait on task-scoped status between jobs.

**Tech Stack:** Python, FastAPI, Pydantic, pytest, React, TypeScript, Zustand, React Query, existing Radix Checkbox.

## Global Constraints

- Do not add dependencies, concurrency, retries, persistent queues, or a new scheduler.
- Do not modify any of the seven platform core files.
- Multi-platform selection is available only in Search Mode and always uses QR-code login.
- A failed job stops the queue; Stop cancels pending jobs before terminating the current task.
- Preserve unrelated dirty-worktree changes.

---

### Task 1: Internal Login-Only Job

**Files:**
- Modify: `api/schemas/crawler.py`
- Modify: `cmd_arg/arg.py`
- Modify: `api/services/crawler_manager.py`
- Test: `tests/test_api_limits.py`

**Interfaces:**
- Produces: `CrawlerTypeEnum.LOGIN = "login"` in both API and CLI enums.
- Produces: a nonzero crawler exit leaves `CrawlerManager.status == "error"` and sets `error_message`.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_api_limits.py`:

```python
from io import StringIO


def test_login_only_job_builds_without_crawl_arguments():
    cm = CrawlerManager()
    request = CrawlerStartRequest(
        platform=PlatformEnum.XHS,
        crawler_type="login",
        keywords="must-not-run",
    )

    command = cm._build_command(request)

    assert command[command.index("--type") + 1] == "login"
    assert "--keywords" not in command


@pytest.mark.asyncio
async def test_nonzero_crawler_exit_sets_error_status():
    cm = CrawlerManager()
    cm.status = "running"
    cm.process = SimpleNamespace(
        poll=lambda: 1,
        returncode=1,
        stdout=StringIO(""),
    )

    await cm._read_output()

    assert cm.status == "error"
    assert cm.error_message == "Crawler exited with code: 1"
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
uv run pytest tests/test_api_limits.py -k "login_only_job or nonzero_crawler_exit" -q
```

Expected: login request validation fails and nonzero exit still reports `idle`.

- [ ] **Step 3: Add the internal enum value**

Add this member to `CrawlerTypeEnum` in both `api/schemas/crawler.py` and `cmd_arg/arg.py`:

```python
LOGIN = "login"
```

Do not add `login` to `/api/config/options`; it is internal and must not appear in the normal mode picker.

- [ ] **Step 4: Preserve failure status**

In `CrawlerManager._read_output`, replace the unconditional final `idle` assignment with:

```python
if exit_code == 0:
    entry = self._create_log_entry("Crawler completed successfully", "success")
    self.status = "idle"
    self.error_message = None
else:
    self.error_message = f"Crawler exited with code: {exit_code}"
    entry = self._create_log_entry(self.error_message, "error")
    self.status = "error"
await self._push_log(entry)
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
uv run pytest tests/test_api_limits.py -k "login_only_job or nonzero_crawler_exit" -q
```

Expected: `2 passed`.

### Task 2: Task-Scoped WebUI Queue Hook

**Files:**
- Modify: `webui/src/lib/api.ts`
- Modify: `webui/src/hooks/useCrawler.ts`

**Interfaces:**
- Produces: `crawlerApi.start(config) -> { task_id: string }`.
- Produces: `crawlerApi.getStatus(taskId?)`.
- Produces: `crawlerApi.stop(taskId)`.
- Produces: `useCrawlerQueue() -> { startQueue, stopQueue, isQueueRunning }`.
- `startQueue(platforms: string[], config: CrawlerConfig)` runs login jobs before crawl jobs only when more than one platform is selected.

- [ ] **Step 1: Make the task identity contract explicit**

In `webui/src/lib/api.ts`, add `task_id` to `CrawlerStatus`, add:

```ts
export interface CrawlerStartResponse {
  status: string
  message: string
  task_id: string
}
```

and change the API methods to:

```ts
start: (config: CrawlerConfig) =>
  api.post<CrawlerStartResponse>('/crawler/start', config),
stop: (taskId: string) =>
  api.post('/crawler/stop', { task_id: taskId }),
getStatus: (taskId?: string) =>
  api.get<CrawlerStatus>('/crawler/status', {
    params: taskId ? { task_id: taskId } : undefined,
  }),
```

- [ ] **Step 2: Run the build and verify the old stop hook fails**

Run:

```powershell
npm.cmd run build
```

Expected: TypeScript reports that the old `crawlerApi.stop()` call needs a task ID.

- [ ] **Step 3: Replace duplicate start/stop hooks with one queue hook**

In `webui/src/hooks/useCrawler.ts`, remove `useStartCrawler` and `useStopCrawler`. Add a hook using `useRef` and `useState` with this behavior:

```ts
const wait = (milliseconds: number) =>
  new Promise((resolve) => window.setTimeout(resolve, milliseconds))

export function useCrawlerQueue() {
  const queryClient = useQueryClient()
  const setStatus = useCrawlerStore((state) => state.setStatus)
  const clearLogs = useCrawlerStore((state) => state.clearLogs)
  const [isQueueRunning, setIsQueueRunning] = useState(false)
  const cancelled = useRef(false)
  const currentTaskId = useRef<string | null>(null)

  const waitForTask = async (taskId: string) => {
    while (!cancelled.current) {
      await wait(1000)
      const { data } = await crawlerApi.getStatus(taskId)
      setStatus(data.status)
      if (data.status === 'error') {
        throw new Error(data.error_message || 'Crawler task failed')
      }
      if (data.status === 'idle') return
    }
  }

  const startQueue = async (platforms: string[], config: CrawlerConfig) => {
    if (isQueueRunning || platforms.length === 0) return
    cancelled.current = false
    setIsQueueRunning(true)
    clearLogs()

    const crawlJobs = platforms.map((platform) => ({
      ...config,
      platform,
      login_type: platforms.length > 1 ? 'qrcode' : config.login_type,
    }))
    const loginJobs = platforms.length > 1
      ? platforms.map((platform) => ({
          ...config,
          platform,
          login_type: 'qrcode',
          crawler_type: 'login',
          enable_comments: false,
          enable_sub_comments: false,
        }))
      : []

    let activePlatform = ''
    try {
      for (const job of [...loginJobs, ...crawlJobs]) {
        if (cancelled.current) break
        activePlatform = job.platform
        setStatus('running')
        const { data } = await crawlerApi.start(job)
        currentTaskId.current = data.task_id
        if (cancelled.current) {
          await crawlerApi.stop(data.task_id).catch(() => undefined)
          break
        }
        await waitForTask(data.task_id)
      }
      if (!cancelled.current) toast.success('全部平台抓取完成')
    } catch (error) {
      if (!cancelled.current) {
        setStatus('error')
        const message = error instanceof Error ? error.message : 'Crawler task failed'
        toast.error(`${activePlatform}: ${message}`)
      }
    } finally {
      currentTaskId.current = null
      setIsQueueRunning(false)
      queryClient.invalidateQueries({ queryKey: ['crawlerStatus'] })
    }
  }

  const stopQueue = async () => {
    cancelled.current = true
    setStatus('stopping')
    try {
      const taskId =
        currentTaskId.current ?? (await crawlerApi.getStatus()).data.task_id
      if (taskId) await crawlerApi.stop(taskId)
      toast.success('Crawler stopped')
      setStatus('idle')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to stop crawler')
    } finally {
      currentTaskId.current = null
      setIsQueueRunning(false)
      queryClient.invalidateQueries({ queryKey: ['crawlerStatus'] })
    }
  }

  return { startQueue, stopQueue, isQueueRunning }
}
```

- [ ] **Step 4: Run the WebUI build**

Run:

```powershell
npm.cmd run build
```

Expected: build reaches the config panel import error because it still references the removed hooks.

### Task 3: Search-Mode Platform Multi-Select

**Files:**
- Modify: `webui/src/components/config/CrawlerConfigPanel.tsx`

**Interfaces:**
- Consumes: `useCrawlerQueue`.
- Produces: ordered local `selectedPlatforms: string[]`.

- [ ] **Step 1: Replace the hook usage**

Use:

```ts
const { startQueue, stopQueue, isQueueRunning } = useCrawlerQueue()
```

Set:

```ts
const isDisabled =
  isQueueRunning || status === 'running' || status === 'stopping'
const isRunning = isQueueRunning || status === 'running'
const isBusy = status === 'stopping'
```

Start with the selected search platforms, otherwise keep the single platform:

```ts
const handleStart = () => {
  const platformsToRun =
    config.crawler_type === 'search' ? selectedPlatforms : [config.platform]
  void startQueue(platformsToRun, config)
}

const handleStop = () => {
  void stopQueue()
}
```

- [ ] **Step 2: Add ordered platform selection**

Initialize:

```ts
const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([
  config.platform,
])
```

Toggle without allowing an empty selection:

```ts
const togglePlatform = (platform: string, checked: boolean) => {
  const next = checked
    ? [...selectedPlatforms.filter((value) => value !== platform), platform]
    : selectedPlatforms.filter((value) => value !== platform)
  if (next.length === 0) return
  setSelectedPlatforms(next)
  updateConfig({
    platform: next[0],
    ...(next.length > 1 ? { login_type: 'qrcode' } : {}),
  })
}
```

- [ ] **Step 3: Reuse the existing Checkbox component**

For Search Mode, replace the single Select with:

```tsx
<div className="grid grid-cols-2 gap-2">
  {platforms?.map((platform) => {
    const checked = selectedPlatforms.includes(platform.value)
    return (
      <label
        key={platform.value}
        className="flex items-center gap-2 rounded-md border border-cyber-border-subtle bg-cyber-bg-tertiary/30 p-2 text-xs font-mono"
      >
        <Checkbox
          checked={checked}
          onCheckedChange={(value) =>
            togglePlatform(platform.value, value === true)
          }
          disabled={isDisabled || (checked && selectedPlatforms.length === 1)}
        />
        {platform.label}
      </label>
    )
  })}
</div>
```

Keep the existing single Select unchanged for Detail and Creator modes. Disable the login-method Select while multiple search platforms are selected.

- [ ] **Step 4: Build and run focused backend tests**

Run:

```powershell
npm.cmd run build
uv run pytest tests/test_api_limits.py -q
```

Expected: WebUI build succeeds and the API test file passes.

- [ ] **Step 5: Browser verification**

At `http://127.0.0.1:5173/` verify:

1. Search Mode shows seven platform checkboxes and prevents clearing the last selection.
2. Selecting a second platform forces QR Code Login and disables the login selector.
3. Detail and Creator modes still show the original single-platform selector.
4. The Start button becomes Stop for the whole queue, including the gap between jobs.
5. The existing page remains vertically scrollable and the removed author footer does not return.

Do not start a real social-platform crawl during automated verification; the first actual two-phase run is user-driven because it may require QR scans.
