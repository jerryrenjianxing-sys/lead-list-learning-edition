# 三种采集模式与不限量采集实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在现有 Apple 风格 WebUI 中恢复 Search、Detail、Creator 三种采集方式，并让作品、一级评论、二级回复和三平台公开账号资料以 `0` 表示不限量，持续到平台分页自然结束。

**Architecture:** 不恢复旧 WebUI，也不新增调度系统。前端复用现有 `CrawlerConfig`、标签输入和 `useCrawlerQueue`；后端沿用现有命令行参数与七个平台客户端，只统一“0＝不限量”的判断，并为分页增加最小的重复页终止保护。公开账号补采继续走现有 `LeadSink` 及抖音/小红书/快手资料链路。

**Tech Stack:** React、TypeScript、FastAPI/Pydantic、Python asyncio、pytest、现有 MediaCrawler 平台客户端。

## 全局约束

- 保留当前工作区已有修改，不重置、不覆盖无关文件。
- 不新增依赖，不引入大模型、关键词扩展、并发调度或新平台接口。
- 不用大数字模拟不限量；统一使用 `0`。
- 负数仍在 API 边界拒绝。
- “全量”只表示项目不主动截断；平台未返回、隐藏、风控或权限不可见的数据不作保证。
- 测试先行；每项只补能证明关键行为的一组最小回归检查。

---

## Task 1：打通 API、命令行和默认配置的 `0＝不限量`

**Files:**

- Modify: `config/base_config.py`
- Modify: `api/schemas/crawler.py`
- Verify/Modify: `api/services/crawler_manager.py`
- Verify/Modify: `cmd_arg/arg.py`
- Modify: `tests/test_api_limits.py`
- Modify: `tests/test_required_crawler_inputs.py`

### Step 1：先写失败检查

在现有 API 限制测试中增加：

```python
def test_zero_means_unlimited_for_notes_comments_and_profiles():
    request = CrawlerStartRequest(
        platform="dy",
        login_type="qrcode",
        crawler_type="search",
        keywords="雨衣",
        max_notes_count=0,
        max_comments_count=0,
        lead_max_accounts=0,
    )
    assert request.max_notes_count == 0
    assert request.max_comments_count == 0
    assert request.lead_max_accounts == 0
```

同时保留/补充负数校验，确认三个字段都拒绝 `< 0`。

Run:

```powershell
uv run pytest tests/test_api_limits.py tests/test_required_crawler_inputs.py -q
```

Expected: 新增的 `max_notes_count=0`、`max_comments_count=0` 用例先失败。

### Step 2：最小修改边界

- 将 `CrawlerStartRequest.max_notes_count`、`max_comments_count` 的最小值改为 `0`。
- 将 `CRAWLER_MAX_NOTES_COUNT`、`CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES` 默认值改为 `0`。
- 保持 `lead_max_accounts=0` 的现有语义。
- 确认 `_build_command()` 会原样传递 `0`，不要把 `0` 当作缺省值删除。
- 更新命令行帮助文字，明确 `0` 表示不限量；解析结构不变。

### Step 3：验证

Run:

```powershell
uv run pytest tests/test_api_limits.py tests/test_required_crawler_inputs.py -q
```

Expected: 相关测试全部通过，负数仍被拒绝。

### Step 4：提交

```powershell
git add config/base_config.py api/schemas/crawler.py api/services/crawler_manager.py cmd_arg/arg.py tests/test_api_limits.py tests/test_required_crawler_inputs.py
git commit -m "fix: allow zero as unlimited crawl limit"
```

---

## Task 2：统一作品分页的不限量判断与重复页保护

**Files:**

- Modify: `tools/crawler_util.py`
- Modify: `tests/test_crawler_util.py`
- Modify: `media_platform/douyin/core.py`
- Modify: `media_platform/xhs/core.py`
- Modify: `media_platform/kuaishou/core.py`
- Modify: `media_platform/bilibili/core.py`
- Modify: `media_platform/weibo/core.py`
- Modify: `media_platform/zhihu/core.py`
- Modify: `media_platform/tieba/core.py`
- Modify: `media_platform/xhs/client.py`

### Step 1：先给共享停止语义补失败检查

在现有 `tests/test_crawler_util.py` 增加两个小函数的行为检查：

```python
def test_crawl_limit_zero_never_reaches_local_limit():
    assert not crawler_util.crawl_limit_reached(1, 0)
    assert not crawler_util.crawl_limit_reached(2000, 0)
    assert crawler_util.crawl_limit_reached(15, 15)


def test_page_progress_rejects_empty_or_repeated_pages():
    seen = set()
    assert crawler_util.register_page_ids(seen, ["a", "b"])
    assert not crawler_util.register_page_ids(seen, ["a", "b"])
    assert not crawler_util.register_page_ids(seen, [])
```

Run:

```powershell
uv run pytest tests/test_crawler_util.py -q
```

Expected: 新增测试先失败，因为共享判断尚不存在。

### Step 2：只增加两个共享函数

在 `tools/crawler_util.py` 增加：

- `crawl_limit_reached(current, limit)`：仅当 `limit > 0 and current >= limit` 时为真。
- `register_page_ids(seen, page_ids)`：页面有至少一个新稳定 ID 时登记并返回真；空页或全重复页返回假。

不新增类、配置对象或分页框架。

### Step 3：七个平台复用共享判断

将各平台原有的 `count <= CRAWLER_MAX_NOTES_COUNT`、`count >= ...` 等条件替换为共享判断：

- `0` 时继续请求；
- 有限正数时保持旧行为；
- 平台返回 `has_more=false`、`no_more`、空列表时结束；
- 每个关键词/创作者维护自己的 `seen_page_ids`；
- 页面 ID 全部重复时记录警告并结束当前分页，防止死循环。

稳定 ID 使用各平台原响应中已经存在的字段：

- 抖音：作品 `aweme_id`；
- 小红书：笔记 `note_id/id`；
- 快手：作品 `photo.id`；
- B站：视频 `aid/bvid`；
- 微博：博文 `id/idstr`；
- 知乎：内容 `id/content_id`；
- 贴吧：帖子 `note_id/tid`。

只复用现有返回字段，不为缺少 ID 的响应猜测新标识。

### Step 4：验证

Run:

```powershell
uv run pytest tests/test_crawler_util.py tests/test_douyin_lead_collection.py tests/test_xhs_lead_collection.py tests/test_kuaishou_lead_collection.py -q
```

再做结构检查：

```powershell
rg -n "CRAWLER_MAX_NOTES_COUNT" media_platform/*/core.py media_platform/xhs/client.py
```

Expected: 所有仍使用数量上限的位置都通过 `crawl_limit_reached()` 判断；`0` 不会直接结束或被抬升到平台单页大小。

### Step 5：提交

```powershell
git add tools/crawler_util.py tests/test_crawler_util.py media_platform
git commit -m "fix: crawl content pages until platform completion"
```

---

## Task 3：一级评论与二级回复不限量

**Files:**

- Modify: `media_platform/douyin/client.py`
- Modify: `media_platform/xhs/client.py`
- Modify: `media_platform/kuaishou/client.py`
- Modify: `media_platform/bilibili/client.py`
- Modify: `media_platform/weibo/client.py`
- Verify: `media_platform/zhihu/client.py`
- Modify: `media_platform/tieba/client.py`
- Add: `tests/test_unlimited_comment_pagination.py`

### Step 1：先写分页行为检查

建立一个最小测试文件，使用各客户端现有的请求方法替身返回“两页数据＋结束页”，至少覆盖：

- 一个 `has_more` 型客户端（抖音或小红书）；
- 一个游标型客户端（快手）；
- 一个页码型客户端（B站、微博或贴吧）。

每个测试同时断言：

1. `max_count=0` 会消费全部两页；
2. 正数上限仍会截断；
3. 空页/结束标记会返回，不死循环；
4. 二级回复仍由现有 `enable_sub_comments` 控制，不受一级评论的 `0` 误判影响。

Run:

```powershell
uv run pytest tests/test_unlimited_comment_pagination.py -q
```

Expected: 旧的 `len(result) < max_count` 在 `max_count=0` 时导致测试失败。

### Step 2：修改现有循环，不新建评论框架

把各客户端的：

```python
while has_more and len(result) < max_count:
```

改为复用 `crawl_limit_reached()`；截断切片也只在 `max_count > 0` 时执行。

二级回复继续使用平台自身 `has_more/cursor/page` 循环；不增加项目数量上限。

知乎客户端当前已按平台结束标记分页，仅补测试确认，无需为了“统一外观”改代码。

### Step 3：验证

Run:

```powershell
uv run pytest tests/test_unlimited_comment_pagination.py tests/test_douyin_lead_collection.py tests/test_xhs_lead_collection.py tests/test_kuaishou_lead_collection.py -q
```

Expected: `0` 能取完模拟的全部页面，有限正数兼容旧行为。

### Step 4：提交

```powershell
git add media_platform tests/test_unlimited_comment_pagination.py
git commit -m "fix: support unlimited comment pagination"
```

---

## Task 4：把作品作者加入现有账号资料补采队列

**Files:**

- Modify: `tools/lead_sink.py`
- Modify: `tests/test_lead_sink.py`

### Step 1：先写失败检查

复用现有 `LeadSink.record_content()`、`record_comment()` 和 `profile_references()` 测试结构，增加：

```python
def test_content_author_and_commenters_share_one_deduplicated_profile_queue(tmp_path):
    # 记录一个作品作者、一级评论者、二级回复者；
    # 同一个账号重复出现时只保留一次。
    ...
    assert {item["accountId"] for item in sink.profile_references()} == {
        "author-id",
        "commenter-id",
        "replier-id",
    }
```

再断言 `max_accounts=0` 时超过 150 个有效账号仍可进入队列。

Run:

```powershell
uv run pytest tests/test_lead_sink.py -q
```

Expected: 作者尚未登记或旧上限仍截断时失败。

### Step 2：复用 `LeadSink` 现有登记逻辑

- `record_content()` 保存作品原始记录后，将小红书、抖音、快手作品作者按现有 profile reference 结构登记。
- 一级评论、二级回复继续走现有 `_consider_comment()` 简单过滤。
- 统一按“平台＋accountId”去重。
- 保留空评论、纯表情、作者回复等现有过滤边界；作品作者本人因作品记录独立登记，不依赖评论文字。
- `max_accounts=0` 不截断；正数仍保持兼容。

不新增意向评分、不抓作者额外作品、不更改 raw JSONL 格式。

### Step 3：验证

Run:

```powershell
uv run pytest tests/test_lead_sink.py tests/test_douyin_lead_collection.py tests/test_xhs_lead_collection.py tests/test_kuaishou_lead_collection.py -q
```

Expected: 作者、一级评论者、二级回复者都进入同一去重队列，原评论保存不受影响。

### Step 4：提交

```powershell
git add tools/lead_sink.py tests/test_lead_sink.py
git commit -m "feat: include content authors in profile collection"
```

---

## Task 5：在 Apple 风格 WebUI 恢复三种模式

**Files:**

- Modify: `webui/src/types/crawler.ts`
- Modify: `webui/src/lib/searchConfig.ts`
- Modify: `webui/src/components/search/LeadSearchApp.tsx`
- Modify: `webui/src/index.css`
- Modify: `webui/scripts/check-search-config.mjs`
- Modify: `webui/scripts/check-simple-search-ui.mjs`

### Step 1：先扩展前端自检并确认失败

`check-search-config.mjs` 增加三种模式断言：

- Search → `crawler_type=search`、`keywords` 有值；
- Detail → `crawler_type=detail`、`specified_ids` 有值；
- Creator → `crawler_type=creator`、`creator_ids` 有值；
- 三者都传 `max_notes_count=0`、`max_comments_count=0`、`lead_max_accounts=0`。

`check-simple-search-ui.mjs` 增加结构断言：

- 出现“关键词搜索 / 指定作品 / 指定创作者”；
- Search 保留多平台；
- Detail/Creator 使用单平台；
- 回车只调用标签提交，不直接调用 `startQueue()`；
- 空标签不能启动。

Run:

```powershell
Set-Location webui
npm.cmd run check
```

Expected: 当前只有 Search 模式，自检先失败。

### Step 2：扩展现有类型和配置构造器

- 在 `CrawlerConfig` 增加现有 API 已支持的三个可选数量字段。
- 将 `buildSearchConfig()` 最小扩展为能根据 `crawler_type` 填充 `keywords`、`specified_ids` 或 `creator_ids` 的现有配置构造器；不建立新的表单框架。
- 三种模式统一提交：

```text
max_notes_count = 0
max_comments_count = 0
lead_max_accounts = 0
enable_comments = true
enable_sub_comments = true
lead_mode = true
login_type = qrcode
headless = false
```

### Step 3：在现有侧栏增加紧凑模式选择

- 复用现有标签输入、删除标签和中文/英文逗号解析。
- Search：
  - 输入关键词；
  - 回车只生成标签；
  - 点击开始后调用现有多平台队列。
- Detail：
  - 输入作品链接或 ID；
  - 回车生成标签；
  - 只保留一个已选平台；
  - 点击开始调用现有单平台队列。
- Creator：
  - 输入创作者主页链接或 ID；
  - 回车生成标签；
  - 只保留一个已选平台；
  - 点击开始调用现有单平台队列。

只调整标题、占位文字、启动校验和平台选择约束；终端、二维码、停止、恢复、数据管理不改。

### Step 4：验证前端

Run:

```powershell
Set-Location webui
npm.cmd run check
npm.cmd run build
```

Expected: 自检和正式构建均通过。

### Step 5：提交

```powershell
git add webui/src/types/crawler.ts webui/src/lib/searchConfig.ts webui/src/components/search/LeadSearchApp.tsx webui/src/index.css webui/scripts
git commit -m "feat: restore three crawl modes in simple UI"
```

---

## Task 6：全链路验证、服务重启与封装同步

**Files:**

- Verify: all changed files
- Update if needed: `docs/superpowers/reports/2026-07-30-unlimited-crawl-modes-verification.md`
- Run existing learning-edition sync/package scripts; do not hand-copy files.

### Step 1：后端回归

Run focused suite:

```powershell
uv run pytest tests/test_api_limits.py tests/test_required_crawler_inputs.py tests/test_crawler_util.py tests/test_unlimited_comment_pagination.py tests/test_lead_sink.py tests/test_douyin_lead_collection.py tests/test_xhs_lead_collection.py tests/test_kuaishou_lead_collection.py -q
```

Run project suite excluding the known external Redis requirement only if the full run hits unavailable Redis:

```powershell
uv run pytest -q
```

Expected: 本轮功能测试全部通过；外部 Redis 缺失必须单独说明，不能掩盖其他失败。

### Step 2：前端回归

```powershell
Set-Location webui
npm.cmd run check
npm.cmd run build
```

Expected: TypeScript、静态交互检查和 Vite 构建通过。

### Step 3：本地实际页面检查

安全重启当前项目的 API 和 WebUI，验证：

1. Search：输入两个逗号分隔关键词，回车产生两个标签，未自动启动；
2. Detail：多 ID 标签，单平台；
3. Creator：多主页标签，单平台；
4. 空输入拦截；
5. 开始请求中的三个上限均为 `0`；
6. 终端、停止、二维码、恢复、数据管理仍可打开；
7. 桌面 1440×900、1280×600 与移动端 390×844 无横向溢出。

页面检查只验证界面和请求结构；不擅自启动长时间真实平台全量任务。

### Step 4：同步学习版与封装

运行项目现有的正式版→学习版同步、发布校验和打包脚本，不手工复制：

```powershell
.\scripts\sync-from-formal.ps1
.\scripts\verify-release.ps1
.\scripts\package-learning.ps1
```

若实际脚本位于封装目录，先读取现有说明并从其真实位置运行；禁止新写一套打包脚本。

Expected:

- 学习版与正式版本轮功能一致；
- 发布校验通过；
- 生成新的学习群分发包；
- 包内不含本机登录态、历史数据、虚拟环境、`node_modules` 或本机绝对路径。

### Step 5：最终复审

检查：

```powershell
git diff --check
git status --short
```

人工确认：

- 没有使用 10000/100000 模拟不限量；
- 没有恢复旧 WebUI；
- 没有把其他平台内部 ID 冒充公开号；
- 没有覆盖用户既有未提交改动；
- 没有宣称平台不可见数据也能采集。

### Step 6：提交最终接线与验证记录

```powershell
git add docs/superpowers/reports/2026-07-30-unlimited-crawl-modes-verification.md
git commit -m "docs: verify unlimited crawl modes"
```

