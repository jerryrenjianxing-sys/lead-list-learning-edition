const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const os=require('node:os');
const {DesktopUpdater,githubReleaseUrl}=require('../dist/updates');
const asset={PackageId:'MediaWorkbench.Desktop',Version:'0.3.1',Type:'Full',FileName:'MediaWorkbench.Desktop-0.3.1-full.nupkg',Size:123,SHA256:'a'.repeat(64)};
const update={TargetFullRelease:asset,DeltasToTarget:[],IsDowngrade:false};
async function fixture() {
 const data=await fs.mkdtemp(path.join(os.tmpdir(),'mdr-updater-')); const calls=[];
 const manager={getAppId:()=> 'MediaWorkbench.Desktop',getCurrentVersion:()=> '0.3.0',checkForUpdatesAsync:async()=>update,getUpdatePendingRestart:()=>null,downloadUpdateAsync:async(_u,p)=>{calls.push('download');p?.(100);},waitExitThenApplyUpdate:()=>calls.push('apply')};
 const d={data,root:'C:/app/current',version:'0.3.0',manager:()=>manager,releases:async()=> 'https://github.com/release',verify:async()=>calls.push('verify'),prepare:async()=>{calls.push('prepare');return true;},stop:async()=>calls.push('stop'),recover:async()=>calls.push('recover'),exit:()=>calls.push('exit')};
 return {data,calls,manager,d,updater:new DesktopUpdater(d),clean:()=>fs.rm(data,{recursive:true})};
}
test('update waits for user choice; install verifies prepares stops then applies',async()=>{const f=await fixture();try{assert.equal((await f.updater.check()).state,'available');assert.deepEqual(f.calls,[]);await f.updater.download();assert.equal(f.updater.state.state,'downloaded');await f.updater.install();assert.deepEqual(f.calls,['download','verify','verify','prepare','stop','apply','exit']);}finally{await f.clean();}});
test('busy backend defers install without stop or apply',async()=>{const f=await fixture();try{f.d.prepare=async()=>false;await f.updater.check();await f.updater.download();assert.equal((await f.updater.install()).state,'waiting_idle');assert.ok(!f.calls.includes('stop'));assert.ok(!f.calls.includes('apply'));}finally{await f.clean();}});
test('download network and checksum failures return retryable state',async()=>{const f=await fixture();try{await f.updater.check();f.manager.downloadUpdateAsync=async()=>{throw Error('offline');};assert.equal((await f.updater.download()).state,'available');f.manager.downloadUpdateAsync=async()=>{};f.d.verify=async()=>{throw Error('corrupt');};assert.equal((await f.updater.download()).state,'available');f.d.verify=async()=>{};assert.equal((await f.updater.download()).state,'downloaded');}finally{await f.clean();}});
test('failed apply recovers current host and preserves pending package',async()=>{const f=await fixture();try{await f.updater.check();await f.updater.download();f.manager.waitExitThenApplyUpdate=()=>{throw Error('updater failed');};assert.equal((await f.updater.install()).state,'downloaded');assert.ok(f.calls.includes('recover'));assert.ok(!f.calls.includes('exit'));}finally{await f.clean();}});
test('concurrent clicks reuse operation and downgrade is rejected',async()=>{const f=await fixture();try{let resolve;f.manager.checkForUpdatesAsync=()=>new Promise(r=>resolve=r);const check=f.updater.check();for(let i=0;i<20&&!resolve;i++)await new Promise(r=>setTimeout(r,5));assert.equal((await f.updater.check()).state,'checking');resolve({...update,IsDowngrade:true});assert.equal((await check).state,'error');}finally{await f.clean();}});
test('automatic check israte limited and doesnot trigger download',async()=>{const f=await fixture();try{await fs.writeFile(path.join(f.data,'update-check.txt'),new Date().toISOString());f.d.releases=()=>{throw Error('not called');};assert.equal((await f.updater.check(true)).state,'idle');assert.deepEqual(f.calls,[]);}finally{await f.clean();}});
test('GitHub source skips oldlearning and unmatchingchannels, chooses highest version',async()=>{
 const release=(v,channel='win-preview')=>({draft:false,tag_name:`v${v}`,assets:[{name:`releases.${channel}.json`,browser_download_url:`https://github.com/jerryrenjianxing-sys/lead-list-learning-edition/releases/download/v${v}/releases.${channel}.json`},{name:`MediaWorkbench.Desktop-${v}-full.nupkg`}]});
 const responses=[release('9.0.0','win'),{draft:false,tag_name:'old-learning',assets:[]},release('0.3.1'),release('0.4.0')];
 const request=async url=>({ok:true,json:async()=>String(url).includes('api.github.com')?responses:{Assets:[{...asset,Version:String(url).includes('0.4.0')?'0.4.0':'0.3.1',FileName:String(url).includes('0.4.0')?'MediaWorkbench.Desktop-0.4.0-full.nupkg':asset.FileName}]}});
 assert.equal(await githubReleaseUrl('0.3.0',request),'https://github.com/jerryrenjianxing-sys/lead-list-learning-edition/releases/download/v0.4.0');
});
