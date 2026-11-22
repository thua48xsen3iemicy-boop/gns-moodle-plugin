'use strict';
/**
 * Checks the static and default routes on L3 (canvasview.js renderRoutes, markReach; facts.js deriveRoutes, reach), in
 * Chromium, on the demo lab with routes added:
 *
 *   PC1 and PC2 have the default route to R2 (their `gateway`); R2 a default route to R1 and a static route to
 *   10.2.20.0/24 via R3; R3 a static route back to 10.1.10.0/24 via R2, and a default route to a next hop nobody has
 *   in its subnet 10.0.13.0/30. R1 has no route back: PC1 reaches 10.2.20.0/24 there and back, 192.168.122.0/24 only one way.
 *   The arrows go from their node to the next hop, the broken one ends in "?", the findings name it, a tap on an arrow
 *   selects its node and paints the subnet pills by what it reaches; the card of the node lists its routes and reach.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_routes.js [--min]
 *
 * Playwright with Chromium must be installed (require('playwright')). The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18137;
const BASE = `http://127.0.0.1:${PORT}`;
const MIN = process.argv.includes('--min');
const PRE = '6b1f3c2e-4a7d-4e5b-9c10-0000000000';

let failed = 0;
const check = (name, ok, detail = '') => {
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  ' + detail : ''}`);
    if (!ok) {
        failed++;
    }
};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, ms = 8000) {
    const end = Date.now() + ms;
    for (;;) {
        const value = await fn().catch(() => null);
        if (value) {
            return value;
        }
        if (Date.now() > end) {
            return null;
        }
        await sleep(50);
    }
}
const get = url => new Promise((resolve, reject) => {
    http.get(url, res => {
        res.resume();
        resolve(res.statusCode);
    }).on('error', reject);
});

async function scenario(browser, theme) {
    const context = await browser.newContext({viewport: {width: 1300, height: 760}, colorScheme: theme});
    const page = await context.newPage();
    const problems = [];
    page.on('pageerror', e => problems.push('pageerror: ' + e.message));
    page.on('console', m => {
        if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) {
            problems.push('console: ' + m.text());
        }
    });
    await page.goto(`${BASE}/index.ru.html?fast${MIN ? '&min' : ''}`);
    await page.evaluate(pre => {
        const f = window.__stub.facts;
        f.patchNode(pre + '03', {routes: [{dst: 'default', via: '10.0.12.1', proto: 'static'},
            {dst: '10.2.20.0/24', via: '10.0.23.2', proto: 'static'}]});
        f.patchNode(pre + '04', {routes: [{dst: '10.1.10.0/24', via: '10.0.23.1', proto: 'static'},
            {dst: 'default', via: '10.0.13.3', proto: 'static'}]});
        f.patchNode(pre + '02', {routes: []});
    }, PRE);
    await page.locator('[data-region="start"]').click();
    await page.locator('[data-region="status"]', {hasText: 'Лаба работает'}).waitFor({timeout: 15000});
    await sleep(1500);
    await page.locator('[data-layer="l3"]').click();
    await page.evaluate(() => new Promise(resolve => require(['mod_gns3lab/stage'], ({Stage}) => {
        const original = Stage.prototype.applyView;
        Stage.prototype.applyView = function(...args) {
            window.canvasStage = this;
            return original.apply(this, args);
        };
        resolve();
    })));
    await page.locator('[data-region="zoomFit"]').click();
    await page.waitForFunction(() => window.canvasStage);
    await sleep(700);

    const arrows = await page.locator('.route-arrow').evaluateAll(gs => gs.map(g => ({
        from: g.dataset.routeNode.slice(-2), to: (g.dataset.routeTo || '').slice(-2), cls: g.getAttribute('class'),
        label: g.querySelector('.route-label').textContent, end: g.querySelector('.route-end text')?.textContent || null})));
    const find = (from, to) => arrows.find(a => a.from === from && a.to === to);
    check('PC1 and PC2 have their default route to R2', ['07', '08'].every(id => find(id, '03') && / default/.test(find(id, '03').cls)
        && find(id, '03').label === '0/0'), JSON.stringify(arrows));
    check('R2 → R1 default and R2 → R3 static', find('03', '02')?.label === '0/0' && find('03', '04')?.label === '10.2.20.0/24');
    check('R3 → R2 static back', find('04', '03')?.label === '10.1.10.0/24' && / ok/.test(find('04', '03').cls));
    const stub = arrows.find(a => a.from === '04' && !a.to);
    // Some demo nodes have no addresses in their facts: a missing next hop is a doubt (warn), not an error.
    check('R3: the default route to a missing next hop is a stub with "?"', stub && stub.end === '?' && / warn/.test(stub.cls), JSON.stringify(stub));
    check('the arrows are visible strokes', await page.locator('.route-arrow .stroke').first().evaluate(p => {
        const st = getComputedStyle(p);
        return st.stroke !== 'none' && parseFloat(st.strokeWidth) >= 1 && p.getTotalLength() > 20;
    }));

    await page.locator('[data-region="issueCount"]').waitFor();
    const issues = await page.evaluate(() => document.querySelector('[data-region="issueCount"]').textContent);
    check('the findings count the route problem', Number(issues) >= 1, issues);

    // A tap on PC1's arrow selects PC1 and paints the subnets by its reach.
    const at = await page.locator(`.route-arrow[data-route-node="${PRE}07"] .stroke`).evaluate(p => {
        const q = p.getPointAtLength(p.getTotalLength() * .3).matrixTransform(p.getScreenCTM());
        return {x: q.x, y: q.y};
    });
    await page.mouse.click(at.x, at.y);
    await sleep(400);
    const sel = await page.locator(`.node[data-node="${PRE}07"]`).getAttribute('class');
    check('a tap on a route arrow selects its node', /\bsel\b/.test(sel), sel);
    const reach = await page.locator('.subnet-object').evaluateAll(gs => Object.fromEntries(gs.map(g => [g.dataset.subnet,
        [...g.classList].find(c => c.startsWith('reach-')) || ''])));
    check('PC1 reach: its own subnet is connected', reach['10.1.10.0/24'] === 'reach-direct', JSON.stringify(reach));
    check('PC1 reach: 10.2.20.0/24 there and back', reach['10.2.20.0/24'] === 'reach-ok');
    check('PC1 reach: 192.168.122.0/24 only one way (R1 has no route back)', reach['192.168.122.0/24'] === 'reach-oneway');
    const title = await page.locator('.subnet-object[data-subnet="192.168.122.0/24"] title.reach').textContent();
    check('the pill tells where the way back ends', /у R1 нет маршрута до 10\.1\.10\.11/.test(title), title);
    const opacity = await page.locator('.subnet-object[data-subnet="10.2.20.0/24"]').evaluate(g => +getComputedStyle(g).opacity);
    check('a reached subnet is not dimmed by the selection', opacity > .8, String(opacity));

    // The card of R3 on L3: its routes and its reach.
    await page.evaluate(id => {
        const s = window.canvasStage;
        s.select({kind: 'node', id});
        s.call('details', s.sel);
    }, PRE + '04');
    await page.locator('[data-card-layer="l3"]').click();
    await sleep(300);
    const card = await page.locator('.object-card').innerText();
    check('the card of R3 lists its routes', /Маршруты/.test(card) && /10\.1\.10\.0\/24/.test(card) && /10\.0\.13\.3/.test(card), card.slice(0, 300));
    check('the card of R3 shows its reach', /Достижимость/.test(card));
    await page.screenshot({path: path.join(__dirname, 'dist', `routes-${theme}.png`)});

    check(`no errors in the page (${theme})`, problems.length === 0, problems.join(' | '));
    await context.close();
}

(async() => {
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch();
    try {
        const ready = await until(async() => (await get(`${BASE}/gw/stats`)) === 200, 10000);
        if (!ready) {
            throw new Error('server did not start');
        }
        await scenario(browser, 'light');
        await scenario(browser, 'dark');
    } catch (e) {
        check('the run went to the end', false, String(e.stack || e));
    } finally {
        await browser.close();
        server.kill();
    }
    console.log(failed ? `\n${failed} failed` : '\nall checks passed');
    process.exit(failed);
})();
