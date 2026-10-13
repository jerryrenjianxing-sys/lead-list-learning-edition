import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type { UpdateInfo, UpdateOptions } from 'velopack';
import { acceptableAsset, automaticCheckDue, verifyPackage } from './policy';
import { Asset, CHANNEL, PACK_ID, REPOSITORY, UpdateState } from './types';

interface Release { draft: boolean; tag_name: string; assets: { name: string; browser_download_url: string }[] }
export interface ReleaseSelection { asset: Asset; downloadUrl: string }
export interface Manager {
  getCurrentVersion(): string; getAppId(): string;
  checkForUpdatesAsync(): Promise<UpdateInfo | null>;
  getUpdatePendingRestart(): Asset | null;
  downloadUpdateAsync(update: UpdateInfo, progress?: (percent: number) => void): Promise<void>;
  waitExitThenApplyUpdate(update: UpdateInfo | Asset, silent?: boolean, restart?: boolean, restartArgs?: string[]): void;
}
export interface UpdateDependencies {
  data: string; root: string; version: string;
  manager(url: string, options: UpdateOptions): Manager;
  releases(): Promise<ReleaseSelection | undefined>;
  prepare(): Promise<boolean>; stop(): Promise<void>; recover(): Promise<void>; exit(): void;
  verify?(asset: Asset): Promise<void>;
  changed?(state: UpdateState): void;
  cacheAsset?(selection: ReleaseSelection, directory: string, progress: (percent: number) => void): Promise<void>;
}
function githubAssetUrl(value: string, tag: string, filename: string): string {
  const url = new URL(value);
  if (url.origin !== 'https://github.com' || url.pathname !== `/jerryrenjianxing-sys/lead-list-learning-edition/releases/download/${tag}/${filename}` || url.search || url.hash) throw new Error('发行附件来源错误。');
  return url.href;
}
export async function githubRelease(version: string, request: typeof fetch = fetch): Promise<ReleaseSelection | undefined> {
  const base = 'https://api.github.com/repos/jerryrenjianxing-sys/lead-list-learning-edition/releases';
  const candidates: ReleaseSelection[] = [];
  for (let page = 1; page <= 4; page++) {
    const response = await request(`${base}?per_page=100&page=${page}`, { headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'MediaWorkbench-Desktop' }, signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`GitHub (${response.status})`);
    const releases = await response.json() as Release[];
    if (!Array.isArray(releases)) throw new Error('发行列表格式错误。');
    for (const release of releases) {
      if (release.draft || !/^v\d+\.\d+\.\d+$/.test(release.tag_name) || !release.assets?.some(a => a.name === `releases.${CHANNEL}.json`)) continue;
      const feed = release.assets.find(a => a.name === `releases.${CHANNEL}.json`)!;
      const url = githubAssetUrl(feed.browser_download_url, release.tag_name, `releases.${CHANNEL}.json`);
      const metadata = await request(url, { signal: AbortSignal.timeout(15000) });
      if (!metadata.ok) throw new Error('无法读取预览更新清单。');
      if (metadata.url && new URL(metadata.url).protocol !== 'https:') throw new Error('更新清单连接必须使用 HTTPS。');
      const assets = (await metadata.json() as { Assets?: Asset[] }).Assets;
      if (!Array.isArray(assets)) throw new Error('预览更新清单格式错误。');
      for (const asset of assets) {
        const attached = release.assets.find(a => a.name === asset.FileName);
        if (acceptableAsset(asset, version) && attached && asset.Version === release.tag_name.slice(1)) candidates.push({ asset, downloadUrl: githubAssetUrl(attached.browser_download_url, release.tag_name, asset.FileName) });
      }
    }
    if (releases.length < 100) break;
  }
  candidates.sort((a, b) => {
    const x = a.asset.Version.split('.').map(Number), y = b.asset.Version.split('.').map(Number);
    for (let n = 0; n < 3; n++) if (x[n] !== y[n]) return y[n] - x[n];
    return 0;
  });
  return candidates[0];
}
export async function githubReleaseUrl(version: string, request: typeof fetch = fetch): Promise<string | undefined> {
  const selected = await githubRelease(version, request);
  return selected ? `${REPOSITORY}/releases/download/v${selected.asset.Version}` : undefined;
}
export async function prepareSourceCache(data: string): Promise<string> {
  const directory = path.join(data, 'update-source');
  await fs.promises.mkdir(directory, { recursive: true });
  const leaf = await fs.promises.lstat(directory);
  if (leaf.isSymbolicLink() || !leaf.isDirectory()) throw new Error('更新缓存目录不匹配。');
  const [actual, parent] = await Promise.all([fs.promises.realpath(directory), fs.promises.realpath(data)]);
  if (actual.toLowerCase() !== path.join(parent, 'update-source').toLowerCase()) throw new Error('更新缓存目录不匹配。');
  const marker = path.join(directory, 'owner.json');
  const owner = { product: PACK_ID, channel: CHANNEL, schema: 1 };
  try { if (JSON.stringify(JSON.parse(await fs.promises.readFile(marker, 'utf8'))) !== JSON.stringify(owner)) throw new Error('更新缓存身份不匹配。'); }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; await fs.promises.writeFile(marker, JSON.stringify(owner), { flag: 'wx' }); }
  const uuid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
  const temporaryName = new RegExp(`^(?:releases\\.${CHANNEL}\\.json\\.${uuid}\\.tmp|[A-Za-z0-9._-]+-full\\.nupkg\\.${uuid}\\.part)$`, 'i');
  for (const name of await fs.promises.readdir(directory)) {
    if (!temporaryName.test(name)) continue;
    const file = path.join(directory, name), info = await fs.promises.lstat(file);
    if (info.isSymbolicLink() || !info.isFile()) throw new Error('更新缓存临时文件不匹配。');
    await fs.promises.rm(file);
  }
  return directory;
}
async function writeFeed(directory: string, selection?: ReleaseSelection): Promise<void> {
  const file = path.join(directory, `releases.${CHANNEL}.json`), temporary = file + '.' + crypto.randomUUID() + '.tmp';
  try { await fs.promises.writeFile(temporary, JSON.stringify({ Assets: selection ? [selection.asset] : [] }), { flag: 'wx' }); await fs.promises.rename(temporary, file); }
  finally { await fs.promises.rm(temporary, { force: true }); }
}
export async function downloadSourceAsset(selection: ReleaseSelection, directory: string, progress: (percent: number) => void, request: typeof fetch = fetch): Promise<void> {
  const file = path.join(directory, selection.asset.FileName);
  if (path.dirname(path.resolve(file)) !== path.resolve(directory)) throw new Error('更新文件名不合法。');
  try { await verifyPackage(file, selection.asset); progress(100); return; }
  catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') await fs.promises.rm(file, { force: true }); }
  const temporary = file + '.' + crypto.randomUUID() + '.part';
  let handle: fs.promises.FileHandle | undefined;
  try {
    let downloadUrl = selection.downloadUrl;
    const requireHttps = downloadUrl.startsWith('https:');
    const signal = AbortSignal.timeout(30 * 60 * 1000);
    let response: Response | undefined;
    for (let redirects = 0; redirects <= 10; redirects++) {
      response = await request(downloadUrl, { signal, redirect: 'manual' });
      if (![301, 302, 303, 307, 308].includes(response.status)) break;
      const location = response.headers.get('location');
      if (!location || redirects === 10) throw new Error('更新附件重定向未完成。');
      const next = new URL(location, downloadUrl);
      if ((requireHttps && next.protocol !== 'https:') || next.username || next.password) throw new Error('更新附件连接必须使用 HTTPS。');
      await response.body?.cancel();
      downloadUrl = next.href;
    }
    if (!response?.ok || !response.body) throw new Error('无法下载更新附件。');
    if (selection.downloadUrl.startsWith('https:') && response.url && new URL(response.url).protocol !== 'https:') throw new Error('更新附件连接必须使用 HTTPS。');
    handle = await fs.promises.open(temporary, 'wx');
    const reader = response.body.getReader();
    let received = 0, lastProgress = -1;
    try {
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        received += value.byteLength;
        if (received > selection.asset.Size) throw new Error('更新附件超过清单大小。');
        let offset = 0;
        while (offset < value.byteLength) { const written = await handle.write(value, offset, value.byteLength - offset); if (!written.bytesWritten) throw new Error('更新附件写入未完成。'); offset += written.bytesWritten; }
        const percent = Math.floor(received * 100 / selection.asset.Size);
        if (percent !== lastProgress) { progress(percent); lastProgress = percent; }
      }
    } finally { await reader.cancel().catch(() => {}); }
    await handle.close(); handle = undefined;
    await verifyPackage(temporary, selection.asset);
    await fs.promises.rename(temporary, file);
  } finally { if (handle) await handle.close(); await fs.promises.rm(temporary, { force: true }); }
}
export class DesktopUpdater {
  state: UpdateState = { state: 'idle', message: '可检查是否有新版本。' };
  private busy = false;
  private manager?: Manager;
  private candidate?: UpdateInfo;
  private selection?: ReleaseSelection;
  private source?: string;
  private downloaded = false;
  constructor(private readonly d: UpdateDependencies) {}
  private set(state: string, message: string, progress = 0): UpdateState {
    const asset = this.candidate?.TargetFullRelease;
    this.state = { state, message, progress, ...(asset ? { version: asset.Version, size: asset.Size, notes: asset.NotesMarkdown || '' } : {}) };
    this.d.changed?.({ ...this.state }); return this.state;
  }
  private async packagesDirectory(): Promise<string | undefined> {
    const install = path.dirname(this.d.root), directory = path.join(install, 'packages');
    let leaf: fs.Stats;
    try { leaf = await fs.promises.lstat(directory); }
    catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return; throw error; }
    if (leaf.isSymbolicLink() || !leaf.isDirectory()) throw new Error('更新待安装目录不匹配。');
    const [actual, parent] = await Promise.all([fs.promises.realpath(directory), fs.promises.realpath(install)]);
    if (actual.toLowerCase() !== path.join(parent, 'packages').toLowerCase()) throw new Error('更新待安装目录不匹配。');
    return directory;
  }
  private async verify(asset: Asset): Promise<void> {
    if (this.d.verify) return this.d.verify(asset);
    const packages = await this.packagesDirectory();
    if (!packages) throw new Error('更新待安装目录尚未就绪。');
    await verifyPackage(path.join(packages, asset.FileName), asset);
  }
  private async discardCorruptPending(asset: Asset): Promise<void> {
    if (!acceptableAsset(asset, this.d.version)) throw new Error('更新文件身份不匹配。');
    const directory = await this.packagesDirectory();
    if (!directory) return;
    const file = path.join(directory, asset.FileName);
    try { await verifyPackage(file, asset); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return;
      const info = await fs.promises.lstat(file);
      if (info.isSymbolicLink() || !info.isFile()) throw new Error('更新待安装文件不匹配。');
      // The name includes the strictly newer version and exact channel. Never
      // erase the current package, even when native SDK skips existing files.
      await fs.promises.rm(file);
    }
  }
  async check(automatic = false): Promise<UpdateState> {
    if (this.busy) return this.state;
    const stamp = path.join(this.d.data, 'update-check.txt');
    if (automatic) {
      let value: string | undefined; try { value = await fs.promises.readFile(stamp, 'utf8'); } catch {}
      if (!automaticCheckDue(value)) return this.state;
    }
    this.busy = true;
    try {
      // The installed identity is checked before any network request.
      const source = await prepareSourceCache(this.d.data);
      const probe = this.d.manager(source, { ExplicitChannel: CHANNEL, AllowVersionDowngrade: false, MaximumDeltasBeforeFallback: -1 });
      if (probe.getAppId() !== PACK_ID || probe.getCurrentVersion() !== this.d.version) throw new Error('安装身份不匹配。');
      await fs.promises.mkdir(this.d.data, { recursive: true });
      await fs.promises.writeFile(stamp, new Date().toISOString());
      this.set('checking', '正在检查 GitHub 预览版本…');
      const selection = await this.d.releases();
      if (selection && !acceptableAsset(selection.asset, this.d.version)) throw new Error('预览更新清单不匹配。');
      await writeFeed(source, selection);
      const candidate = await probe.checkForUpdatesAsync();
      if (!candidate) { this.source = source; this.selection = undefined; this.manager = probe; this.candidate = undefined; this.downloaded = false; return this.set('current', '当前已是此通道最新版本。'); }
      if (!selection || candidate.IsDowngrade || !acceptableAsset(candidate.TargetFullRelease, this.d.version) || ['Version', 'FileName', 'Size', 'SHA256'].some(key => (candidate.TargetFullRelease as unknown as Record<string, unknown>)[key] !== (selection.asset as unknown as Record<string, unknown>)[key])) throw new Error('更新包身份或版本不匹配。');
      this.source = source; this.selection = selection; this.manager = probe;
      this.candidate = { ...candidate, DeltasToTarget: [] };
      this.downloaded = false;
      const pending = this.manager.getUpdatePendingRestart();
      if (pending?.FileName === candidate.TargetFullRelease.FileName) {
        try { await this.verify(candidate.TargetFullRelease); this.downloaded = true; } catch { /* A damaged cached package is downloaded again. */ }
      }
      return this.set(this.downloaded ? 'downloaded' : 'available', this.downloaded ? '更新已下载，可在任务空闲时安装。' : '发现新版本，点击下载后再安装。');
    } catch (error) {
      const name = (error as Error).message;
      return this.set(/not installed|not found|locat|manifest/i.test(name) ? 'unavailable' : 'error', /not installed|not found|locat|manifest/i.test(name) ? '当前为源码或未安装版本，请从 GitHub 下载完整安装包。' : '暂时无法检查更新。本地功能仍可使用，请重试或打开发行页。');
    } finally { this.busy = false; }
  }
  async download(): Promise<UpdateState> {
    if (this.busy || !this.candidate || !this.manager || !this.selection || !this.source) return this.state;
    this.busy = true;
    try {
      this.set('downloading', '正在下载更新…');
      await (this.d.cacheAsset || downloadSourceAsset)(this.selection, this.source, percent => this.set('downloading', '正在下载更新…', Math.floor(percent * .9)));
      await this.discardCorruptPending(this.candidate.TargetFullRelease);
      await this.manager.downloadUpdateAsync(this.candidate, percent => this.set('downloading', '正在校验并准备更新…', 90 + Math.floor(Math.max(0, Math.min(100, percent)) * .1)));
      await this.verify(this.candidate.TargetFullRelease);
      await fs.promises.rm(path.join(this.source, this.selection.asset.FileName), { force: true });
      this.downloaded = true; return this.set('downloaded', '更新已下载并校验，点击安装并重启。');
    } catch { this.downloaded = false; return this.set('available', '下载或校验未完成，可重新下载。当前版本未改变。'); }
    finally { this.busy = false; }
  }
  async install(): Promise<UpdateState> {
    if (this.busy || !this.downloaded || !this.candidate || !this.manager) return this.state;
    this.busy = true;
    let prepared = false, verified = false;
    try {
      await this.verify(this.candidate.TargetFullRelease);
      verified = true;
      if (!await this.d.prepare()) return this.set('waiting_idle', '采集或自检尚未结束。更新已保存，空闲后点击安装。');
      prepared = true;
      this.set('installing', '已备份数据，正在退出后台并安装更新…');
      await this.d.stop();
      this.manager.waitExitThenApplyUpdate(this.candidate.TargetFullRelease, false, true, []);
      this.d.exit(); return this.state;
    } catch {
      if (!verified) { this.downloaded = false; return this.set('available', '下载的更新校验未完成，请重新下载。当前版本未改变。'); }
      this.set('downloaded', '安装未完成，正在恢复当前版本。');
      if (prepared) { try { await this.d.recover(); } catch { return this.set('downloaded', '当前后台恢复未完成，请退出后重开；更新和数据仍保留。'); } }
      return this.state;
    } finally { this.busy = false; }
  }
}
