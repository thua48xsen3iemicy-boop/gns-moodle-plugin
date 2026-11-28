'use strict';
/**
 * Checks a lab worked over SSH in Chromium (manager CONTRACTS §6.4, lab_definition::NOCONSOLE):
 *
 *   - a node whose console is closed to the student (R1) opens no console: the page says why and asks for no ticket;
 *   - a workstation with additional console lines (R2, two lines) gets a "New terminal" button: each press opens the
 *     next line as its own terminal ("R2 · 2", "R2 · 3"), the fake gateway sees `&line=1` and `&line=2`, and a third
 *     press says that all terminals are open;
 *   - input goes to the terminal it is typed in; closing one terminal keeps the mark of the node on the scheme while
 *     another is open, a closed line comes back with "New terminal";
 *   - a node without lines has no such button; the action wheel of the workstation offers "New terminal".
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_lines.js [--min]
 *
 * --min runs the minified modules of plugin/amd/build instead of amd/src.
 *
 * Playwright with Chromium must be installed (require('playwright')). The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18133;
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

/**
 * Polls until the function returns something truthy.
 *
 * @param {Function} fn async function
 * @param {number} ms how long to wait
 * @returns {Promise<*>} the value, or null on timeout
 */
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

const getJson = url => new Promise((resolve, reject) => {
    http.get(url, res => {
        let body = '';
        res.on('data', chunk => {
            body += chunk;
        });
        res.on('end', () => {
            try {
                resolve({status: res.statusCode, body: JSON.parse(body)});
            } catch (e) {
                resolve({status: res.statusCode, body: null});
            }
        });
    }).on('error', reject);
});

const demo = JSON.parse(fs.readFileSync(path.join(__dirname, '../../plugin/demo/ospf-multi-area.gns3'), 'utf8')).topology;
const id = name => demo.nodes.find(n => n.name === name).node_id;
const R2 = id('R2'), R1 = id('R1'), SW1 = id('SW1');

/**
 * Brings a node into view and returns the centre of its halo.
 *
 * @param {Page} page page
 * @param {string} nodeId node id
 * @returns {Promise<{x: number, y: number}>}
 */
async function nodeCentre(page, nodeId) {
    const halo = page.locator(`.node[data-node="${nodeId}"] circle.halo`);
    let box = await halo.boundingBox();
    const area = await page.locator('[data-region="wrap"]').boundingBox();
    if (box.x + box.width / 2 < area.x || box.x + box.width / 2 > area.x + area.width
        || box.y + box.height / 2 < area.y || box.y + box.height / 2 > area.y + area.height) {
        await page.locator('[data-region="zoomFit"]').click();
        await sleep(400);
        box = await halo.boundingBox();
    }
    return {x: box.x + box.width / 2, y: box.y + box.height / 2};
}

const openConsole = async(page, nodeId) => {
    const p = await nodeCentre(page, nodeId);
    await page.mouse.dblclick(p.x, p.y);
};

const screenText = page => page.evaluate(() => Array.from(document.querySelectorAll('.termhost'))
    .filter(h => h.isConnected && h.offsetParent).map(h => h.querySelector('.xterm-rows')?.innerText || '').join('\n'));
const shows = async(page, text, ms = 4000) => !!(await until(async() => (await screenText(page)).includes(text), ms));
const toast = page => page.evaluate(() => {
    const t = document.querySelector('[data-region="toast"]');
    return t && !t.hidden ? t.textContent : '';
});
const tabs = page => page.evaluate(() => Array.from(document.querySelectorAll('[data-region="tabs"] .tab'))
    .map(t => t.textContent.trim()));
const visiblePlus = page => page.evaluate(() => Array.from(document.querySelectorAll('.termhost'))
    .filter(h => h.offsetParent).some(h => {
        const b = h.querySelector('.newterm');
        return b && !b.hidden && b.offsetParent !== null;
    }));

(async() => {
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch();
    try {
        const ready = await until(async() => (await getJson(`${BASE}/gw/stats`)).status === 200, 10000);
        check('the fake gateway is up', !!ready);
        if (!ready) {
            throw new Error('server did not start');
        }
        const problems = [];
        const context = await browser.newContext({viewport: {width: 1440, height: 860}});
        // One console on the screen at a time: the panel mode (windows are the default).
        await context.addInitScript(() => localStorage.setItem('gns3lab.mode', JSON.stringify('panel')));
        const page = await context.newPage();
        page.on('pageerror', e => problems.push(String(e)));
        page.on('console', m => {
            if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) {
                problems.push(m.text());
            }
        });
        await page.goto(`${BASE}/index.en.ssh.html?fast&lines=R2:2&closed=R1${MIN ? '&min' : ''}`);
        await page.locator('[data-region="start"]').click();
        await page.locator('[data-region="status"]', {hasText: 'The lab is running'}).waitFor({timeout: 15000});
        await page.locator(`.node[data-node="${SW1}"].st-started`).waitFor();
        await page.locator(`.node[data-node="${R2}"].st-started`).waitFor({state: "attached"});
        await sleep(500);

        // The closed console: nothing opens, the student is told, no ticket is asked for.
        const tickets = () => page.evaluate(() => window.__stub.tickets || 0);
        const before = await tickets();
        await openConsole(page, R1);
        await sleep(600);
        check('a closed console does not open', (await tabs(page)).length === 0, JSON.stringify(await tabs(page)));
        check('...and the student is told to use the network', /closed in this lab/.test(await toast(page)), await toast(page));
        check('...and no ticket is asked for', (await tickets()) === before);

        // The workstation: its console and two more terminals.
        await openConsole(page, R2);
        check('the console of the workstation opens', await shows(page, 'R2 console'));
        check('it has a New terminal button', await visiblePlus(page));
        await page.locator('.termhost .newterm:visible').click();
        check('New terminal opens line 1 as its own tab', !!(await until(async() =>
            JSON.stringify(await tabs(page)) === JSON.stringify(['R2', 'R2 · 2']))), JSON.stringify(await tabs(page)));
        check('...connected to line 1 of the node', await shows(page, 'R2 line 1'));
        await page.locator('.xterm-helper-textarea:visible').focus();
        await page.keyboard.type('echo first-line');
        check('typing goes to the terminal of the line', await shows(page, 'echo first-line'));
        await page.locator('.termhost .newterm:visible').click();
        check('a second press opens line 2', !!(await until(async() =>
            (await tabs(page)).join('|') === 'R2|R2 · 2|R2 · 3')), JSON.stringify(await tabs(page)));
        check('...connected to line 2', await shows(page, 'R2 line 2'));
        check('...a fresh login: nothing typed elsewhere shows', !(await screenText(page)).includes('first-line'));
        await page.locator('.termhost .newterm:visible').click();
        check('a third press: all terminals are open', /All terminals of R2 are open/.test(await toast(page)), await toast(page));
        const stats = (await getJson(`${BASE}/gw/stats`)).body;
        const opened = stats.lines.filter(l => l.node === R2).map(l => l.line).sort();
        check('the gateway got the console and lines 1 and 2', JSON.stringify(opened) === '[0,1,2]', JSON.stringify(opened));

        // Closing one terminal keeps the mark of the node; the line comes back with New terminal.
        await page.locator('[data-region="tabs"] .tab', {hasText: 'R2 · 2'}).locator('[data-x]').click();
        check('a closed line leaves the others', (await tabs(page)).join('|') === 'R2|R2 · 3', JSON.stringify(await tabs(page)));
        check('the node keeps its console mark', await page.locator(`.node[data-node="${R2}"].has-con`).count() === 1);
        await page.locator('.termhost .newterm:visible').click();
        check('New terminal brings line 1 back', !!(await until(async() => (await tabs(page)).includes('R2 · 2'))));
        check('...as it was (still connected)', await shows(page, 'first-line'));

        // A node without lines: no button. The wheel of the workstation offers New terminal.
        await openConsole(page, SW1);
        check('the console of SW1 opens', await shows(page, 'SW1'));
        check('a node without lines has no New terminal button', !(await visiblePlus(page)));
        // The wheel (the keyboard way: the key ContextMenu on the focused node).
        const wheelLabels = async nodeId => {
            await page.keyboard.press('Escape');
            await page.locator(`.node[data-node="${nodeId}"]`).focus();
            await page.keyboard.press('ContextMenu');
            const wedges = page.locator('[data-region="radial"] .wedge');
            await wedges.first().waitFor({timeout: 3000});
            return wedges.evaluateAll(gs => gs.map(g => (g.classList.contains('off') ? '-' : '') + g.textContent.trim()));
        };
        const labels = await wheelLabels(R2);
        check('the wheel of the workstation offers New terminal', labels.includes('New terminal'), JSON.stringify(labels));
        const r1labels = await wheelLabels(R1);
        check('the wheel of the closed node has its console off and no New terminal',
            r1labels.includes('-Console') && !r1labels.some(l => /New terminal/.test(l)), JSON.stringify(r1labels));
        await page.keyboard.press('Escape');

        check('no errors in the page', problems.length === 0, problems.join(' | '));
        await context.close();
    } catch (e) {
        check('the run went to the end', false, String(e.stack || e));
    } finally {
        await browser.close();
        server.kill();
    }
    console.log(failed ? `\n${failed} failed` : '\nall checks passed');
    process.exit(failed);
})();
