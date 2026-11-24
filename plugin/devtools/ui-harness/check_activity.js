'use strict';
/**
 * Checks that a teacher who looks at a student's lab sees the student's consoles (consoleactivity.js, manager
 * CONTRACTS §6.3), in Chromium:
 *
 *   the observer page opens the activity of the fake gateway → `open` puts the student's badge on the node, `input`
 *   makes it blink and marks the console of the last input, the blink stops a moment after the input, `close` takes the
 *   badge away; a click on the badge opens the console; a page loaded later gets the state from the snapshot; the
 *   student's own page asks for no activity ticket.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_activity.js [--min]
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

const request = (method, url, body) => new Promise((resolve, reject) => {
    const req = http.request(url, {method, headers: {'Content-Type': 'application/json'}}, res => {
        const chunks = [];
        res.on('data', c => chunks.push(c));
        res.on('end', () => resolve({status: res.statusCode, body: Buffer.concat(chunks).toString()}));
    });
    req.on('error', reject);
    req.end(body === undefined ? undefined : JSON.stringify(body));
});
const stats = async() => JSON.parse((await request('GET', `${BASE}/gw/stats`)).body);
const send = frame => request('POST', `${BASE}/gw/activity`, frame);

const demo = JSON.parse(fs.readFileSync(path.join(__dirname, '../../plugin/demo/ospf-multi-area.gns3'), 'utf8')).topology;
const id = name => demo.nodes.find(n => n.name === name).node_id;
const SW1 = id('SW1'), R1 = id('R1');

const node = (page, nodeId) => page.locator(`.node[data-node="${nodeId}"]`);
const classes = (page, nodeId) => node(page, nodeId).evaluate(e => ['stu-con', 'stu-typing', 'stu-last']
    .filter(c => e.classList.contains(c)).join(' '));
const badgeShown = (page, nodeId) => node(page, nodeId).locator('.sbadge').evaluate(e => getComputedStyle(e).display !== 'none');

async function observer(browser) {
    const context = await browser.newContext({viewport: {width: 1440, height: 860}});
    const page = await context.newPage();
    const problems = [];
    page.on('pageerror', e => problems.push('pageerror: ' + e.message));
    page.on('console', m => {
        if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) {
            problems.push('console: ' + m.text());
        }
    });
    await page.goto(`${BASE}/index.en.html?fast&observer${MIN ? '&min' : ''}`);
    await node(page, SW1).waitFor({timeout: 15000});
    check('observer: the page follows the activity of the gateway', !!(await until(async() => (await stats()).watchers === 1)));
    check('observer: no student badge while nothing is open', (await classes(page, SW1)) === '' && !(await badgeShown(page, SW1)));

    await send({type: 'open', node: SW1});
    check('open: the student badge appears on SW1', !!(await until(async() => (await classes(page, SW1)) === 'stu-con'))
        && await badgeShown(page, SW1));
    await send({type: 'input', node: SW1});
    check('input: SW1 blinks and is the console of the last input',
        !!(await until(async() => (await classes(page, SW1)) === 'stu-con stu-typing stu-last')));
    const blink = await node(page, SW1).locator('.sbadge rect').evaluate(e => getComputedStyle(e).animationName);
    check('input: ...the badge is animated', blink !== 'none', blink);
    check('input: the blink stops a moment after the input, the mark of the last input stays',
        !!(await until(async() => (await classes(page, SW1)) === 'stu-con stu-last', 3000)));

    await send({type: 'open', node: R1});
    await send({type: 'input', node: R1});
    check('another console: R1 blinks and takes the mark of the last input from SW1',
        !!(await until(async() => (await classes(page, R1)) === 'stu-con stu-typing stu-last'
            && (await classes(page, SW1)) === 'stu-con')));

    const box = await node(page, R1).locator('.sbadge rect').boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
    check('a click on the student badge opens the console of R1',
        !!(await until(async() => (await page.locator(`[data-con="${R1}"]`).count()) > 0, 5000)));

    await send({type: 'close', node: SW1});
    check('close: the student badge leaves SW1', !!(await until(async() => (await classes(page, SW1)) === ''))
        && !(await badgeShown(page, SW1)));
    check('observer: no errors in the page', problems.length === 0, problems.join(' | '));
    await context.close();
    check('leaving the page closes the activity', !!(await until(async() => (await stats()).watchers === 0)));

    // A page opened later learns the state from the snapshot: R1 is open and got the last input.
    const later = await browser.newContext({viewport: {width: 1440, height: 860}});
    const page2 = await later.newPage();
    await page2.goto(`${BASE}/index.en.html?fast&observer${MIN ? '&min' : ''}`);
    await node(page2, R1).waitFor({timeout: 15000});
    check('snapshot: a new observer page sees R1 open with the last input at once',
        !!(await until(async() => (await classes(page2, R1)) === 'stu-con stu-last')) && (await classes(page2, SW1)) === '');
    await later.close();
    await send({type: 'close', node: R1});
}

async function student(browser) {
    const context = await browser.newContext({viewport: {width: 1440, height: 860}});
    const page = await context.newPage();
    await page.goto(`${BASE}/index.en.html?fast${MIN ? '&min' : ''}`);
    await page.locator('[data-region="start"]').click();
    await page.locator('[data-region="status"]', {hasText: 'The lab is running'}).waitFor({timeout: 15000});
    await sleep(800);
    const asked = await page.evaluate(() => window.__stub.calls.mod_gns3lab_get_activity_ticket || 0);
    check('student: the own page asks for no activity ticket and opens no activity', asked === 0 && (await stats()).watchers === 0,
        `tickets ${asked}`);
    await context.close();
}

(async() => {
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch();
    try {
        const ready = await until(async() => (await request('GET', `${BASE}/gw/stats`)).status === 200, 10000);
        if (!ready) {
            throw new Error('server did not start');
        }
        await observer(browser);
        await student(browser);
    } catch (e) {
        check('the run went to the end', false, String(e.stack || e));
    } finally {
        await browser.close();
        server.kill();
    }
    console.log(failed ? `\n${failed} failed` : '\nall checks passed');
    process.exit(failed);
})();
