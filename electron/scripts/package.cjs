const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { api: forge } = require('@electron-forge/core');
process.on('uncaughtException', error => { process.stderr.write(String(error.stack) + '\n'); process.exitCode = 1; });
const root = path.resolve(__dirname, '..', '..');
const version = /^version\s*=\s*"([^"]+)"/m.exec(fs.readFileSync(path.join(root, 'pyproject.toml'), 'utf8'))?.[1];
if (!version || !/^\d+\.\d+\.\d+$/.test(version)) throw new Error('Source version unavailable');
const generated = path.join(root, 'build', 'electron-source');
fs.mkdirSync(generated, { recursive: true });
function copyDirectory(source, target) {
  fs.mkdirSync(target, { recursive: true });
  for (const entry of fs.readdirSync(source, { withFileTypes: true })) {
    const from = path.join(source, entry.name), to = path.join(target, entry.name);
    if (entry.isDirectory()) copyDirectory(from, to);
    else if (entry.isSymbolicLink()) { const resolved = fs.realpathSync(from); if (fs.statSync(resolved).isDirectory()) copyDirectory(resolved, to); else fs.copyFileSync(resolved, to); }
    else fs.copyFileSync(from, to);
  }
}
for (const name of ['dist', 'node_modules']) copyDirectory(path.join(root, 'electron', name), path.join(generated, name));
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'electron', 'package.json'), 'utf8'));
manifest.version = version;
manifest.config = { forge: path.join(root, 'electron', 'forge.config.cjs') };
fs.writeFileSync(path.join(generated, 'package.json'), JSON.stringify(manifest, null, 2) + '\n');
// Forge's programmatic API avoids editing source manifest or its lockfile.
(async () => {
  await forge.package({ dir: generated, arch: 'x64', platform: 'win32', outDir: path.join(root, 'build', 'electron') });
  const source_revision = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).stdout?.trim();
  const source_dirty = !!spawnSync('git', ['status', '--porcelain'], { cwd: root, encoding: 'utf8' }).stdout?.trim();
  fs.writeFileSync(path.join(root, 'build', 'electron', 'build-shell.json'), JSON.stringify({ version, electron: manifest.devDependencies.electron, forge: manifest.devDependencies['@electron-forge/cli'], velopack: manifest.dependencies.velopack, platform: 'win32', arch: 'x64', source_revision, source_dirty }, null, 2) + '\n');
})().catch(error => { process.stderr.write(String(error.stack) + '\n'); process.exitCode = 1; });
