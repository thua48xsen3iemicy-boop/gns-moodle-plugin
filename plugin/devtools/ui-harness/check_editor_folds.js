'use strict';
/**
 * Checks the side columns of the lab editor in Chromium: the properties panel is wide enough for the devices of a segment
 * (no sideways scroll), the device list and the properties fold into tabs on the edges of the scheme and back, the scheme
 * keeps its centre, the browser remembers the choice over a reload, and on a phone the tabs sit on the top and the bottom.
 * The edges of the columns are dragged (and moved with the arrow keys) to set their widths, which the browser remembers;
 * the help of the network check fits the properties panel.
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
        check('the properties panel is 440 px wide', props.w === 440, JSON.stringify(props));
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
            && (await tab('props').getAttribute('aria-expanded')) === 'false' && (await svgWidth()) === w1 + 440);
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

        // ---- the help of the network check fits the panel (a long option of the select does not widen its column)
        await page.goto(`${BASE}/editor.ru.checks.html${MIN}`);
        await page.locator('.node').first().waitFor();
        await page.locator('[data-network]').waitFor();
        const fit = await page.evaluate(() => {
            const p = document.querySelector('.ed-props').getBoundingClientRect();
            const help = document.querySelector('[data-network]').parentElement.querySelector('small').getBoundingClientRect();
            const sel = document.querySelector('[data-network]').getBoundingClientRect();
            return {panel: Math.round(p.right), help: Math.round(help.right), select: Math.round(sel.right),
                sw: document.querySelector('.ed-props').scrollWidth, cw: document.querySelector('.ed-props').clientWidth};
        });
        check('the help of the network check fits the properties panel',
            fit.help <= fit.panel && fit.select <= fit.panel && fit.sw <= fit.cw, JSON.stringify(fit));

        // ---- the edges are dragged to set the widths, the browser remembers them, a double click resets
        const propsW = () => page.locator('.ed-props').evaluate(e => Math.round(e.getBoundingClientRect().width));
        const paletteW = () => page.locator('.ed-palette').evaluate(e => Math.round(e.getBoundingClientRect().width));
        const drag = async(side, dx) => {
            const b = await page.locator(`[data-resize="${side}"]`).boundingBox();
            const x = b.x + b.width / 2, y = b.y + b.height / 2;
            await page.mouse.move(x, y);
            await page.mouse.down();
            await page.mouse.move(x + dx / 2, y);
            await page.mouse.move(x + dx, y);
            await page.mouse.up();
        };
        const c0 = await offset(page);
        await drag('props', -120);
        check('dragging the left edge of the properties widens them', await propsW() === 560, String(await propsW()));
        const c1 = await offset(page);
        check('...and the scheme keeps its centre', Math.abs(c1.x - c0.x) <= 1 && Math.abs(c1.y - c0.y) <= 1,
            JSON.stringify({c0, c1}));
        await drag('palette', 60);
        check('dragging the right edge of the device list widens it', await paletteW() === 280, String(await paletteW()));
        await drag('props', 1000);
        check('the properties do not get narrower than 320 px', await propsW() === 320, String(await propsW()));
        await drag('props', -2000);
        const sw = await svgWidth();
        check('...and the scheme keeps at least 240 px', sw >= 239, String(sw));
        await page.locator('[data-resize="props"]').focus();
        await page.keyboard.press('Home');
        await page.keyboard.press('ArrowLeft');
        check('the arrow keys move a focused edge', await propsW() === 336
            && (await page.locator('[data-resize="props"]').getAttribute('aria-valuenow')) === '336', String(await propsW()));
        await page.reload();
        await page.locator('.node').first().waitFor();
        check('the widths stay after a reload', await propsW() === 336 && await paletteW() === 280,
            `${await propsW()} ${await paletteW()}`);
        await page.locator('[data-resize="props"]').dblclick();
        await page.locator('[data-resize="palette"]').dblclick();
        check('a double click brings the usual widths back', await propsW() === 440 && await paletteW() === 220,
            `${await propsW()} ${await paletteW()}`);
        await tab('props').click();
        check('a folded column has no edge to drag', await page.locator('[data-resize="props"]').isHidden());
        await tab('props').click();
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
        check('phone: no edges to drag', await phone.locator('[data-resize="palette"]').isHidden()
            && await phone.locator('[data-resize="props"]').isHidden());
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
