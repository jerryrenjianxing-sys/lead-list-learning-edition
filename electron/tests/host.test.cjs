const test=require('node:test');
const assert=require('node:assert/strict');
const http=require('node:http');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const crypto=require('node:crypto');
const {HostController}=require('../dist/host');
const {EventEmitter}=require('node:events');
async function hostFixture({version='0.3.0',dataMismatch=false,handshakeMismatch=false,unauthorizedHandshake=false,slowDiagnostics=false}={}) {
 const data=await fs.mkdtemp(path.join(os.tmpdir(),'mdr-host-'));const canonical=await fs.realpath(data);const workspace=crypto.createHash('sha256').update(process.platform==='win32'?canonical.toLowerCase():canonical).digest('hex');const requests=[];
 const server=http.createServer((req,res)=>{assert.equal(req.headers.authorization,'Bearer isolated-test');requests.push(req.url);res.setHeader('Content-Type','application/json');
  if(req.url==='/api/v1/agent/handshake'){
   if(unauthorizedHandshake){res.writeHead(401);res.end('{}');return;}
   res.end(JSON.stringify({product:'MediaWorkbench',version,api_version:1,status:'ok',workspace_id:handshakeMismatch?'0'.repeat(64):workspace,instance_id:'fixture-instance'}));return;
  }
  if(req.url==='/api/v1/diagnostics'&&slowDiagnostics)return;
  res.end(JSON.stringify({product:'MediaWorkbench',version,data_directory:dataMismatch?`${data}-other`:data}));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r)); const instance=path.join(data,'instance.json'); await fs.writeFile(instance,JSON.stringify({product:'MediaWorkbench',version,api_version:1,base_url:`http://127.0.0.1:${server.address().port}`,token:'isolated-test',pid:process.pid,data_directory:data}));
 return {controller:new HostController({root:data,python:'missing',data,instance,version:'0.3.0'}),requests,workspace,instancePath:instance,clean:async()=>{server.closeAllConnections();await new Promise(r=>server.close(r));await fs.rm(data,{recursive:true});}};
}
test('exactmatchingrunningbackend isreused without spawningPython',async()=>{const f=await hostFixture();try{assert.equal((await f.controller.start()).version,'0.3.0');assert.equal(f.controller.child,undefined);}finally{await f.clean();}});
test('livelegacyversion isblocked before spawningPython',async()=>{const f=await hostFixture({version:'0.2.0'});try{await assert.rejects(f.controller.start(),/旧版/);assert.equal(f.controller.child,undefined);}finally{await f.clean();}});
test('health data mismatch is blocked before attaching',async()=>{const f=await hostFixture({dataMismatch:true});try{await assert.rejects(f.controller.start(),/数据目录/);assert.equal(f.controller.child,undefined);}finally{await f.clean();}});
test('healthy backend reconnect does not wait for a slow database diagnostic',async()=>{
 const f=await hostFixture({slowDiagnostics:true});try{
  assert.equal((await f.controller.start()).version,'0.3.0');
  assert.equal(f.controller.child,undefined);
  assert(f.requests.includes('/api/v1/agent/handshake'));
  assert(!f.requests.includes('/api/v1/diagnostics'));
 }finally{await f.clean();}
});
test('authorized handshake rejects another workspace even when discovery and health match',async()=>{
 const f=await hostFixture({handshakeMismatch:true});try{await assert.rejects(f.controller.start(),/数据目录/);assert.equal(f.controller.child,undefined);}finally{await f.clean();}
});
test('failed protected handshake cannot be replaced by a successful public health check',async()=>{
 const f=await hostFixture({unauthorizedHandshake:true});try{await assert.rejects(f.controller.start(),/身份|授权|数据目录/);assert.equal(f.controller.child,undefined);}finally{await f.clean();}
});
test('declared discovery workspace and instance must agree with the authorized handshake',async()=>{
 for(const mismatch of [{workspace_id:'0'.repeat(64)},{instance_id:'a-replaced-host'}]){
  const f=await hostFixture();try{
   const discovery=JSON.parse(await fs.readFile(f.instancePath,'utf8'));await fs.writeFile(f.instancePath,JSON.stringify({...discovery,...mismatch}));
   await assert.rejects(f.controller.start(),/数据目录|实例身份/);assert.equal(f.controller.child,undefined);
  }finally{await f.clean();}
 }
});
test('concurrent reconnects share one authorized identity check',async()=>{
 const f=await hostFixture();try{
  const discovery=JSON.parse(await fs.readFile(f.instancePath,'utf8'));await fs.writeFile(f.instancePath,JSON.stringify({...discovery,workspace_id:f.workspace,instance_id:'fixture-instance'}));
  const instances=await Promise.all(Array.from({length:6},()=>f.controller.start()));assert(instances.every(x=>x===instances[0]));
  assert.equal(f.requests.filter(x=>x==='/api/v1/agent/handshake').length,1);assert.equal(f.controller.child,undefined);
 }finally{await f.clean();}
});
test('retry retains an existing starting child instead of spawning another host',async()=>{
 const data=await fs.mkdtemp(path.join(os.tmpdir(),'mdr-child-'));const controller=new HostController({root:data,python:process.execPath,data,instance:path.join(data,'missing'),version:'0.3.0'},{startupMs:1});
 const child=new EventEmitter();child.exitCode=null;controller.child=child;controller.discover=async()=>undefined;
 try{await assert.rejects(controller.start(),/及时就绪/);assert.equal(controller.child,child);}finally{await fs.rm(data,{recursive:true});}
});
test('unready owned host prevents unsafe exit and keeps child reference',async()=>{
 const data=await fs.mkdtemp(path.join(os.tmpdir(),'mdr-child-'));const controller=new HostController({root:data,python:process.execPath,data,instance:path.join(data,'missing'),version:'0.3.0'},{shutdownMs:1});
 const child=new EventEmitter();child.exitCode=null;controller.child=child;controller.discover=async()=>undefined;
 try{await assert.rejects(controller.stop(),/无法确认安全退出/);assert.equal(controller.child,child);}finally{await fs.rm(data,{recursive:true});}
});
test('missing health response does not mean an attached live PID has stopped',async()=>{
 const controller=new HostController({root:'unused',python:'unused',data:'unused',instance:'unused',version:'0.3.0'},{shutdownMs:1});controller.instance={product:'MediaWorkbench',version:'0.3.0',api_version:1,base_url:'http://127.0.0.1:1',token:'test',pid:process.pid,data_directory:'unused'};
 await assert.rejects(controller.stop());assert.equal(controller.instance.pid,process.pid);
});
