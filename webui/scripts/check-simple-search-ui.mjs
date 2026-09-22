import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = await readFile(
  new URL('../src/components/search/LeadSearchApp.tsx', import.meta.url),
  'utf8',
)
const crawlerSource = await readFile(
  new URL('../src/hooks/useCrawler.ts', import.meta.url),
  'utf8',
)
const apiSource = await readFile(
  new URL('../src/lib/api.ts', import.meta.url),
  'utf8',
)
const dataSource = await readFile(
  new URL('../src/components/data/DataExplorer.tsx', import.meta.url),
  'utf8',
)
const fileCardSource = await readFile(
  new URL('../src/components/data/FileCard.tsx', import.meta.url),
  'utf8',
)
const storeSource = await readFile(
  new URL('../src/store/crawlerStore.ts', import.meta.url),
  'utf8',
)
const promptSource = await readFile(
  new URL('../public/social-lead-analysis-prompt.txt', import.meta.url),
  'utf8',
)
const stylesSource = await readFile(
  new URL('../src/index.css', import.meta.url),
  'utf8',
)

for (const required of [
  '输入产品，输出名单',
  "useState<string[]>(['dy'])",
  'useCrawlerQueue',
  'useCrawlerStatus',
  'useCrawlerLogs',
  'useLogWebSocket',
  'buildCrawlerConfig',
  'DataExplorerDialog',
  'settings-cluster',
  'terminal-drawer',
  '关键词搜索',
  '指定作品',
  '指定创作者',
  "useState<CrawlerType>('search')",
  'const [keywordTags, setKeywordTags]',
  'onKeyDown={commitKeywords}',
  'className="keyword-tags"',
  'type="button" className="search-action"',
  'const [qrOpen, setQrOpen]',
  "'/api/crawler/qrcode'",
  "'/api/crawler/qrcode/refresh'",
  "response.headers.get('x-qr-version')",
  '扫描二维码登录',
  '刷新二维码',
  '重新登录并继续',
  '稍后继续补账号',
  '账号资料补充中断',
  '普通搜索和评论错误不会在此显示续补入口',
  "document.querySelector('.apple-dialog')",
  'MediaCrawler',
  '/platforms/xhs.svg',
  '/platforms/zhihu.svg',
  '强制终止',
  '强制终止当前任务',
]) {
  assert.ok(source.includes(required), `missing UI contract: ${required}`)
}

assert.ok(apiSource.includes('recoverProfiles'), 'generic profile recovery API is missing')
assert.ok(
  apiSource.includes("'profile_resume'"),
  'generic profile recovery status is missing',
)
assert.ok(
  source.includes('等待续补'),
  'paused recovery must not show an active stop button',
)
assert.ok(
  source.includes('const canForceStop =') &&
    source.includes('active && canForceStop ? ('),
  'recovery must allow force-stop only while its process is still running',
)
assert.ok(
  source.includes("setQrOpen(true)") &&
    source.includes('重新显示登录二维码'),
  'a dismissed login QR code must be reopenable',
)

for (const required of [
  '请使用 1M 上下文模型进行分析（如 DeepSeek V4 Flash）',
  '获得的数据包与',
  '完整发给大模型',
  '并要求大模型严格按照提示词执行，必须全量分析所有数据',
  '/api/data/prompt',
  '批量删除',
  '永久删除且无法恢复',
]) {
  assert.ok(dataSource.includes(required), `missing data handoff contract: ${required}`)
}

assert.ok(apiSource.includes('deleteFiles'), 'batch delete API is missing')
assert.ok(fileCardSource.includes('selected'), 'file selection control is missing')

for (const required of [
  "platform: 'dy'",
  'enable_comments: true',
  'enable_sub_comments: true',
  'lead_mode: true',
]) {
  assert.ok(storeSource.includes(required), `missing default config: ${required}`)
}

assert.ok(promptSource.length > 1000, 'prompt file must contain the full prompt')
assert.match(
  stylesSource,
  /\.search-hero h1\s*\{[^}]*white-space:\s*nowrap;/s,
  'search title must stay on one line',
)

for (const removed of [
  '起始页',
  '保存格式',
  '无头模式',
  'API 版本',
  '<form className="search-form" onSubmit={startSearch}>',
]) {
  assert.ok(!source.includes(removed), `old control leaked into UI: ${removed}`)
}

assert.ok(
  crawlerSource.includes('for (const job of [...loginJobs, ...crawlJobs])'),
  'multi-platform queue must pre-login before crawling',
)
assert.ok(
  crawlerSource.includes("crawler_type: 'login'"),
  'multi-platform queue must keep the login-only pass',
)

console.log('simple search UI structure checks passed')
