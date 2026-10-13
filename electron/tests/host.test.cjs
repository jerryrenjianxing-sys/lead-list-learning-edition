const test=require('node:test');
const assert=require('node:assert/strict');
const http=require('node:http');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {HostController}=require('../dist/host');
const {EventEmitter}=require('node:events');
async function hostFixture({version='0.3.0',dataMismatch=false}={}) {
 const data=await fs.mkdtemp(path.join(os.tmpdir(),'mdr-host-')); const server=http.createServer((req,res)=>{assert.equal(req.headers.authorization,'Bearer isolated-test');res.setHeader('Content-Type','application/json');res.end(JSON.stringify({product:'MediaWorkbench',version,data_directory:dataMismatch?`${data}-other`:data}));});
 await new Promise(r=>server.listen(0,'127.0.0.1',r)); const instance=path.join(data,'instance.json'); await fs.writeFile(instance,JSON.stringify({product:'MediaWorkbench',version,api_version:1,base_url:`http://127.0.0.1:${server.address().port}`,token:'isolated-test',pid:process.pid,data_directory:data}));
 return {controller:new HostController({root:data,python:'missing',data,instance,version:'0.3.0'}),clean:async()=>{await new Promise(r=>server.close(r));await fs.rm(data,{recursive:true});}};
}
test('exactmatchingrunningbackend isreused without spawningPython',async()=>{const f=await hostFixture();try{assert.equal((await f.controller.start()).version,'0.3.0');assert.equal(f.controller.child,undefined);}finally{await f.clean();}});
test('livelegacyversion isblocked before spawningPython',async()=>{const f=await hostFixture({version:'0.2.0'});try{await assert.rejects(f.controller.start(),/旧版/);assert.equal(f.controller.child,undefined);}finally{await f.clean();}});
test('authenticateddiagnosticsdata mismatch isblocked',async()=>{const f=await hostFixture({dataMismatch:true});try{await assert.rejects(f.controller.start(),/数据目录/);assert.equal(f.controller.child,undefined);}finally{await f.clean();}});
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
