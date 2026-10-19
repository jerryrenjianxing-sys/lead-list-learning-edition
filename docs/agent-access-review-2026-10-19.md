# Media Deep Researcher：架构与 Agent 接入方案会议

日期：2026-10-19。本轮是审查、复现与方案讨论，没有修改运行中的软件、Skill、正式数据、账号或发行。

## 决定建议

**保留 Electron、FastAPI、SQLite 和 MediaCrawler 底座；重做首次 Agent 接入，并按行为契约逐块规范整个项目。当前证据不支持整库推倒重写。**

用户的两个诉求分别验收：一是项目的架构、术语、接口、测试和上游维护规范；二是外部 Agent 首次使用的速度、可靠性和引导。两者相互支撑，不能只缩短提示词，也不能把换架构当作性能修复。

三位子智能体分别提出最小接口、最大灵活性、常见首次使用最简三个备选，随后进行了交叉质询。使用 mattpocock-skills 的 codebase-design（含 Design It Twice）、diagnosing-bugs、research 方法。项目源为本机 `ca02030d541016a04536cb912e7fa2c0f0c3590b`，对应公开 0.3.1 的发行源码；MediaSail 为 0.5.5、`8d40a92c7fb5d946e1da4d16d6e4bef4ab26bb98`。已读取用户指定会话「Find open-source media analyst tools」，确认 MediaSail 实际开发目录。

## 一、实际发生了什么

读取本机保存的同次自检记录，编号 `659b1782220049d581058e4e33359083`，与用户截图一致：

| 项目 | 实际记录 | 能说明什么 |
| --- | --- | --- |
| TRAE 会话 | 截图 5 分 31 秒 | 包含引导、命令、自检及回答，不能作为纯连接耗时 |
| 完整 self-test | 180.0 秒，timed_out | 达到总预算；不是连接握手 |
| 运行环境 | 93.8 秒，passed | 阶段内包含启动浏览器；具体慢在哪里尚未定位 |
| 独立后台与页面 | 77.7 秒，passed | 含独立后台和另一次浏览器启动；具体慢在哪里尚未定位 |
| Skill 子步骤 | 0.3 秒，passed | 仅表示隔离自检内的新客户端连接，不是陌生 Agent 端到端接入 |
| 其余本机步骤 | 队列 1.3、数据/导出 3.6、恢复 1.6 秒，passed | 本机六项通过 |
| 联网步骤 | 0 秒，failed；总预算截断 | 未取得可用执行时间，不能据此判断平台、网络或程序失败 |
| 清理 | cleanup_complete=true | 该次临时检查内容已清理 |

前两项共 171.5 秒，已消耗总预算约 95%。会话余下约 151 秒没有分阶段记录，不能具体分摊给下载、模型思考或工具调度。93.8/77.7 秒的原因仍待复现和细分测量，不能提前归因于杀毒软件、硬盘、浏览器损坏或网络。[M3]

本轮只读探测时，发现文件所指进程已不存在，也没有 MediaWorkbench 桌面进程；因此没有取得新的在线暖连接基线，没有启动软件或重新跑三分钟自检。

另有一个已在隔离环境复现的正确性缺口：**错误 token 也能使当前 `check` 返回 status=ok，而同凭据访问受保护接口返回 401。** 原因路径可直接验证：check 仅请求公开的 health 与 capabilities，没有验证授权。[M1][M2]

复现入口：`.venv/Scripts/python.exe .cache/architecture-meeting/check_auth_signal.py`。本轮约 1.39 秒，使用真实 Client.check 和真实 FastAPI 中间件，替换的只有本机 ASGI transport；临时数据库、合成错误凭据，无正式数据。结果 `check_returned_ok=true, protected_status=401, false_positive_ready=true`，断言退出 1，按预期暴露缺陷。不是绕过认证：受保护接口正确拒绝，缺陷是连接成功提示不完整。

## 二、MediaSail 哪些做法值得复用

| 环节 | MDR 现状 | MediaSail 0.5.5 | 建议 |
| --- | --- | --- | --- |
| 取得 Skill | 首页短卡要求下载 ZIP、解压、再读 SKILL | 优先提供本机 Skill 和客户端，ZIP 备用 | 使用已经随包分发的文件，消除重复准备 |
| 运行环境 | 已有绑定 Python 的入口，但卡片未直接定位它 | 提供发现位置并调用绑定的随包 Python | 软件生成可信的完整入口，Agent 不猜解释器 |
| 初次反馈 | 条件式自检文案混在接入卡中 | 轻检查后自然介绍能力，有任务继续 | 区分连接、能力介绍、研究、明确诊断 |
| 接口资料 | 客户端每条命令先完整 check | 按任务读取 Skill references | 简短能力摘要，按需读 schema 和参考资料 |
| 业务数据 | 数据集、分析、证据与事务回执 | 有直接 workspace 文件工作流 | 保留 MDR 的事务与证据规则，不照搬文件写库 |

两款软件的 Skill 正文本来都不长，差异主要在复制后的操作链与可靠性实现，不是字数。[M1][M2][S1][S2][S3]

**不照抄 MediaSail 的授权判断。** 它的 check 使用公共信息和服务状态，业务保护模型与 MDR 的 Bearer token 不同；其中缺失 serviceReady 字段时的默认值也不能作为 MDR 的成功判定。应借鉴其定位和引导设计，MDR 自行做严格、受保护的轻量握手。[S2]

本轮不新建跨项目公共运行库，不把两款软件的安装、发布和资料位置绑在一起。若复制具体代码，按文件核对来源、版权和 NOTICE；MediaSail 根许可证不能替代 MDR 采集上游的许可，本轮未做完整许可兼容法律审查。[S4]

## 三、交叉讨论中保留与否决的方案

- **最少入口方案**有利于把发现、授权、恢复集中，但 connect/execute/observe 三个名字本身不产生 Depth。若 execute 仍让 Agent 拼全部 URL/JSON，只是改名；若变成固定研究 DSL，又会限制分析自由。
- **最大灵活性方案**保留通用 API、任意字段和工具，但不能要求每个 Agent 自己实现重试、回执、分页与环境检测。重复易错流程应进入现有客户端的 implementation。
- **MediaSail 常见路径方案**最适合首页默认使用：一份本机 Skill、一次现成命令、简短反馈。必须补上 MDR 自己的授权和恢复保证，不能仅“读完卡片就宣称连接成功”。

推荐组合：**以本机优先作为接入方式，在现有客户端内部深化连接与恢复；保留现有命令及通用 API。** 暂不把 MCP、新常驻进程或换技术栈当作未经实测的提速办法。

## 四、第一迭代：接入重做 + 全项目规范基线

### 可检查的规范产物

1. 实际模块/入口图：标清生产入口、上游代码、兼容适配、历史参考和构建产物，不见旧文件就删除。
2. 术语及状态定义：采集任务 Job、数据集 Dataset、分析 Analysis、批次 Batch、成果 Artifact、平台会话 PlatformSession；不同对象保留各自生命周期。
3. 三份行为契约：连接与授权；请求身份/回执/恢复；诊断状态、预算和清理。
4. 当前行为到目标行为的验收矩阵，以及上游固定版本、定制补丁和升级差异清单。

### 新的首次使用路径

`复制 Skill → 读本机完整 Skill → 一次连接命令 → 就绪及简短能力说明 → 继续用户任务`

- Windows 主路径由软件生成**随包 Python 与客户端的绝对路径原生调用**，正确引用中文、空格和引号；不要求安装系统 Python，不要求降低 PowerShell 执行策略。现有 .ps1 入口继续兼容。
- ZIP 保留为本机文件不可用或需要独立 Skill 包时的后备。不能访问用户电脑的纯云端 Agent，应明确说明执行位置要求，不盲试 localhost 或自动向公网开放后台。
- 不把瞬时端口当作永久配置。客户端私下读取当前发现信息、绑定运行环境，公开卡片不能复制含 token 的 instance.json 全文。
- 新增一次受保护、只读、紧凑的握手：产品、协议/实例身份、真实授权、能力 revision 与帮助索引。旧 check 保留兼容；旧软件走可确认授权的回退，而非公开健康页即成功。
- 能力摘要区分声明支持、已经验收和当前可用；七平台的待验证能力不能因连接成功就自动变成已通过。
- 业务请求不再无条件重复两次全量检查。发现变化或 401 时有限重新发现、验证；写请求继续使用原 request_id 查回执与恢复，不换编号盲重试。
- 客户端隐藏可靠性流程：发现、兼容、认证头、回执、有限重试、分页游标和短时观察。仅给确实重复且易错的多步操作增加 helper，不给每个 HTTP 路由再造一个命令。
- 保留 api、导入/上传/下载及用户任意分析方式，不新增题材、数量、字段、报告模板限制；潜客模板仍然按需、可修改。

### 引导与诊断的语义

- “连接一下”“能干什么”或仅粘贴接入卡：轻量验连，简短介绍，不创建业务任务、不启动自检浏览器。
- 用户已给具体研究目标：连接后直接处理，不重复开场或强制问卷。
- 用户要验证安装、检查整套运行或明确排障：按真实意图运行相应诊断，不要求背固定口令，也不为了显得快而偷偷省去其要求的完整检查。
- 完整自检保留隔离与三分钟总预算，增加进程启动、导入、浏览器启动、页面加载、重启及清理的阶段计时；按证据定位 171.5 秒的来源。
- 明确分配本机、联网和清理预算。联网未启动应是“未执行/未验证，前序阶段耗尽预算”，区别于实际失败、平台限制和空结果；新状态的界面、客户端及旧版兼容一起处理。不依赖“再跑一遍热缓存”作为首次可用验收。

## 五、第二迭代：按契约逐块重构

| Module | 处理方式 |
| --- | --- |
| Agent 接入 | 首先集中发现、授权、错误分类、恢复和能力摘要；首页/Skill/客户端共用同一接入描述 |
| 诊断 | 与接入分离；保持真实隔离检查，优化前先测量，不删检查项伪造提速 |
| HTTP 适配与组合 | app.py 逐渐只做请求适配和模块组合，领域行为移到真实职责处；不只是把大文件拆成多个小文件 |
| 任务台读取 | 分别呈现各区块错误、按可见页面读取，减少当前全量轮询；不把该问题当作本次五分钟的根因 |
| 队列、账号与分析 | 保留持久化、登录租约、加密快照、去重、稳定编号、分析输入快照/覆盖率和证据规则；通过契约后才局部调整实现 |
| Electron、构建与更新 | 保留沙盒、窄 preload、后台身份、托盘、更新校验和安装身份；接入修复不改变账号路径或制造数据迁移 |

以小 Interface 隐藏真实复杂性，不为每条 SQL、函数或未来想象中的扩展制造抽象。纯计算直接测；SQLite/文件用临时环境；自有本机 HTTP 用真实适配与可控失败适配；社媒真站测试单列。先建立接口行为测试，再替换重复的浅层实现测试；真实安装、安全、升级和数据保留验收继续保留。[M4]

## 六、发布验收与测量口径

以下是**候选工程目标，不是本轮达成的性能承诺**：已就绪后台的暖授权连接 p95 ≤2 秒，客户端冷进程 ≤5 秒；新 Agent 首次仅连接/能力说明尽量 ≤30 秒，并记录模型排队和工具权限开销。失败在有限预算内返回明确原因及一个下一步。

同机、同模型、同工具权限，以真正授权成功和首个正确操作作为共同终点比较 MediaSail 与 MDR。至少 20 个样本才报告初步 p95；少量先报中位数/最大值。分别记录新 Agent 工作目录冷、进程冷、系统缓存冷，不清系统缓存或关闭安全软件制造好成绩。

必须新增的端到端验收：没有项目历史的外部 Agent 仅凭首页卡片，完成连接、能力说明、一个用户指定的有用操作及写回；记录工具次数、读文档量、各段时间。之前客户端功能测试提前提供了实例及解释器，Electron 测试也主要确认复制按钮；这些不是这个真实首用流程的替代。[M5]

故障矩阵包括未启动、旧端口、损坏发现、错误凭据、协议不兼容、升级换路径、重启、代理环境、中文空格路径、Windows/可用 WSL 桥接/纯云端，以及写入执行后丢响应、同 ID 并发请求与续接。保证不会假 ready、不会泄露凭据、不会重复创建任务。

连接/能力说明两类意图的验收必须观测到零个自检启动、零个采集提交、零个正式数据写入、无扫码。完整自检、七平台真站、模型分析质量与桌面安装仍分别验收。TRAE、Codex、Hermes 的实际支持以各自实测为准，不由品牌名推断。

## 本轮产物与边界

只新增此会议纪要及 `.cache/architecture-meeting/` 内研究/复现材料。没有修复生产缺陷，没有重新打包、推送或发布，也没有改变个人 Skill 的显式调用策略。浏览器冷启动原因、真实外部 Agent 首次端到端耗时、MediaSail 同条件性能比较均尚待实施阶段测量。

## 直接来源

- [M1] [MDR 首页接入卡](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/workbench/app.py:244>)、[客户端 check](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/skills/media-workbench/scripts/workbench.py:73>)、[公开路径与认证](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/workbench/app.py:135>)。
- [M2] [MDR Skill](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/skills/media-workbench/SKILL.md>)、[Python 定位](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/skills/media-workbench/scripts/workbench.ps1>)、[私有实例信息](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/workbench/__main__.py:47>)。
- [M3] [隔离自检运行环境和后台](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/workbench/selftest_runner.py:174>)、[总预算](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/workbench/selftest.py:147>)。真实摘要来自用户本机 diagnostics/self-test/latest.json，公开纪要不复制凭据或账号档案。
- [M4] [数据库回执](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/workbench/database.py:112>)、[分析](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/workbench/analysis.py>)、[账号会话](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/workbench/sessions.py>)、[队列](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/workbench/queue.py>)、[桌面 preload](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/electron/src/preload.ts>)。
- [M5] [现有客户端功能验收](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/tests/client_smoke.py:15>)、[Electron 复制和后续验收](<C:/Users/jerry/Documents/爬虫工作流/media-workbench/electron/tests/smoke.cjs:73>)。
- [S1] [MediaSail 接入卡](<C:/Users/jerry/Documents/Codex/2026-10-08/ban-2/work/electroneasel/bridge/agent_skill.py:35>)、[公共发现](<C:/Users/jerry/Documents/Codex/2026-10-08/ban-2/work/electroneasel/desktop/agent-connection.cjs:12>)。
- [S2] [MediaSail 客户端](<C:/Users/jerry/Documents/Codex/2026-10-08/ban-2/work/electroneasel/agent-skill/mediasail/scripts/mediasail.py:38>)、[状态响应](<C:/Users/jerry/Documents/Codex/2026-10-08/ban-2/work/electroneasel/vendor/easel/web/app.py:585>)、[桌面独立保护](<C:/Users/jerry/Documents/Codex/2026-10-08/ban-2/work/electroneasel/bridge/server.py:25>)。
- [S3] [MediaSail Skill](<C:/Users/jerry/Documents/Codex/2026-10-08/ban-2/work/electroneasel/agent-skill/mediasail/SKILL.md:16>)、[兼容 PowerShell 入口](<C:/Users/jerry/Documents/Codex/2026-10-08/ban-2/work/electroneasel/agent-skill/mediasail/scripts/mediasail.ps1:13>)。
- [S4] [MediaSail LICENSE](<C:/Users/jerry/Documents/Codex/2026-10-08/ban-2/work/electroneasel/LICENSE:1>)、[NOTICE](<C:/Users/jerry/Documents/Codex/2026-10-08/ban-2/work/electroneasel/NOTICE:4>)。这里只核对本地来源，不代替完整许可审查。
