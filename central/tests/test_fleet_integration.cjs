// Requires an isolated central on FLEET_TEST_URL (default http://localhost:18082).
// FLEET_TEST_PASSWORD and FLEET_TEST_AGENT_TOKEN are test credentials, never production.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const base = process.env.FLEET_TEST_URL || 'http://localhost:18082';
const password = process.env.FLEET_TEST_PASSWORD || 'Fleet-Local-2026!';
const token = process.env.FLEET_TEST_AGENT_TOKEN || 'fleet-preview-agent-token';
const agents = [], received = [];

async function agent(name) {
  const ws = new WebSocket(base.replace(/^http/,'ws') + '/ws/agent?agent_token=' + encodeURIComponent(token));
  agents.push(ws);
  await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
  await new Promise((resolve,reject)=>{
    ws.onmessage=resolve; ws.onerror=reject;
    ws.send(JSON.stringify({type:'register',agent_name:name,hostname:name,ip:'10.0.0.1',cpus:4,total_memory:8*1024**3,capabilities:{command_protocol:1,host_commands:true}}));
  });
  ws.onmessage=event=>{
    const request=JSON.parse(event.data);
    if(request.type==='request' && request.action==='host_command') {
      received.push({name,...request.params});
      setTimeout(()=>ws.send(JSON.stringify({type:'response',request_id:request.request_id,data:{stdout:name+'\n',stderr:'',exit_code:0,timed_out:false,truncated:false,duration_ms:80}})),80);
    }
  };
  ws.send(JSON.stringify({type:'data',timestamp:Date.now()/1000,containers:[{name:'elastic',image:'elastic:9.2.6',status:'running',cpu_percent:10,memory:{usage_bytes:1024**3},logs:'private log',labels:{'com.docker.compose.service':'elastic'},compose:'services:\n  elastic:\n    image: elastic:9.2.6\n'}]}));
  return ws;
}

(async()=>{
  const browser=await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL||'msedge',headless:true});
  let heartbeat;
  try {
    await agent('integration-one'); await agent('integration-two');
    heartbeat=setInterval(()=>agents.forEach(ws=>ws.readyState===1 && ws.send(JSON.stringify({type:'ping'}))),10000);
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    // Only missing vendored test assets are served by Playwright. All API and WS traffic is real.
    await page.route('**/static/*',async route=>{
      const name=path.basename(new URL(route.request().url()).pathname);
      if(['index.html','fleet.js','fleet.css'].includes(name)) return route.continue();
      const local=path.join(process.env.FLEET_TEST_ASSETS||'.tmp-fleet/assets',name);
      return route.fulfill({contentType:name.endsWith('.css')?'text/css':'application/javascript',body:fs.existsSync(local)?fs.readFileSync(local):''});
    });
    page.on('dialog',dialog=>dialog.accept());
    await page.goto(base);
    await page.locator('[x-model="lf.u"]').fill('admin');
    await page.locator('[x-model="lf.p"]').fill(password);
    await page.getByRole('button',{name:'Zaloguj się',exact:true}).click();
    await page.waitForFunction(()=>Alpine.$data(document.body).servers.filter(s=>s.agent_id.startsWith('integration-')).every(s=>s.containers.length===1) && Alpine.$data(document.body).servers.length>=2);
    const snapshot=await page.evaluate(()=>Alpine.$data(document.body).servers.find(s=>s.agent_id==='integration-one').containers[0]);
    assert.equal(snapshot.compose_tag,'9.2.6'); assert(!('logs' in snapshot)); assert(!('compose' in snapshot));
    await page.getByLabel('Zaznacz integration-one',{exact:true}).check();
    await page.getByLabel('Zaznacz integration-two',{exact:true}).check();
    await page.getByRole('button',{name:'Wspólne komendy →',exact:true}).click();
    await page.getByLabel('Komenda do wykonania').fill('hostname');
    await page.getByRole('button',{name:'Uruchom na 2 serwerach',exact:true}).click();
    await page.waitForFunction(()=>Alpine.$data(document.body).commandResults.filter(r=>r.status==='success').length===2);
    assert.equal(received.length,2); assert(received.every(r=>r.command==='hostname'));
    const csrfStatus=await page.evaluate(async()=> (await fetch('/api/servers/integration-one/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:'hostname'})})).status);
    assert.equal(csrfStatus,403);
    assert.equal(received.length,2);
    const missing=await page.evaluate(async()=>{const r=await fetch('/api/missing-fleet-endpoint');return {status:r.status,body:await r.json()};});
    assert.equal(missing.status,404); assert(missing.body.detail);
    await page.getByRole('button',{name:/Ustawienia/}).click();
    await page.getByRole('button',{name:'Reguły kontenerów',exact:true}).click();
    await page.evaluate(async()=>{const app=Alpine.$data(document.body);for(const r of await app._get('/api/inventory-rules'))await app._del(`/api/inventory-rules/${r.id}`);});
    await page.getByLabel('Nazwa reguły',{exact:true}).fill('Integration version');
    await page.getByLabel('Fragmenty nazw',{exact:false}).fill('elastic');
    await page.getByLabel('Grupa kontenerów',{exact:true}).fill('Elastic Stack');
    await page.getByLabel('Wymagany tag w Compose',{exact:true}).fill('9.3.1');
    await page.getByRole('button',{name:'Dodaj regułę',exact:true}).click();
    await page.waitForFunction(()=>Alpine.$data(document.body).alertBadge===2);
    // No new data reports: editing must recalculate the existing snapshots immediately.
    await page.getByRole('button',{name:'Edytuj',exact:true}).click();
    await page.getByLabel('Wymagany tag w Compose',{exact:true}).fill('9.2.6');
    await page.getByRole('button',{name:'Zapisz zmiany',exact:true}).click();
    await page.waitForFunction(()=>Alpine.$data(document.body).alertBadge===0);
    assert.deepEqual(errors,[]);
    console.log('Integration OK: actual login, agent/dashboard WebSockets, initial metrics, two-host command routing, CSRF, API JSON errors, immediate policy alert/resolution.');
  } finally {
    clearInterval(heartbeat); agents.forEach(ws=>ws.close()); await browser.close();
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
