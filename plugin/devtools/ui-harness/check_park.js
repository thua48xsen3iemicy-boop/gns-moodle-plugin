'use strict';
/**
 * Checks that closing a terminal keeps its connection (consoles.js, PARK_MAX), in Chromium:
 *
 *   a command in the console of SW1 → the console is closed → opened again: the same screen, no new ticket,
 *   the gateway kept the socket; stopping the lab drops the kept terminal for good.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_park.js [--min]
 *
 * Playwright with Chromium must be installed (require('playwright')). The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18131;
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
const SW1 = demo.nodes.find(n => n.name === 'SW1').node_id;

const node = (page, id) => page.locator(`.node[data-node="${id}"]`);
const tickets = page => page.evaluate(() => window.__stub.calls.mod_gns3lab_get_console_ticket || 0);
const screenText = page => page.evaluate(() => Array.from(document.querySelectorAll('.xterm-rows')).map(e => e.innerText).join('\n'));

async function openConsole(page, id) {
    let box = await node(page, id).locator('circle.halo').boundingBox();
    const area = await page.locator('[data-region="wrap"]').boundingBox();
    if (box.x + box.width / 2 < area.x || box.x + box.width / 2 > area.x + area.width
        || box.y + box.height / 2 < area.y || box.y + box.height / 2 > area.y + area.height) {
        await page.locator('[data-region="zoomFit"]').click();
        await sleep(400);
        box = await node(page, id).locator('circle.halo').boundingBox();
    }
    await page.mouse.dblclick(box.x + box.width / 2, box.y + box.height / 2);
}

async function scenario(browser, mode) {
    const context = await browser.newContext({viewport: {width: 1440, height: 860}});
    await context.addInitScript(m => localStorage.setItem('gns3lab.mode', JSON.stringify(m)), mode);
    const page = await context.newPage();
    const problems = [];
    page.on('pageerror', e => problems.push('pageerror: ' + e.message));
    page.on('console', m => {
        if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) {
            problems.push('console: ' + m.text());
        }
    });
    // The sockets the page opens to the gateway, and which of them are closed.
    const sockets = [];
    page.on('websocket', ws => {
        const s = {closed: false};
        sockets.push(s);
        ws.on('close', () => { s.closed = true; });
    });
    await page.goto(`${BASE}/index.en.html?fast${MIN ? '&min' : ''}`);
    await page.locator('[data-region="start"]').click();
    await page.locator('[data-region="status"]', {hasText: 'The lab is running'}).waitFor({timeout: 15000});
    await page.locator(`.node[data-node="${SW1}"].st-started`).waitFor();
    await sleep(300);

    await openConsole(page, SW1);
    check(`${mode}: the console of SW1 shows a prompt`, !!(await until(async() => /SW1#$/.test((await screenText(page)).trimEnd()))));
    await sleep(400);
    await page.keyboard.type('show parked-marker');
    await page.keyboard.press('Enter');
    await until(async() => /parked-marker[\s\S]*SW1#$/.test((await screenText(page)).trimEnd()));
    // Half-typed input stays on the line too.
    await page.keyboard.type('conf');
    await sleep(200);
    const before = await tickets(page);

    await page.locator(`[data-con="${SW1}"] [data-x]`).first().click();
    const gone = await until(async() => (await page.locator('.xterm-rows').count()) === 0, 3000);
    check(`${mode}: closing hides the terminal`, !!gone);
    check(`${mode}: ...and drops the console badge on the scheme`,
        !(await node(page, SW1).evaluate(e => e.classList.contains('has-con'))));
    await sleep(500);
    check(`${mode}: ...but the socket to the gateway stays open`, sockets.length === 1 && !sockets[0].closed,
        JSON.stringify(sockets));

    await openConsole(page, SW1);
    const text = await until(async() => {
        const t = (await screenText(page)).trimEnd();
        return /parked-marker/.test(t) && /SW1#conf$/.test(t) ? t : null;
    }, 3000);
    check(`${mode}: opened again, the terminal shows the screen as it was`, !!text);
    check(`${mode}: ...with no new ticket and no new socket`, (await tickets(page)) === before && sockets.length === 1,
        `tickets ${before} → ${await tickets(page)}, sockets ${sockets.length}`);
    await page.keyboard.type('ig t');
    await page.keyboard.press('Enter');
    check(`${mode}: ...and keeps working`, !!(await until(async() => /SW1#config t[\s\S]*SW1#$/.test((await screenText(page)).trimEnd()), 3000)));

    // Stopping the lab closes the kept terminal for good.
    await page.locator(`[data-con="${SW1}"] [data-x]`).first().click();
    await sleep(300);
    await page.locator('[data-region="stop"]').click();
    const closed = await until(async() => sockets.every(s => s.closed), 8000);
    check(`${mode}: stopping the lab closes the kept connection`, !!closed);

    check(`${mode}: no errors in the page`, problems.length === 0, problems.join(' | '));
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
        await scenario(browser, 'panel');
        await scenario(browser, 'windows');
    } catch (e) {
        check('the run went to the end', false, String(e.stack || e));
    } finally {
        await browser.close();
        server.kill();
    }
    console.log(failed ? `\n${failed} failed` : '\nall checks passed');
    process.exit(failed);
})();
