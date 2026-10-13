import fs from 'node:fs';
import path from 'node:path';
import type { UpdateInfo, UpdateOptions, UpdateManager } from 'velopack';
import { acceptableAsset, automaticCheckDue, verifyPackage } from './policy';
import { Asset, CHANNEL, PACK_ID, REPOSITORY, UpdateState } from './types';

interface Release { draft: boolean; tag_name: string; assets: { name: string; browser_download_url: string }[] }
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
  releases(): Promise<string | undefined>;
  prepare(): Promise<boolean>; stop(): Promise<void>; recover(): Promise<void>; exit(): void;
  verify?(asset: Asset): Promise<void>;
  changed?(state: UpdateState): void;
}
export async function githubReleaseUrl(version: string, request: typeof fetch = fetch): Promise<string | undefined> {
  const base = 'https://api.github.com/repos/jerryrenjianxing-sys/lead-list-learning-edition/releases';
  const candidates: { version: string; url: string }[] = [];
  for (let page = 1; page <= 4; page++) {
    const response = await request(`${base}?per_page=100&page=${page}`, { headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'MediaWorkbench-Desktop' }, signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(`GitHub (${response.status})`);
    const releases = await response.json() as Release[];
    if (!Array.isArray(releases)) throw new Error('发行列表格式错误。');
    for (const release of releases) {
      if (release.draft || !/^v\d+\.\d+\.\d+$/.test(release.tag_name) || !release.assets?.some(a => a.name === `releases.${CHANNEL}.json`)) continue;
      const feed = release.assets.find(a => a.name === `releases.${CHANNEL}.json`)!;
      const url = new URL(feed.browser_download_url);
      if (url.origin !== 'https://github.com' || !url.pathname.startsWith('/jerryrenjianxing-sys/lead-list-learning-edition/releases/download/') || url.pathname.split('/').at(-2) !== release.tag_name) throw new Error('发行附件来源错误。');
      const metadata = await request(url, { signal: AbortSignal.timeout(15000) });
      if (!metadata.ok) throw new Error('无法读取预览更新清单。');
      const assets = (await metadata.json() as { Assets?: Asset[] }).Assets;
      if (!Array.isArray(assets)) throw new Error('预览更新清单格式错误。');
      for (const asset of assets) if (acceptableAsset(asset, version) && release.assets.some(a => a.name === asset.FileName) && asset.Version === release.tag_name.slice(1)) candidates.push({ version: asset.Version, url: `${REPOSITORY}/releases/download/${release.tag_name}` });
    }
    if (releases.length < 100) break;
  }
  candidates.sort((a, b) => {
    const x = a.version.split('.').map(Number), y = b.version.split('.').map(Number);
    for (let n = 0; n < 3; n++) if (x[n] !== y[n]) return y[n] - x[n];
    return 0;
  });
  return candidates[0]?.url;
}
export class DesktopUpdater {
  state: UpdateState = { state: 'idle', message: '可检查是否有新版本。' };
  private busy = false;
  private manager?: Manager;
  private candidate?: UpdateInfo;
  private downloaded = false;
  constructor(private readonly d: UpdateDependencies) {}
  private set(state: string, message: string, progress = 0): UpdateState {
    const asset = this.candidate?.TargetFullRelease;
    this.state = { state, message, progress, ...(asset ? { version: asset.Version, size: asset.Size, notes: asset.NotesMarkdown || '' } : {}) };
    this.d.changed?.({ ...this.state }); return this.state;
  }
  private async verify(asset: Asset): Promise<void> {
    if (this.d.verify) return this.d.verify(asset);
    const packages = path.join(path.dirname(this.d.root), 'packages');
    await verifyPackage(path.join(packages, asset.FileName), asset);
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
      const probe = this.d.manager(REPOSITORY, { ExplicitChannel: CHANNEL, AllowVersionDowngrade: false, MaximumDeltasBeforeFallback: -1 });
      if (probe.getAppId() !== PACK_ID || probe.getCurrentVersion() !== this.d.version) throw new Error('安装身份不匹配。');
      await fs.promises.mkdir(this.d.data, { recursive: true });
      await fs.promises.writeFile(stamp, new Date().toISOString());
      this.set('checking', '正在检查 GitHub 预览版本…');
      const url = await this.d.releases();
      if (!url) { this.candidate = undefined; this.downloaded = false; return this.set('current', '当前已是此通道最新版本。'); }
      this.manager = this.d.manager(url, { ExplicitChannel: CHANNEL, AllowVersionDowngrade: false, MaximumDeltasBeforeFallback: -1 });
      const candidate = await this.manager.checkForUpdatesAsync();
      if (!candidate) { this.candidate = undefined; this.downloaded = false; return this.set('current', '当前已是此通道最新版本。'); }
      if (candidate.IsDowngrade || !acceptableAsset(candidate.TargetFullRelease, this.d.version)) throw new Error('更新包身份或版本不匹配。');
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
    if (this.busy || !this.candidate || !this.manager) return this.state;
    this.busy = true;
    try {
      this.set('downloading', '正在下载更新…');
      await this.manager.downloadUpdateAsync(this.candidate, percent => this.set('downloading', '正在下载更新…', Math.max(0, Math.min(100, percent))));
      await this.verify(this.candidate.TargetFullRelease);
      this.downloaded = true; return this.set('downloaded', '更新已下载并校验，点击安装并重启。');
    } catch { this.downloaded = false; return this.set('available', '下载或校验未完成，可重新下载。当前版本未改变。'); }
    finally { this.busy = false; }
  }
  async install(): Promise<UpdateState> {
    if (this.busy || !this.downloaded || !this.candidate || !this.manager) return this.state;
    this.busy = true;
    let prepared = false;
    try {
      await this.verify(this.candidate.TargetFullRelease);
      if (!await this.d.prepare()) return this.set('waiting_idle', '采集或自检尚未结束。更新已保存，空闲后点击安装。');
      prepared = true;
      this.set('installing', '已备份数据，正在退出后台并安装更新…');
      await this.d.stop();
      this.manager.waitExitThenApplyUpdate(this.candidate.TargetFullRelease, false, true, []);
      this.d.exit(); return this.state;
    } catch {
      this.set('downloaded', '安装未完成，正在恢复当前版本。');
      if (prepared) { try { await this.d.recover(); } catch { return this.set('downloaded', '当前后台恢复未完成，请退出后重开；更新和数据仍保留。'); } }
      return this.state;
    } finally { this.busy = false; }
  }
}
