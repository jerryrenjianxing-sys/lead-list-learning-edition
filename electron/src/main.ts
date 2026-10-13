import { app, BrowserWindow, Menu, Tray, dialog, ipcMain, nativeImage, shell } from 'electron';
import { UpdateManager } from 'velopack';
import fs from 'node:fs';
import path from 'node:path';
import { DesktopGuard } from './guard';
import { HostController } from './host';
import { externalUrl, trustedSource } from './policy';
import { DesktopUpdater, githubReleaseUrl } from './updates';
import { DISPLAY_NAME, Paths } from './types';

app.setName(DISPLAY_NAME);
app.setAppUserModelId('velopack.MediaWorkbench.Desktop');
const root = app.isPackaged ? path.dirname(app.getPath('exe')) : path.resolve(__dirname, '..', '..');
const local = process.env.LOCALAPPDATA || app.getPath('appData');
const paths: Paths = {
  root, python: path.join(root, 'runtime', 'python', 'python.exe'),
  data: path.resolve(process.env.MEDIAWORKBENCH_DATA_DIR || path.join(local, 'MediaWorkbench', 'data')),
  instance: path.resolve(process.env.MEDIAWORKBENCH_INSTANCE || path.join(local, 'MediaWorkbench', 'instance.json')),
  version: app.isPackaged ? app.getVersion() : (/^version\s*=\s*"([^"]+)"/m.exec(fs.readFileSync(path.join(root, 'pyproject.toml'), 'utf8'))?.[1] || app.getVersion())
};
const iconPath = path.join(root, app.isPackaged ? 'api/webui/brand' : 'webui/public/brand', 'media-deep-researcher.ico');
if (!app.isPackaged && !fs.existsSync(paths.python)) paths.python = path.join(root, '.venv', 'Scripts', 'python.exe');
app.setPath('userData', path.join(paths.data, 'electron-ui'));
app.setPath('sessionData', path.join(paths.data, 'electron-ui', 'session'));
const guard = new DesktopGuard();
const host = new HostController(paths);
let window: BrowserWindow | undefined;
let tray: Tray | undefined;
let exiting = false, closing = false, starting = false, checkingHost = false;
let healthTimer: NodeJS.Timeout | undefined;
let updater: DesktopUpdater;
const restore = () => { if (!window) return; window.show(); if (window.isMinimized()) window.restore(); window.focus(); };
const escape = (value: string) => value.replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]!));
async function status(text: string): Promise<void> {
  if (!window || window.isDestroyed()) return;
  const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>${DISPLAY_NAME}</title><style>body{font:16px system-ui;background:#f7f7f4;color:#19312a;display:grid;place-content:center;min-height:90vh;padding:30px;white-space:pre-wrap;text-align:center;line-height:1.8}</style><body>${escape(text)}</body></html>`;
  await window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
}
async function startHost(): Promise<void> {
  if (starting || exiting) return;
  starting = true;
  try {
    const instance = await host.start();
    await window?.loadURL(`${instance.base_url}/`);
  } catch (error) { await status(`启动未完成\n\n${(error as Error).message}\n\n可从托盘打开日志目录，或点击“重新连接后台”。`); }
  finally { starting = false; }
}
async function exitApp(): Promise<void> {
  if (closing) return;
  closing = true;
  try { await host.stop(); finishExit(); }
  catch (error) { closing = false; restore(); await dialog.showMessageBox(window!, { type: 'warning', title: DISPLAY_NAME, message: (error as Error).message }); }
}
function finishExit(): void { exiting = true; clearInterval(healthTimer); guard.release(); tray?.destroy(); app.quit(); }
async function openExternal(target: string): Promise<void> { if (externalUrl(target)) await shell.openExternal(target); }
function installBridge(): void {
  const handlers: Record<string, () => unknown> = {
    status: () => ({ ...updater.state }), check: () => updater.check(), download: () => updater.download(), install: () => updater.install()
  };
  for (const [action, handler] of Object.entries(handlers)) ipcMain.handle(`mdr:update:${action}`, event => {
    if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame || !host.instance || !trustedSource(event.senderFrame.url, host.instance.base_url)) throw new Error('Desktop access denied');
    return handler();
  });
}
async function launch(): Promise<void> {
  if (process.argv.includes('--quit')) { await DesktopGuard.signal(paths, 'exit'); exiting = true; app.quit(); return; }
  if (!app.requestSingleInstanceLock()) { await DesktopGuard.signal(paths, 'activate'); exiting = true; app.quit(); return; }
  app.on('second-instance', (_event, argv) => { if (argv.includes('--quit')) void exitApp(); else restore(); });
  await app.whenReady();
  await fs.promises.mkdir(paths.data, { recursive: true });
  if (!await guard.acquire(paths)) { exiting = true; app.quit(); return; }
  guard.on('activate', restore); guard.on('exit', () => void exitApp());
  guard.on('lost', () => { if (!exiting) { void status('桌面实例锁意外退出，请正常退出软件后重开。'); void exitApp(); } });
  window = new BrowserWindow({ width: 1280, height: 850, minWidth: 760, minHeight: 540,
    title: DISPLAY_NAME, backgroundColor: '#f7f7f4', frame: true, show: false,
    icon: iconPath,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, allowRunningInsecureContent: false, webviewTag: false, devTools: !app.isPackaged } });
  window.setMenu(null);
  window.on('page-title-updated', event => { event.preventDefault(); });
  window.on('close', event => { if (!exiting) { event.preventDefault(); window?.hide(); tray?.displayBalloon({ title: DISPLAY_NAME, content: '已进入托盘，后台任务继续运行。' }); } });
  window.webContents.setWindowOpenHandler(({ url }) => { void openExternal(url); return { action: 'deny' }; });
  window.webContents.on('will-navigate', (event, url) => { if (!host.instance || !trustedSource(url, host.instance.base_url)) { event.preventDefault(); void openExternal(url); } });
  window.webContents.on('will-redirect', (event, url) => { if (!host.instance || !trustedSource(url, host.instance.base_url)) { event.preventDefault(); void openExternal(url); } });
  window.webContents.on('will-frame-navigate', details => { if (!host.instance || !trustedSource(details.url, host.instance.base_url)) details.preventDefault(); });
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  window.webContents.session.setPermissionRequestHandler((contents, permission, callback, details) => callback(permission === 'clipboard-sanitized-write' && contents === window?.webContents && !!host.instance && trustedSource(details.requestingUrl, host.instance.base_url)));
  window.webContents.session.setPermissionCheckHandler((contents, permission, requestingOrigin) => permission === 'clipboard-sanitized-write' && contents === window?.webContents && !!host.instance && trustedSource(requestingOrigin, host.instance.base_url));
  const icon = nativeImage.createFromPath(iconPath);
  tray = new Tray(icon);
  tray.setToolTip(`${DISPLAY_NAME} · 后台运行中`); tray.on('double-click', restore);
  updater = new DesktopUpdater({ ...paths, manager: (url, options) => new UpdateManager(url, options), releases: () => githubReleaseUrl(paths.version),
    prepare: () => host.prepareUpdate(), stop: () => host.stop(), recover: async () => { await host.stop(); await startHost(); }, exit: finishExit,
    changed: state => { if (window && !window.isDestroyed() && host.instance && trustedSource(window.webContents.getURL(), host.instance.base_url)) window.webContents.send('mdr:update:state', state); }
  });
  installBridge();
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: `打开 ${DISPLAY_NAME}`, click: restore },
    { label: '检查更新', click: () => { restore(); if (host.instance) void window?.loadURL(`${host.instance.base_url}/#manage/settings`); void updater.check(); } },
    { label: '重新连接后台', click: () => void startHost() },
    { label: '打开数据目录', click: () => void shell.openPath(paths.data) },
    { label: '打开日志目录', click: () => void shell.openPath(path.join(paths.data, 'logs')) },
    { type: 'separator' }, { label: '退出并保存进度', click: () => void exitApp() }
  ]));
  await status(`正在启动 ${DISPLAY_NAME}…`); window.show();
  await startHost();
  if (host.instance) void updater.check(true);
  healthTimer = setInterval(() => {
    if (exiting || closing || starting || checkingHost || !host.instance) return;
    checkingHost = true;
    void host.discover().then(instance => { if (!instance) { host.instance = undefined; return startHost(); } }).catch(error => status(`后台连接未完成\n\n${error.message}\n\n可从托盘重新连接后台。`)).finally(() => { checkingHost = false; });
  }, 15000);
}
app.on('before-quit', event => { if (!exiting) { event.preventDefault(); void exitApp(); } });
app.on('window-all-closed', () => { if (exiting) app.quit(); });
void launch().catch(async error => {
  await app.whenReady();
  await dialog.showMessageBox({ type: 'error', title: DISPLAY_NAME, message: `启动未完成：${error.message}` });
  guard.release(); exiting = true; app.quit();
});
