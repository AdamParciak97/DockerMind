const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

let browser;
(async () => {
  browser = await chromium.launch({channel: 'msedge', headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const agent = new WebSocket('ws://localhost:18081/ws/agent?agent_token=inventory-preview-agent-token');
  await new Promise((resolve, reject) => { agent.onopen = resolve; agent.onerror = reject; });
  agent.send(JSON.stringify({type: 'register', agent_name: 'inventory-demo', hostname: 'demo-host'}));
  await new Promise(resolve => { agent.onmessage = resolve; });
  const report = tag => agent.send(JSON.stringify({type: 'data', timestamp: Date.now()/1000, containers: [{
    name: 'elastic-demo', image: 'elasticsearch:9.2.6', status: 'running', cpu_percent: 2,
    memory: {percent: 3, usage_bytes: 1024, limit_bytes: 4096},
    labels: {'com.docker.compose.service': 'elastic'},
    compose: `services:\n  elastic:\n    image: elasticsearch:${tag}\n`,
  }]}));
  let keepAlive;
  try {
    keepAlive = setInterval(() => agent.send(JSON.stringify({type: 'ping'})), 10000);
    await page.goto('http://localhost:18081');
    await page.locator('[x-model="lf.u"]').fill('admin');
    await page.locator('[x-model="lf.p"]').fill('Inventory-Local-2026!');
    await page.getByRole('button', {name: 'Zaloguj się'}).click();
    await page.waitForFunction(() => Alpine.$data(document.body).loggedIn && Alpine.$data(document.body).wsOk);
    report('9.2.6');
    await page.waitForFunction(() => Alpine.$data(document.body)._srv('inventory-demo')?.containers?.length);
    await page.evaluate(() => Alpine.$data(document.body).selectSrv('inventory-demo'));
    await page.getByLabel('Nazwa wyświetlana', {exact: true}).fill('Elastic Production');
    await page.getByRole('button', {name: 'Zapisz nazwę'}).click();
    await page.waitForFunction(() => Alpine.$data(document.body).selSrv?.display_name === 'Elastic Production');
    await page.getByRole('button', {name: /Ustawienia/}).click();
    await page.getByRole('button', {name: 'Reguły kontenerów', exact: true}).click();
    // Clear rules from an earlier test run in this temporary preview database.
    await page.evaluate(async () => {
      const app = Alpine.$data(document.body);
      for (const rule of await app._get('/api/inventory-rules')) await app._del(`/api/inventory-rules/${rule.id}`);
    });
    await page.getByLabel('Nazwa reguły', {exact: true}).fill('Elastic Version');
    await page.getByLabel('Fragmenty nazw', {exact: false}).fill('elk\nelastic');
    await page.getByLabel('Grupa kontenerów', {exact: true}).fill('Elastic Stack');
    await page.getByLabel('Wymagany tag w Compose', {exact: true}).fill('9.3.1');
    await page.getByRole('button', {name: 'Dodaj regułę', exact: true}).click();
    await page.waitForFunction(() => Alpine.$data(document.body).inventoryRules.some(r => r.expected_tag === '9.3.1'));
    fs.mkdirSync('.tmp-inventory', {recursive: true});
    await page.screenshot({path: '.tmp-inventory/rules-desktop.png', fullPage: true});
    report('9.2.6');
    await page.waitForFunction(() => Alpine.$data(document.body).alertBadge > 0);
    await page.evaluate(() => { const app=Alpine.$data(document.body); app.page='dashboard'; app.selectSrv('inventory-demo'); });
    await page.getByRole('heading', {name: 'Elastic Stack (1)', exact: true}).waitFor();
    await page.screenshot({path: '.tmp-inventory/server-desktop.png', fullPage: true});
    const active = await page.evaluate(() => Alpine.$data(document.body)._get('/api/alert-events?status=active'));
    assert(active.some(e => e.message.includes('9.2.6') && e.message.includes('9.3.1')));
    report('9.3.1');
    await page.waitForFunction(() => Alpine.$data(document.body).alertBadge === 0);
    await page.setViewportSize({width: 390, height: 844});
    await page.getByRole('button', {name: 'Menu', exact: true}).click();
    await page.getByRole('button', {name: /Ustawienia/}).click();
    await page.getByRole('button', {name: 'Reguły kontenerów', exact: true}).click();
    await page.getByLabel('Nazwa reguły', {exact: true}).scrollIntoViewIfNeeded();
    const box = await page.getByLabel('Nazwa reguły', {exact: true}).boundingBox();
    assert(box.x >= 0 && box.x + box.width <= 390);
    await page.screenshot({path: '.tmp-inventory/rules-mobile.png', fullPage: true});
    assert.deepEqual(errors, []);
    console.log('Browser flow OK: rename, rule creation, grouping, mismatch alert, resolution; desktop/mobile screenshots saved.');
  } catch (error) {
    console.error('Browser errors:', errors);
    fs.mkdirSync('.tmp-inventory', {recursive: true});
    await page.screenshot({path: '.tmp-inventory/failure.png', fullPage: true});
    throw error;
  } finally {
    clearInterval(keepAlive);
    agent.close();
    await browser.close();
  }
})().catch(async error => { console.error(error); if (browser) await browser.close(); process.exitCode = 1; });
