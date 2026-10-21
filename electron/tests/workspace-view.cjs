// Public renderer contract, using isolated HTTP fixtures and no real backend.
const assert = require('node:assert/strict');
const path = require('node:path');
const net = require('node:net');
const fs = require('node:fs/promises');
const { spawn } = require('node:child_process');
const { chromium } = require('playwright');

const repo = path.resolve(__dirname, '../..');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function unusedPort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
async function until(action, label, timeout = 6000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await action()) return;
    await pause(75);
  }
  throw Error(label);
}

async function main() {
  const port = await unusedPort(), base = `http://127.0.0.1:${port}`;
  const vite = spawn(process.execPath, [path.join(repo, 'webui/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: path.join(repo, 'webui'), windowsHide: true, stdio: 'ignore',
  });
  let browser;
  const checks = [];
  try {
    await until(async () => { try { return (await fetch(base)).ok; } catch { return false; } }, 'Fixture renderer did not start', 30000);
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    const errors = [];
    let failDiagnostics = false, failJobs = false, datasetsOnly = false, jobMode = 'normal';
    let savedBrowserPath = 'C:/fixture-only/Chrome.exe', releaseSettingsSave;
    const unrelatedReads = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/v1/**', async route => {
      const url = new URL(route.request().url());
      const routePath = url.pathname.slice('/api/v1'.length);
      if (routePath === '/settings' && route.request().method() === 'PUT') {
        const submitted = route.request().postDataJSON().browser_path;
        await new Promise(resolve => { releaseSettingsSave = resolve; });
        savedBrowserPath = submitted;
        return route.fulfill({ json: { browser_path: submitted } });
      }
      if (datasetsOnly && ['/jobs', '/analyses', '/artifacts', '/platforms'].includes(routePath)) unrelatedReads.push(routePath);
      if (routePath === '/diagnostics' && failDiagnostics) return route.fulfill({ status: 503, json: { detail: '夹具后台状态暂不可读' } });
      if (routePath === '/jobs' && failJobs) return route.fulfill({ status: 503, json: { detail: '夹具任务暂不可读' } });
      const fixtures = {
        '/session': { token: 'fixture-only-no-user-credential' },
        '/health': { version: '0.4.0-test' },
        '/jobs': { items: [{ id: 'fixture-job', platform: 'wb', options: { keywords: '夹具关键词', crawler_type: 'search' }, state: jobMode === 'unconfirmed' ? 'interrupted' : 'completed', checkpoint: { collection: { empty: jobMode === 'empty', message: jobMode === 'unconfirmed' ? '平台返回空列表，尚未确认结果。' : '平台本轮返回无搜索结果。', issues: jobMode === 'unconfirmed' ? [{ reason: 'empty_unconfirmed' }] : [] } }, created: '2026-10-19T01:00:00Z', attempt: 1 }], archived_count: 0 },
        '/datasets': { items: [{ id: 'fixture-data', name: '夹具数据集', record_count: 37, created: '2026-10-19T01:00:00Z' }] },
        '/analyses': { items: [] },
        '/platforms': { items: [{ id: 'dy', name: '抖音', can_select: true, login_state: 'authenticated' }] },
        '/settings': { data_directory: 'C:/fixture-only/data', browser_path: savedBrowserPath },
        '/diagnostics': { queue_alive: true, python: '3.11-fixture', version: '0.4.0-test' },
        '/diagnostics/self-tests/latest': {
          id: 'fixture-check', state: 'timed_out', message: '本机检查超时，联网未执行', elapsed_seconds: 120,
          local_state: 'incomplete', network_state: 'skipped',
          steps: [{ id: 'network', name: '免登录联网试采', state: 'skipped', attempted: false, reason: 'phase_budget_exhausted', message: '', elapsed_seconds: 0 }],
          phases: { local: { state: 'incomplete', budget_seconds: 120, elapsed_seconds: 120 }, network: { state: 'skipped', budget_seconds: 45, elapsed_seconds: 0 } },
          timings: { local: { browser_start: { state: 'interrupted', elapsed_seconds: 93.8 } } },
        },
      };
      if (routePath === '/artifacts') return route.fulfill({ status: 503, json: { detail: '夹具成果暂不可读' } });
      if (Object.hasOwn(fixtures, routePath)) return route.fulfill({ json: fixtures[routePath] });
      return route.fulfill({ status: 404, json: { detail: 'Unknown fixture route' } });
    });
    await page.goto(base + '/#manage');
    await page.getByRole('heading', { name: '让数据，连接你的想法。' }).waitFor();
    await until(() => page.getByText('夹具关键词', { exact: false }).count(), 'A failed file section hid successfully loaded recent tasks');
    await until(() => page.getByText('夹具成果暂不可读', { exact: false }).count(), 'The failed section needs its own visible reason');
    assert.equal(await page.getByText('37', { exact: true }).count(), 1);
    checks.push('Overview preserves successful sections when an independent section fails');
    await page.getByRole('button', { name: '设置', exact: true }).click();
    await page.getByRole('heading', { name: '运行自检', exact: true }).waitFor();
    await until(() => page.getByText('未执行', { exact: true }).count(), 'An unattempted network check must be shown as not executed');
    assert.equal(await page.getByText('本机阶段预算已用完，本步骤尚未开始。', { exact: true }).count(), 1);
    checks.push('An unattempted diagnostic is shown as not executed with its reason');
    await page.getByText('查看阶段耗时', { exact: true }).click();
    assert.equal(await page.getByText('启动测试浏览器：已中断 · 93.8 秒', { exact: true }).count(), 1);
    checks.push('Detailed diagnostic phase timings remain available on demand');
    const browserInput = page.getByLabel('浏览器路径（可留空）');
    await browserInput.fill('C:/用户尚未保存/Chrome.exe');
    failDiagnostics = true;
    await page.getByRole('button', { name: '重新检查', exact: true }).click();
    await until(() => page.getByText('夹具后台状态暂不可读', { exact: false }).count(), 'A settings subsection error was not reported locally');
    assert.equal(await browserInput.inputValue(), 'C:/用户尚未保存/Chrome.exe');
    assert.equal(await page.getByText('后台队列：正常', { exact: false }).count(), 1);
    checks.push('Settings refresh preserves unsaved edits and last successful diagnostics');
    await browserInput.fill('C:/正在提交的路径/Chrome.exe');
    await page.getByRole('button', { name: '保存设置', exact: true }).click();
    await until(() => Boolean(releaseSettingsSave), 'The delayed settings request did not start');
    await browserInput.fill('C:/提交期间继续编辑/Chrome.exe');
    releaseSettingsSave();
    await until(() => page.getByRole('button', { name: '保存设置', exact: true }).isEnabled(), 'The delayed save did not finish');
    assert.equal(await browserInput.inputValue(), 'C:/提交期间继续编辑/Chrome.exe', 'A successful save must retain edits made after submission');
    checks.push('A delayed successful save preserves newer browser-path edits');
    await page.getByRole('button', { name: '数据集', exact: true }).click();
    await page.getByText('夹具数据集', { exact: true }).waitFor();
    datasetsOnly = true;
    await pause(3300);
    assert.deepEqual(unrelatedReads, [], 'The data page must not poll hidden tasks, accounts, analyses or files');
    assert.equal(await page.getByText('夹具数据集', { exact: true }).count(), 1);
    checks.push('The data page reads its own section without polling unrelated pages');
    datasetsOnly = false;
    await page.getByRole('button', { name: '采集任务', exact: true }).click();
    const checkbox = page.getByRole('checkbox', { name: '抖音', exact: true });
    await until(() => checkbox.isChecked(), 'Previously authenticated platforms should be selected on first load');
    await checkbox.uncheck();
    await page.getByLabel('关键词（多个用逗号分隔）').fill('用户正在编辑的关键词');
    failJobs = true;
    await until(() => page.getByText('夹具任务暂不可读', { exact: false }).count(), 'A failed task refresh did not report the local error');
    assert.equal(await page.getByRole('button', { name: /夹具关键词/ }).count(), 1);
    assert.equal(await page.getByLabel('关键词（多个用逗号分隔）').inputValue(), '用户正在编辑的关键词');
    assert.equal(await checkbox.isChecked(), false);
    checks.push('A failed task refresh retains existing tasks, input and manual platform selection');
    failJobs = false;
    jobMode = 'empty';
    await until(() => page.getByText('无搜索结果', { exact: true }).count(), 'A normal empty result must be distinct from collected data');
    jobMode = 'unconfirmed';
    await until(() => page.getByText('平台返回空列表，尚未确认结果。', { exact: true }).count(), 'An unconfirmed stop must show its reason beside the task');
    assert.equal(await page.getByText('无搜索结果', { exact: true }).count(), 0);
    assert.equal(await page.getByText('已中断', { exact: true }).count(), 1);
    checks.push('Normal empty search and unconfirmed collection have distinct visible outcomes');
    assert.deepEqual(errors, []);
    const directory = path.join(repo, '.cache/architecture-meeting');
    await fs.mkdir(directory, { recursive: true });
    await fs.writeFile(path.join(directory, 'workspace-view-result.json'), JSON.stringify({ ok: true, seam: 'real renderer with isolated HTTP fixtures', checks }, null, 2));
    console.log(JSON.stringify({ ok: true, checks }));
  } finally {
    await browser?.close();
    vite.kill();
    await new Promise(resolve => vite.exitCode !== null ? resolve() : vite.once('exit', resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
