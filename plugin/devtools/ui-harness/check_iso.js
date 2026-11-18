'use strict';
/**
 * Checks "Mount ISO" of the action wheel (manager CONTRACTS §2.12) in Chromium: the wheel of a source node has the
 * ISO action, pressing it starts an operation with the id of the page, the page follows it and says where the image
 * went; a failed build gets its own text; nodes that are not sources and an observer get no action.
 *
 * The stub of core/ajax plays the plugin and the manager (stubs/ajax.js): every QEMU node of the demo is a source and
 * a target, an image is ready after two polls, `__stub.iso.next = '<code>'` fails the next one.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_iso.js [--min] [--shots <dir>]
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
const [R1, PC1] = ['R1', 'PC1'].map(idOf);

/**
 * Labels of the wheel of a node, opened from the keyboard.
 *
 * @param {Page} page page
 * @param {string} id node id
 * @returns {Promise<string[]>}
 */
async function wheel(page, id) {
    await page.keyboard.press('Escape');
    await page.locator(`.node[data-node="${id}"]`).focus();
    await page.keyboard.press('ContextMenu');
    await page.locator('[data-region="radial"] .wedge').first().waitFor();
    return page.locator('[data-region="radial"] .wedge text').evaluateAll(es => es.map(e => e.textContent));
}

const toast = page => page.locator('[data-region="toast"]:not([hidden])').innerText().catch(() => '');

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
        const page = await browser.newPage({viewport: {width: 1440, height: 860}});
        const problems = [];
        page.on('pageerror', e => problems.push('pageerror: ' + e.message));
        await page.goto(`${BASE}/index.ru.html?fast${MIN ? '&min' : ''}`);
        await page.locator('[data-region="start"]').click();
        await page.locator(`.node[data-node="${R1}"].st-started`).waitFor({timeout: 15000});
        await sleep(300);

        // The scheme shows the nodes started before the page reads the session that names the sources.
        const labels = await until(async() => {
            const items = await wheel(page, R1);
            return items.includes('ISO') ? items : null;
        }, 6000);
        check('the wheel of a source node has ISO', !!labels, JSON.stringify(await wheel(page, R1)));
        check('...a VPCS node has not', !(await wheel(page, PC1)).includes('ISO'));

        // ---- a successful build
        await wheel(page, R1);
        await page.locator('[data-region="radial"] .wedge', {hasText: 'ISO'}).click();
        const started = await until(async() => /Собираю ISO-образ из файлов R1/.test(await toast(page)), 3000);
        check('the page says the image is being built', !!started, await toast(page));
        const gated = await page.evaluate(() => window.__stub.seat.gated.filter(g => g.name === 'start_iso'));
        check('start_iso carries the id of the page', gated.length === 1 && !!gated[0].tabid, JSON.stringify(gated));
        const busy = await wheel(page, R1);
        const off = await page.locator('[data-region="radial"] .wedge.off', {hasText: 'ISO'}).count();
        check('while it is built the action is off', busy.includes('ISO') && off === 1);
        const done = await until(async() => {
            const text = await toast(page);
            return /ISO-образ подключён: .*R2.*/.test(text) && !/R1,/.test(text) ? text : null;
        }, 8000);
        check('the page names the devices that took it, not the source', !!done, await toast(page));
        await page.screenshot({path: path.join(SHOTS, 'iso-1-done.png')});

        // ---- a failed build
        await page.evaluate(() => {
            window.__stub.iso.next = 'ISO_SOURCE_EMPTY';
        });
        await wheel(page, R1);
        await page.locator('[data-region="radial"] .wedge', {hasText: 'ISO'}).click();
        const empty = await until(async() => /В оговорённом каталоге нет файлов/.test(await toast(page)), 8000);
        check('an empty directory has its own text', !!empty, await toast(page));
        check('the page has no errors', problems.length === 0, problems.join('; '));

        // ---- an observer looks at someone else's lab: no ISO
        const observer = await browser.newPage({viewport: {width: 1440, height: 860}});
        await observer.goto(`${BASE}/index.ru.html?fast&observer${MIN ? '&min' : ''}`);
        await observer.locator(`.node[data-node="${R1}"]`).waitFor({timeout: 15000});
        await sleep(800);
        check('an observer gets no ISO', !(await wheel(observer, R1)).includes('ISO'));
    } catch (e) {
        check('scenario', false, e.stack);
    } finally {
        await browser.close();
        server.kill();
    }
    process.exit(failed);
})();
