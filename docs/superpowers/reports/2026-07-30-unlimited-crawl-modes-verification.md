# 不限量采集与三模式 WebUI 验证报告

日期：2026-07-30

## 结论

- Search、Detail、Creator 三种模式已进入新版 Apple 风格 WebUI，未恢复旧 WebUI。
- 正式版默认传递：
  - `max_notes_count = 0`
  - `max_comments_count = 0`
  - `lead_max_accounts = 0`
- 后端以 `0` 表示不限量，没有使用 `10000` 或 `100000` 冒充不限量。
- 正式版已同步到学习版，发布校验通过，并生成新的学习群分发 ZIP。
- 本轮未启动真实平台全量采集，避免产生长时间任务、验证码和重复数据。

## 后端验证

### 本轮核心回归

命令：

```powershell
uv run pytest tests/test_api_limits.py tests/test_required_crawler_inputs.py tests/test_crawler_util.py tests/test_unlimited_comment_pagination.py tests/test_lead_sink.py tests/test_douyin_lead_collection.py tests/test_xhs_lead_collection.py tests/test_kuaishou_lead_collection.py -q
```

结果：`148 passed`。

覆盖内容包括：

- API 接受三个 `0` 上限；
- Search、Detail、Creator 输入契约；
- 内容和评论分页的不限量语义；
- 重复页终止保护；
- 原内容、一级评论、二级回复与评论人资料旁路；
- 抖音、小红书、快手账号资料采集。

### 完整项目回归

首次完整运行结果：

- `251 passed`
- `8 skipped`
- `6 failed`

6 个失败全部来自本机 `127.0.0.1:6379` 未启动 Redis：

- `test/test_proxy_ip_pool.py`：3 项
- `test/test_redis_cache.py`：3 项

排除上述两个外部 Redis 集成文件后重新运行：

```powershell
uv run pytest -q --ignore=test/test_proxy_ip_pool.py --ignore=test/test_redis_cache.py
```

结果：`249 passed, 8 skipped`，没有其他失败。

## 前端验证

命令：

```powershell
npm.cmd run check
npm.cmd run build
```

结果：

- 搜索配置行为检查通过；
- 新版页面结构检查通过；
- TypeScript 编译通过；
- Vite 正式构建通过；
- 构建产物写入 `api/webui`。

现有检查确认：

- 逗号分隔输入由回车生成标签，不自动开始；
- 空输入不能提交；
- Search 可多平台；
- Detail、Creator 归一化为单平台；
- 三种模式分别提交 `keywords`、`specified_ids`、`creator_ids`；
- 三个上限均提交 `0`。

## 服务检查

安全检查确认 5173 和 8080 原先均未被占用，随后按项目原有命令启动：

- API：`http://127.0.0.1:8080`
- WebUI：`http://127.0.0.1:5173`

结果：

- `/api/health` 返回 `200`；
- WebUI 首页返回 `200`；
- `/api/crawler/status` 为 `idle`，未误启动采集任务；
- API 进程 PID：`26932`；
- WebUI 进程 PID：`29504`。

运行中的 OpenAPI 已确认：

- `max_notes_count` 最小值为 `0`；
- `max_comments_count` 最小值为 `0`；
- `lead_max_accounts` 默认值和最小值均为 `0`；
- 二维码刷新、停止、恢复、数据管理等路由仍存在。

### 浏览器真实交互检查

已在本地 WebUI 实际检查 1440×900、1280×600、390×844 三种视口：

- 搜索框输入中文、英文逗号分隔内容并按回车，只生成关键词标签，不会启动任务；
- Search 保持平台多选，Detail、Creator 保持平台单选；
- 三种模式分别显示关键词、作品、创作者输入与对应提示；
- 移动端设置面板与底部终端入口均可打开、关闭；
- 终端关闭后状态不丢失；
- 三种视口未观察到横向溢出；
- 浏览器控制台错误为 `0`；
- 未点击开始按钮，未触发真实平台采集。

## 学习版同步与封装

只复用项目已有脚本：

```powershell
node packaging/sync-from-formal.mjs
node packaging/verify-release.mjs "release/潜客名单-学习版"
.\packaging\package-learning.ps1
```

结果：

- 正式版同步到 `lead-list-learning-edition/app`；
- 正式版同步到学习版发布目录；
- 发布校验通过；
- 新 ZIP 已生成：

```text
C:\Users\jerry\Documents\爬虫工作流\lead-list-learning-edition\lead-list-learning-edition_2026-07-30_12-14-54.zip
```

文件信息：

- 大小：`63,114,634` 字节；
- SHA-256：`C787E33575ADDF4C76DA38615DCD6D69EA5D9BF43D236D53DC52AC170A95D41D`；
- ZIP 条目：`209`；
- 禁止内容命中：`0`。

现有发布校验与 ZIP 复核确认未包含：

- `browser_data` 中的登录资料；
- `data` 中的历史采集结果；
- `.venv`；
- `node_modules`；
- Python/测试缓存和日志；
- `C:\Users\...` 本机绝对路径。

## 最终差异检查

```powershell
git diff --check
```

结果：通过。

本轮没有执行 `reset`、`checkout` 或提交，也没有覆盖工作区已有未提交改动。

## 已知边界

- “不限量”表示持续翻页，直到平台明确无下一页、返回空页、重复页保护触发、用户停止或平台错误中断；不承诺取得平台未展示或风控隐藏的内容。
- Redis/代理池的 6 项集成测试需要本机 Redis 服务后才能运行。
- 真平台全量采集仍可能受到登录失效、验证码、网络波动和平台限流影响。
