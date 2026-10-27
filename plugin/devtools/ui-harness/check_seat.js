'use strict';
/**
 * Checks the seat of the lab page (docs/seat.md) in Chromium, end to end: only one page drives the lab of a student.
 *
 *   the page claims the seat when it opens → start and console tickets carry the id of the page → somebody else takes
 *   the seat → the next refused call (or the next beat) puts up the wall, closes the consoles, makes the lab under it
 *   inert → the seat comes back → the wall goes.
 *
 * The server side of the rule (who gets the seat, the log, the gates of the external functions) is in the PHPUnit tests
 * of the plugin; here the stub of core/ajax plays it (stubs/ajax.js). Starts the fake gateway of server.js itself.
 * Screenshots go to --shots (default dist/shots).
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_seat.js [--min] [--shots <dir>]
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

const PORT = 18127;
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
const [SW1, PC1] = ['SW1', 'PC1'].map(idOf);

const wall = page => page.locator('[data-region="seatwall"]');
const stub = (page, fn, arg) => page.evaluate(fn, arg);
const screenText = page => page.evaluate(() => Array.from(document.querySelectorAll('.xterm-rows')).map(e => e.innerText).join('\n'));

/**
 * Opens the console of a node the way a student does: a double click on it.
 *
 * @param {Page} page page
 * @param {string} id node id
 */
async function openConsole(page, id) {
    const box = await page.locator(`.node[data-node="${id}"] circle.halo`).boundingBox();
    await page.mouse.dblclick(box.x + box.width / 2, box.y + box.height / 2);
}

/**
 * Makes the page ask for the seat now, the way a beat does (a page that wakes up in the background asks at once).
 *
 * @param {Page} page page
 */
const beat = page => page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));

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
        const problems = [];
        const open = async(url, viewport = {width: 1440, height: 860}) => {
            const page = await (await browser.newContext({viewport})).newPage();
            page.on('pageerror', e => problems.push('pageerror: ' + e.message));
            page.on('console', m => {
                if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) {
                    problems.push('console: ' + m.text());
                }
            });
            await page.goto(`${BASE}/${url}${url.includes('?') ? '&' : '?'}${MIN ? 'min&' : ''}fast`);
            return page;
        };

        // ---- a page that holds the seat works as before, and says who it is in every call that needs the seat
        let page = await open('index.en.seat.html');
        await until(async() => (await stub(page, () => window.__stub.seat.claims.length)) >= 1);
        const first = await stub(page, () => window.__stub.seat.claims[0]);
        check('the page claimed the seat when it opened, taking it over', first.takeover === true, JSON.stringify(first));
        check('...with an id of 32 hex digits', /^[0-9a-f]{32}$/.test(first.tabid), first.tabid);
        check('no wall while it holds the seat', await wall(page).isHidden());
        await page.locator('[data-region="start"]').click();
        await page.locator('[data-region="status"]', {hasText: 'The lab is running'}).waitFor({timeout: 15000});
        const gated = () => stub(page, () => window.__stub.seat.gated);
        check('start_session carried the same id', (await gated()).some(c => c.name === 'start_session' && c.tabid === first.tabid),
            JSON.stringify(await gated()));
        await page.locator(`.node[data-node="${SW1}"].st-started`).waitFor();
        await openConsole(page, SW1);
        check('the console opens and shows a prompt', !!(await until(async() => /SW1#$/.test((await screenText(page)).trimEnd()))));
        check('...its ticket carried the same id', (await gated()).some(c => c.name === 'get_console_ticket' && c.tabid === first.tabid),
            JSON.stringify(await gated()));
        check('a beat is not a takeover', await (async() => {
            await beat(page);
            await until(async() => (await stub(page, () => window.__stub.seat.claims.length)) >= 2);
            return (await stub(page, () => window.__stub.seat.claims[1].takeover)) === false;
        })());

        // ---- another login takes the seat: the next call that needs it is refused, the wall goes up
        await stub(page, () => window.__stub.seat.give('otherlogin'));
        await openConsole(page, PC1);
        const up = await until(async() => await wall(page).isVisible(), 4000);
        check('another login: the next console ticket is refused and the wall goes up', !!up);
        check('...it says the lab is open under another login',
            (await page.locator('[data-region="seatTitle"]').innerText()) === 'The lab is open under another login');
        check('...there is no "use here" button: another login is waited for, not taken over',
            await page.locator('[data-region="seatTake"]').isHidden());
        check('...the console that was open is closed', (await page.locator('.xterm-rows').count()) === 0,
            `${await page.locator('.xterm-rows').count()} terminals left`);
        check('...the lab under the wall takes no click: the session bar is inert',
            (await page.locator('header.bar').getAttribute('inert')) !== null);
        await page.screenshot({path: path.join(SHOTS, 'seat-1-otherlogin.png')});

        // ---- the seat comes back (the other login went away): the next beat takes it, the wall goes
        await stub(page, () => window.__stub.seat.give('me'));
        await beat(page);
        check('the seat is free again: the beat takes it back and the wall goes',
            !!(await until(async() => await wall(page).isHidden(), 4000)));
        check('...and the lab is usable again', (await page.locator('header.bar').getAttribute('inert')) === null);

        // ---- another page of the same login takes the seat: the wall offers "use here"
        await stub(page, () => window.__stub.seat.give('othertab'));
        await beat(page);
        check('another page: the beat finds the seat taken, the wall goes up',
            !!(await until(async() => await wall(page).isVisible(), 4000)));
        check('...it says the lab is open in another tab',
            (await page.locator('[data-region="seatTitle"]').innerText()) === 'The lab is open in another tab');
        check('...and offers to use the lab here', await page.locator('[data-region="seatTake"]').isVisible());
        await page.screenshot({path: path.join(SHOTS, 'seat-2-othertab.png')});
        await page.locator('[data-region="seatTake"]').click();
        check('"Use the lab here" takes the seat over (a takeover claim) and the wall goes',
            !!(await until(async() => await wall(page).isHidden(), 4000))
            && (await stub(page, () => window.__stub.seat.claims.slice(-1)[0].takeover)) === true);
        check('...the seat is ours', (await stub(page, () => window.__stub.seat.holder)) === 'me');
        await page.context().close();

        // ---- a page opened while another login holds the seat: the wall at once, no way to start the lab
        page = await open('index.en.seat.html?seat=otherlogin');
        check('opened under another login: the wall is up at once',
            !!(await until(async() => await wall(page).isVisible(), 4000)));
        check('...and the start button takes no click', (await page.locator('header.bar').getAttribute('inert')) !== null);
        check('...the lab was not started', (await stub(page, () => window.__stub.calls.mod_gns3lab_start_session || 0)) === 0);
        await page.context().close();

        // ---- a page opened while another page of the login holds the seat takes it over by itself
        page = await open('index.en.seat.html?seat=othertab');
        await until(async() => (await stub(page, () => window.__stub.seat.claims.length)) >= 1);
        check('opened next to another page of the same login: it takes the seat, no wall',
            (await stub(page, () => window.__stub.seat.holder)) === 'me' && await wall(page).isHidden());
        await page.context().close();

        // ---- a phone and the Russian texts
        page = await open('index.ru.seat.html?seat=otherlogin', {width: 390, height: 780});
        await wall(page).waitFor({state: 'visible'});
        check('Russian: the title and the text of the wall',
            (await page.locator('[data-region="seatTitle"]').innerText()) === 'Лаба открыта под другим входом'
            && /другом сеансе входа/.test(await page.locator('[data-region="seatText"]').innerText()));
        await page.screenshot({path: path.join(SHOTS, 'seat-3-phone-ru.png')});
        await page.context().close();

        // ---- the other pages of the harness have no seat at all (the activity setting is off)
        page = await open('index.en.html');
        await sleep(500);
        check('an activity without the limit never asks for the seat',
            (await stub(page, () => window.__stub.seat.claims.length)) === 0 && await wall(page).isHidden());
        await page.context().close();

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
