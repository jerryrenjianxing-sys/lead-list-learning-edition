// Exercise the shipped renderer and preload against an isolated real backend.
const { _electron, chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const run = promisify(execFile);
const args = process.argv.slice(2);
const option = (name, fallback) => args.includes(name) ? args[args.indexOf(name) + 1] : fallback;
const repo = path.resolve(__dirname, '../..');
const directory = path.resolve(repo, option('--directory', 'build/MediaWorkbench'));
const output = path.resolve(repo, option('--output', '.cache/electron-migration/renderer-result.json'));
const temporary = path.join(repo, '.cache', 'electron-ui', `界面 验证-${crypto.randomUUID()}`);
const data = path.join(temporary, '用户数据');
const discovery = path.join(temporary, 'instance.json');
const executable = path.join(directory, 'MediaWorkbench.exe');
const env = { ...process.env, MEDIAWORKBENCH_DATA_DIR: data, MEDIAWORKBENCH_INSTANCE: discovery, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' };
delete env.ELECTRON_RUN_AS_NODE;
fs.mkdirSync(temporary, { recursive: true });
assert(fs.existsSync(path.join(directory, 'resources/app.asar')), 'Expected packaged Electron desktop');
const checks = [], errors = [];
let desktop, desktopProcess, browser, instance, mainWindowId, report;
const hostPids = new Set();
function alive(pid) { try { process.kill(pid, 0); return true; } catch (error) { return error.code !== 'ESRCH'; } }
const started = performance.now();
async function until(fn, timeout = 60000) {
  const end = performance.now() + timeout;
  while (performance.now() < end) {
    const result = await fn();
    if (result) return result;
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  throw new Error('Timed out waiting for the requested state');
}
async function api(method, route, body) {
  const response = await fetch(instance.base_url + '/api/v1' + route, {
    method, headers: { Authorization: `Bearer ${instance.token}`, 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(30000),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(`API request failed (${response.status}): ${route}`);
  return value;
}
async function record(name, operation) { await operation(); checks.push(name); }
async function main() {
  try {
    desktop = await _electron.launch({ executablePath: executable, cwd: directory, env, timeout: 90000 });
    desktopProcess = desktop.process();
    const page = await desktop.firstWindow();
    page.on('pageerror', error => errors.push(error.message));
    await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90000 });
    mainWindowId = await desktop.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].id);
    instance = JSON.parse(fs.readFileSync(discovery, 'utf8'));
    hostPids.add(instance.pid);
    assert.equal(instance.product, 'MediaWorkbench');
    assert.equal(path.resolve(instance.data_directory), path.resolve(data));
    const startup_seconds = (performance.now() - started) / 1000;
    await record('real-electron-renderer-and-loopback-page', async () => {
      await page.getByRole('heading', { level: 1 }).waitFor();
      const state = await desktop.evaluate(({ app, BrowserWindow }) => {
        const preferences = BrowserWindow.getAllWindows()[0].webContents.getLastWebPreferences();
        return { version: app.getVersion(), isolated: preferences.contextIsolation, sandbox: preferences.sandbox, node: preferences.nodeIntegration };
      });
      assert.equal(state.version, instance.version);
      assert.equal(state.isolated, true); assert.equal(state.sandbox, true); assert.equal(state.node, false);
      assert.equal(await page.evaluate(() => typeof window.require), 'undefined');
      assert.deepEqual(await page.evaluate(() => Object.keys(window.mdrDesktop).sort()), ['copySkillFile','checkForUpdates','downloadUpdate','getUpdateStatus','installUpdate','onUpdateState'].sort());
      assert.equal(typeof (await page.evaluate(() => window.mdrDesktop.getUpdateStatus())).state, 'string');
    });
    await record('skill-copy-from-home', async () => {
      await page.getByRole('button', { name: '复制 Skill', exact: true }).click();
      await page.getByRole('button', { name: '已复制', exact: true }).waitFor();
    });
    await record('light-dark-animation-and-persistence', async () => {
      const before = await page.evaluate(() => document.documentElement.dataset.theme);
      await page.getByRole('button', { name: /切换到.*主题/ }).click();
      await page.waitForFunction(previous => document.documentElement.dataset.theme !== previous, before);
      const after = await page.evaluate(() => document.documentElement.dataset.theme);
      assert(['light','dark'].includes(after));
      await page.locator('.skill-theme-transition').waitFor({ state: 'hidden' });
      await page.screenshot({ path: path.join(temporary, `${after}-home.png`) });
      await page.reload();
      await page.waitForFunction(value => document.documentElement.dataset.theme === value, after);
      await page.getByRole('button', { name: /切换到.*主题/ }).click();
      await page.waitForFunction(previous => document.documentElement.dataset.theme !== previous, after);
      await page.locator('.skill-theme-transition').waitFor({ state: 'hidden' });
      await page.screenshot({ path: path.join(temporary, 'other-theme-home.png') });
    });
    await record('subframes-have-no-desktop-bridge', async () => {
      await page.evaluate(() => { const frame = document.createElement('iframe'); frame.name = 'untrusted-test-frame'; document.body.appendChild(frame); });
      const frame = await until(() => page.frames().find(item => item.name() === 'untrusted-test-frame'));
      assert.equal(await frame.evaluate(() => typeof window.mdrDesktop), 'undefined');
      await page.locator('iframe[name="untrusted-test-frame"]').evaluate(element => element.remove());
    });
    await record('other-window-ipc-denied', async () => {
      const waiting = desktop.waitForEvent('window');
      const extraWindowId = await desktop.evaluate(async ({ BrowserWindow }, value) => {
        const original = BrowserWindow.fromId(value.mainWindowId);
        const preload = original.webContents.getLastWebPreferences().preload;
        const extra = new BrowserWindow({ show: false, webPreferences: { preload, sandbox: true, contextIsolation: true, nodeIntegration: false } });
        await extra.loadURL(value.url);
        return extra.id;
      }, {url: instance.base_url + '/', mainWindowId});
      const extraPage = await waiting;
      assert(await extraPage.evaluate(async () => {
        try { await window.mdrDesktop.getUpdateStatus(); return false; } catch { return true; }
      }));
      assert(await extraPage.evaluate(async () => {
        try { await window.mdrDesktop.copySkillFile({ name: 'forbidden.md' }); return false; } catch { return true; }
      }));
      await desktop.evaluate(({ BrowserWindow }, id) => BrowserWindow.fromId(id).destroy(), extraWindowId);
    });
    await record('settings-electron-updater-and-keyboard', async () => {
      await page.goto(instance.base_url + '/#manage/settings');
      await page.getByRole('heading', { name: '软件更新', exact: true }).waitFor();
      await page.getByRole('button', { name: '检查更新', exact: true }).focus();
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => !document.querySelector('button:focus')?.disabled);
      assert(!(await page.getByText('请在桌面软件中检查和安装更新，或从发行页下载完整安装包。', { exact: true }).count()));
      await page.screenshot({ path: path.join(temporary, 'settings-desktop.png') });
      await desktop.evaluate(({ BrowserWindow }, id) => BrowserWindow.fromId(id).setSize(760, 540), mainWindowId);
      await page.waitForTimeout(300);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
      await page.screenshot({ path: path.join(temporary, 'settings-small-window.png') });
    });
    await record('ordinary-browser-update-fallback', async () => {
      const locations = fs.readdirSync(path.join(directory, 'runtime/browsers')).filter(name => /^chromium-\d+$/.test(name));
      assert.equal(locations.length, 1);
      browser = await chromium.launch({ executablePath: path.join(directory, 'runtime/browsers', locations[0], 'chrome-win64/chrome.exe'), headless: true });
      const ordinary = await browser.newPage({ viewport: { width: 480, height: 800 } });
      await ordinary.goto(instance.base_url + '/#manage/settings');
      await ordinary.getByText('请在桌面软件中检查和安装更新，或从发行页下载完整安装包。', { exact: true }).waitFor();
      assert.equal(await ordinary.evaluate(() => typeof window.mdrDesktop), 'undefined');
      assert(await ordinary.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
      await record('legacy-webview2-update-bridge-compatibility', async () => {
        const legacy = await browser.newPage();
        await legacy.addInitScript(() => {
          const listeners = new Set();
          window.__legacyActions = [];
          window.__legacyListeners = () => listeners.size;
          window.chrome = window.chrome || {};
          window.chrome.webview = {
            addEventListener: (_name, handler) => listeners.add(handler),
            removeEventListener: (_name, handler) => listeners.delete(handler),
            postMessage: value => {
              window.__legacyActions.push(value.action);
              const state = value.action === 'download' ? 'downloaded' : value.action === 'install' ? 'waiting_idle' : 'available';
              queueMicrotask(() => listeners.forEach(handler => handler({data:{type:'workbench-update',value:{state,version:'0.3.1',message:'旧版桥兼容验证'}}})));
            },
          };
        });
        await legacy.goto(instance.base_url + '/#manage/settings');
        await legacy.getByText('旧版桥兼容验证', { exact:true }).waitFor();
        await legacy.getByRole('button', { name:'检查更新', exact:true }).click();
        await legacy.getByRole('button', { name:'下载更新', exact:true }).click();
        await legacy.getByRole('button', { name:'安装并重启', exact:true }).click();
        assert.deepEqual(await legacy.evaluate(() => window.__legacyActions), ['status','check','download','install']);
        await legacy.getByRole('button', { name:'概览', exact:true }).click();
        await legacy.waitForFunction(() => window.__legacyListeners() === 0);
        assert.equal(await legacy.evaluate(() => window.__legacyListeners()), 0);
        await legacy.close();
      });
      await browser.close(); browser = undefined;
    });
    await record('fresh-distributed-skill-and-fifteen-exports', async () => {
      const result = await run(path.join(directory, 'runtime/python/python.exe'), [path.join(repo, 'tests/client_smoke.py'), '--instance', discovery, '--output', path.join(temporary, '全新 Skill')], { cwd: directory, env, timeout: 150000, maxBuffer: 4*1024*1024, encoding: 'utf8', windowsHide: true });
      assert.equal(JSON.parse(result.stdout).exports, 15);
    });
    await record('isolated-self-test-from-electron-ui', async () => {
      const before = await api('GET','/diagnostics');
      await page.getByRole('button', { name: '仅检查本机', exact: true }).click();
      const result = await until(async () => {
        const latest = await api('GET','/diagnostics/self-tests/latest');
        return latest && !['running','cancelling'].includes(latest.state) ? latest : null;
      }, 210000);
      assert.equal(result.local_state, 'passed', JSON.stringify(result));
      assert.equal(result.cleanup_complete, true);
      assert.equal((await api('GET','/diagnostics')).data_directory, before.data_directory);
      await page.reload();
      await page.getByRole('heading', { name: '运行自检', exact: true }).waitFor();
      await page.getByText(/检查结束.*已用/).waitFor();
    });
    await record('backend-restart-reconnects-and-retains-analyses', async () => {
      const before = await api('GET', '/analyses');
      const priorPid = instance.pid;
      await api('POST', '/host/stop', {});
      instance = await until(() => {
        if (!fs.existsSync(discovery)) return null;
        const found = JSON.parse(fs.readFileSync(discovery,'utf8'));
        return found.pid !== priorPid ? found : null;
      }, 60000);
      hostPids.add(instance.pid);
      await page.waitForURL(instance.base_url+'/', {timeout:60000});
      assert.deepEqual(await api('GET','/analyses'), before);
      assert(!alive(priorPid));
    });
    assert.deepEqual(errors, [], 'Unexpected renderer script errors');
    report = { ok: true, version: instance.version, runtime: 'Electron 44.7.0', checks, startup_seconds, elapsed_seconds: (performance.now()-started)/1000, screenshots: path.relative(repo, temporary), limits: ['Current Windows machine; no clean VM or platform-account validation.','Analysis uses fixed synthetic content; no independent model reasoning.'] };
  } finally {
    if (browser) await browser.close().catch(() => {});
    if (desktop) {
      if (desktopProcess.exitCode === null && desktopProcess.signalCode === null) await run(executable, ['--quit'], { cwd: directory, env, windowsHide: true, timeout: 15000 });
      await until(() => desktopProcess.exitCode !== null || desktopProcess.signalCode !== null, 45000);
      await until(() => [...hostPids].every(pid => !alive(pid)) && !fs.existsSync(discovery), 45000);
      await desktop.close().catch(() => {});
    }
  }
  if (report) {
    report.cleanup_complete = true;
    checks.push('renderer-test-graceful-exit-and-host-cleanup');
    fs.mkdirSync(path.dirname(output), {recursive:true});
    fs.writeFileSync(output, JSON.stringify(report,null,2));
    console.log(JSON.stringify({ok:true,checks,startup_seconds:report.startup_seconds,output}));
  }
}
main().catch(error => {
  fs.mkdirSync(path.dirname(output), {recursive:true});
  fs.writeFileSync(output, JSON.stringify({ok:false,version:instance?.version,checks,cleanup_complete:!desktopProcess || desktopProcess.exitCode !== null,error:error.message},null,2));
  console.error(error.stack); process.exitCode = 1;
});
