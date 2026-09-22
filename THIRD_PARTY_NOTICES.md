# 第三方来源与许可

本项目是 MediaCrawler 的衍生版本，保留根目录 LICENSE（NON-COMMERCIAL LEARNING LICENSE 1.1）及原作者文件头；不能将整个衍生项目改称 MIT。个人学习与源码展示遵循原许可。

| 组件 | 来源及版本 | 说明 |
| --- | --- | --- |
| MediaCrawler | https://github.com/NanmiCoder/MediaCrawler ，380b426000aac3d612837ed72c99808347dc94c9 | 采集与原有 API；保留本机定制分支与合并历史 |
| MediaFlow 首页和窗口生命周期 | 用户提供的本地 MediaFlow 项目，2026-09-22 复制 | 首页组件、CSS、旧版 favicon、Agent 图标；窗口与托盘设计移植 |
| Agent 图标 | webui/public/agents/NOTICE.md | 原商标属于对应权利人；展示不表示厂商背书或全部实测通过 |
| GSAP | https://github.com/greensock/GSAP ，3.15.0 | 用于明暗切换；遵循 GSAP Standard License（https://gsap.com/standard-license），不标作 MIT；圆形过渡改编自用户 MediaFlow 历史实现 |
| Lucide | webui/package-lock.json 中锁定的 lucide-react | 太阳和月亮图标，ISC；随前端保留来源和许可 |
| Hermes ComfyUI Skill | https://github.com/NousResearch/hermes-agent/tree/main/optional-skills/creative/comfyui ，5.1.0 | 借鉴 Skill/参考文档/客户端结构；未复制其执行脚本或 Agent 运行时 |
| Velopack | https://github.com/velopack/velopack ，1.2.0 | MIT，安装和更新工具；随包保留版权与许可 |
| WebView2 | https://developer.microsoft.com/microsoft-edge/webview2/ | 微软运行时及 SDK；使用官方再分发条款，并非开源浏览器运行时 |
| python-build-standalone | https://github.com/astral-sh/python-build-standalone ，CPython 3.11.16 | uv 管理的独立再分发 Python；保留其许可证目录 |
| Node.js | https://nodejs.org/ ，22.22.3 | 随包 LICENSE；官方 SHA256 校验 |
| Playwright / Chromium | https://github.com/microsoft/playwright ，1.61.0 / Chromium 1228 | 保留浏览器自带许可；用于自动化和无本机浏览器时的备用环境 |

Python 依赖以 uv.lock、packaging/requirements.lock.txt 为准；前端以 webui/package-lock.json 为准。Python wheel 的 dist-info 许可证随完整环境一起交付；Node、Chromium 的许可位于 runtime 相应目录。网页依赖的许可随包放在 licenses/frontend。原采集源码保留媒体模块以便跟踪上游，但应用关闭媒体下载入口；公开预览包不再携带独立 FFmpeg/FFprobe 程序。

当前 Media Deep Researcher 应用 Logo 由内置 ImageGen 为本项目生成，不使用 MediaFlow 的旧 favicon；选定原图、备选图和生成提示词保存在 assets/branding/。

下载来源与 SHA256 记录于 packaging/downloads.lock.json；发行目录 build-manifest.json 记录全部文件哈希。Windows 安装包作为未签名的公开预览版提供；验收范围及未完成项目见 docs/VALIDATION.md。WebView2 仍使用微软官方离线安装程序及其原始条款。
