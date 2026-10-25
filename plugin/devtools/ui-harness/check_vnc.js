'use strict';
/**
 * Checks the graphical console (noVNC) of the student interface in Chromium, end to end:
 *
 *   browser → lab page of the harness (real modules, real noVNC) → fake gateway of server.js → fake Lab Slot of the
 *   mock manager (a minimal RFB 3.8 server) and back.
 *
 * It starts both servers itself (python3 with aiohttp for the mock manager, see ../README.md), drives the page with
 * Playwright and reads what reached the RFB server from GET /mock/vnc/<node_id>. Screenshots go to the directory of
 * --shots (default dist/shots).
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_vnc.js [--shots <dir>]
 *
 * Environment: PYTHON (default python3). Playwright with Chromium must be installed (require('playwright')).
 * The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PYTHON = process.env.PYTHON || 'python3';
const PORTS = {mock: 18090, slot: 18091, page: 18123};
const BASE = `http://127.0.0.1:${PORTS.page}`;
const PVE1 = '6b1f3c2e-4a7d-4e5b-9c10-000000000011'; // the node with console_type vnc in the demo lab
const R1 = '6b1f3c2e-4a7d-4e5b-9c10-000000000002'; // a node with a text console
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
        await sleep(100);
    }
}

const request = (method, url) => new Promise((resolve, reject) => {
    const req = http.request(url, {method}, res => {
        let body = '';
        res.on('data', chunk => (body += chunk));
        res.on('end', () => resolve({status: res.statusCode, body}));
    });
    req.on('error', reject);
    req.end();
});
const json = async(method, url) => JSON.parse((await request(method, url)).body);
const screenOf = () => json('GET', `http://127.0.0.1:${PORTS.mock}/mock/vnc/${PVE1}`);

/**
 * Colour of a pixel of the remote screen (the canvas of noVNC keeps the size of the remote framebuffer).
 *
 * @param {Page} page page
 * @param {number} x x in the remote screen
 * @param {number} y y in the remote screen
 * @returns {Promise<number[]>} r, g, b
 */
const pixel = (page, x, y) => page.evaluate(([px, py]) => {
    const canvas = document.querySelector('.vncscreen canvas');
    return Array.from(canvas.getContext('2d').getImageData(px, py, 1, 1).data.slice(0, 3));
}, [x, y]);
const near = (a, b, d = 12) => a.every((v, i) => Math.abs(v - b[i]) <= d);

/**
 * Starts the lab of the harness and waits until it runs.
 *
 * @param {Page} page page
 */
async function startLab(page) {
    await page.locator('[data-region="start"]').click();
    await page.locator('[data-region="stop"]:not([hidden])').waitFor({timeout: 10000});
    // The page learns that the lab runs from its poll, every 3 s while the lab is starting.
    await page.locator('[data-region="status"]', {hasText: 'The lab is running'}).waitFor({timeout: 15000});
    await page.locator(`.node[data-node="${PVE1}"].st-started`).waitFor();
}

const node = (page, id) => page.locator(`.node[data-node="${id}"]`);

/**
 * Opens the console of a node the way a student does: a double click on it.
 *
 * @param {Page} page page
 * @param {string} id node id
 */
const openConsole = async(page, id) => {
    const box = await node(page, id).locator('circle.halo').boundingBox();
    await page.mouse.dblclick(box.x + box.width / 2, box.y + box.height / 2);
};

(async() => {
    fs.mkdirSync(SHOTS, {recursive: true});
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const children = [
        spawn(PYTHON, [path.join(__dirname, '../mock-manager/mock_manager.py')], {
            env: {...process.env, MOCK_PORT: PORTS.mock, MOCK_SLOT_PORT: PORTS.slot}, stdio: 'ignore',
        }),
        spawn(process.execPath, [path.join(__dirname, 'server.js'), PORTS.page], {
            env: {...process.env, HARNESS_SLOT_URL: `ws://127.0.0.1:${PORTS.slot}`}, stdio: 'ignore',
        }),
    ];
    const browser = await chromium.launch();
    try {
        const ready = await until(async() => (await request('GET', `http://127.0.0.1:${PORTS.mock}/mock/vnc/x`)).status === 404
            && (await request('GET', `${BASE}/gw/stats`)).status === 200, 15000);
        check('mock manager and fake gateway are up', !!ready);
        if (!ready) {
            throw new Error('servers did not start (is aiohttp installed for ' + PYTHON + '?)');
        }

        // ---- the owner of the lab, desktop
        const context = await browser.newContext({viewport: {width: 1440, height: 860}, permissions: ['clipboard-read', 'clipboard-write']});
        const page = await context.newPage();
        const problems = [];
        page.on('pageerror', e => problems.push('pageerror: ' + e.message));
        page.on('console', m => {
            if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) {
                problems.push('console: ' + m.text());
            }
        });
        await page.goto(`${BASE}/index.en.html?fast`);
        await startLab(page);
        await openConsole(page, PVE1);

        const canvas = page.locator('.vncscreen canvas');
        check('a canvas of noVNC appears in the console panel', !!(await until(async() => (await canvas.count()) === 1)));
        check('no terminal, snippets or font size for a screen',
            (await page.locator('.vncview .xterm, .tbar .snipbtn, .tbar .tfont').count()) === 0
            && (await page.locator('.vncbar').count()) === 1);
        const drawn = await until(async() => {
            const bar = await pixel(page, 60, 30);
            return near(bar, [235, 235, 16]) ? bar : null;
        });
        check('the picture of the RFB server is drawn (yellow colour bar)', !!drawn, JSON.stringify(drawn));
        check('the logo letters are drawn (white at 96,80)', near(await pixel(page, 96, 80), [250, 250, 250]));
        check('the blue bar is where it should be (260,30)', near(await pixel(page, 260, 30), [16, 16, 235]));
        const size = await canvas.evaluate(c => ({w: c.width, h: c.height, cw: c.clientWidth, ch: c.clientHeight}));
        check('remote screen is 320x200 and scaled up to the tile (scaleViewport)',
            size.w === 320 && size.h === 200 && size.cw > 400 && Math.abs(size.cw / size.ch - 1.6) < 0.02, JSON.stringify(size));
        check('a note is not shown over a connected screen', await page.locator('.vncnote').isHidden());
        check('one ticket was used', (await page.evaluate(() => window.__stub.tickets)) === 1);
        await page.screenshot({path: path.join(SHOTS, '1-panel-connected.png')});

        // ---- input reaches the RFB server
        const box = await canvas.boundingBox();
        await page.mouse.move(box.x + box.width * 0.25, box.y + box.height * 0.5);
        await page.mouse.click(box.x + box.width * 0.75, box.y + box.height * 0.5);
        await page.keyboard.press('a');
        await page.keyboard.press('Shift+B');
        await page.keyboard.press('Enter');
        await page.keyboard.press('Escape');
        const got = await until(async() => {
            const s = await screenOf();
            return s.counts.click >= 1 && s.keys >= 4 ? s : null;
        });
        check('the click and the keys arrive at the RFB server', !!got, JSON.stringify(got && got.counts));
        if (got) {
            const click = got.events.find(e => e.type === 'pointer' && (e.mask & 1));
            check('the click is where it was made (240,100 in the remote screen)',
                !!click && Math.abs(click.x - 240) <= 3 && Math.abs(click.y - 100) <= 3, JSON.stringify(click));
            const down = got.events.filter(e => e.type === 'key' && e.down).map(e => e.keysym);
            check('keys: a, Shift, B, Enter, Escape as keysyms', [0x61, 0xffe1, 0x42, 0xff0d, 0xff1b].every(k => down.includes(k)),
                down.map(k => k.toString(16)).join(' '));
        }
        // The yellow cross of the pointer covers the middle of the square, so a pixel near its corner is looked at.
        const painted = await until(async() => near(await pixel(page, 234, 94), [230, 30, 30], 20));
        check('the answer of the server (red square at the click) is drawn back', !!painted);
        check('Escape of the screen did not go to the page (the screen is still there)', (await canvas.count()) === 1);

        // ---- the buttons of the bar
        const before = (await screenOf()).events.length;
        await page.getByRole('button', {name: 'Send Ctrl+Alt+Del to the device'}).click();
        const cad = await until(async() => {
            const downs = (await screenOf()).events.slice(before).filter(e => e.type === 'key' && e.down).map(e => e.keysym);
            return downs.join() === [0xffe3, 0xffe9, 0xffff].join() ? downs : null;
        });
        check('Ctrl+Alt+Del sends Control_L, Alt_L, Delete', !!cad);
        await page.evaluate(() => navigator.clipboard.writeText('text from the clipboard'));
        await page.getByRole('button', {name: 'Paste', exact: true}).click();
        const cut = await until(async() => (await screenOf()).events.find(e => e.type === 'cut'));
        check('Paste sends the clipboard text as ClientCutText', !!cut && cut.text === 'text from the clipboard', JSON.stringify(cut));
        await page.screenshot({path: path.join(SHOTS, '2-panel-after-input.png')});

        // ---- the arrangements keep the connection
        const tickets = await page.evaluate(() => window.__stub.tickets);
        for (const mode of ['grid', 'windows', 'panel']) {
            await page.locator(`[data-region="modes"] button[data-mode="${mode}"]`).click();
            await sleep(500);
            const ok = (await canvas.count()) === 1 && near(await pixel(page, 60, 30), [235, 235, 16]);
            const inside = await canvas.evaluate(c => {
                const r = c.getBoundingClientRect(), host = c.closest('.termhost').getBoundingClientRect();
                return r.width > 100 && r.left >= host.left - 1 && r.right <= host.right + 1 && r.top >= host.top - 1
                    && r.bottom <= host.bottom + 1;
            });
            check(`${mode}: the same screen, its picture kept, scaled inside the tile`, ok && inside);
            if (mode !== 'panel') {
                await page.screenshot({path: path.join(SHOTS, `3-${mode}.png`)});
            }
        }
        check('switching the arrangement did not reconnect', (await page.evaluate(() => window.__stub.tickets)) === tickets);

        // ---- the slot goes away: pause, notice, a new ticket, a new picture
        await request('POST', `http://127.0.0.1:${PORTS.mock}/mock/nodes/${PVE1}/stop`);
        const note = await until(async() => {
            const text = await page.locator('.vncnote').innerText();
            return /reconnecting in \d+ s/.test(text) ? text : null;
        });
        check('the slot closed: a note with the pause is shown over the screen', !!note, JSON.stringify(note));
        await page.screenshot({path: path.join(SHOTS, '4-reconnecting.png')});
        await request('POST', `http://127.0.0.1:${PORTS.mock}/mock/nodes/${PVE1}/start`);
        const back = await until(async() => near(await pixel(page, 60, 30), [235, 235, 16]) && (await page.locator('.vncnote').isHidden()), 30000);
        check('the node is back: the screen reconnects by itself', !!back);
        check('every reconnection takes a new ticket', (await page.evaluate(() => window.__stub.tickets)) > tickets);
        check('the old canvas is gone: one canvas, one connection', (await canvas.count()) === 1 && (await json('GET', `${BASE}/gw/stats`)).screens === 1);

        // ---- the node is stopped: no retries, a note and a button
        const mock = `http://127.0.0.1:${PORTS.mock}/mock/nodes/${PVE1}`;
        await page.evaluate(id => window.__stub.setNode(id, 'stopped'), PVE1);
        await request('POST', `${mock}/stop`);
        const stopped = await until(async() => /The node is stopped/.test(await page.locator('.vncnote').innerText()));
        check('the node is stopped: the note says so and the screen waits for a click on Reconnect',
            !!stopped && (await page.locator('.creconnect').count()) === 1);
        await sleep(1500);
        check('and nothing tries to connect meanwhile', (await json('GET', `${BASE}/gw/stats`)).screens === 0);
        await page.evaluate(id => window.__stub.setNode(id, 'started'), PVE1);
        await request('POST', `${mock}/start`);
        await page.locator('.creconnect').click();
        check('Reconnect brings the screen back',
            !!(await until(async() => near(await pixel(page, 60, 30), [235, 235, 16]) && (await page.locator('.creconnect').count()) === 0)));

        // ---- closing the console lets everything go
        await page.locator('.tab [data-x]').first().click();
        const gone = await until(async() => (await canvas.count()) === 0 && (await json('GET', `${BASE}/gw/stats`)).screens === 0);
        check('the console closed: no canvas, no connection at the gateway', !!gone, JSON.stringify(await json('GET', `${BASE}/gw/stats`)));
        check('no errors on the page', problems.length === 0, problems.join(' | '));

        // ---- a text console still works next to it
        await openConsole(page, R1);
        const term = await until(async() => (await page.locator('.xterm').count()) === 1 && /R1#/.test(await page.locator('.xterm-rows').innerText()));
        check('a text console of the same page is a terminal with snippets and font size',
            !!term && (await page.locator('.tbar .tfont').count()) === 1 && (await page.locator('.vncbar').count()) === 0);
        await context.close();

        // ---- the teacher: no screen
        const teacher = await browser.newContext({viewport: {width: 1440, height: 860}});
        const view = await teacher.newPage();
        await view.goto(`${BASE}/index.en.html?observer`);
        await view.locator(`.node[data-node="${PVE1}"].st-started`).waitFor();
        await openConsole(view, PVE1);
        const toast = await until(async() => {
            const text = await view.locator('[data-region="toast"]').innerText();
            return /graphical console of a student/.test(text) ? text : null;
        });
        check('the teacher is told that he does not see the screen of a student', !!toast, JSON.stringify(toast));
        check('and no ticket is asked, no canvas appears',
            (await view.evaluate(() => window.__stub.tickets || 0)) === 0 && (await view.locator('.vncscreen').count()) === 0);
        await teacher.close();

        // ---- a phone: hold the node, drag to "Console" in the wheel, let go; the screen is in the sheet
        const phone = await browser.newContext({viewport: {width: 390, height: 800}, hasTouch: true, isMobile: true, deviceScaleFactor: 2});
        const mobile = await phone.newPage();
        await mobile.goto(`${BASE}/index.en.html?fast`);
        await startLab(mobile);
        const cdp = await phone.newCDPSession(mobile);
        const touch = (type, x, y) => cdp.send('Input.dispatchTouchEvent', {type, touchPoints: type === 'touchEnd' ? [] : [{x, y}]});
        const halo = await node(mobile, PVE1).locator('circle.halo').boundingBox();
        await touch('touchStart', halo.x + halo.width / 2, halo.y + halo.height / 2);
        await sleep(900);
        const wedge = await mobile.locator('[data-region="radial"]').getByText('Console', {exact: true}).boundingBox();
        await touch('touchMove', wedge.x + wedge.width / 2, wedge.y + wedge.height / 2);
        await sleep(200);
        await touch('touchEnd');
        const sheet = mobile.locator('.dive .vncscreen canvas');
        check('phone: the screen opens in the sheet', !!(await until(async() => (await sheet.count()) === 1)));
        check('phone: the screen is drawn and the key strip of terminals is hidden',
            !!(await until(async() => near(await pixel(mobile, 60, 30), [235, 235, 16]))) && await mobile.locator('[data-region="keys"]').isHidden());
        await sleep(300);
        const eventsBefore = (await screenOf()).events.length;
        const cb = await sheet.boundingBox();
        await touch('touchStart', cb.x + cb.width / 2, cb.y + cb.height / 2);
        await sleep(60);
        await touch('touchEnd');
        const tap = await until(async() => (await screenOf()).events.slice(eventsBefore).find(e => e.type === 'pointer' && (e.mask & 1)));
        check('phone: a tap on the screen is a click at that place (160,100)',
            !!tap && Math.abs(tap.x - 160) <= 6 && Math.abs(tap.y - 100) <= 6, JSON.stringify(tap));
        await mobile.screenshot({path: path.join(SHOTS, '5-phone-sheet.png')});
        await phone.close();
    } catch (error) {
        check('the scenario ran to the end', false, error.stack || String(error));
    } finally {
        await browser.close();
        children.forEach(child => child.kill());
    }
    console.log(failed ? `${failed} check(s) failed` : 'all checks passed');
    console.log('screenshots:', SHOTS);
    process.exit(failed);
})();
