# 连接与恢复

- `check` 找不到实例：启动Media Deep Researcher后重试。默认发现文件为 `%LOCALAPPDATA%/MediaWorkbench/instance.json`；可用 `--instance FILE` 指定。文件内有本机访问凭据，不放入报告或公开仓库。
- Windows PowerShell 入口直接使用软件携带的 Python。Hermes 若在同一电脑的 WSL，可通过 `powershell.exe -NoProfile -File <Windows路径> check` 使用 Windows 入口；纯云端 Agent 无法直接访问用户电脑的 localhost。
- 请求超时：保留客户端输出的 request_id，执行 `receipt ID`。已登记时使用原响应；未登记时用相同 request_id 和原请求内容重试。改变请求内容必须换编号。
- job interrupted/cancelled/partial/failed：先读 events 和 checkpoint，修复实际原因，再 resume。保存的数据会去重；通用采集恢复会重新经过部分已抓页面，不宣称所有平台都有精确游标续跑。
- needs_login：提示用户在“平台登录”完成登录或验证。同账号验证且保存成功后，后台自动继续原任务及原数据集；先查询状态，避免重复创建。账号不一致或无法确认时由用户在页面确认继续，主动停止的任务不会自动续接。
- last_check 为 network_error/unknown：账号和最近成功记录仍保留，不能报告为正常在线，也不要要求重新扫码；网络恢复后重试原任务。session_status 为 failed 表示登录可能有效但保存未完成，软件的“更多操作 → 重试保存”会重新检查并保存。
- 登录信息保存在专用浏览器档案及 Windows 用户加密文件中。不要读取、输出或传送这些凭据；清理任务记录不会清除账号。只有用户明确要移除账号时才使用“忘记此账号”。
- Agent 会话中断：列出 analyses，选择原任务，查看 coverage 和 inputs 的 pending/failed 项后继续。批次编号和写入编号保留，避免重复成果。
- 数据库属于更新版本：运行匹配版本的软件，或在保留现有数据副本后使用之前的备份。客户端不会擅自降级数据库。
