# Electron 桌面迁移

## 版本与边界

0.3.0 面向 Windows 10/11 x64。桌面使用 Electron 44.7.0、Electron Forge 8.0.1、Velopack 1.2.0，源码版本由 pyproject.toml 决定；依赖和官方二进制下载固定版本及哈希。

页面继续使用 React/Vite，由现有 FastAPI 后台在本机提供。正式采集、登录保存和通用分析继续使用同一套 API。平台登录浏览器独立于 Electron 桌面浏览器。

## 安装与数据

安装身份仍为 MediaWorkbench.Desktop，主程序仍为 MediaWorkbench.exe，更新通道仍为 win-preview。Python、Node、Playwright 浏览器及后台模块保持原包内位置，避免破坏已保存账号的浏览器路径绑定。

默认数据目录为 `%LOCALAPPDATA%/MediaWorkbench/data`，发现文件为 `%LOCALAPPDATA%/MediaWorkbench/instance.json`。MEDIAWORKBENCH_DATA_DIR 和 MEDIAWORKBENCH_INSTANCE 继续有效。Electron 自身页面缓存与账号浏览器档案分开；卸载不主动删除业务数据。

0.2.0 可通过软件更新或完整安装包升级。本轮没有数据库结构迁移；安装前仍执行数据库备份。账号若已被平台判定失效，需要按正常登录流程验证，桌面迁移不延长平台会话有效期。

## 生命周期与接口

关闭窗口进入托盘，已提交任务继续；托盘“退出并保存进度”正常关闭后台和受管理采集浏览器。旧版与新版使用兼容的 Windows 单实例协调机制。`--quit` 保留为本机自动化退出入口；使用独立数据/发现路径的测试实例使用独立协调名称。

桌面只复用产品、版本和实际数据目录匹配的后台。旧后台仍在运行时提示正常退出后重试，不通过删除发现文件或强制结束任务解决。

页面通过隔离 preload 使用 getUpdateStatus、checkForUpdates、downloadUpdate、installUpdate、onUpdateState。主进程核对调用者和精确本机来源；不向页面暴露通用文件访问、进程执行或任意 IPC。普通浏览器仍可使用工作台，安装更新通过 GitHub 发行入口完成。

## 构建

Electron Forge 生成目录包，再与现有后台和随包运行时合并，最后由 Velopack 打包。后台源码与 Python 运行时不放入 app.asar；原生桌面模块解包后加载。完整清单在合并完成后生成。

```powershell
uv sync --locked --python 3.11.16
npm.cmd --prefix webui ci
npm.cmd --prefix webui run build
npm.cmd --prefix electron ci
npm.cmd --prefix electron test
powershell -ExecutionPolicy Bypass -File packaging/build-windows-release.ps1 -Tag v0.3.0
```

发行包含安装包、便携包、完整更新包、源码、Skill、校验值、许可、自检和桌面验证结果。GitHub Actions 拒绝版本与标签不匹配、源码被构建修改、包内文件哈希不一致或桌面/本机确定性检查失败的发布。

标签构建还会下载并校验固定的 0.2.0 安装包，在隔离目录验证完整安装升级、原生完整包更新、损坏包重试及卸载保留数据。packaging/prepare_test_release.py 从干净 stage 生成仅用于验收的下一补丁版本；不修改源码版本、不创建公开标签，发布只上传脱敏验收摘要，不上传测试版本。

## 验证说明

验收分别记录后台回归、Electron 原生生命周期、真实渲染与 preload、旧版升级、更新失败恢复和用户数据保留。测试环境使用独立目录、端口和协调名称，不读取个人平台凭据。

实际结果以发行附件 ELECTRON_TEST_RESULT.json、SELF_TEST_RESULT.json 及 VALIDATION.md 为准。当前机器的安装验证不代替干净 Windows 10/11、全部显示缩放组合、跨天登录或七平台真实采集矩阵。macOS/Linux 本轮没有发行。
