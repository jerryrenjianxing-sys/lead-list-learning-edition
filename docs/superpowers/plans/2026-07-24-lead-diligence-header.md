# 潜客尽调页头 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将页头左侧改为“潜客尽调”，移除品牌、GitHub、警示横幅和许可弹窗，保持右侧区域与环境检查不变。

**Architecture:** 直接精简现有 `Sidebar` 和 `App`，不新增组件、不改后端、不删除未引用的许可组件文件。

**Tech Stack:** React、TypeScript、Vite

## Global Constraints

- 右侧主题、语言、API 版本和本地状态保持原样。
- 运行状态提示保留。
- 不新增依赖。

---

### Task 1: 精简页头与入口弹窗

**Files:**
- Modify: `webui/src/components/layout/Sidebar.tsx`
- Modify: `webui/src/App.tsx`

**Interfaces:**
- Consumes: 现有 `useCrawlerStatus`、`ThemeToggle`、`LanguageSwitch`
- Produces: 无参数的 `Sidebar()` 组件

- [ ] **Step 1: 修改 `Sidebar`**

删除 Logo、MediaCrawler、GitHub Star、警示横幅及相关导入和回调参数；左侧只显示“潜客尽调”与现有运行状态，右侧 JSX 原样保留。

- [ ] **Step 2: 修改 `App`**

删除 `LicenseDisclaimer`、许可接受状态、手动弹窗状态和相关回调；环境检查条件改为仅依赖 `envChecked`；使用 `<Sidebar />`。

- [ ] **Step 3: 构建验证**

Run: `cd webui && npm.cmd run build`

Expected: TypeScript 和 Vite 构建成功。

- [ ] **Step 4: 页面验证**

打开 `http://127.0.0.1:5173/`，确认左侧仅显示“潜客尽调”，许可弹窗和橙色横幅不出现，右侧四项仍在且页面无控制台错误。
