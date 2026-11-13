'use strict';
/**
 * Checks the option "no pasting into consoles" of an activity in Chromium:
 *
 *   clipboard or dragged text → the terminal of the lab page → fake gateway of server.js (it echoes what it gets).
 *
 * With the option on, a paste (Ctrl+V, the paste event), a drop and input that arrives in one long piece do not reach
 * the device, typing and the snippets do, and a screen has no Paste button. Without the option a paste goes through.
 * It starts the fake gateway itself and drives the page with Playwright.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_nopaste.js [--min]
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
const SW1 = demo.nodes.find(n => n.name === 'SW1').node_id;
const VNC = demo.nodes.find(n => n.console_type === 'vnc').node_id;

/**
 * Opens the console of a node the way a student does: a double click on it.
 *
 * @param {Page} page page
 * @param {string} id node id
 */
async function openConsole(page, id) {
    const halo = page.locator(`.node[data-node="${id}"] circle.halo`);
    let box = await halo.boundingBox();
    const area = await page.locator('[data-region="wrap"]').boundingBox();
    if (box.x + box.width / 2 < area.x || box.x + box.width / 2 > area.x + area.width
        || box.y + box.height / 2 < area.y || box.y + box.height / 2 > area.y + area.height) {
        await page.locator('[data-region="zoomFit"]').click();
        await sleep(400);
        box = await halo.boundingBox();
    }
    await page.mouse.dblclick(box.x + box.width / 2, box.y + box.height / 2);
}

const screenText = page => page.evaluate(() => Array.from(document.querySelectorAll('.xterm-rows')).map(e => e.innerText).join('\n'));
const shows = async(page, text, ms = 3000) => !!(await until(async() => (await screenText(page)).includes(text), ms));
const toast = page => page.evaluate(() => {
    const t = document.querySelector('[data-region="toast"]');
    return t && !t.hidden ? t.textContent : '';
});

/**
 * A paste event with this text on the text area of the terminal, as the browser sends it.
 *
 * @param {Page} page page
 * @param {string} text text of the clipboard
 */
const pasteEvent = (page, text) => page.evaluate(value => {
    const data = new DataTransfer();
    data.setData('text/plain', value);
    document.querySelector('.xterm-helper-textarea').dispatchEvent(
        new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true}));
}, text);

/**
 * Input of the text area in one piece, the way a keyboard of a phone or a tool that goes around the paste event gives it.
 *
 * @param {Page} page page
 * @param {string} text text
 */
const inputEvent = (page, text) => page.evaluate(value => {
    document.querySelector('.xterm-helper-textarea').dispatchEvent(
        new InputEvent('input', {data: value, inputType: 'insertText', bubbles: true}));
}, text);

/**
 * Opens a page of the harness and starts the lab.
 *
 * @param {Page} page page
 * @param {string} name page name
 */
async function start(page, name) {
    await page.goto(`${BASE}/${name}?fast${MIN ? '&min' : ''}`);
    await page.locator('[data-region="start"]').click();
    await page.locator('[data-region="status"]', {hasText: 'The lab is running'}).waitFor({timeout: 15000});
    await page.locator(`.node[data-node="${SW1}"].st-started`).waitFor();
    await sleep(500);
}

/**
 * Opens a page of the harness with a running lab and the console of SW1.
 *
 * @param {Browser} browser browser
 * @param {string} name page name
 * @param {string[]} problems collects the errors of the page
 * @returns {Promise<Page>}
 */
async function labPage(browser, name, problems) {
    const context = await browser.newContext({viewport: {width: 1440, height: 860}});
    // The scenario reads the terminal of the one console on the screen: the panel mode (windows are the default).
    await context.addInitScript(() => localStorage.setItem('gns3lab.mode', JSON.stringify('panel')));
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], {origin: BASE});
    const page = await context.newPage();
    page.on('pageerror', e => problems.push(String(e)));
    page.on('console', m => {
        if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) {
            problems.push(m.text());
        }
    });
    await start(page, name);
    await openConsole(page, SW1);
    const up = await until(async() => /SW1[#>]/.test(await screenText(page)), 10000);
    check(`${name}: the console of SW1 is open`, !!up);
    await page.locator('.xterm-helper-textarea').focus();
    return page;
}

(async() => {
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

        // The option is on.
        let page = await labPage(browser, 'index.en.nopaste.html', problems);
        await page.keyboard.type('typed1');
        check('typing reaches the device', await shows(page, 'typed1'));

        await pasteEvent(page, 'PASTE-EVENT');
        check('a paste event does not reach the device', !(await shows(page, 'PASTE-EVENT', 800)));
        check('...and the student is told', /Pasting is turned off/.test(await toast(page)), await toast(page));

        await page.evaluate(() => navigator.clipboard.writeText('CTRL-V-TEXT'));
        await page.keyboard.press('Control+V');
        await page.keyboard.press('Control+Shift+V');
        await page.keyboard.press('Shift+Insert');
        check('Ctrl+V, Ctrl+Shift+V and Shift+Insert do not paste', !(await shows(page, 'CTRL-V-TEXT', 800)));

        await page.evaluate(() => {
            const data = new DataTransfer();
            data.setData('text/plain', 'DROPPED-TEXT');
            const drop = new DragEvent('drop', {dataTransfer: data, bubbles: true, cancelable: true});
            document.querySelector('.xterm-helper-textarea').dispatchEvent(drop);
            window.__dropPrevented = drop.defaultPrevented;
        });
        check('a drop is refused', await page.evaluate(() => window.__dropPrevented));

        await inputEvent(page, 'ONE-LONG-PIECE-OF-INPUT');
        check('input in one long piece does not reach the device', !(await shows(page, 'ONE-LONG-PIECE', 800)));
        await inputEvent(page, 'shortword');
        check('a short piece (a word of a phone keyboard) does', await shows(page, 'shortword'));
        await inputEvent(page, '\x1b[200~brk\x1b[201~');
        check('a bracketed paste does not reach the device', !(await shows(page, 'brk', 800)));

        await page.keyboard.press('Control+U');
        await page.keyboard.press('Enter');
        // The first pinned snippet of a switch is "show ip interface brief"; the fake device answers with a table.
        const snippet = page.locator('.tbar .snips button').first();
        check('the terminal has a pinned snippet', (await snippet.count()) === 1);
        await snippet.click();
        check('a snippet still goes to the device', await shows(page, 'IP-Address'));
        await page.context().close();

        // A screen has no Paste button.
        const context = await browser.newContext({viewport: {width: 1440, height: 860}});
        for (const [name, buttons] of [['index.en.nopaste.html', 1], ['index.en.html', 2]]) {
            const view = await context.newPage();
            await start(view, name);
            await openConsole(view, VNC);
            const bar = await until(async() => (await view.locator('.vncbar .vncbtn').count()) > 0);
            check(`${name}: buttons under a screen: ${buttons}`, !!bar && (await view.locator('.vncbar .vncbtn').count()) === buttons);
            await view.close();
        }
        await context.close();

        // The option is off: a paste goes through.
        page = await labPage(browser, 'index.en.html', problems);
        await pasteEvent(page, 'PASTE-EVENT');
        check('without the option a paste reaches the device', await shows(page, 'PASTE-EVENT'));
        await inputEvent(page, 'ONE-LONG-PIECE-OF-INPUT');
        check('...and so does long input', await shows(page, 'ONE-LONG-PIECE'));
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
