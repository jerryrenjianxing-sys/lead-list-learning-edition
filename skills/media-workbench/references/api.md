# 接口与通用分析

以下请求均使用 `scripts/workbench.ps1 api METHOD PATH --json-file FILE`，或者 Python 客户端。路径以 `/api/v1` 开头；返回 JSON，非成功响应返回非零退出码。

## 发现与采集

- GET `/health`、`/capabilities`、`/platforms`、`/diagnostics`。
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
