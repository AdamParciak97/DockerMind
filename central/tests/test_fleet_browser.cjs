// Run from repository root; uses local Alpine/Tailwind assets (or FLEET_TEST_ASSETS).
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const assets = process.env.FLEET_TEST_ASSETS || '.tmp-fleet/assets';
const fixture = (id, online = true, capabilities = {host_commands: true, command_protocol: 1}) => ({
  agent_id: id, online, last_seen: Date.now()/1000,
  info: {agent_name: id, ip: '10.20.1.' + (id === 'prod-one' ? 10 : 11), cpus: 8, total_memory: 16*1024**3, capabilities},
  containers: [{name: 'elastic-' + id, image: 'elastic:9.2.6', compose_image: 'elastic:9.2.6', compose_tag: '9.2.6',
    status: 'running', cpu_percent: 85, memory: {usage_bytes: 1024**3, percent: 10}}],
});

(async () => {
  const browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const agents = [fixture('prod-one'), fixture('prod-two'), fixture('legacy', true, {}), fixture('offline', false)];
    await page.addInitScript(agents => {
      window.WebSocket = class {
        constructor() { setTimeout(() => { this.onopen?.(); this.onmessage?.({data: JSON.stringify({event:'init',data:{agents}})}); }, 20); }
        send() {} close() {} static OPEN = 1;
      };
    }, agents);
    const requests = [];
    let active = 0, peak = 0, rules = [];
    await page.route('http://fleet.test/**', async route => {
      const url = new URL(route.request().url());
      const fulfill = body => route.fulfill({contentType:'application/json', body:JSON.stringify(body)});
      if (url.pathname === '/') return route.fulfill({contentType:'text/html', body:fs.readFileSync('central/static/index.html','utf8')});
      if (url.pathname.startsWith('/static/')) {
        const filename = path.basename(url.pathname);
        const local = ['fleet.js','fleet.css'].includes(filename) ? path.join('central/static',filename) : path.join(assets,filename);
        return route.fulfill({contentType:filename.endsWith('.css') ? 'text/css' : 'application/javascript', body:fs.existsSync(local) ? fs.readFileSync(local) : ''});
      }
      if (url.pathname === '/api/auth/me') return fulfill({username:'admin',role:'admin'});
      if (url.pathname.endsWith('/command')) {
        requests.push({id:url.pathname.split('/')[3], body:route.request().postDataJSON()});
        active++; peak = Math.max(peak,active);
        await new Promise(resolve => setTimeout(resolve, 150)); active--;
        return fulfill({exit_code: url.pathname.includes('prod-two') ? 2 : 0, stdout:'<script>unsafe output</script>\nhost result', stderr:'', duration_ms:150});
      }
      if (url.pathname === '/api/inventory-rules') {
        if (route.request().method() === 'POST') { const rule = {...route.request().postDataJSON(),id:rules.length+1}; rules.push(rule); return fulfill(rule); }
        return fulfill(rules);
      }
      if (url.pathname.includes('count')) return fulfill({count:0});
      return fulfill([]);
    });
    page.on('dialog', dialog => dialog.accept());
    await page.goto('http://fleet.test');
    await page.getByRole('heading',{name:'Twoja infrastruktura'}).waitFor();
    await page.waitForFunction(() => Alpine.$data(document.body).servers.length === 4);
    await page.getByLabel('Szukaj w infrastrukturze').fill('prod');
    await page.getByLabel('Wybierz wszystkie widoczne serwery').check();
    assert.equal(await page.evaluate(() => Alpine.$data(document.body).selectedServers.length), 2);
    await page.getByLabel('Szukaj w infrastrukturze').fill('');
    assert(await page.getByLabel('Wybierz wszystkie widoczne serwery').evaluate(el => el.indeterminate));
    await page.getByLabel('Zaznacz offline',{exact:true}).check();
    await page.getByLabel('Zaznacz legacy',{exact:true}).check();
    fs.mkdirSync('.tmp-fleet',{recursive:true});
    await page.screenshot({path:'.tmp-fleet/dashboard-desktop.png',fullPage:true});
    await page.getByRole('button',{name:'Wspólne komendy →',exact:true}).click();
    await page.getByLabel('Komenda do wykonania').fill('hostname\ndocker ps');
    await page.getByRole('button',{name:'Uruchom na 2 serwerach',exact:true}).click();
    await page.waitForFunction(() => {
      const app=Alpine.$data(document.body); return app.commandResults.length===4 && !app.commandRunning && app.commandCompleted===4;
    });
    assert.equal(peak,2);
    assert.deepEqual(requests.map(r=>r.id).sort(),['prod-one','prod-two']);
    assert(requests.every(r=>r.body.command==='hostname\ndocker ps'));
    const statuses=await page.evaluate(()=>Alpine.$data(document.body).commandResults.map(r=>r.status).sort());
    assert.deepEqual(statuses,['failed','skipped','skipped','success']);
    assert.equal(await page.locator('.command-result script').count(),0);
    await page.screenshot({path:'.tmp-fleet/commands-desktop.png',fullPage:true});
    await page.getByRole('button',{name:/Ustawienia/}).click();
    await page.getByRole('button',{name:'Reguły kontenerów',exact:true}).click();
    await page.getByLabel('Nazwa reguły',{exact:true}).fill('Elastic');
    await page.getByLabel('Fragmenty nazw',{exact:false}).fill('elastic');
    await page.getByLabel('Grupa kontenerów',{exact:true}).fill('Elastic Stack');
    await page.getByLabel('Wymagany tag w Compose',{exact:true}).fill('9.3.1');
    await page.getByText('Podgląd dopasowania: 4 kontenerów',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Dodaj regułę',exact:true}).click();
    await page.waitForFunction(()=>Alpine.$data(document.body).inventoryRules.length===1);
    await page.screenshot({path:'.tmp-fleet/rules-desktop.png',fullPage:true});
    // HTML from an outdated backend must produce a readable error, not SyntaxError.
    await page.route('**/api/inventory-rules', route => route.fulfill({contentType:'text/html',body:'<!doctype html><html>old central</html>'}));
    await page.evaluate(()=>Alpine.$data(document.body).loadInventoryRules());
    assert.match(await page.evaluate(()=>Alpine.$data(document.body).inventoryError),/odpowiedź API/);
    await page.evaluate(()=>Alpine.$data(document.body).showFleet());
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:'.tmp-fleet/dashboard-mobile.png',fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth));
    await page.evaluate(()=>Alpine.$data(document.body).showCommands());
    await page.screenshot({path:'.tmp-fleet/commands-mobile.png',fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth));
    assert.deepEqual(errors,[]);
    console.log('Fleet browser OK: filters, checkbox selection, parallel commands, independent failures, escaped output, rules, API errors, mobile layout.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
