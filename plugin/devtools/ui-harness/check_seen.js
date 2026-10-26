'use strict';
/**
 * Checks the layer "seen in the console" (docs/live-state.md §8) in Chromium, end to end:
 *
 *   keys in the terminal → fake gateway of server.js (an IOL switch, a VPCS) → xterm.js → the watch of the page →
 *   the facts laid over the polled ones → the scheme and the card of the node.
 *
 * After a command the scheme must change at once, without the page asking the server for facts. It starts the
 * fake gateway itself and drives the page with Playwright. Screenshots go to --shots (default dist/shots).
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_seen.js [--min] [--shots <dir>]
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

const PORT = 18124;
const BASE = `http://127.0.0.1:${PORT}`;
const MIN = process.argv.includes('--min');
const shotArg = process.argv.indexOf('--shots');
const SHOTS = path.resolve(shotArg > 0 ? process.argv[shotArg + 1] : path.join(__dirname, 'dist/shots'));

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

const get = url => new Promise((resolve, reject) => {
    http.get(url, res => {
        res.resume();
        resolve(res.statusCode);
    }).on('error', reject);
});

const demo = JSON.parse(fs.readFileSync(path.join(__dirname, '../../plugin/demo/ospf-multi-area.gns3'), 'utf8')).topology;
const idOf = name => demo.nodes.find(n => n.name === name).node_id;
const [SW1, PC1, PC2] = ['SW1', 'PC1', 'PC2'].map(idOf);
// The cable between SW1 port 2 and PC2.
const LINK = demo.links.find(l => l.nodes.some(e => e.node_id === SW1 && e.port_number === 2) && l.nodes.some(e => e.node_id === PC2)).link_id;

const node = (page, id) => page.locator(`.node[data-node="${id}"]`);
const calls = page => page.evaluate(() => ({...window.__stub.calls}));

/**
 * Opens the console of a node the way a student does: a double click on it.
 *
 * @param {Page} page page
 * @param {string} id node id
 */
async function openConsole(page, id) {
    const box = await node(page, id).locator('circle.halo').boundingBox();
    await page.mouse.dblclick(box.x + box.width / 2, box.y + box.height / 2);
}

/**
 * Text of the terminal of the console that is open.
 *
 * @param {Page} page page
 * @returns {Promise<string>}
 */
const screenText = page => page.evaluate(() => Array.from(document.querySelectorAll('.xterm-rows')).map(e => e.innerText).join('\n'));

/**
 * Types a command into the terminal that has the focus and waits for the prompt after it.
 *
 * @param {Page} page page
 * @param {string} command command
 * @param {RegExp} prompt what the screen ends with when the command is done
 */
async function run(page, command, prompt) {
    await page.keyboard.type(command);
    await page.keyboard.press('Enter');
    const done = await until(async() => prompt.test((await screenText(page)).trimEnd()));
    check(`the device answers ${command}`, !!done);
}

(async() => {
    fs.mkdirSync(SHOTS, {recursive: true});
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch();
    try {
        const ready = await until(async() => (await get(`${BASE}/gw/stats`)) === 200, 10000);
        check('the fake gateway is up', !!ready);
        if (!ready) {
            throw new Error('server did not start');
        }

        const context = await browser.newContext({viewport: {width: 1440, height: 860}});
        const page = await context.newPage();
        const problems = [];
        page.on('pageerror', e => problems.push('pageerror: ' + e.message));
        page.on('console', m => {
            if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) {
                problems.push('console: ' + m.text());
            }
        });
        await page.goto(`${BASE}/index.en.html?fast${MIN ? '&min' : ''}`);
        await page.locator('[data-region="start"]').click();
        await page.locator('[data-region="status"]', {hasText: 'The lab is running'}).waitFor({timeout: 15000});
        await page.locator(`.node[data-node="${SW1}"].st-started`).waitFor();
        await page.locator('[data-region="layerSeg"]:not([hidden])').waitFor();
        await sleep(500);
        const base = await calls(page);
        check('the page has read the facts once', base.mod_gns3lab_get_facts === 1, JSON.stringify(base));
        check('the cable to PC2 works', (await page.locator(`.link.down[data-link="${LINK}"]`).count()) === 0);

        // ---- the IOL switch: show interfaces status shuts a port down on the scheme at once
        await openConsole(page, SW1);
        check('the console of SW1 shows a prompt', !!(await until(async() => /SW1#$/.test((await screenText(page)).trimEnd()))));
        await sleep(400); // The prompt is taken after a moment of silence.
        const t0 = Date.now();
        await run(page, 'sh int status', /SW1#$/);
        const down = await until(async() => (await page.locator(`.link.down[data-link="${LINK}"]`).count()) === 1, 3000);
        check('the cable SW1 Et0/2 - PC2 goes down on the scheme', !!down, `after ${Date.now() - t0} ms`);
        check('...within a second of the prompt', Date.now() - t0 < 2500);
        const after = await calls(page);
        check('...without the page asking for facts', after.mod_gns3lab_get_facts === base.mod_gns3lab_get_facts
            && !after.mod_gns3lab_refresh_facts, JSON.stringify(after));
        await page.screenshot({path: path.join(SHOTS, 'seen-1-link-down.png')});

        // ---- the card of the node: when and by which command, and the marks on the fields
        await page.locator('[data-region="overview"]').click();
        await page.locator(`.rowbtn[data-goto-node="${SW1}"]`).click();
        await page.locator('[data-layer="l2"]').click();
        const details = page.locator('[data-region="details"]');
        const caption = await until(async() => {
            const text = await details.locator('.seenline').innerText();
            return text || null;
        }, 3000);
        check('the card says "Seen in the console at HH:MM (show interfaces status)"',
            /^Seen in the console at \d\d?:\d\d( [AP]M)? \(show interfaces status\)$/.test((caption || '').trim()), JSON.stringify(caption));
        check('...and marks the fields that came from it', (await details.locator('.seenmark').count()) >= 3);
        await page.screenshot({path: path.join(SHOTS, 'seen-2-card.png')});

        // ---- show vlan brief: the VLAN list is replaced
        await page.keyboard.press('Escape');
        await openConsole(page, SW1);
        await sleep(300);
        await run(page, 'sh vl br', /SW1#$/);
        await until(async() => (await calls(page)).mod_gns3lab_get_facts >= 0);
        check('still no poll of facts after the second command',
            (await calls(page)).mod_gns3lab_get_facts === base.mod_gns3lab_get_facts);

        // ---- VPCS: show ip moves the address of PC1 on the L3 layer (the interface is eth0 there, e0 in the facts)
        await page.locator('[data-layer="l3"]').click();
        const pills = () => page.locator('.ppill').evaluateAll(els => els.map(e => e.textContent.trim()));
        check('before: PC1 is .11 on L3', (await pills()).includes('.11'), JSON.stringify(await pills()));
        await openConsole(page, PC1);
        check('the console of PC1 shows a VPCS prompt', !!(await until(async() => /PC1>$/.test((await screenText(page)).trimEnd()))));
        await sleep(400);
        await run(page, 'show ip', /PC1>$/);
        const moved = await until(async() => (await pills()).includes('.77'), 3000);
        check('after: PC1 is .77 on L3 and .11 is gone', !!moved && !(await pills()).includes('.11'), JSON.stringify(await pills()));
        check('...still without polling', (await calls(page)).mod_gns3lab_get_facts === base.mod_gns3lab_get_facts);
        await page.screenshot({path: path.join(SHOTS, 'seen-3-l3.png')});

        // ---- write memory: the facts are asked for at once, but at most once in 10 s
        await openConsole(page, SW1);
        await sleep(300);
        await run(page, 'wr', /SW1#$/);
        const asked = await until(async() => (await calls(page)).mod_gns3lab_refresh_facts === 1);
        check('write + [OK] asks for fresh facts', !!asked);
        await run(page, 'write memory', /SW1#$/);
        await sleep(500);
        check('a second save within 10 s does not ask again', (await calls(page)).mod_gns3lab_refresh_facts === 1);
        const reset = await until(async() => (await page.locator(`.link.down[data-link="${LINK}"]`).count()) === 0, 9000);
        check('the new snapshot (5 s later) drops what was seen on SW1: the cable is up again', !!reset);
        check('...but what was seen on PC1 stays (its facts did not change)', (await pills()).includes('.77'));

        check('no errors in the page', problems.length === 0, problems.join(' | '));
    } catch (e) {
        check('the run went to the end', false, String(e.stack || e));
    } finally {
        await browser.close();
        server.kill();
    }
    console.log(failed ? `\n${failed} failed` : '\nall checks passed');
    process.exit(failed);
})();
