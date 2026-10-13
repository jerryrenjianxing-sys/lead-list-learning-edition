const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const p = require('../dist/policy');
const asset = { PackageId:'MediaWorkbench.Desktop', Version:'0.3.1', Type:'Full', FileName:'MediaWorkbench.Desktop-0.3.1-win-preview-full.nupkg', Size:3, SHA256:crypto.createHash('sha256').update('abc').digest('hex') };
test('IPC origin policy accepts exact loopback authority only', () => {
  const base = 'http://127.0.0.1:48139';
  assert.equal(p.trustedSource(`${base}/#manage/settings`, base), true);
  for (const url of ['http://127.0.0.1:48140/', 'http://localhost:48139/', 'https://127.0.0.1:48139/', 'http://127.0.0.1.evil.test:48139/', 'file:///index.html', 'http://secret@127.0.0.1:48139/']) assert.equal(p.trustedSource(url, base), false, url);
});
test('update policy rejects wrong product, path, hash, channel package kind and downgrade', () => {
  assert.equal(p.acceptableAsset(asset, '0.3.0'), true);
  for (const value of [{PackageId:'MediaFlow'}, {Type:'Delta'}, {Version:'0.2.0'}, {Version:'0.3.0'}, {Version:'0.4.0-beta'}, {FileName:'../evil-full.nupkg'}, {FileName:'C:\\evil-full.nupkg'}, {FileName:'MediaWorkbench.Desktop-0.3.0-win-preview-full.nupkg'}, {FileName:'MediaWorkbench.Desktop-0.3.1-win-full.nupkg'}, {SHA256:'00'}, {Size:0}]) assert.equal(p.acceptableAsset({...asset,...value},'0.3.0'), false, JSON.stringify(value));
});
test('checksum verifies full stream and actual file length', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'mdr-update-test-'));
  try { const file = path.join(dir,'test'); await fs.writeFile(file,'abc'); await p.verifyPackage(file,asset); await fs.writeFile(file,'abd'); await assert.rejects(p.verifyPackage(file,asset),/校验/); await fs.writeFile(file,'abcd'); await assert.rejects(p.verifyPackage(file,asset),/大小/); } finally { await fs.rm(dir,{recursive:true}); }
});
test('automatic update check budget is24hours and manual invalid stamp isdue', () => {
  const now=Date.now(); assert.equal(p.automaticCheckDue(new Date(now-86400000).toISOString(),now),true); assert.equal(p.automaticCheckDue(new Date(now-1000).toISOString(),now),false); assert.equal(p.automaticCheckDue('invalid',now),true);
});
test('discovery requires valid local identity and version/data compatibility', () => {
  const i={product:'MediaWorkbench',version:'0.3.0',api_version:1,base_url:'http://127.0.0.1:48139',token:'test-private',pid:1,data_directory:'C:\\data'};
  assert.equal(p.validateDiscovery(i),i); assert.equal(p.compatibleInstance(i,'0.3.0','C:\\DATA'),true); assert.equal(p.compatibleInstance(i,'0.2.0','C:\\data'),false); assert.throws(()=>p.validateDiscovery({...i,base_url:'http://example.com'}));
});
