'use strict';
/**
 * Checks the feedback of "Refresh now" (lab.js paintRefresh) and the hiding of the layers HUD (canvasview.js
 * setHudOff), in Chromium:
 *
 *   a click on "Refresh now" turns it into a busy, disabled "Collecting…" until the new facts are on the scheme, then
 *   it says "Updated" for a moment and is "Refresh now" again; the button at the corner of the HUD slides the HUD
 *   beyond the left edge and leaves a tongue with the current layer; a click on the tongue brings the HUD back; the
 *   choice is kept after a reload; on a phone the tongue is there too.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_hud.js [--min]
 *
 * Playwright with Chromium must be installed (require('playwright')). The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18136;
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
const watchErrors = page => {
    const problems = [];
    page.on('pageerror', e => problems.push('pageerror: ' + e.message));
    page.on('console', m => {
        if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) {
            problems.push('console: ' + m.text());
        }
    });
    return problems;
};

async function start(page) {
    await page.goto(`${BASE}/index.en.html?fast${MIN ? '&min' : ''}`);
    await page.locator('[data-region="start"]').click();
    await page.locator('[data-region="status"]', {hasText: 'The lab is running'}).waitFor({timeout: 15000});
    await until(async() => !(await page.locator('[data-region="canvasRefresh"]').isDisabled()), 8000);
}

async function refresh(browser) {
    const context = await browser.newContext({viewport: {width: 1300, height: 760}});
    const page = await context.newPage();
    const problems = watchErrors(page);
    await start(page);
    const button = page.locator('[data-region="canvasRefresh"]');
    const state = () => button.getAttribute('data-state');
    const facts = () => page.evaluate(() => window.__stub.calls.mod_gns3lab_get_facts || 0);
    check('"Refresh now" is idle', (await state()) === '' && (await button.textContent()) === 'Refresh now');
    const before = await facts();
    await button.click();
    await sleep(150);
    check('a click makes it busy', (await state()) === 'busy' && /Collecting/.test(await button.textContent()), await button.textContent());
    check('...disabled, so a second click asks nothing', await button.isDisabled());
    check('...and says so to a screen reader', (await button.getAttribute('aria-busy')) === 'true');
    const done = await until(async() => ['refreshed', 'nochange'].includes(await state()) ? state() : null, 15000);
    check('new facts end the wait: "Updated"', done === 'refreshed', String(done));
    check('...the page read the facts again', (await facts()) > before);
    check('...the time of the collection flashes', await page.locator('[data-region="factStatus"]').evaluate(e => e.classList.contains('fresh')));
    check('...the button works again', !(await button.isDisabled()));
    const idle = await until(async() => (await state()) === '' ? true : null, 6000);
    check('...and soon reads "Refresh now" again', !!idle && (await button.textContent()) === 'Refresh now');
    check('no errors in the page (refresh)', problems.length === 0, problems.join(' | '));
    await context.close();
}

async function hud(browser, phone) {
    const context = await browser.newContext(phone ? {viewport: {width: 390, height: 780}, isMobile: true, hasTouch: true}
        : {viewport: {width: 1300, height: 760}});
    const page = await context.newPage();
    const problems = watchErrors(page);
    const tag = phone ? 'phone' : 'desktop';
    await start(page);
    const wrap = page.locator('[data-region="wrap"]'), tongue = page.locator('[data-region="hudTongue"]');
    const fold = page.locator('[data-region="hudFold"]'), layers = page.locator('[data-region="canvasUi"]');
    check(`${tag}: the HUD is shown with its hide button`, await fold.isVisible() && !(await tongue.isVisible()));
    await page.locator('[data-layer="l2"]').click();
    await (phone ? fold.tap() : fold.click());
    await sleep(450);
    check(`${tag}: the hide button hides the HUD`, await wrap.evaluate(w => w.classList.contains('hud-off')));
    check(`${tag}: ...the layers and the tools are out of sight`, await page.evaluate(() => {
        const box = document.querySelector('[data-region="wrap"]').getBoundingClientRect();
        return [...document.querySelectorAll('[data-region="canvasUi"] .hud-bg, [data-region="hudTools"]')].every(e => {
            const r = e.getBoundingClientRect();
            return r.right <= box.left + 1 && getComputedStyle(e).visibility === 'hidden';
        });
    }));
    check(`${tag}: ...a tongue stays with the current layer`, await tongue.isVisible() && (await tongue.textContent()).trim() === 'L2');
    check(`${tag}: ...the tongue is at the left edge`, await tongue.evaluate(t => t.getBoundingClientRect().left
        - document.querySelector('[data-region="wrap"]').getBoundingClientRect().left) < 1);
    if (!phone) {
        check(`${tag}: ...and has the keyboard`, await tongue.evaluate(t => document.activeElement === t));
        // The layer keys still work, and the tongue follows them.
        await page.locator('[data-region="svg"]').focus();
        await page.keyboard.press('3');
        await sleep(200);
        check(`${tag}: the tongue follows the layer keys`, (await tongue.textContent()).trim() === 'L3');
    }
    await page.reload();
    await page.locator('[data-region="wrap"]').waitFor();
    await sleep(500);
    check(`${tag}: a hidden HUD stays hidden after a reload`, await wrap.evaluate(w => w.classList.contains('hud-off')) && await tongue.isVisible());
    await (phone ? tongue.tap() : tongue.click());
    await sleep(450);
    check(`${tag}: a click on the tongue brings the HUD back`, !(await wrap.evaluate(w => w.classList.contains('hud-off')))
        && await layers.evaluate(g => getComputedStyle(g).visibility === 'visible'));
    check(`${tag}: ...the tongue is gone`, !(await tongue.isVisible()));
    if (!phone) {
        check(`${tag}: ...the hide button has the keyboard`, await fold.evaluate(b => document.activeElement === b));
    }
    // The hide button sits in the HUD, at the right end of the kicker row, clear of the kicker text.
    const fb = await fold.boundingBox(), bg = await page.locator('[data-region="canvasUi"] .hud-bg').boundingBox();
    const kb = await page.locator('[data-region="canvasUi"] .hud-kicker').boundingBox();
    check(`${tag}: the hide button sits in the HUD corner`, fb.x > bg.x && fb.x + fb.width < bg.x + bg.width && fb.y > bg.y
        && fb.x > kb.x + kb.width, JSON.stringify({fb, bg, kb}));
    check(`no errors in the page (${tag})`, problems.length === 0, problems.join(' | '));
    await context.close();
}

async function scenario(browser) {
    await refresh(browser);
    await hud(browser, false);
    await hud(browser, true);
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
