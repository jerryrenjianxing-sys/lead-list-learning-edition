# Media Deep Researcher

<img src="webui/public/brand/media-deep-researcher.png" alt="Media Deep Researcher Logo" width="96">

原「潜客名单学习版」现升级为 Media Deep Researcher。保留原仓库地址，新增完整源码、桌面工作台和外置 Agent Skill。

使用自己的 Agent，操作本地七平台采集、数据管理和通用分析。Windows 桌面首页提供 Skill；软件没有内置模型或模型账户。

产品显示名为 Media Deep Researcher；内部应用标识及数据目录继续使用 MediaWorkbench，兼容已有安装、登录会话和 Skill 客户端。

> 当前是 0.2.0 预览版，GitHub 标签为 `v0.2.0`。新增独立运行自检及 GitHub 软件内升级；见[版本说明](docs/RELEASE_NOTES.md)。真实平台、独立 Agent 推理及干净 Windows 系统仍按[验证报告](docs/VALIDATION.md)逐项验收。

## 下载

- [下载 Windows x64 安装包](https://github.com/jerryrenjianxing-sys/lead-list-learning-edition/releases/download/v0.2.0/MediaWorkbench.Desktop-win-preview-Setup.exe)：包含 Python、Node、采集浏览器与 WebView2 离线安装程序，无需自行配置开发环境。
- [新版发行页](https://github.com/jerryrenjianxing-sys/lead-list-learning-edition/releases/tag/v0.2.0)：便携版、完整源码、Skill、更新包、校验值与验收说明。
- [旧版下载](https://github.com/jerryrenjianxing-sys/lead-list-learning-edition/releases/tag/v2026.07.29)：保留 2026 年 7 月的学习版。

普通使用者下载安装包；GitHub 的 **Code → Download ZIP** 提供开发源码。安装新版不会自动导入 7 月旧版的采集文件，可在任务台按需导入。

## 使用

安装完整包后启动「Media Deep Researcher」，在首页点击「复制 Skill」，粘贴给能执行本地命令的 Agent。Agent 按说明获取完整 Skill 和配套客户端，然后连接软件。Windows 客户端 scripts/workbench.ps1 自动找到软件和内置 Python。也可以进入任务台手动采集、导入数据、查看分析与导出成果。

只想确认软件能否运行时，在任务台「设置 → 运行自检」点击「一键自检」；断网可选「仅检查本机」。自检无需社媒账号或模型，测试数据自动清理，不创建正式任务或研究报告。Skill 也支持 `scripts/workbench.ps1 self-test`。本机检查和游客联网试采分别给出状态，不代表七平台全部功能通过。

要体验分析，可点击「加载演示数据」。这是合成的通勤反馈；分析目标、方法、字段、数量和输出形式由用户决定。Agent 能保存任意结构化结果、证据引用和其他文件附件。

发行附件和本地 `release/0.2.0` 目录包含完整安装包 `MediaWorkbench.Desktop-win-preview-Setup.exe`、便携包、Skill ZIP、源码 ZIP 和 SHA256 校验表。系统验收目标为 Windows 10/11 x64；干净系统验收尚待完成。

0.1.1 首次运行新版完整安装包接入更新通道；以后在设置或托盘检查更新。自动检查最多每天一次，只提示；下载和安装分别由用户点击。采集或自检运行时暂缓安装，安装前备份数据。

- 七个平台：小红书、抖音、快手、哔哩哔哩、微博、贴吧、知乎；具体能力通过接口与平台登录页展示。
- 持久化队列：界面与 Skill 操作同一任务，刷新页面或关闭 Agent 不丢队列。
- 登录会话：每个平台固定浏览器档案，Windows 加密保存 Cookie、Local Storage 和 IndexedDB，采集自动检查和恢复；网络异常不强制扫码，同账号认证后可继续暂停任务。
- 采集保存内容、评论和公开资料，不下载图片或视频文件；旧任务恢复也遵循此规则。
- 通用分析：固定数据快照、批次进度、证据引用、处理覆盖率与幂等提交。
- 导出：JSON、CSV、Excel、Word、Markdown；其他文件作为成果附件。
- Windows：单实例、托盘、关闭窗口后后台继续运行、Velopack 安装与更新。
- 程序与数据分开：默认数据位于 %LOCALAPPDATA%/MediaWorkbench/data；卸载不主动删除此目录。

## 开发

需要 uv、Node.js 与 Windows 开发环境。

```powershell
uv python install 3.11.16
uv sync --locked --python 3.11.16
npm.cmd --prefix webui ci
npm.cmd --prefix webui run build
uv run python -m workbench
```

默认端口 48139；占用时分配本机空闲端口，并更新 %LOCALAPPDATA%/MediaWorkbench/instance.json。实例文件含本机凭据，不公开上传。Vite 开发代理默认指向 48139，使用动态端口时相应调整代理。

```powershell
uv run python -m pytest tests -q
powershell -ExecutionPolicy Bypass -File packaging/build-windows-release.ps1
```

完整包从 python-build-standalone 提供的 Python 3.11.16、锁定的 Python wheels、Node.js 22.22.3、Playwright Chromium 和官方 WebView2 离线安装程序构建。构建脚本会校验已锁定下载的 SHA256。应用不下载媒体，不再分发独立 FFmpeg/FFprobe 程序。Windows 壳使用系统 C# 编译器、WebView2 SDK 与 Velopack SDK，无需安装 Python/Node 到用户系统。

版本唯一来源为 `pyproject.toml`。公开 `main` 提交触发检查；`v版本号` 标签触发 Windows 构建、自检及 GitHub 草稿上传。全部附件校验通过后才公开预览发行，标签与源码版本不一致时拒绝发布。`win-preview` 通道用于本阶段预览版本，已公开的同版本文件不覆盖。

## 架构

用户 Agent → Skill 客户端 → FastAPI /api/v1 → SQLite 任务/数据/分析 → 七平台采集子进程。桌面页面使用同一 API；写请求有事务内回执，网络重试可按相同请求编号查询结果。

采集在后台运行；分析推理由外部 Agent 完成，Agent 离线时保存进度并等待继续。通用采集恢复采用重新请求与数据去重，不能把它等同于所有平台都支持精确远端游标恢复。

任务采集的原始 JSONL 与数据版本保留；创建分析时固定输入快照。统计覆盖率检查是否全量处理，结构及引用验证不等于模型结论准确性评估。

Skill 内置[可选潜客模板](skills/media-workbench/references/lead-analysis.md)，整合原有买方判断、高召回、P0–P3、账号来源核对和报告方法。用户询问能力或提出相关需求时可按需推荐、采用；用户要求优先，不强制评分、人数、行业、问卷或报告格式。详细[账号规则](skills/media-workbench/references/public-accounts.md)按需读取。`webui/public/social-lead-analysis-prompt.txt` 是旧版独立提示词存档，新 Skill 不加载其中的固定流程。

应用 Logo 使用内置 ImageGen 生成的 M＋研究镜方案；原图、另外两版和完整提示词见 [Logo 资产说明](assets/branding/README.md)。网页与 Windows 图标已包含在源码中，可运行 `packaging/build-brand-assets.ps1` 从选定原图重新生成尺寸和 ICO。

恢复与备份见 [恢复说明](docs/RECOVERY.md)；对外介绍可参考 [项目展示说明](docs/PROJECT_SHOWCASE.md)。

## 来源与许可

基于 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)，保留其 NON-COMMERCIAL LEARNING LICENSE 1.1。首页复用用户自己的 MediaFlow；Skill 结构借鉴 Hermes ComfyUI。详见 [第三方说明](THIRD_PARTY_NOTICES.md)、[升级记录](docs/UPGRADE_STATUS.md)和 [上游原始说明](docs/UPSTREAM_README.md)。本项目不声称拥有各平台与 Agent 商标。

仓库地址：[jerryrenjianxing-sys/lead-list-learning-edition](https://github.com/jerryrenjianxing-sys/lead-list-learning-edition)。预览版手动下载安装更新，自动更新源尚未启用。真实采集数据、Cookie、浏览器会话、访问凭据和构建缓存不进入仓库或发行包。
