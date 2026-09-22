import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from 'react'
import {
  Menu,
  PanelLeftClose,
  Search,
  Settings2,
  Square,
  SquareTerminal,
} from 'lucide-react'
import { Terminal } from '@/components/console/Terminal'
import { DataExplorerDialog } from '@/components/data/DataExplorerDialog'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  useCrawlerQueue,
  useCrawlerLogs,
  useCrawlerStatus,
} from '@/hooks/useCrawler'
import { useLogWebSocket } from '@/hooks/useWebSocket'
import {
  buildCrawlerConfig,
  getCrawlerPlatforms,
  parseTagInput,
  validateCrawlerTags,
} from '@/lib/searchConfig'
import { crawlerApi } from '@/lib/api'
import { useCrawlerStore } from '@/store/crawlerStore'
import {
  LEAD_MODE_PLATFORMS,
  type CrawlerType,
} from '@/types/crawler'

const PLATFORMS = [
  { value: 'xhs', label: '小红书', icon: '/platforms/xhs.svg' },
  { value: 'dy', label: '抖音', icon: '/platforms/dy.svg' },
  { value: 'ks', label: '快手', icon: '/platforms/ks.svg' },
  { value: 'bili', label: '哔哩哔哩', icon: '/platforms/bili.svg' },
  { value: 'wb', label: '微博', icon: '/platforms/wb.svg' },
  { value: 'tieba', label: '贴吧', icon: '/platforms/tieba.svg' },
  { value: 'zhihu', label: '知乎', icon: '/platforms/zhihu.svg' },
] as const
const CRAWLER_TYPES = [
  {
    value: 'search',
    label: '关键词搜索',
    title: '输入产品，输出名单',
    placeholder: '输入关键词，支持中英文逗号，按回车生成标签',
    tagLabel: '关键词',
    action: '搜索',
  },
  {
    value: 'detail',
    label: '指定作品',
    title: '输入作品，采集公开数据',
    placeholder: '输入作品链接或 ID，按回车生成标签',
    tagLabel: '作品',
    action: '开始采集',
  },
  {
    value: 'creator',
    label: '指定创作者',
    title: '输入创作者，采集公开数据',
    placeholder: '输入创作者主页链接或 ID，按回车生成标签',
    tagLabel: '创作者',
    action: '开始采集',
  },
] as const

const PLATFORM_NAMES = { dy: '抖音', ks: '快手', xhs: '小红书' } as const
const PROFILE_RECOVERY_COPY = {
  risk_control: {
    title: '账号资料访问暂时受限',
    action: '稍后继续补账号',
  },
  login_expired: {
    title: '登录已失效',
    action: '重新登录并继续',
  },
  browser_closed: {
    title: '账号资料补充中断',
    action: '继续补账号',
  },
  network_error: {
    title: '账号资料补充中断',
    action: '继续补账号',
  },
} as const

type Drawer = 'settings' | 'terminal' | null
type Outcome = 'idle' | 'running' | 'completed' | 'stopped' | 'error'

export function LeadSearchApp() {
  const [drawer, setDrawer] = useState<Drawer>(null)
  const [disclaimerOpen, setDisclaimerOpen] = useState(false)
  const [qrOpen, setQrOpen] = useState(false)
  const [qrVersion, setQrVersion] = useState('')
  const [crawlerType, setCrawlerType] = useState<CrawlerType>('search')
  const [keywordInput, setKeywordInput] = useState('')
  const [keywordTags, setKeywordTags] = useState<string[]>([])
  const [keywordError, setKeywordError] = useState('')
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>(['dy'])
  const [outcome, setOutcome] = useState<Outcome>('idle')
  const [recovering, setRecovering] = useState(false)
  const [refreshingQr, setRefreshingQr] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const settingsTriggerRef = useRef<HTMLButtonElement>(null)
  const terminalTriggerRef = useRef<HTMLButtonElement>(null)
  const settingsPanelRef = useRef<HTMLElement>(null)
  const terminalPanelRef = useRef<HTMLElement>(null)
  const lastTriggerRef = useRef<HTMLButtonElement | null>(null)
  const stopRequestedRef = useRef(false)
  const qrVersionRef = useRef('')

  const config = useCrawlerStore((state) => state.config)
  const status = useCrawlerStore((state) => state.status)
  const logs = useCrawlerStore((state) => state.logs)
  const updateConfig = useCrawlerStore((state) => state.updateConfig)
  const { startQueue, stopQueue, isQueueRunning } = useCrawlerQueue()

  const { data: crawlerStatus } = useCrawlerStatus()
  useCrawlerLogs()
  useLogWebSocket()

  const active = isQueueRunning || status === 'running' || status === 'stopping'
  const busy = active || Boolean(crawlerStatus?.recovery_type)
  const canForceStop =
    !crawlerStatus?.recovery_type ||
    crawlerStatus.status === 'running' ||
    crawlerStatus.status === 'stopping'
  const profileRecovery =
    crawlerStatus?.recovery_type === 'profile_resume'
      ? PROFILE_RECOVERY_COPY[crawlerStatus.recovery_reason ?? 'network_error']
      : null
  const recoveryPlatformName = crawlerStatus?.recovery_platform
    ? PLATFORM_NAMES[crawlerStatus.recovery_platform]
    : '当前平台'
  const mode = CRAWLER_TYPES.find((item) => item.value === crawlerType)!
  const leadModeAvailable = useMemo(
    () =>
      selectedPlatforms.some((platform) =>
        LEAD_MODE_PLATFORMS.includes(platform),
      ),
    [selectedPlatforms],
  )

  useEffect(() => {
    if (!busy) {
      qrVersionRef.current = ''
      setQrOpen(false)
      setQrVersion('')
      setRefreshingQr(false)
      return
    }

    let disposed = false
    const checkQrCode = async () => {
      try {
        const response = await fetch('/api/crawler/qrcode', {
          cache: 'no-store',
        })
        const version = response.headers.get('x-qr-version')
        if (
          disposed ||
          !response.ok ||
          !version ||
          version === qrVersionRef.current
        ) {
          return
        }
        qrVersionRef.current = version
        setQrVersion(version)
        setRefreshingQr(false)
        setQrOpen(true)
      } catch {
        // The local API may be restarting; the next poll will retry.
      }
    }

    void checkQrCode()
    const timer = window.setInterval(checkQrCode, 1000)
    return () => {
      disposed = true
      window.clearInterval(timer)
    }
  }, [busy])

  useEffect(() => {
    settingsPanelRef.current?.toggleAttribute('inert', drawer !== 'settings')
    terminalPanelRef.current?.toggleAttribute('inert', drawer !== 'terminal')

    const previousOverflow = document.body.style.overflow
    if (drawer) document.body.style.overflow = 'hidden'

    const panel =
      drawer === 'settings'
        ? settingsPanelRef.current
        : drawer === 'terminal'
          ? terminalPanelRef.current
          : null

    const focusTimer = panel
      ? window.setTimeout(() => {
          panel
            .querySelector<HTMLElement>('[data-autofocus]')
            ?.focus()
        }, 30)
      : null

    const handleKeyDown = (event: KeyboardEvent) => {
      if (!panel) return
      if (event.key === 'Escape') {
        if (document.querySelector('.apple-dialog')) return
        event.preventDefault()
        setDrawer(null)
        return
      }
      if (event.key !== 'Tab') return

      const focusable = Array.from(
        panel.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), a[href]',
        ),
      )
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      if (focusTimer !== null) window.clearTimeout(focusTimer)
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = previousOverflow
      if (drawer) lastTriggerRef.current?.focus()
    }
  }, [drawer])

  const toggleDrawer = (
    next: Exclude<Drawer, null>,
    trigger: HTMLButtonElement | null,
  ) => {
    lastTriggerRef.current = trigger
    setDrawer((current) => (current === next ? null : next))
  }

  const togglePlatform = (platform: string, checked: boolean) => {
    if (busy) return
    if (crawlerType !== 'search') {
      setSelectedPlatforms([platform])
      return
    }
    const next = checked
      ? [...selectedPlatforms.filter((item) => item !== platform), platform]
      : selectedPlatforms.filter((item) => item !== platform)
    if (next.length === 0) return
    setSelectedPlatforms(next)
  }

  const changeCrawlerType = (next: CrawlerType) => {
    if (busy || next === crawlerType) return
    setCrawlerType(next)
    setKeywordInput('')
    setKeywordTags([])
    setKeywordError('')
    setOutcome('idle')
    if (next !== 'search') {
      setSelectedPlatforms((current) => current.slice(0, 1))
    }
  }

  const commitKeywords = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return
    event.preventDefault()
    const additions = parseTagInput(keywordInput)
    setKeywordInput('')
    if (additions.length === 0) return
    setKeywordTags((current) => [...new Set([...current, ...additions])])
    setKeywordError('')
  }

  const startSearch = async () => {
    if (!validateCrawlerTags(keywordTags)) {
      setKeywordError(`请先按回车生成至少一个${mode.tagLabel}标签。`)
      inputRef.current?.focus()
      return
    }

    setKeywordError('')
    setOutcome('running')
    stopRequestedRef.current = false

    const searchConfig = buildCrawlerConfig({
      platform: selectedPlatforms[0],
      crawlerType,
      values: keywordTags.join(','),
      enableComments: config.enable_comments,
      enableSubComments: config.enable_sub_comments,
      leadMode: config.lead_mode,
    })
    const queuePlatforms = getCrawlerPlatforms(crawlerType, selectedPlatforms)
    await startQueue(queuePlatforms, searchConfig)

    if (stopRequestedRef.current) {
      setOutcome('stopped')
    } else if (useCrawlerStore.getState().status === 'error') {
      setOutcome('error')
    } else {
      setOutcome('completed')
    }
  }

  const stopSearch = async () => {
    if (!window.confirm('强制终止当前任务？已经采集的数据会保留，当前步骤将立即中断。')) return
    stopRequestedRef.current = true
    await stopQueue()
    setOutcome('stopped')
  }

  const recoverTask = async () => {
    const taskId = crawlerStatus?.recovery_task_id
    if (!taskId) return
    setRecovering(true)
    setKeywordError('')
    try {
      if (crawlerStatus.recovery_type === 'profile_resume') {
        await crawlerApi.recoverProfiles(taskId)
      } else {
        await crawlerApi.recoverXhs(taskId)
      }
    } catch {
      setKeywordError(
        crawlerStatus.recovery_type === 'profile_resume'
          ? '续补未启动，原任务断点仍保留，请打开终端查看具体错误。'
          : '小红书恢复未启动，请打开终端查看具体错误。',
      )
    } finally {
      setRecovering(false)
    }
  }

  const refreshQrCode = async () => {
    setRefreshingQr(true)
    try {
      const response = await fetch('/api/crawler/qrcode/refresh', {
        method: 'POST',
      })
      if (!response.ok) throw new Error('QR refresh failed')
    } catch {
      setKeywordError('二维码刷新失败，请打开终端查看具体错误。')
    } finally {
      setRefreshingQr(false)
    }
  }

  const statusText = (() => {
    if (status === 'stopping') return '正在安全停止当前任务…'
    if (crawlerStatus?.recovery_type === 'profile_resume') {
      return `${recoveryPlatformName}账号资料补充已暂停，断点已保留，当前没有启动下一个平台。`
    }
    if (busy) return '正在依次检查平台登录并采集公开数据…'
    if (outcome === 'completed') return '采集完成，可查看或下载采集资料。'
    if (outcome === 'stopped') return '任务已停止，未开始的平台不会继续运行。'
    if (outcome === 'error' || status === 'error') {
      return '任务未完成，请打开终端查看具体错误。'
    }
    return `输入${mode.tagLabel}后按回车生成标签，再手动点击${mode.action}。`
  })()

  return (
    <div className="lead-app">
      <div
        className={`drawer-backdrop ${drawer ? 'is-open' : ''}`}
        aria-hidden={!drawer}
        onClick={() => setDrawer(null)}
      />

      <div
        className={`settings-cluster ${drawer === 'settings' ? 'is-open' : ''}`}
      >
        <aside
          ref={settingsPanelRef}
          className="settings-panel"
          aria-label="采集设置"
          aria-hidden={drawer !== 'settings'}
          aria-modal="true"
          role="dialog"
        >
          <header className="settings-header">
            <div className="settings-brand">
              <span aria-hidden="true">潜</span>
              <strong>潜客名单</strong>
            </div>
            <button
              type="button"
              className="apple-icon-button"
              data-autofocus
              onClick={() => setDrawer(null)}
              aria-label="收起设置"
            >
              <PanelLeftClose size={20} />
            </button>
          </header>

          <div className="settings-body" aria-disabled={busy}>
            <section className="settings-group">
              <p>采集方式</p>
              <div className="crawler-type-list">
                {CRAWLER_TYPES.map((item) => (
                  <button
                    key={item.value}
                    type="button"
                    className={crawlerType === item.value ? 'is-selected' : ''}
                    disabled={busy}
                    onClick={() => changeCrawlerType(item.value)}
                    aria-pressed={crawlerType === item.value}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </section>

            <section className="settings-group">
              <p>
                社媒平台 · {crawlerType === 'search' ? '可多选' : '单选'}
              </p>
              <div className="platform-list">
                {PLATFORMS.map((platform) => {
                  const checked = selectedPlatforms.includes(platform.value)
                  return (
                    <label
                      key={platform.value}
                      className={`platform-row ${checked ? 'is-selected' : ''}`}
                    >
                      <img src={platform.icon} alt="" />
                      <span>{platform.label}</span>
                      <input
                        type={crawlerType === 'search' ? 'checkbox' : 'radio'}
                        name={crawlerType === 'search' ? undefined : 'crawler-platform'}
                        checked={checked}
                        disabled={
                          busy ||
                          (crawlerType === 'search' &&
                            checked &&
                            selectedPlatforms.length === 1)
                        }
                        onChange={(event) =>
                          togglePlatform(platform.value, event.target.checked)
                        }
                        aria-label={`选择${platform.label}`}
                      />
                    </label>
                  )
                })}
              </div>
            </section>

            <section className="settings-group">
              <p>采集内容</p>
              <label className="switch-row">
                <span>
                  <strong>评论</strong>
                  <small>采集公开一级评论</small>
                </span>
                <input
                  className="apple-switch"
                  type="checkbox"
                  checked={config.enable_comments}
                  disabled={busy}
                  onChange={(event) =>
                    updateConfig({
                      enable_comments: event.target.checked,
                      enable_sub_comments: event.target.checked
                        ? config.enable_sub_comments
                        : false,
                    })
                  }
                />
              </label>
              <label className="switch-row">
                <span>
                  <strong>子评论</strong>
                  <small>需先开启评论</small>
                </span>
                <input
                  className="apple-switch"
                  type="checkbox"
                  checked={config.enable_sub_comments}
                  disabled={busy || !config.enable_comments}
                  onChange={(event) =>
                    updateConfig({
                      enable_sub_comments: event.target.checked,
                    })
                  }
                />
              </label>
              <label className="switch-row">
                <span>
                  <strong>评论人账号</strong>
                  <small>
                    {leadModeAvailable
                      ? '补充公开账号资料；仅小红书、抖音、快手生效'
                      : '请先选择小红书、抖音或快手'}
                  </small>
                </span>
                <input
                  className="apple-switch"
                  type="checkbox"
                  checked={config.lead_mode && leadModeAvailable}
                  disabled={busy || !leadModeAvailable}
                  onChange={(event) =>
                    updateConfig({ lead_mode: event.target.checked })
                  }
                />
              </label>
            </section>

            {busy && (
              <p className="settings-lock-note">
                任务运行时设置已锁定。
              </p>
            )}
          </div>

          <footer className="settings-footer">
            <button
              type="button"
              onClick={() => {
                setDrawer(null)
                setDisclaimerOpen(true)
              }}
            >
              免责声明
            </button>
          </footer>
        </aside>

        <nav className="side-rail" aria-label="快捷操作">
          <button
            ref={settingsTriggerRef}
            type="button"
            className={drawer === 'settings' ? 'is-active' : ''}
            onClick={(event) =>
              toggleDrawer('settings', event.currentTarget)
            }
            aria-label={drawer === 'settings' ? '收起设置' : '展开设置'}
            aria-expanded={drawer === 'settings'}
          >
            <Settings2 size={21} />
          </button>
        </nav>
      </div>

      <header className="mobile-bar">
        <button
          type="button"
          onClick={(event) =>
            toggleDrawer('settings', event.currentTarget)
          }
          aria-label="打开设置"
        >
          <Menu size={22} />
        </button>
        <strong>潜客名单</strong>
        <button
          type="button"
          onClick={(event) =>
            toggleDrawer('terminal', event.currentTarget)
          }
          aria-label="打开终端"
        >
          <SquareTerminal size={22} />
        </button>
      </header>

      <button
        ref={terminalTriggerRef}
        className="terminal-trigger"
        type="button"
        onClick={(event) =>
          toggleDrawer('terminal', event.currentTarget)
        }
        aria-label="打开运行终端"
        aria-expanded={drawer === 'terminal'}
      >
        <SquareTerminal size={21} />
        <span
          className={`terminal-status-dot status-${status}`}
          aria-hidden="true"
        />
      </button>

      <main className="search-main">
        <section className="search-hero" aria-labelledby="search-title">
          <p className="product-kicker">潜客名单</p>
          <h1 id="search-title">{mode.title}</h1>
          <div className="search-form">
            <Search size={21} aria-hidden="true" />
            <input
              ref={inputRef}
              value={keywordInput}
              disabled={busy}
              onChange={(event) => {
                setKeywordInput(event.target.value)
                if (keywordError) setKeywordError('')
              }}
              onKeyDown={commitKeywords}
              placeholder={mode.placeholder}
              aria-label={mode.tagLabel}
              aria-invalid={Boolean(keywordError)}
            />
            {active && canForceStop ? (
              <button
                type="button"
                className="search-action is-stop"
                onClick={stopSearch}
                disabled={status === 'stopping'}
              >
                <Square size={15} />
                强制终止
              </button>
            ) : crawlerStatus?.recovery_type ? (
              <button type="button" className="search-action" disabled>
                等待续补
              </button>
            ) : (
              <button type="button" className="search-action" onClick={startSearch}>
                {mode.action}
              </button>
            )}
          </div>
          {keywordTags.length > 0 && (
            <div
              className="keyword-tags"
              aria-label={`已添加的${mode.tagLabel}`}
            >
              {keywordTags.map((keyword) => (
                <span className="keyword-tag" key={keyword}>
                  {keyword}
                  {!busy && (
                    <button
                      type="button"
                      onClick={() =>
                        setKeywordTags((current) =>
                          current.filter((item) => item !== keyword),
                        )
                      }
                        aria-label={`删除${mode.tagLabel} ${keyword}`}
                    >
                      ×
                    </button>
                  )}
                </span>
              ))}
            </div>
          )}
          <p
            className={`search-status ${keywordError ? 'is-error' : ''}`}
            role={keywordError ? 'alert' : 'status'}
          >
            {keywordError || statusText}
          </p>
          {busy && qrVersion && !qrOpen && (
            <button
              type="button"
              className="apple-secondary-button"
              onClick={() => setQrOpen(true)}
            >
              重新显示登录二维码
            </button>
          )}

          {/* 普通搜索和评论错误不会在此显示续补入口。 */}
          {crawlerStatus?.recovery_type && (
            <div className="result-card" role="alert">
              <div>
                <strong>
                  {profileRecovery
                    ? `${recoveryPlatformName}${profileRecovery.title}`
                    : '小红书需要人工验证'}
                </strong>
                <span>
                  {profileRecovery
                    ? '已采集的内容、评论和账号不会重抓；续补只处理剩余评论人账号，当前没有启动下一个平台。'
                    : crawlerStatus.recovery_type === 'xhs_verification'
                    ? '采集请求已暂停。点击后会打开原浏览器，完成验证后从当前任务继续。'
                    : '上次任务未完成。点击后使用原任务和已有检查点继续补采。'}
                </span>
              </div>
              <button
                type="button"
                className="apple-secondary-button h-10 px-4"
                onClick={recoverTask}
                disabled={recovering}
              >
                {recovering
                  ? '正在恢复…'
                  : profileRecovery
                    ? profileRecovery.action
                    : '打开验证并继续'}
              </button>
            </div>
          )}

          {outcome === 'completed' && (
            <div className="result-card">
              <div>
                <strong>采集任务已完成</strong>
                <span>数据管理展示本机保存的全部历史采集文件。</span>
              </div>
              <DataExplorerDialog
                label="查看/下载采集资料"
                variant="light"
              />
            </div>
          )}
        </section>
      </main>

      <aside
        ref={terminalPanelRef}
        className={`terminal-drawer ${drawer === 'terminal' ? 'is-open' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label="运行终端"
        aria-hidden={drawer !== 'terminal'}
      >
        <Terminal onClose={() => setDrawer(null)} />
      </aside>

      <Dialog open={qrOpen} onOpenChange={setQrOpen}>
        <DialogContent className="apple-dialog max-w-sm text-center">
          <DialogHeader>
            <DialogTitle className="apple-dialog-title">
              扫描二维码登录
            </DialogTitle>
            <DialogDescription>
              请使用当前平台的手机 App 扫码。登录成功后此窗口会自动关闭。
            </DialogDescription>
          </DialogHeader>
          {qrVersion && (
            <img
              src={`/api/crawler/qrcode?v=${encodeURIComponent(qrVersion)}`}
              alt="平台登录二维码"
              className="mx-auto w-64 max-w-full rounded-2xl border border-black/10 bg-white p-3"
            />
          )}
          {crawlerStatus?.platform === 'ks' && (
            <button
              type="button"
              className="apple-secondary-button mx-auto h-10 px-4"
              onClick={refreshQrCode}
              disabled={refreshingQr}
            >
              {refreshingQr ? '正在刷新…' : '刷新二维码'}
            </button>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={disclaimerOpen} onOpenChange={setDisclaimerOpen}>
        <DialogContent className="apple-dialog max-w-2xl">
          <DialogHeader>
            <DialogTitle className="apple-dialog-title">
              免责声明
            </DialogTitle>
            <DialogDescription>
              本工具基于 MediaCrawler 公开源码二次开发并免费提供。原项目采用非商业学习使用许可证，本工具不改变原项目的版权和许可条件。
            </DialogDescription>
          </DialogHeader>
          <div className="disclaimer-content">
            <ul>
              <li>仅限学习、研究与技术交流使用。</li>
              <li>禁止商业用途及大规模、高频抓取。</li>
              <li>请遵守适用法律法规和各平台规则。</li>
              <li>使用行为及其产生的风险由使用者自行承担。</li>
            </ul>
            <div className="disclaimer-links">
              <a
                href="https://github.com/NanmiCoder/MediaCrawler"
                target="_blank"
                rel="noreferrer"
              >
                MediaCrawler 原始仓库
              </a>
              <a
                href="https://github.com/NanmiCoder/MediaCrawler#%E5%85%8D%E8%B4%A3%E5%A3%B0%E6%98%8E"
                target="_blank"
                rel="noreferrer"
              >
                README 免责声明
              </a>
              <a
                href="https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE"
                target="_blank"
                rel="noreferrer"
              >
                LICENSE
              </a>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <div className="sr-only" aria-live="polite">
        终端日志 {logs.length} 条
      </div>
    </div>
  )
}
