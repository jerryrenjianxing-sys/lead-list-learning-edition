// Real renderer/preload and Windows file clipboard; never sends a chat message.
const { _electron, chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { promisify } = require('node:util');
const run = promisify(require('node:child_process').execFile);
const repo = path.resolve(__dirname, '../..');
const args = process.argv.slice(2);
const option = (key, fallback) => args.includes(key) ? args[args.indexOf(key) + 1] : fallback;
const directory = option('--directory', '');
const packaged = !!directory;
const root = packaged ? path.resolve(directory) : repo;
const executable = packaged ? path.join(root, 'MediaWorkbench.exe') : path.join(repo, '.cache/electron-test-runtime/electron.exe');
const python = packaged ? path.join(root, 'runtime/python/python.exe') : path.join(repo, '.venv/Scripts/python.exe');
const temporary = path.join(repo, '.cache', `skill-file-${crypto.randomUUID()}`);
fs.mkdirSync(temporary, { recursive: true });
const data = path.join(temporary, '中文用户数据');
const discovery = path.join(temporary, 'instance.json');
const env = { ...process.env, MEDIAWORKBENCH_DATA_DIR: data, MEDIAWORKBENCH_INSTANCE: discovery, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' };
delete env.ELECTRON_RUN_AS_NODE;
const readDrop = `import ctypes,json
from ctypes import wintypes
u=ctypes.WinDLL('user32',use_last_error=True);s=ctypes.WinDLL('shell32',use_last_error=True)
u.GetClipboardData.argtypes=[wintypes.UINT];u.GetClipboardData.restype=wintypes.HANDLE
s.DragQueryFileW.argtypes=[wintypes.HANDLE,wintypes.UINT,wintypes.LPWSTR,wintypes.UINT]
assert u.IsClipboardFormatAvailable(15),'CF_HDROP missing'
assert u.OpenClipboard(None)
try:
 h=u.GetClipboardData(15);count=s.DragQueryFileW(h,0xffffffff,None,0);files=[]
 for i in range(count):
  n=s.DragQueryFileW(h,i,None,0);b=ctypes.create_unicode_buffer(n+1);s.DragQueryFileW(h,i,b,n+1);files.append(b.value)
 print(json.dumps(files,ensure_ascii=False))
finally:u.CloseClipboard()
`;
let desktop, browser, processHandle, info;
const checks = [];
let retainedFile;
async function copiedFiles() {
  const value = await run(python, ['-c', readDrop], { env, windowsHide: true, encoding: 'utf8' });
  return JSON.parse(value.stdout);
}
async function main() {
  try {
    if (!packaged && !fs.existsSync(executable)) {
      const verified = JSON.parse((await run(python, [path.join(repo, 'packaging/bootstrap-electron.py')], { env, encoding: 'utf8' })).stdout);
      assert.equal(verified.verified, true);
      await run(python, ['-m', 'zipfile', '-e', verified.electron_distribution, path.dirname(executable)], { env, windowsHide: true });
    }
    desktop = await _electron.launch({ executablePath: executable, args: packaged ? [] : [path.join(repo, 'electron')], cwd: root, env, timeout: 90000 });
    processHandle = desktop.process();
    const page = await desktop.firstWindow();
    await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//, { timeout: 90000 });
    info = JSON.parse(fs.readFileSync(discovery, 'utf8'));
    const result = await page.evaluate(() => window.mdrDesktop.copySkillFile({ name: '我的研究助手.md' }));
    assert.equal(result.name, '我的研究助手.md');
    const files = await copiedFiles();
    assert.equal(files.length, 1);
    assert.equal(path.basename(files[0]), result.name);
    assert(path.resolve(files[0]).startsWith(path.resolve(data) + path.sep));
    const content = fs.readFileSync(files[0], 'utf8');
    assert(content.startsWith('# Media Deep Researcher'));
    assert(content.includes('--instance') && content.includes(discovery));
    assert(!content.includes(info.token));
    assert(content.includes(path.join(root, 'skills/media-workbench/SKILL.md')));
    checks.push('real-preload-copy-to-native-windows-file-clipboard');
    await page.getByRole('button', { name: '复制 Skill', exact: true }).click();
    await page.getByText('已复制 MD 文件', { exact: true }).waitFor();
    assert.equal(await page.locator('.skill-feedback').innerText(), '已复制 MD 文件');
    await page.getByText('修改文件名', { exact: true }).click();
    const name = page.getByRole('textbox', { name: 'Skill 文件名' });
    await name.fill('研究入口.md');
    await page.getByRole('button', { name: '复制 Skill', exact: true }).click();
    await page.getByText('已复制 MD 文件', { exact: true }).waitFor();
    const renamed = await copiedFiles();
    assert.equal(path.basename(renamed[0]), '研究入口.md');
    assert.notEqual(renamed[0], files[0]);
    assert.equal(fs.readFileSync(files[0], 'utf8'), content);
    retainedFile = renamed[0];
    checks.push('homepage-rename-preserves-previous-clipboard-file');
    for (const invalid of [{ name: '../outside.md' }, { name: 'CON.md' }, { name: '.md' }, { name: 'a.txt' }, { name: 'a.md', content: 'untrusted' }]) {
      assert.equal(await page.evaluate(async value => {
        try { await window.mdrDesktop.copySkillFile(value); return false; } catch { return true; }
      }, invalid), true);
    }
    assert.deepEqual(await copiedFiles(), renamed);
    checks.push('invalid-file-names-and-arbitrary-content-rejected');
    assert.equal(await page.getByRole('button', { name: '复制文字', exact: true }).count(), 0);
    assert.equal(await page.getByRole('dialog').count(), 0);
    checks.push('desktop-has-only-md-copy-and-brief-success');
    const command = content.match(/```powershell\r?\n([^\r\n]+)\r?\n```/)[1];
    const connected = await run('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', command], { env, cwd: temporary, encoding: 'utf8', windowsHide: true, timeout: 15000 });
    assert.equal(JSON.parse(connected.stdout).status, 'ok');
    checks.push('copied-markdown-connect-command-from-arbitrary-directory');
    for (const mode of ['light', 'dark']) {
      await page.evaluate(value => { document.documentElement.dataset.theme = value; }, mode);
      await desktop.evaluate(({ BrowserWindow }, size) => BrowserWindow.getAllWindows()[0].setSize(...size), [760, 600]);
      await name.focus();
      assert.equal(await name.evaluate(el => el === document.activeElement), true);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
      await page.screenshot({ path: path.join(temporary, `${mode}-skill-file.png`) });
    }
    checks.push('keyboard-narrow-window-and-both-themes');
    let browserOptions = { headless: true };
    if (packaged) {
      const [folder] = fs.readdirSync(path.join(root, 'runtime/browsers')).filter(name => /^chromium-\d+$/.test(name));
      browserOptions.executablePath = path.join(root, 'runtime/browsers', folder, 'chrome-win64/chrome.exe');
    }
    browser = await chromium.launch(browserOptions);
    for (const legacy of [false, true]) {
      const ordinary = await browser.newPage({ viewport: { width: 480, height: 800 } });
      await ordinary.addInitScript(value => {
        window.__textCopies = 0;
        Object.defineProperty(navigator, 'clipboard', { value: { writeText: async () => { window.__textCopies++; } } });
        if (value) window.mdrDesktop = {};
      }, legacy);
      const skillRequests = [];
      ordinary.on('request', request => { if (/\/api\/(?:v1\/skill|platform-skill)/.test(request.url())) skillRequests.push(request.url()); });
      await ordinary.goto(info.base_url);
      await ordinary.getByRole('button', { name: '复制 Skill', exact: true }).click();
      await ordinary.getByText('请在新版桌面软件中复制', { exact: true }).waitFor();
      assert.equal(await ordinary.evaluate(() => window.__textCopies), 0);
      assert.equal(skillRequests.length, 0);
      assert.equal(await ordinary.getByRole('dialog').count(), 0);
      await ordinary.close();
    }
    checks.push('ordinary-browser-and-legacy-desktop-never-fall-back-to-text');
  } finally {
    if (browser) await browser.close();
    if (desktop) {
      await pageQuit();
      await desktop.close().catch(() => {});
    }
  }
  assert(fs.existsSync(retainedFile), 'Copied file must survive desktop exit');
  checks.push('copied-file-survives-normal-exit');
  const report = { ok: true, version: info.version, checks, cleanup_complete: true, limitations: ['Native Windows file clipboard verified; receiving Agent attachment paste requires separate validation.'] };
  if (option('--output', '')) fs.writeFileSync(path.resolve(option('--output')), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
}
async function pageQuit() {
  if (processHandle.exitCode !== null) return;
  if (packaged) await run(executable, ['--quit'], { cwd: root, env, windowsHide: true, timeout: 45000 });
  else await run(executable, [path.join(repo, 'electron'), '--quit'], { cwd: root, env, windowsHide: true, timeout: 45000 });
  const deadline = Date.now() + 45000;
  while (processHandle.exitCode === null && Date.now() < deadline) await new Promise(r => setTimeout(r, 100));
  assert.notEqual(processHandle.exitCode, null, 'Owned desktop did not exit');
  assert(!fs.existsSync(discovery), 'Owned backend discovery survived exit');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
