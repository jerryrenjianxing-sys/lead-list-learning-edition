# Prompt Pack Download Hint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在数据管理弹窗增加一行提示，并提供用户给定 Prompt 整合包的直接下载链接。

**Architecture:** 复用现有 `DataExplorer` 头部布局，在操作区下方插入一条静态提示。ZIP 作为 Vite `public` 静态资源提供，不增加 API 或后端逻辑。

**Tech Stack:** React 18、TypeScript、Vite

## Global Constraints

- 只修改前端，不改 API、数据打包逻辑、爬虫任务或后端代码。
- 不新增依赖。
- 不把采集资料和 Prompt 整合包重新合并成一个 ZIP。
- 下载文件名保持为 `多Agent潜客报告质量统一方案_2026-07-24.zip`。

---

### Task 1: 增加提示与 Prompt 整合包下载

**Files:**
- Modify: `webui/src/components/data/DataExplorer.tsx`
- Create: `webui/public/prompt-package.zip`

**Interfaces:**
- Consumes: Vite `public` 目录的静态资源映射。
- Produces: 页面链接 `/prompt-package.zip`，下载文件名为 `多Agent潜客报告质量统一方案_2026-07-24.zip`。

- [ ] **Step 1: 复制用户提供的 ZIP**

将：

`C:\Users\jerry\Documents\爬虫工作流\多Agent潜客报告质量统一方案_2026-07-24.zip`

复制为：

`webui/public/prompt-package.zip`

复制前后使用 SHA256 校验，两者哈希必须一致。

- [ ] **Step 2: 在数据管理头部下方增加提示**

在 `DataExplorer` 顶部 Header 与 Category Tabs 之间加入：

```tsx
<div className="mb-4 rounded-md border border-cyber-neon-cyan/30 bg-cyber-neon-cyan/5 px-3 py-2 text-xs text-cyber-text-secondary">
  请将「打包下载当前列表」获得的采集资料，与
  <a
    href="/prompt-package.zip"
    download="多Agent潜客报告质量统一方案_2026-07-24.zip"
    className="mx-1 font-medium text-cyber-neon-cyan hover:underline"
  >
    下载 Prompt 整合包
  </a>
  一起发送给 Agent。
</div>
```

- [ ] **Step 3: 构建验证**

Run:

```powershell
cd webui
npm.cmd run build
```

Expected: TypeScript 与 Vite 构建成功，`api/webui/prompt-package.zip` 存在。

- [ ] **Step 4: 在线验证**

在运行中的 `http://127.0.0.1:5173/` 打开数据管理，确认：

- 提示显示在顶部操作区与分类按钮之间。
- 点击“下载 Prompt 整合包”返回 HTTP 200。
- 下载内容 SHA256 与原始 ZIP 一致。
- “打包下载当前列表”“重新扫描”和文件卡片仍正常显示。

- [ ] **Step 5: 提交本次文件**

```powershell
git add -- webui/src/components/data/DataExplorer.tsx webui/public/prompt-package.zip docs/superpowers/plans/2026-07-24-prompt-pack-download.md
git commit -m "feat: add prompt pack download hint"
```
