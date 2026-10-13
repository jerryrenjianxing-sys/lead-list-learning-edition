import { spawn, ChildProcessWithoutNullStreams } from 'node:child_process';
import { createInterface } from 'node:readline';
import { EventEmitter } from 'node:events';
import { hostEnvironment } from './host';
import { Paths } from './types';
export class DesktopGuard extends EventEmitter {
  private child?: ChildProcessWithoutNullStreams;
  async acquire(paths: Paths): Promise<boolean> {
    const child = spawn(paths.python, ['-m', 'workbench.desktop_guard', 'hold'], { cwd: paths.root, env: hostEnvironment(paths), windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
    this.child = child;
    return new Promise((resolve, reject) => {
      let ready = false;
      const timer = setTimeout(() => { reject(new Error('桌面实例锁未能就绪。')); child.stdin.end(); }, 10000);
      const lines = createInterface({ input: child.stdout });
      lines.on('line', line => {
        try {
          const value = JSON.parse(line) as { event: string; owner?: boolean };
          if (value.event === 'ready') { ready = true; clearTimeout(timer); resolve(value.owner === true); }
          else if (value.event === 'activate' || value.event === 'exit') this.emit(value.event);
        } catch {}
      });
      child.on('error', error => { clearTimeout(timer); reject(error); });
      child.on('exit', () => { clearTimeout(timer); if (!ready) reject(new Error('桌面实例锁启动失败。')); else if (this.child === child) this.emit('lost'); });
      child.stderr.on('data', () => {});
    });
  }
  release(): void { const child = this.child; this.child = undefined; child?.stdin.end('{"command":"release"}\n'); }
  static async signal(paths: Paths, action: 'exit' | 'activate'): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      const child = spawn(paths.python, ['-m', 'workbench.desktop_guard', 'signal', action], { cwd: paths.root, env: hostEnvironment(paths), windowsHide: true, stdio: 'ignore' });
      child.once('error', reject); child.once('exit', code => code === 0 ? resolve() : reject(new Error('无法通知已有桌面实例。')));
    });
  }
}
