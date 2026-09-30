const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
vm.runInThisContext(fs.readFileSync('central/static/fleet.js','utf8'));
const source = fs.readFileSync('central/static/index.html','utf8').match(/<script>\s*function app\(\)[\s\S]*?<\/script>/)[0].replace(/^<script>|<\/script>$/g,'');
vm.runInThisContext(source);
global.confirm = () => true;

function fixture() {
  const state=app(); state.myRole='admin'; state.loggedIn=true; state.commandText='hostname';
  state.servers=Array.from({length:9},(_,i)=>({agent_id:'host-'+i,online:true,info:{capabilities:{command_protocol:1,host_commands:true}},containers:[]}));
  state.bulkServers=Object.fromEntries(state.servers.map(s=>[s.agent_id,true]));
  return state;
}

(async()=>{
  const state=fixture();
  let active=0,peak=0;
  const calls=[];
  state._post=async(url,body)=>{
    calls.push({url,body}); active++; peak=Math.max(peak,active);
    // Changing selection and editor while a batch runs must not change its targets or command.
    state.bulkServers={}; state.commandText='different command';
    await new Promise(resolve=>setTimeout(resolve,10)); active--;
    if(url.includes('host-3')) throw new Error('connection lost');
    return {exit_code:0,stdout:'ok'};
  };
  await state.runFleetCommand();
  assert.equal(calls.length,9); assert.equal(peak,4);
  assert(calls.every(c=>c.body.command==='hostname'));
  assert.equal(state.commandResults.find(r=>r.agent_id==='host-3').status,'unknown');
  assert.equal(state.commandResults.filter(r=>r.status==='success').length,8);
  assert.equal(state.commandRunning,false);

  const logout=fixture(); let sent=0;
  logout._post=async()=>{
    sent++;
    await new Promise(resolve=>setTimeout(resolve,10));
    logout.commandGeneration++; // logout invalidates even if another user immediately logs in
    return {exit_code:0};
  };
  await logout.runFleetCommand();
  assert.equal(sent,4);
  assert.equal(logout.commandResults.filter(r=>r.status==='skipped').length,5);

  const slowLogout=fixture(); let resolveLogout;
  global.fetch=()=>new Promise(resolve=>{resolveLogout=resolve;});
  const signout=slowLogout.logout();
  assert.equal(slowLogout.loggedIn,false);
  assert.equal(slowLogout.commandGeneration,1);
  assert.equal(slowLogout.commandText,'');
  resolveLogout({ok:true}); await signout;

  const cancelled=fixture(); global.confirm=()=>false;
  cancelled._post=async()=>assert.fail('Rejected confirmation must not dispatch');
  await cancelled.runFleetCommand();
  assert.equal(cancelled.commandResults.length,0);
  const readonly=fixture(); readonly.myRole='user';
  readonly._post=async()=>assert.fail('Viewer must not dispatch');
  await readonly.runFleetCommand();
  console.log('Fleet logic OK: 4-worker bound, immutable batch, partial failure, no retries, logout queue invalidation, confirmation, viewer.');
})().catch(error=>{console.error(error);process.exitCode=1;});
