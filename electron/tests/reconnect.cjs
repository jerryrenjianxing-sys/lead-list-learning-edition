// Real packaged desktop recovery; all data and injected failures are isolated.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { _electron } = require('playwright');
const repo = path.resolve(__dirname, '../..');
const args = process.argv.slice(2);
const option = (key, fallback) => args.includes(key) ? args[args.indexOf(key) + 1] : fallback;
const directory = path.resolve(repo, option('--directory', 'build/MediaWorkbench'));
const output = path.resolve(repo, option('--output', '.cache/electron-reconnect/result.json'));
const root = path.join(repo, '.cache', 'electron-reconnect', `连接恢复-${crypto.randomUUID()}`);
const discovery = path.join(root, 'instance.json');
fs.mkdirSync(root, { recursive: true });
const env = { ...process.env, MEDIAWORKBENCH_DATA_DIR: path.join(root, 'data'), MEDIAWORKBENCH_INSTANCE: discovery, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' };
delete env.ELECTRON_RUN_AS_NODE;
let desktop, hostPid;
const result = { ok: false, faultObserved: false, recovered: false, healthyRoutePreserved: false, manualRecoveryPreserved: false, cleanup_complete: false };
function alive(pid) { try { process.kill(pid, 0); return true; } catch (error) { return error.code !== 'ESRCH'; } }
(async () => {
  try {
    desktop = await _electron.launch({ executablePath: path.join(directory, 'MediaWorkbench.exe'), cwd: directory, env, timeout: 60000 });
    const page = await desktop.firstWindow();
    await desktop.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().forEach(window => window.hide()));
    await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 60000 });
    await page.getByRole('heading', { level: 1 }).waitFor();
    hostPid = JSON.parse(fs.readFileSync(discovery, 'utf8')).pid;
    await desktop.evaluate(() => {
      globalThis.__reconnectOriginalFetch = globalThis.fetch;
      globalThis.fetch = async (input, init) => {
        const route = new URL(typeof input === 'string' ? input : input.url || String(input)).pathname;
        if (route === '/api/v1/agent/handshake' || route === '/api/v1/diagnostics') return new Response('{}', { status: 503, headers: { 'Content-Type': 'application/json' } });
        return globalThis.__reconnectOriginalFetch(input, init);
      };
    });
    await page.waitForURL(/^data:/, { timeout: 22000 });
    assert((await page.locator('body').innerText()).includes('后台连接未完成'));
    result.faultObserved = true;
    await desktop.evaluate(() => { globalThis.fetch = globalThis.__reconnectOriginalFetch; delete globalThis.__reconnectOriginalFetch; });
    await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 22000 });
    await page.getByRole('heading', { level: 1 }).waitFor();
    result.recovered = true;
    await page.goto(new URL('/#manage/settings', page.url()).href);
    const healthyUrl = page.url();
    await page.waitForTimeout(16000);
    assert.equal(page.url(), healthyUrl, 'Healthy polling reset the current view');
    result.healthyRoutePreserved = true;
    // A user can reopen settings from the tray while the status page is visible.
    // That successful navigation must not leave stale error-page state behind.
    await desktop.evaluate(() => {
      globalThis.__reconnectOriginalFetch = globalThis.fetch;
      globalThis.fetch = async (input, init) => {
        const route = new URL(typeof input === 'string' ? input : input.url || String(input)).pathname;
        if (route === '/api/v1/agent/handshake' || route === '/api/v1/diagnostics') return new Response('{}', { status: 503 });
        return globalThis.__reconnectOriginalFetch(input, init);
      };
    });
    await page.waitForURL(/^data:/, { timeout: 22000 });
    await desktop.evaluate(() => { globalThis.fetch = globalThis.__reconnectOriginalFetch; delete globalThis.__reconnectOriginalFetch; });
    await page.goto(healthyUrl);
    await page.waitForTimeout(16000);
    assert.equal(page.url(), healthyUrl, 'Readiness recovery overwrote the manually reopened view');
    result.manualRecoveryPreserved = true;
    result.ok = true;
    fs.mkdirSync(path.dirname(output), { recursive: true });
    await page.screenshot({ path: output.replace(/\.json$/, '.png') });
  } catch (error) { result.error = error.message; process.exitCode = 1; }
  finally {
    if (desktop) {
      try {
        await desktop.evaluate(() => { if (globalThis.__reconnectOriginalFetch) { globalThis.fetch = globalThis.__reconnectOriginalFetch; delete globalThis.__reconnectOriginalFetch; } });
        await desktop.close();
        const end = Date.now() + 5000;
        while (hostPid && alive(hostPid) && Date.now() < end) await new Promise(resolve => setTimeout(resolve, 50));
        assert(!hostPid || !alive(hostPid), 'Isolated backend remains after desktop exit');
        result.cleanup_complete = true;
      } catch (error) { result.cleanup_error = error.message; process.exitCode = 1; }
    }
    fs.mkdirSync(path.dirname(output), { recursive: true });
    fs.writeFileSync(output, JSON.stringify(result, null, 2));
    process.stdout.write(JSON.stringify(result) + '\n');
  }
})();
