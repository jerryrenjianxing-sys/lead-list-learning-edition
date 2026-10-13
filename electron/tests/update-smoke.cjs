// Exercise the shipped DesktopUpdater class and Velopack N-API binding. The
// Python wrapper preserves the user's registration before any isolated install.
const { _electron } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const http = require('node:http');
const assert = require('node:assert/strict');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const run = promisify(execFile);
const repo = path.resolve(__dirname, '../..');
const args = process.argv.slice(2);
const option = (name,fallback) => args.includes(name) ? args[args.indexOf(name)+1] : fallback;
const output = path.resolve(option('--output',path.join(repo,'.cache/native-updater-result.json')));
const nextPackage = path.resolve(option('--next-package'));
const directory = path.resolve(option('--directory',path.join(repo,'build/MediaWorkbench')));
const alive = pid => { try { process.kill(pid,0); return true; } catch { return false; } };
async function until(fn,budget=90000) { const end=Date.now()+budget;while(Date.now()<end){const result=await fn();if(result)return result;await new Promise(resolve=>setTimeout(resolve,150));}throw Error('Native update check timed out'); }
async function checksum(file) { const hash=crypto.createHash('sha256');for await(const chunk of fs.createReadStream(file))hash.update(chunk);return hash.digest('hex'); }

async function installed() {
 const install=path.resolve(option('--installed-root')), current=path.join(install,'current');
 const data=process.env.MEDIAWORKBENCH_DATA_DIR, discovery=process.env.MEDIAWORKBENCH_INSTANCE;
 assert(data&&discovery&&path.resolve(data).startsWith(path.dirname(install)+path.sep));
 const version=JSON.parse(fs.readFileSync(path.join(directory,'build-manifest.json'),'utf8')).version;
 const feed=JSON.parse(fs.readFileSync(path.join(path.dirname(nextPackage),'releases.win-preview.json'),'utf8'));
 const asset=feed.Assets.find(value=>value.FileName===path.basename(nextPackage));assert(asset);assert.equal(asset.PackageId,'MediaWorkbench.Desktop');assert.equal(asset.Type,'Full');assert.equal(asset.Size,fs.statSync(nextPackage).size);assert.equal(asset.SHA256.toLowerCase(),await checksum(nextPackage));
 const nextVersion=asset.Version;assert.notEqual(nextVersion,version);
 fs.mkdirSync(data,{recursive:true});fs.writeFileSync(path.join(data,'update-check.txt'),new Date().toISOString());
 let server,desktop,processHandle,info,newInfo,newDesktopPid,mode='broken',requests=0,report;
 const checks=[];
 const env={...process.env};delete env.ELECTRON_RUN_AS_NODE;
 async function api(method,route,body) { const response=await fetch(info.base_url+'/api/v1'+route,{method,headers:{Authorization:'Bearer '+info.token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(15000)});assert(response.ok,`${route} ${response.status}`);return response.json(); }
 try {
  server=http.createServer((request,response)=>{assert.equal(request.url,'/full.nupkg');requests++;response.setHeader('Content-Type','application/octet-stream');if(mode==='broken'){response.end(Buffer.alloc(1234,1));return;}fs.createReadStream(nextPackage).pipe(response);});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const downloadUrl=`http://127.0.0.1:${server.address().port}/full.nupkg`;
  desktop=await _electron.launch({executablePath:path.join(current,'MediaWorkbench.exe'),cwd:current,env,timeout:90000});processHandle=desktop.process();
  const page=await desktop.firstWindow();await page.waitForURL(/^http:\/\/127\.0\.0\.1:\d+\//,{timeout:90000});info=JSON.parse(fs.readFileSync(discovery,'utf8'));assert.equal(info.version,version);
  await run(path.join(current,'runtime/python/python.exe'),[path.join(repo,'tests/client_smoke.py'),'--instance',discovery,'--output',path.join(path.dirname(install),'client-proof')],{cwd:current,env,windowsHide:true,timeout:150000,maxBuffer:4*1024*1024,encoding:'utf8'});
  const before={datasets:await api('GET','/datasets'),analyses:await api('GET','/analyses'),artifacts:await api('GET','/artifacts')};
  const testImplementation=option('--implementation');
  await desktop.evaluate(async({app},configuration)=>{
   const nodePath=process.getBuiltinModule('path'),createRequire=process.getBuiltinModule('module').createRequire;
   const load=createRequire(nodePath.join(app.getAppPath(),'dist/main.js'));
   const {DesktopUpdater}=configuration.implementation?load(configuration.implementation):load('./updates');
   const {HostController}=load('./host');const {UpdateManager}=load('velopack');
   const host=new HostController({root:configuration.current,python:nodePath.join(configuration.current,'runtime/python/python.exe'),data:configuration.data,instance:configuration.discovery,version:configuration.version});await host.discover();
   global.__nativeFutureSelection=async()=>({asset:configuration.asset,downloadUrl:configuration.downloadUrl});
   global.__nativeUpdateDependencies={data:configuration.data,root:configuration.current,version:configuration.version,manager:(source,options)=>{if(/^https?:/i.test(source))throw Error('Native SDK must receive FileSource');return new UpdateManager(source,options);},releases:async()=>undefined,prepare:()=>host.prepareUpdate(),stop:()=>host.stop(),recover:()=>host.start(),exit:()=>app.quit()};
   global.__nativeUpdateClass=DesktopUpdater;global.__nativeUpdater=new DesktopUpdater(global.__nativeUpdateDependencies);
  },{current,data,discovery,version,asset,downloadUrl,implementation:testImplementation&&path.resolve(testImplementation)});
  assert.equal((await desktop.evaluate(()=>global.__nativeUpdater.check())).state,'current');checks.push('actual-native-empty-filesource-confirms-current-version');
  await desktop.evaluate(()=>{global.__nativeUpdateDependencies.releases=global.__nativeFutureSelection;});
  assert.equal((await desktop.evaluate(()=>global.__nativeUpdater.check())).state,'available');checks.push('desktop-updater-native-filesource-finds-private-next-preview');
  assert.equal((await desktop.evaluate(()=>global.__nativeUpdater.download())).state,'available');
  assert(!fs.readdirSync(path.join(data,'update-source')).some(name=>name.endsWith('.part')||name.endsWith('.nupkg')));checks.push('broken-http-transfer-rejected-and-partial-file-cleaned');
  mode='good';assert.equal((await desktop.evaluate(()=>global.__nativeUpdater.download())).state,'downloaded');assert.equal(requests,2);
  const cached=path.join(install,'packages',asset.FileName);assert.equal(await checksum(cached),asset.SHA256.toLowerCase());assert(!fs.existsSync(path.join(data,'update-source',asset.FileName)));checks.push('real-stream-native-copy-size-sha256-and-source-cleanup');
  await desktop.evaluate(()=>{global.__nativeUpdater=new global.__nativeUpdateClass(global.__nativeUpdateDependencies);});assert.equal((await desktop.evaluate(()=>global.__nativeUpdater.check())).state,'downloaded');assert.equal(requests,2);checks.push('pending-native-package-restored-without-redownload');
  fs.writeFileSync(cached,Buffer.from('corrupted after download'));
  assert.equal((await desktop.evaluate(()=>global.__nativeUpdater.install())).state,'available');
  assert.equal((await desktop.evaluate(()=>global.__nativeUpdater.download())).state,'downloaded');assert.equal(requests,3);assert.equal(await checksum(cached),asset.SHA256.toLowerCase());checks.push('actual-native-existing-corrupt-pending-package-is-replaced');
  const diagnostic=await api('POST','/diagnostics/self-tests',{include_network:false});assert.equal((await desktop.evaluate(()=>global.__nativeUpdater.install())).state,'waiting_idle');checks.push('real-active-selftest-defers-update-before-shutdown');
  await api('POST',`/diagnostics/self-tests/${diagnostic.id}/cancel`,{});await until(async()=>{const latest=await api('GET','/diagnostics/self-tests/latest');return !['running','cancelling'].includes(latest.state);});
  try { await desktop.evaluate(()=>global.__nativeUpdater.install()); } catch(error) { assert(/closed|destroyed|context|Target/i.test(error.message),error.message); }
  await until(()=>processHandle.exitCode!==null||processHandle.signalCode!==null);
  newInfo=await until(()=>{if(!fs.existsSync(discovery))return null;const candidate=JSON.parse(fs.readFileSync(discovery,'utf8'));return candidate.pid!==info.pid&&candidate.version===nextVersion?candidate:null;},120000);assert(!alive(info.pid));info=newInfo;
  const parent=await run(path.join(current,'runtime/python/python.exe'),['-c','import psutil,sys;print(psutil.Process(int(sys.argv[1])).ppid())',String(info.pid)],{env,cwd:current,windowsHide:true,encoding:'utf8',timeout:15000});newDesktopPid=Number(parent.stdout.trim());assert(alive(newDesktopPid));
  assert.deepEqual(await api('GET','/datasets'),before.datasets);assert.deepEqual(await api('GET','/analyses'),before.analyses);assert.deepEqual(await api('GET','/artifacts'),before.artifacts);checks.push('actual-native-wait-exit-apply-restart-keeps-synthetic-data');
  report={ok:true,version,next_version:nextVersion,implementation_override:!!testImplementation,checks,limitations:['Transport failure/download uses an isolated HTTP fixture; it does not claim a future public GitHub release exists.',testImplementation?'Development class override is used for preliminary verification; canonical acceptance must use the shipped class.':'Actual shipped DesktopUpdater and Velopack .node check/copy/apply/restart are exercised.']};
 } finally {
  if(fs.existsSync(discovery))await run(path.join(current,'MediaWorkbench.exe'),['--quit'],{cwd:current,env,windowsHide:true,timeout:15000});
  if(processHandle)await until(()=>processHandle.exitCode!==null||processHandle.signalCode!==null,45000);
  if(info)await until(()=>!alive(info.pid)&&!fs.existsSync(discovery),45000);
  if(newDesktopPid)await until(()=>!alive(newDesktopPid),45000);
  if(desktop)await desktop.close().catch(()=>{});
  if(server){server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
 }
 if(report){report.cleanup_complete=true;fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,JSON.stringify(report,null,2));console.log(JSON.stringify(report));}
}

async function wrapped() {
 const setup=path.resolve(option('--setup'));assert(fs.existsSync(setup));assert(fs.existsSync(nextPackage));
 const python=path.join(directory,'runtime/python/python.exe');
 const wrapper=String.raw`
import importlib.util,json,os,shutil,subprocess,sys,uuid
from pathlib import Path
repo,stage,setup,package,target,node,script,implementation=map(str,sys.argv[1:])
spec=importlib.util.spec_from_file_location('upgrade_smoke',Path(repo)/'tests/electron_upgrade_smoke.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
case=m.safe_root(Path(repo)/'.cache'/('native-update-'+uuid.uuid4().hex[:10]));case.mkdir();registration=m.ShellRegistration(case);install=case/'app';data=case/'data';instance=case/'instance.json'
env={**os.environ,'MEDIAWORKBENCH_DATA_DIR':str(data),'MEDIAWORKBENCH_INSTANCE':str(instance),'PYTHONUTF8':'1','PYTHONIOENCODING':'utf-8'};env.pop('ELECTRON_RUN_AS_NODE',None)
try:
 m.run_command([setup,'--silent','--installto',install,'--log',case/'setup.log'],env)
 command=[node,script,'--phase','installed','--directory',stage,'--installed-root',str(install),'--next-package',package,'--output',target]
 if implementation:command+=['--implementation',implementation]
 result=subprocess.run(command,cwd=repo,env=env,check=True,timeout=420,text=True,encoding='utf-8')
finally:
 registration.restore();registration.verify()
 # All fixture processes were checked dead by the child. Remove only our
 # verified installation copy; avoid the SDK's detached CHOICE console helper.
 if install.exists():
  owned_install=m.safe_root(install);assert owned_install.parent==case.resolve() and not install.is_symlink();shutil.rmtree(owned_install)
value=json.loads(Path(target).read_text(encoding='utf-8'));value['checks'].append('original-registration-and-shortcuts-restored');value['cleanup_complete']=not install.exists();assert value['cleanup_complete'];Path(target).write_text(json.dumps(value,indent=2),encoding='utf-8')
`;
 const result=await run(python,['-c',wrapper,repo,directory,setup,nextPackage,output,process.execPath,__filename,option('--implementation','')],{cwd:repo,env:{...process.env,PYTHONPATH:directory,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'},windowsHide:true,timeout:480000,maxBuffer:4*1024*1024,encoding:'utf8'});console.log(result.stdout.trim());
}
(option('--phase')==='installed'?installed():wrapped()).catch(error=>{const report={ok:false,cleanup_complete:false,error:error.message};fs.mkdirSync(path.dirname(output),{recursive:true});fs.writeFileSync(output,JSON.stringify(report,null,2));console.error(error.stack);process.exitCode=1;});
