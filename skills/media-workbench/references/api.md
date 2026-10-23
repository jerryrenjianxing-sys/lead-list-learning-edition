# 接口与通用分析

沿用首页短卡中的随包 Python、客户端与 `--instance` 前缀，将末尾 `connect` 替换为所需命令。例如 `api METHOD PATH --json-file FILE`；Windows 主路径直接调用随包 Python，现有 `scripts/workbench.ps1` 仍可兼容。路径以 `/api/v1` 开头；返回 JSON，非成功响应返回非零退出码。请求文件使用 UTF-8。

首次接入使用 `connect`，完成受保护的轻量握手，取得版本、能力摘要与按需帮助；`check` 保留原有能力字段。正常操作由客户端处理连接与授权，不必在每个操作之前另跑一次 `connect`。细化参数按需查询 `api GET /openapi.json`。这份说明不要求读取完整 OpenAPI 或全部参考文档后才开始任务。

## 发现与采集

- GET `/health`、`/capabilities`、`/platforms`、`/diagnostics`。
- GET `/agent/handshake` 是授权就绪与能力摘要，不触发诊断、登录或采集；health 有响应本身不能证明客户端授权成功。完整 capabilities 只在需要核对具体平台能力时读取。
- POST `/jobs`：platform 为 xhs/dy/ks/bili/wb/tieba/zhihu；crawler_type 为 search/detail/creator/login。search 提供 keywords，detail 提供逗号分隔 specified_ids，creator 提供 creator_ids。max_notes_count 和 max_comments_count 为 0 表示没有用户指定数量上限。可设 enable_comments、enable_sub_comments、enrich_profiles、headless。采集保存内容、评论与公开资料，不下载图片或视频文件。
- GET `/jobs`、`/jobs/{id}`、`/jobs/{id}/events?after=0`。任务依次执行。
- POST `/jobs/{id}/stop` 或 `/jobs/{id}/resume`，请求体 `{}`。
- POST `/jobs/clear-history` 清理已结束的任务记录，`{}` 表示全部已结束任务，或传 `{"ids":["任务编号"]}`。运行/排队任务不能清理。清理可逆，保留数据、成果、认证和诊断日志；GET `/jobs?include_archived=true` 可包含已清理记录，POST `/jobs/restore-history` 使用同样的请求体恢复。默认 GET `/jobs` 隐藏已清理记录并返回 `archived_count`。
- GET `/jobs/{id}/qrcode` 返回图像；需要二维码时也可到界面打开任务。

直接提交采集任务即可，后台会恢复已保存账号并检查认证。无需要求用户每次点击登录。

`GET /platforms` 保留兼容字段 `login_state`、`saved_session`、`verified_at`。`login_state` 记录最近认证，不能替代本次检查；`saved_session` 仅表示有浏览器缓存。新增 `last_check.state` 为 authenticated/logged_out/challenge/network_error/unknown/not_checked；`last_check.at` 为检查时间。网络异常保留最近成功记录但不代表当前在线。`can_select` 表示可以尝试采集，实际执行前仍验证。`account.name` 为昵称或脱敏摘要，`session_status` 为 saved/failed/not_saved，`saved_at` 为保存时间，`session_error` 为保存提示，`needs_user_action` 表示明确需要登录或验证。

POST `/platforms/{platform}/session/check` 检查并保存现有登录，`/session/open` 打开平台浏览器，`/session/login` 登录或完成人工验证；均使用现有任务队列并返回任务编号，重复请求复用活动流程。`/session/forget` 删除本机账号档案及加密备份，平台有活动任务时返回 409；只在用户明确要求移除账号时调用。上述操作请求体为 `{}`，不返回 Cookie。软件最多保留一个有界面浏览器，普通任务不抢焦点，空闲五分钟关闭。

`needs_login` 任务在同账号新认证且保存成功后自动恢复；主动停止的任务不自动恢复。`account_confirmation_jobs` 列出账号不一致或无法核对的任务，需用户确认后调用原任务 `/resume`。网络错误或无法确认时任务为 interrupted，可恢复原任务，不创建重复数据集。

`login` 返回最近登录流程的 `job_id`、`attempt`、`phase`、`active`、`started`、`reason` 和用户提示 `message`。阶段包括 queued、starting_browser、browser_ready、opening_login、waiting_scan、verifying、authenticated，以及 failed/cancelled/interrupted。请提示用户等待浏览器弹出、扫码并在手机确认，最终以认证结果为准。登录专用任务使用 `{"platform":"xhs","crawler_type":"login"}`；不创建数据集，不继承采集选项。同平台已有活动登录流程时，创建或恢复会返回已有任务的 `id`（`reused: true`），以返回值继续查询。请求超时先查询平台的活动流程，不把响应慢当作没有启动。

## 数据与分析

1. GET `/datasets` 查看数据，GET `/datasets/{id}/records?after=0&limit=100` 分页读取；可加 q 或 kind。next_after 为 null 表示已读完；一次最多 2000 条，可以持续翻页。
2. POST `/datasets` 可导入：`{"name":"自定义数据","records":[{"id":"source-1","text":"原始内容"}]}`。原始字段不固定。已有 dataset 可 POST `/datasets/{id}/records`，体为 `{"records":[...]}`。JSON/JSONL/CSV/XLSX 文件也可用客户端 `upload /api/v1/imports/file PATH`。
3. POST `/analyses`：`{"dataset_id":"...","name":"用户指定标题","goal":"用户的分析目标"}`。可选 record_ids 限定范围，可选 result_schema 定义结果对象的 JSON Schema，可选 metadata 保存方法。创建时固定输入快照，后续采集不会偷偷改变本次分析数据。
4. GET `/analyses/{id}/inputs?state=pending&after=0&limit=100` 读取待处理输入。id 是工作台稳定记录编号，payload 是原文数据。
5. POST `/analyses/{id}/batches`：`{"batch_id":"自定且唯一的批次编号","processed_ids":["记录编号"],"results":[{"payload":{"任意字段":"用户需要的值"},"evidence_ids":["记录编号"]}],"skipped":{},"failed":{}}`。跳过或失败采用 `{"记录编号":"具体原因"}`，之后可以用新批次补处理。空 results 也可用于记录没有命中的已处理数据。
6. GET `/analyses/{id}` 查看覆盖率；GET `/analyses/{id}/results` 查看已保存结果（offset/limit 分页）。POST `/analyses/{id}/finish`，体为 `{}`；用户选择交付部分成果时可传 `{"allow_partial":true}`，状态保留 partial。
7. POST `/analyses/{id}/export`：`{"format":"xlsx"}`，也支持 csv/json/docx/md。返回 artifact id 与下载路径。客户端 `download /api/v1/artifacts/{id}/download output.xlsx` 保存文件。其他文件用 `upload /api/v1/analyses/{id}/attachments PATH` 保存。

## 其他操作

GET/PUT `/settings` 支持 browser_path、theme、update_feed；GET `/artifacts` 列出成果；POST `/backup` 创建数据库备份；POST `/imports/legacy` 的 `{"path":"旧数据目录绝对路径"}` 导入旧版数据且不修改源文件。POST `/demo` 导入合成演示数据。

以上是接口示例，用户可组合为任意分析流程。软件没有内置模型或固定业务判断器。


## 0.5.2 原生重试计划

搜索任务默认`retry_threshold: 100`：同一数据集累计去重内容、评论和回复不超过100条视为未跑通，随机等待90–120分钟，继续原任务及原数据集。`retry_threshold: null`显式关闭此规则；它是判定阈值，不是采集数量上限。登录及其他任务类型不使用此阈值。

`status_detail.retry.record_count`只统计内容、评论和回复（不含账号资料），`counts.dataset_total`保留全部类型的累计数。定时续接需要账号一致且认证有效；认证未确认时`blocked_reason`为`authentication_required`。0.5.3保留运行中指定的`next_retry_at`，并对`waiting_retry`同样执行原目标重复异常后的人工复核限制。

访问对照提前成功时，到期仍须登记原生重放次数；检查返回网络错误、接口异常或不确定结果时，保存新的随机90–120分钟截止时间和检查操作，`blocked_reason`为`access_check_pending`，不保留无人处理的过期时间。

`POST /jobs/{id}/retry-plan`：`{"enabled":true,"threshold":100,"min_seconds":5400,"max_seconds":7200,"schedule":true}`。可选`next_retry_at`为UTC Unix秒，用于接续已保存的等待；`schedule:false`只保存规则，不主动停止当前执行。每轮随机结果持久保存，重开不重抽。状态为`waiting_retry`，`status_detail.next_retry_at`给出UTC ISO时间，`status_detail.retry`给出规则。`stop`真正取消自动续接；`resume`显式立即恢复，原生访问和认证限制仍有效。不要再用`stop`接口表达系统安排的定时等待，也不要为同一关键词反复新建数据集。
