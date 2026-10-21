import fs from 'node:fs';
import path from 'node:path';
import { spawn, ChildProcess } from 'node:child_process';
import { compatibleInstance, samePath, validateDiscovery } from './policy';
import { Instance, Paths, PRODUCT } from './types';

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
export function processAlive(pid: number): boolean {
  try { process.kill(pid, 0); return true; } catch (error) { return (error as NodeJS.ErrnoException).code === 'EPERM'; }
}
export function hostEnvironment(paths: Paths): NodeJS.ProcessEnv {
  return { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1',
    MEDIAWORKBENCH_DATA_DIR: paths.data, MEDIAWORKBENCH_INSTANCE: paths.instance,
    PLAYWRIGHT_BROWSERS_PATH: path.join(paths.root, 'runtime', 'browsers'),
    PATH: path.join(paths.root, 'runtime', 'node') + path.delimiter + (process.env.PATH || '') };
}
export async function hostRequest(instance: Instance, route: string, method = 'GET', timeout = 8000): Promise<Response> {
  return fetch(new URL(route, instance.base_url), { method, headers: { Authorization: `Bearer ${instance.token}`, ...(method === 'POST' ? { 'Content-Type': 'application/json' } : {}) }, ...(method === 'POST' ? { body: '{}' } : {}), signal: AbortSignal.timeout(timeout), redirect: 'error' });
}
export class HostController {
  instance?: Instance;
  child?: ChildProcess;
  private log?: fs.WriteStream;
  private revision = 0;
  private starting?: Promise<Instance>;
  private stopping?: Promise<void>;
  constructor(readonly paths: Paths, private readonly timing: { startupMs?: number; shutdownMs?: number } = {}) {}
  private cleanup(): void { this.instance = undefined; this.child = undefined; this.log?.end(); this.log = undefined; }
  async discover(): Promise<Instance | undefined> {
    const revision = this.revision;
    let value: Instance;
    try { value = validateDiscovery(JSON.parse(await fs.promises.readFile(this.paths.instance, 'utf8'))); }
    catch (error) { if ((error as NodeJS.ErrnoException).code === 'ENOENT') return; throw error; }
    let response: Response;
    try { response = await hostRequest(value, '/api/v1/health', 'GET', 1500); }
    catch { return; }
    if (!response.ok) throw new Error('现有后台正在运行，但无法确认其身份。请先退出旧版软件。');
    const health = await response.json() as { product?: string; version?: string; data_directory?: string };
    if (health.product !== PRODUCT || health.version !== value.version || !compatibleInstance(value, this.paths.version, this.paths.data) || (health.data_directory && !samePath(health.data_directory, this.paths.data))) throw new Error('旧版或其他数据目录的后台仍在运行。请先正常退出旧版软件后重试。');
    const diagnostics = await hostRequest(value, '/api/v1/diagnostics');
    if (!diagnostics.ok) throw new Error('无法确认现有后台的数据目录。');
    const identity = await diagnostics.json() as { product?: string; version?: string; data_directory?: string };
    if (identity.product !== PRODUCT || identity.version !== value.version || !identity.data_directory || !samePath(identity.data_directory, this.paths.data)) throw new Error('现有后台的数据目录不匹配，请先退出旧版软件。');
    if (revision !== this.revision) return;
    this.instance = value;
    return value;
  }
  start(): Promise<Instance> {
    if (this.stopping) return Promise.reject(new Error('后台正在停止，请稍候重试。'));
    if (this.starting) return this.starting;
    const pending = this.startCurrent(this.revision).finally(() => {
      if (this.starting === pending) this.starting = undefined;
    });
    this.starting = pending;
    return pending;
  }
  private assertActive(revision: number): void {
    if (revision !== this.revision) throw new Error('后台启动已取消。');
  }
  private async startCurrent(revision: number): Promise<Instance> {
    const existing = await this.discover();
    this.assertActive(revision);
    if (existing) return existing;
    if (!fs.existsSync(this.paths.python)) throw new Error('软件运行环境缺失，请重新安装完整版本。');
    let spawnError: Error | undefined;
    let spawned = false;
    const deadline = Date.now() + (this.timing.startupMs ?? 30000);
    while (Date.now() < deadline) {
      this.assertActive(revision);
      if (spawnError) throw spawnError;
      if (!this.child || this.child.exitCode !== null || this.child.signalCode != null) {
        if (spawned) throw new Error('后台启动失败，请从托盘打开日志目录。');
        // The previous listener may close before its process exits. Wait for that
        // owned process, then replace it once within the same startup deadline.
        this.cleanup();
        await fs.promises.mkdir(path.join(this.paths.data, 'logs'), { recursive: true });
        this.assertActive(revision);
        this.log = fs.createWriteStream(path.join(this.paths.data, 'logs', 'host.log'), { flags: 'a' });
        this.log.on('error', () => { this.log = undefined; });
        this.child = spawn(this.paths.python, ['-m', 'workbench', '--data-dir', this.paths.data], { cwd: this.paths.root, env: hostEnvironment(this.paths), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
        this.child.on('error', error => { spawnError = error; });
        this.child.stdout?.on('data', chunk => this.log?.write(chunk));
        this.child.stderr?.on('data', chunk => this.log?.write(chunk));
        spawned = true;
      }
      const result = await this.discover();
      this.assertActive(revision);
      if (result) return result;
      await delay(250);
    }
    throw new Error('后台未能及时就绪，请查看日志后重试。');
  }
  async prepareUpdate(): Promise<boolean> {
    if (!this.instance) throw new Error('后台尚未就绪。');
    const response = await hostRequest(this.instance, '/api/v1/host/prepare-update', 'POST');
    if (response.status === 409) return false;
    if (!response.ok) throw new Error('备份和更新准备未完成。');
    return true;
  }
  stop(): Promise<void> {
    if (this.stopping) return this.stopping;
    this.revision++;
    const pending = this.stopCurrent().finally(() => {
      if (this.stopping === pending) this.stopping = undefined;
    });
    this.stopping = pending;
    return pending;
  }
  private async stopCurrent(): Promise<void> {
    if (!this.instance && this.child && this.child.exitCode === null) {
      const readinessDeadline = Date.now() + (this.timing.shutdownMs ?? 30000);
      while (!this.instance && this.child.exitCode === null && Date.now() < readinessDeadline) { await this.discover(); if (!this.instance) await delay(250); }
      if (!this.instance && this.child.exitCode === null) throw new Error('已启动的后台尚未就绪，无法确认安全退出。请稍候重试或查看日志。');
    }
    const instance = this.instance;
    if (!instance) { this.cleanup(); return; }
    try { const response = await hostRequest(instance, '/api/v1/host/stop', 'POST'); if (!response.ok) throw new Error(`后台拒绝退出 (${response.status})`); }
    catch (error) { if (processAlive(instance.pid)) throw error; }
    const deadline = Date.now() + (this.timing.shutdownMs ?? 30000);
    while (Date.now() < deadline) {
      let reachable = false;
      try { reachable = (await hostRequest(instance, '/api/v1/health', 'GET', 1500)).ok; } catch {}
      if (!reachable && !processAlive(instance.pid) && (!this.child || this.child.exitCode !== null)) { this.cleanup(); return; }
      await delay(250);
    }
    throw new Error('后台尚未退出。请稍候再试，数据仍保留。');
  }
}
