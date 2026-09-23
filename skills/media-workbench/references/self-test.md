# 独立运行自检

软件 0.2.0 起在 capabilities.operations 中公布 `self_test`。旧版不支持时提示升级，不用正式任务模拟自检。

客户端 `self-test` 默认运行本机检查与最多 45 秒的微博游客试采；`--local-only` 不访问社媒。总预算 180 秒。全程使用独立临时目录，结束清理合成数据与测试导出，只保留最近一次检查摘要。

- `POST /api/v1/diagnostics/self-tests`，请求 `{"include_network": true}`：开始或复用。
- `GET /api/v1/diagnostics/self-tests/latest`：最近一次，尚未检查返回 null。
- `GET /api/v1/diagnostics/self-tests/{id}`：状态、耗时与步骤。
- `POST /api/v1/diagnostics/self-tests/{id}/cancel`，请求 `{}`：停止并清理。

`state` 表示整次执行进度，`completed` 只表示检查结束；判定本机是否通过必须看 `local_state`，联网看 `network_state` 和对应步骤。`limited` 是明确登录或人工验证限制，`unconfirmed` 是网络异常、空结果等未能确认；不要把这些状态说成通过或要求用户扫码。`failed` 是程序或本机功能未通过，`skipped` 是未检查。

只测试微博游客搜索与一条详情，不覆盖七个平台全部功能、完整评论、长期登录或模型分析质量。保存的结果只有状态摘要，不包含账号凭据和抓取正文；无需提交正式成果或另写业务报告。
