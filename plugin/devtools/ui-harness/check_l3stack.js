'use strict';
/**
 * Checks the subnet pills of L3 on a messy configuration (canvasview.js positionSubnets), in Chromium:
 *
 *   R1 Gi0/1 and R2 Gi0/0 carry three subnets on one cable, R2 Gi0/1 and R3 Gi0/1 two, and R3 Gi0/1 one more of its
 *   own with a wrong mask. The pills of one cable go as one stack across it, in the order of their addresses, near the
 *   middle of the cable and clear of each other; a pill of another interface of the same node stays out of the stack;
 *   no two pills overlap at any zoom; the cables under L3 are drawn visibly.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_l3stack.js [--min]
 *
 * Playwright with Chromium must be installed (require('playwright')). The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18135;
const BASE = `http://127.0.0.1:${PORT}`;
const MIN = process.argv.includes('--min');

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

const demo = JSON.parse(fs.readFileSync(path.join(__dirname, '../../plugin/demo/ospf-multi-area.gns3'), 'utf8')).topology;
const PRE = '6b1f3c2e-4a7d-4e5b-9c10-0000000000';
const R12 = ['10.0.12.0/30', '10.0.12.8/29', '172.16.1.0/24'];
const R23 = ['10.0.23.0/30', '10.0.23.4/30', '192.168.5.0/24'];

const pills = page => page.locator('.subnet-object').evaluateAll(gs => gs.filter(g => g.style.display !== 'none').map(g => {
    const r = g.querySelector('rect').getBoundingClientRect();
    return {key: g.dataset.subnet, x: r.x, y: r.y, w: r.width, h: r.height, cx: r.x + r.width / 2, cy: r.y + r.height / 2};
}));
const centre = (page, name) => page.locator(`.node[data-node="${PRE}${name}"] circle.halo`).boundingBox()
    .then(b => ({x: b.x + b.width / 2, y: b.y + b.height / 2}));
const overlap = (a, b) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;

async function stack(page, list, a, b, name) {
    const all = await pills(page), mine = list.map(k => all.find(p => p.key === k));
    check(`${name}: every pill of the cable is shown`, mine.every(Boolean), JSON.stringify(all.map(p => p.key)));
    if (!mine.every(Boolean)) {
        return;
    }
    const A = await centre(page, a), B = await centre(page, b);
    const dx = B.x - A.x, dy = B.y - A.y, len = Math.hypot(dx, dy);
    // Across the cable: the offsets of the pills from the cable line grow in the order of the addresses.
    const across = mine.map(p => ((p.cx - A.x) * -dy + (p.cy - A.y) * dx) / len);
    const along = mine.map(p => ((p.cx - A.x) * dx + (p.cy - A.y) * dy) / len / len);
    const sign = Math.sign(across[across.length - 1] - across[0]) || 1;
    check(`${name}: the pills are one stack in the order of their addresses`,
        across.every((v, i) => !i || (v - across[i - 1]) * sign > 0), JSON.stringify(across.map(Math.round)));
    check(`${name}: ...side by side along the cable`, Math.max(...along) - Math.min(...along) < .02, JSON.stringify(along));
    check(`${name}: ...beside the middle of the cable`, along.every(t => t > .2 && t < .8), JSON.stringify(along));
    const mid = (across[0] + across[across.length - 1]) / 2;
    check(`${name}: ...centred on the cable`, Math.abs(mid) < 40, String(Math.round(mid)));
}

async function scenario(browser) {
    const context = await browser.newContext({viewport: {width: 1300, height: 760}});
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
        f.patch(pre + '02', 'Gi0/1', {ipv4: ['10.0.12.1/30', '10.0.12.9/29', '172.16.1.1/24']});
        f.patch(pre + '03', 'Gi0/0', {ipv4: ['10.0.12.2/30', '172.16.1.2/24', '10.0.12.10/29']});
        f.patch(pre + '03', 'Gi0/1', {ipv4: ['10.0.23.1/30', '192.168.5.1/24']});
        f.patch(pre + '04', 'Gi0/1', {ipv4: ['10.0.23.2/30', '192.168.5.2/24', '10.0.23.6/30']});
    }, PRE);
    await page.locator('[data-region="start"]').click();
    await page.locator('[data-region="status"]', {hasText: 'Лаба работает'}).waitFor({timeout: 15000});
    await sleep(1500);
    await page.locator('[data-layer="l3"]').click();
    await page.locator('[data-region="zoomFit"]').click();
    await sleep(700);

    await stack(page, R12, '02', '03', 'R1–R2');
    await stack(page, R23, '03', '04', 'R2–R3');
    // 10.2.30.0/24 sits on another interface of R3: it is not pulled into the stack of R2–R3.
    const all = await pills(page), own = all.find(p => p.key === '10.2.30.0/24'), r3 = await centre(page, '04');
    const sw2 = await centre(page, '06');
    check('a subnet of another interface stays by its own cable', own && Math.hypot(own.cx - (r3.x + sw2.x) / 2, own.cy - (r3.y + sw2.y) / 2) < 120,
        JSON.stringify(own));

    for (const [i, zoom] of ['fit', 'out', 'in'].entries()) {
        if (zoom === 'out') {
            await page.locator('[data-region="zoomOut"]').click();
        } else if (zoom === 'in') {
            await page.locator('[data-region="zoomFit"]').click();
            await sleep(300);
            await page.locator('[data-region="zoomIn"]').click();
        }
        await sleep(700);
        const list = await pills(page);
        const bad = [];
        list.forEach((a, j) => list.slice(j + 1).forEach(b => { if (overlap(a, b)) bad.push(a.key + ' × ' + b.key); }));
        check(`no two pills overlap (${zoom})`, !bad.length && list.length > 0, bad.join(', ') || `${list.length} pills, step ${i}`);
    }

    const wire = await page.locator('.ghost-wire').first().evaluate(l => {
        const st = getComputedStyle(l);
        return {opacity: +st.strokeOpacity, width: parseFloat(st.strokeWidth)};
    });
    check('the cables under L3 are drawn visibly', wire.opacity >= .5 && wire.width >= 2.5, JSON.stringify(wire));

    check('no errors in the page', problems.length === 0, problems.join(' | '));
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
        await scenario(browser);
    } catch (e) {
        check('the run went to the end', false, String(e.stack || e));
    } finally {
        await browser.close();
        server.kill();
    }
    console.log(failed ? `\n${failed} failed` : '\nall checks passed');
    process.exit(failed);
})();
