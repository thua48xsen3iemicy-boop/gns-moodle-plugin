'use strict';
/**
 * Checks that a gateway address the browser refuses does not leave a console "connecting" for good (console.js), in
 * Chromium:
 *
 *   the ticket names an address new WebSocket() throws on (?badgateway; in production a ws:// gateway on an https
 *   page) → the terminal says the console failed, the tab is not stuck connecting, and the page has no uncaught error.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_badgateway.js [--min]
 *
 * Playwright with Chromium must be installed (require('playwright')). The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18137;
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

async function scenario(browser) {
    const context = await browser.newContext({viewport: {width: 1440, height: 860}});
    const page = await context.newPage();
    const problems = [];
    page.on('pageerror', e => problems.push('pageerror: ' + e.message));
    page.on('console', m => {
        if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) {
            problems.push('console: ' + m.text());
        }
    });
    let sockets = 0;
    page.on('websocket', () => {
        sockets++;
    });
    await page.goto(`${BASE}/index.en.html?fast&badgateway${MIN ? '&min' : ''}`);
    await page.locator('[data-region="start"]').click();
    await page.locator('[data-region="status"]', {hasText: 'The lab is running'}).waitFor({timeout: 15000});
    await page.locator(`.node[data-node="${SW1}"].st-started`).waitFor();
    await sleep(300);

    await openConsole(page, SW1);
    const text = await until(async() => {
        const t = await screenText(page);
        return /The console is unavailable/i.test(t) ? t : null;
    });
    check('the terminal says the console is unavailable', !!text, text ? '' : JSON.stringify(await screenText(page)));
    // Three seconds is more than the first reconnection pause: a stuck console would still say nothing new.
    await sleep(3000);
    const after = await screenText(page);
    check('...and does not try again by itself', !/reconnecting in/i.test(after), JSON.stringify(after.slice(-200)));
    check('no socket was opened', sockets === 0, String(sockets));
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
