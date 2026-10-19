# 独立运行自检

用户明确要求验收安装、检查整套运行或排查基础环境时使用。“刚安装，连接一下”“你能做什么”“先确认连接再研究”先完成轻量 `connect`，已有研究目标时继续研究。平台登录、验证码或单次采集问题先查对应状态与原任务日志。

沿用首页短卡的随包 Python、客户端与 `--instance` 前缀，将末尾 `connect` 替换为 `self-test`。软件 0.2.0 起在 capabilities.operations 中公布 `self_test`；旧版不支持时提示升级，不用正式任务模拟自检。

客户端 `self-test` 默认运行本机检查与最多 45 秒的微博游客试采；`--local-only` 不访问社媒。总预算 180 秒：包含联网时，本机 125 秒、联网 45 秒、清理 10 秒；仅本机时为本机 170 秒、清理 10 秒。各阶段启动和退出计入预算。全程使用独立临时目录，结束清理合成数据与测试导出，只保留最近一次脱敏检查摘要。

希望先给用户进度时加 `--no-wait`，取得检查编号后用 `self-test --status ID` 查询同一次检查；取消用 `self-test --cancel ID`。重复提交会复用正在运行的检查；超时先查询已有编号，不重复启动。普通 `self-test` 也可由客户端等待并汇总，告诉用户这是完整诊断而非接入耗时。

- `POST /api/v1/diagnostics/self-tests`，请求 `{"include_network": true}`：开始或复用。
- `GET /api/v1/diagnostics/self-tests/latest`：最近一次，尚未检查返回 null。
- `GET /api/v1/diagnostics/self-tests/{id}`：状态、耗时与步骤。
- `POST /api/v1/diagnostics/self-tests/{id}/cancel`，请求 `{}`：停止并清理。

`state` 表示整次执行进度，`completed` 只表示检查结束；判定本机是否通过必须看 `local_state`，联网看 `network_state` 和对应步骤。`limited` 是明确登录或人工验证限制，`unconfirmed` 是实际尝试后网络异常、空结果等未能确认；这些不是通过，也不要求用户扫码。`failed` 是程序或本机功能未通过；超时的本机阶段为 `incomplete`，保留此前已完成项目。

步骤 `attempted:false`、`state:skipped` 表示未执行，结合 `reason` 说明未开始原因，不能算联网失败或平台限制。`phase_budget_exhausted` 是阶段预算已用完，`disabled` 是本次仅检查本机，`prerequisite_failed` 是前序未完成，`cancelled` 是检查已取消。已开始但未及时结束的项目可能为 `interrupted`、`reason:phase_timeout`。`phases`、`timings` 提供阶段和细项耗时，界面可展开查看；它们帮助区分运行环境、浏览器和后台启动，不代表 Agent 首次接入全程耗时。

只测试微博游客搜索与一条详情，不覆盖七个平台全部功能、完整评论、长期登录或模型分析质量。保存的结果只有状态摘要，不包含账号凭据和抓取正文；无需提交正式成果或另写业务报告。
