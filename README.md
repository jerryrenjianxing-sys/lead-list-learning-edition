# Media Deep Researcher

<img src="webui/public/brand/media-deep-researcher.png" alt="Media Deep Researcher Logo" width="96">

原「潜客名单学习版」现升级为 Media Deep Researcher。保留原仓库地址，新增完整源码、桌面工作台和外置 Agent Skill。

使用自己的 Agent，操作本地七平台采集、数据管理和通用分析。Windows 桌面首页提供 Skill；软件没有内置模型或模型账户。

产品显示名为 Media Deep Researcher；内部应用标识及数据目录继续使用 MediaWorkbench，兼容已有安装、登录会话和 Skill 客户端。

> **0.4.2 预览版**：首页只复制 Markdown Skill 文件，支持改名，移除文字复制和长提示。沿用0.4.0的轻量接入、诊断及任务台改进，保留依赖、业务数据和账号档案。用户本机试用后批准推送新版；真实 Agent 附件粘贴兼容性及新会话验收仍单独记录。见[文件接入规格](docs/specs/0.4.2-md-only.md)、[架构](docs/ARCHITECTURE.md)和[验证说明](docs/VALIDATION.md)。

## 下载

- [下载 Windows x64 安装包](https://github.com/jerryrenjianxing-sys/lead-list-learning-edition/releases/download/v0.4.2/MediaWorkbench.Desktop-win-preview-Setup.exe)：包含 Electron、Python、Node 和采集浏览器，无需自行配置开发环境。
- [新版发行页](https://github.com/jerryrenjianxing-sys/lead-list-learning-edition/releases/tag/v0.4.2)：便携版、完整源码、Skill、更新包、校验值与验收说明。
- [0.3.0 发行页](https://github.com/jerryrenjianxing-sys/lead-list-learning-edition/releases/tag/v0.3.0)：保留 Electron 首版历史；其软件内更新源存在问题，请使用新版完整安装包升级。
- [0.2.0 发行页](https://github.com/jerryrenjianxing-sys/lead-list-learning-edition/releases/tag/v0.2.0)：保留迁移前版本及验证记录。
- [旧版下载](https://github.com/jerryrenjianxing-sys/lead-list-learning-edition/releases/tag/v2026.07.29)：保留 2026 年 7 月的学习版。

普通使用者下载安装包；GitHub 的 **Code → Download ZIP** 提供开发源码。安装新版不会自动导入 7 月旧版的采集文件，可在任务台按需导入。

## 使用

启动「Media Deep Researcher」，在首页点击「复制 Skill」，将 `.md` 文件粘贴给能执行本机命令的 Agent。「修改文件名」可改名。普通浏览器和旧桌面提示在新版桌面软件中复制，不回退成文字。附件是本机入口，包含完整 Skill、随包 Python、客户端和实例文件的绝对路径；从任意工作目录执行一条 `connect` 命令即可验证权限。仅在本机文件不可访问时使用 ZIP 后备，不需要系统 Python 或修改执行策略。也可以进入任务台手动采集、导入数据、查看分析与导出成果。

仅连接、询问能力或已有研究目标时不会自动运行整套自检。正式研究直接按用户目标执行，平台异常先查看对应状态和任务。安装包沿用原产品身份并保留数据；历史0.4.0候选的隔离启动方式见其自带说明。

只想确认软件能否运行时，在任务台「设置 → 运行自检」点击「一键自检」；断网可选「仅检查本机」。自检无需社媒账号或模型，测试数据自动清理，不创建正式任务或研究报告。Skill 也支持 `scripts/workbench.ps1 self-test`。本机检查和游客联网试采分别给出状态，不代表七平台全部功能通过。

要体验分析，可点击「加载演示数据」。这是合成的通勤反馈；分析目标、方法、字段、数量和输出形式由用户决定。Agent 能保存任意结构化结果、证据引用和其他文件附件。

发行附件和本地 `release/0.4.2` 目录包含完整安装包 `MediaWorkbench.Desktop-win-preview-Setup.exe`、便携包、Skill ZIP、源码 ZIP 和 SHA256 校验表。系统验收目标为 Windows 10/11 x64；干净系统验收尚待完成。

已经安装 0.3.0 的用户，请用新版完整安装包覆盖升级一次，以修正该版本的软件内更新源；任务、数据和登录档案保留。0.2.0 可通过原有更新通道或完整安装包升级到 Electron；0.1.1 首次使用完整安装包接入更新通道。修正后在设置或托盘检查更新，自动检查最多每天一次，只提示；下载和安装分别由用户点击。采集或自检运行时暂缓安装，安装前备份数据。

- 七个平台：小红书、抖音、快手、哔哩哔哩、微博、贴吧、知乎；具体能力通过接口与平台登录页展示。
- 持久化队列：界面与 Skill 操作同一任务，刷新页面或关闭 Agent 不丢队列。
- 登录会话：每个平台固定浏览器档案，Windows 加密保存 Cookie、Local Storage 和 IndexedDB，采集自动检查和恢复；网络异常不强制扫码，同账号认证后可继续暂停任务。
- 采集保存内容、评论和公开资料，不下载图片或视频文件；旧任务恢复也遵循此规则。
- 通用分析：固定数据快照、批次进度、证据引用、处理覆盖率与幂等提交。
- 导出：JSON、CSV、Excel、Word、Markdown；其他文件作为成果附件。
- Windows：Electron 桌面、单实例、托盘、关闭窗口后后台继续运行、Velopack 安装与更新。
- 程序与数据分开：默认数据位于 %LOCALAPPDATA%/MediaWorkbench/data；卸载不主动删除此目录。

## 开发

需要 uv、Node.js 与 Windows 开发环境。

```powershell
uv python install 3.11.16
uv sync --locked --python 3.11.16
npm.cmd --prefix webui ci
npm.cmd --prefix webui run build
npm.cmd --prefix electron ci
npm.cmd --prefix electron test
uv run python -m workbench
```

默认端口 48139；占用时分配本机空闲端口，并更新 %LOCALAPPDATA%/MediaWorkbench/instance.json。实例文件含本机凭据，不公开上传。Vite 开发代理默认指向 48139，使用动态端口时相应调整代理。

```powershell
uv run python -m pytest tests -q
powershell -ExecutionPolicy Bypass -File packaging/build-windows-release.ps1
```

完整包使用 Electron 44.7.0、Forge 8.0.1、Velopack 1.2.0，以及 python-build-standalone 提供的 Python 3.11.16、锁定的 Python wheels、Node.js 22.22.3 和 Playwright Chromium。构建脚本校验已锁定下载的 SHA256。Electron 自带的 Node/Chromium 用于桌面，采集继续使用独立运行时与专用账号档案。使用者无需安装 Python、Node、WebView2 或 .NET Framework；构建机仍需打包工具。应用不下载媒体，不分发独立 FFmpeg/FFprobe。

版本唯一来源为 `pyproject.toml`。`main` 和 `modernization/**` 提交触发检查；候选构建通过手动 GitHub Actions 或本地构建，只收集产物。`packaging/release-policy.json` 阻止候选源码调用公开发布器。未来恢复公开发布需先完成真实 Agent 验收；现有 `win-preview` 通道和已发布附件保持原状。开发流程见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 架构

用户 Agent → Skill 客户端 → FastAPI /api/v1 → SQLite 任务/数据/分析 → 七平台采集子进程。Electron 主进程管理窗口、托盘、后台生命周期及更新；页面使用同一 API。隔离 preload 只提供受限更新操作；写请求有事务内回执，网络重试可按相同请求编号查询结果。

采集在后台运行；分析推理由外部 Agent 完成，Agent 离线时保存进度并等待继续。通用采集恢复采用重新请求与数据去重，不能把它等同于所有平台都支持精确远端游标恢复。

任务采集的原始 JSONL 与数据版本保留；创建分析时固定输入快照。统计覆盖率检查是否全量处理，结构及引用验证不等于模型结论准确性评估。

Skill 内置[可选潜客模板](skills/media-workbench/references/lead-analysis.md)，整合原有买方判断、高召回、P0–P3、账号来源核对和报告方法。用户询问能力或提出相关需求时可按需推荐、采用；用户要求优先，不强制评分、人数、行业、问卷或报告格式。详细[账号规则](skills/media-workbench/references/public-accounts.md)按需读取。`webui/public/social-lead-analysis-prompt.txt` 是旧版独立提示词存档，新 Skill 不加载其中的固定流程。

应用 Logo 使用内置 ImageGen 生成的 M＋研究镜方案；原图、另外两版和完整提示词见 [Logo 资产说明](assets/branding/README.md)。网页与 Windows 图标已包含在源码中，可运行 `packaging/build-brand-assets.ps1` 从选定原图重新生成尺寸和 ICO。

恢复与备份见 [恢复说明](docs/RECOVERY.md)；对外介绍可参考 [项目展示说明](docs/PROJECT_SHOWCASE.md)。

## 来源与许可

基于 [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler)，保留其 NON-COMMERCIAL LEARNING LICENSE 1.1。首页复用用户自己的 MediaFlow；Skill 结构借鉴 Hermes ComfyUI。详见 [第三方说明](THIRD_PARTY_NOTICES.md)、[升级记录](docs/UPGRADE_STATUS.md)和 [上游原始说明](docs/UPSTREAM_README.md)。本项目不声称拥有各平台与 Agent 商标。

仓库地址：[jerryrenjianxing-sys/lead-list-learning-edition](https://github.com/jerryrenjianxing-sys/lead-list-learning-edition)。预览版通过 GitHub 检查更新，下载及安装由用户点击。真实采集数据、Cookie、浏览器会话、访问凭据和构建缓存不进入仓库或发行包。
