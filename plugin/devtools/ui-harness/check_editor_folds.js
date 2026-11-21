'use strict';
/**
 * Checks the side columns of the lab editor in Chromium: the properties panel is wide enough for the devices of a segment
 * (no sideways scroll), the device list and the properties fold into tabs on the edges of the scheme and back, the scheme
 * keeps its centre, the browser remembers the choice over a reload, and on a phone the tabs sit on the top and the bottom.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_editor_folds.js [--min] [--shots <dir>]
 *
 * It starts the static server of server.js itself (port 18139). Playwright with Chromium must be installed.
 * The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18139;
const BASE = `http://127.0.0.1:${PORT}`;
const MIN = process.argv.includes('--min') ? '?min' : '';
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
async function until(fn, ms = 5000) {
    const end = Date.now() + ms;
    for (;;) {
        const value = await fn().catch(() => null);
        if (value) {
            return value;
        }
        if (Date.now() > end) {
            return null;
        }
        await sleep(40);
    }
}

const get = url => new Promise((resolve, reject) => {
    http.get(url, res => {
        res.resume();
        resolve(res.statusCode);
    }).on('error', reject);
});

/**
 * Where the first device is drawn, from the centre of the scheme, in pixels.
 *
 * @param {Page} page page
 * @returns {Promise<{x: number, y: number}>}
 */
const offset = page => page.evaluate(() => {
    const s = document.querySelector('[data-region="svg"]').getBoundingClientRect();
    const n = document.querySelector('.node .halo').getBoundingClientRect();
    return {x: Math.round(n.x + n.width / 2 - s.x - s.width / 2), y: Math.round(n.y + n.height / 2 - s.y - s.height / 2)};
});

(async() => {
    fs.mkdirSync(SHOTS, {recursive: true});
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch({executablePath: process.env.CHROMIUM_BIN || undefined});
    const problems = [];
    const watch = page => {
        page.on('pageerror', e => problems.push('pageerror: ' + e.message));
        page.on('console', m => {
            if (m.type() === 'error') {
                problems.push('console: ' + m.text());
            }
        });
        return page;
    };
    try {
        const ready = await until(async() => (await get(`${BASE}/editor.ru.segments.html`)) === 200, 10000);
        check('the harness server is up', !!ready);
        if (!ready) {
            throw new Error('server did not start');
        }
        const context = await browser.newContext({viewport: {width: 1440, height: 860}});
        const page = watch(await context.newPage());
        await page.goto(`${BASE}/editor.ru.segments.html${MIN}`);
        await page.locator('.node').first().waitFor();

        // ---- the properties are wide enough for the devices of a segment
        await page.locator('[data-goto^="segment"]').first().click();
        await page.locator('.ed-members').waitFor();
        const props = await page.evaluate(() => {
            const p = document.querySelector('.ed-props');
            return {w: Math.round(p.getBoundingClientRect().width), sw: p.scrollWidth, cw: p.clientWidth};
        });
        check('the properties panel is 400 px wide', props.w === 400, JSON.stringify(props));
        check('...and a segment with devices does not scroll sideways', props.sw <= props.cw, JSON.stringify(props));

        // ---- the device list folds into a tab and back
        const tab = side => page.locator(`[data-fold="${side}"]`);
        const svgWidth = () => page.locator('[data-region="svg"]').evaluate(e => Math.round(e.getBoundingClientRect().width));
        const w0 = await svgWidth(), o0 = await offset(page);
        await tab('palette').click();
        check('the left tab hides the device list', await page.locator('.ed-palette').isHidden()
            && (await tab('palette').getAttribute('aria-expanded')) === 'false'
            && (await tab('palette').getAttribute('title')) === 'Показать список устройств');
        const w1 = await svgWidth(), o1 = await offset(page);
        check('...the scheme gets its room', w1 === w0 + 220, `${w0} → ${w1}`);
        check('...and keeps its centre', Math.abs(o1.x - o0.x) <= 1 && Math.abs(o1.y - o0.y) <= 1, JSON.stringify({o0, o1}));
        await tab('props').click();
        check('the right tab hides the properties', await page.locator('.ed-props').isHidden()
            && (await tab('props').getAttribute('aria-expanded')) === 'false' && (await svgWidth()) === w1 + 400);
        const tabs = await page.evaluate(() => {
            const s = document.querySelector('[data-region="svg"]').getBoundingClientRect();
            const [l, r] = [...document.querySelectorAll('.ed-fold')].map(b => b.getBoundingClientRect());
            return {left: Math.round(l.left - s.left), right: Math.round(s.right - r.right)};
        });
        check('the tabs sit on the edges of the scheme', tabs.left === 0 && tabs.right === 0, JSON.stringify(tabs));
        await page.screenshot({path: path.join(SHOTS, 'editor-folded.png')});

        // ---- the browser remembers it
        await page.reload();
        await page.locator('.node').first().waitFor();
        check('both stay folded after a reload', await page.locator('.ed-palette').isHidden()
            && await page.locator('.ed-props').isHidden());
        await tab('palette').click();
        await tab('props').click();
        check('the tabs bring both back', await page.locator('.ed-palette').isVisible()
            && await page.locator('.ed-props').isVisible() && (await tab('props').getAttribute('title')) === 'Скрыть панель свойств');
        await page.screenshot({path: path.join(SHOTS, 'editor-unfolded.png')});
        await context.close();

        // ---- a phone: the list above, the properties below
        const phoneContext = await browser.newContext({viewport: {width: 390, height: 844}, hasTouch: true});
        const phone = watch(await phoneContext.newPage());
        await phone.goto(`${BASE}/editor.ru.segments.html${MIN ? MIN + '&' : '?'}theme=dark`);
        await phone.locator('.node').first().waitFor();
        const ptab = side => phone.locator(`[data-fold="${side}"]`);
        const place = await phone.evaluate(() => {
            const s = document.querySelector('[data-region="svg"]').getBoundingClientRect();
            const [l, r] = [...document.querySelectorAll('.ed-fold')].map(b => b.getBoundingClientRect());
            const mid = s.left + s.width / 2;
            return {top: Math.round(l.top - s.top), bottom: Math.round(s.bottom - r.bottom), w: Math.round(l.width),
                off: Math.round(Math.max(Math.abs(l.left + l.width / 2 - mid), Math.abs(r.left + r.width / 2 - mid)))};
        });
        check('phone: the tabs sit in the middle of the top and the bottom edge',
            place.top === 0 && place.bottom === 0 && place.w === 56 && place.off <= 1,
            JSON.stringify(place));
        const h0 = await phone.locator('[data-region="svg"]').evaluate(e => e.getBoundingClientRect().height);
        await ptab('palette').tap();
        const h1 = await phone.locator('[data-region="svg"]').evaluate(e => e.getBoundingClientRect().height);
        check('phone: hiding the list gives the scheme more height', await phone.locator('.ed-palette').isHidden() && h1 > h0,
            `${Math.round(h0)} → ${Math.round(h1)}`);
        await sleep(300); // the arrow turns
        await phone.screenshot({path: path.join(SHOTS, 'editor-folded-phone.png')});
        const noScroll = await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
        check('phone: no sideways scroll of the page', noScroll);
        await phoneContext.close();

        check('no errors in the page', !problems.length, problems.join(' | '));
    } catch (e) {
        check('the scenario ran to the end', false, e.stack);
    } finally {
        await browser.close();
        server.kill();
    }
    console.log(failed ? `\n${failed} check(s) failed` : '\nall checks passed');
    process.exit(failed);
})();
