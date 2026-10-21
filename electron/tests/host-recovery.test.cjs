// Exercise the real controller; only OS process, filesystem and HTTP boundaries are substituted.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');

function recoveryFixture(oldResponse) {
  const data = path.resolve('isolated-host-recovery');
  const child = pid => Object.assign(new EventEmitter(), {
    pid, exitCode: null, signalCode: null, stdout: new EventEmitter(), stderr: new EventEmitter(),
    kill() { throw new Error('A closing backend must exit gracefully'); },
  });
  const old = child(7001), next = child(7002);
  let spawned = 0;
  const discovery = pid => ({ product: 'MediaWorkbench', version: '0.5.0', api_version: 1,
    base_url: 'http://127.0.0.1:49100/', token: 'synthetic-only', pid, data_directory: data });
  const disk = {
    promises: { readFile: async () => JSON.stringify(discovery(spawned ? next.pid : old.pid)), mkdir: async () => {} },
    existsSync: () => true,
    createWriteStream: () => Object.assign(new EventEmitter(), { end() {}, write() {} }),
  };
  const network = async () => {
    if (!spawned) {
      if (!oldResponse) throw new Error('Old HTTP listener is closed');
      await oldResponse();
    }
    return new Response(JSON.stringify({ product: 'MediaWorkbench', version: '0.5.0', data_directory: data }));
  };
  const modules = new Map();
  function load(name) {
    if (modules.has(name)) return modules.get(name).exports;
    const module = { exports: {} }; modules.set(name, module);
    const requireBoundary = id => id === 'node:fs' ? disk : id === 'node:child_process'
      ? { spawn: () => { spawned++; return next; } }
      : id.startsWith('.') ? load(id.slice(2)) : require(id);
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../dist', name + '.js'), 'utf8'),
      { module, exports: module.exports, require: requireBoundary, process, setTimeout, clearTimeout,
        URL, AbortSignal, Response, fetch: network });
    return module.exports;
  }
  const { HostController } = load('host');
  const host = new HostController({ root: data, data, python: 'synthetic-python',
    instance: path.join(data, 'instance.json'), version: '0.5.0' }, { startupMs: 1000 });
  host.child = old; host.instance = discovery(old.pid);
  return { host, old, next, spawned: () => spawned };
}

test('a closing backend is replaced once after its HTTP listener disappears', async () => {
  const { host, old, next, spawned } = recoveryFixture();
  const exit = setTimeout(() => { old.exitCode = 0; old.emit('exit', 0); }, 20);
  try {
    const ready = await host.start();
    assert.equal(spawned(), 1, 'Exactly one replacement backend must be started');
    assert.equal(ready.pid, next.pid);
  } finally { clearTimeout(exit); }
});

test('stopping during recovery prevents a late backend spawn after stop returns', async () => {
  const { host, old, spawned } = recoveryFixture();
  host.instance = undefined;
  const starting = host.start().catch(error => ({ error: error.message }));
  const exit = setTimeout(() => { old.exitCode = 0; old.emit('exit', 0); }, 20);
  try {
    await new Promise(resolve => setTimeout(resolve, 40));
    await host.stop();
    const result = await starting;
    assert.equal(spawned(), 0, 'No backend may spawn after stop completed');
    assert.match(result.error, /取消|停止/);
    assert.equal(host.instance, undefined);
  } finally { clearTimeout(exit); }
});

test('a delayed discovery cannot restore stale readiness after stop completed', async () => {
  let entered, release;
  const requested = new Promise(resolve => { entered = resolve; });
  const response = new Promise(resolve => { release = resolve; });
  const { host, old, spawned } = recoveryFixture(async () => { entered(); await response; });
  host.instance = undefined;
  const starting = host.start().catch(error => ({ error: error.message }));
  await requested;
  old.exitCode = 0;
  await host.stop();
  release();
  const result = await starting;
  assert.match(result.error, /取消/);
  assert.equal(spawned(), 0);
  assert.equal(host.instance, undefined);
});
