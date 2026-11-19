'use strict';
/**
 * Checks the "i" of a VLAN pill on L2 (canvasview.js, stage.js onTap), in Chromium:
 *
 *   a click on a VLAN pill selects it and lights its lines but opens no card; a click on its "i" opens the card of
 *   that VLAN; Enter on a focused pill still opens the card; a double click on the pill still opens the console.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_vlaninfo.js [--min]
 *
 * Playwright with Chromium must be installed (require('playwright')). The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18134;
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
const cardOpen = page => page.locator('[data-region="details"]').evaluate(d => d.classList.contains('open'));

async function scenario(browser) {
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
    await sleep(1500);
    await page.locator('[data-layer="l2"]').click();
    await page.locator('[data-region="zoomFit"]').click();
    await sleep(600);
    const pill = page.locator('.l2-virtual').filter({has: page.locator('.vtitle', {hasText: 'VLAN 10'})}).first();
    await pill.waitFor();
    check('the VLAN pill has an "i"', (await pill.locator('[data-virtual-info]').count()) === 1);

    // The "i" sits inside the pill, at its right end.
    const pb = await pill.locator(':scope > rect').boundingBox(), ib = await pill.locator('[data-virtual-info] .dot').boundingBox();
    check('...inside the pill, at its right end', ib.x > pb.x + pb.width / 2 && ib.x + ib.width <= pb.x + pb.width + 1
        && ib.y >= pb.y - 1 && ib.y + ib.height <= pb.y + pb.height + 1, JSON.stringify({pb, ib}));
    // The title does not run into the "i".
    const tb = await pill.locator('.vtitle').boundingBox();
    check('...clear of the title', tb.x + tb.width < ib.x, JSON.stringify({tb, ib}));

    // A click on the pill itself: selection, no card.
    await page.mouse.click(pb.x + 14, pb.y + pb.height / 2);
    await sleep(400);
    check('a click on the pill selects the VLAN and lights it', await pill.evaluate(g => g.classList.contains('connection-primary')));
    check('...with its lines', (await page.locator('.l2-path.connection-related, .l2-path.connection-primary').count()) > 0);
    check('...and opens no card', !(await cardOpen(page)));

    // A click on the "i": the card.
    await page.mouse.click(ib.x + ib.width / 2, ib.y + ib.height / 2);
    await sleep(400);
    check('a click on the "i" opens the card', await cardOpen(page));
    check('...of that VLAN', /VLAN 10/.test(await page.locator('[data-region="details"]').innerText()));

    // A click on another place of the pill closes the card again and keeps the selection.
    await page.mouse.click(pb.x + 14, pb.y + pb.height / 2);
    await sleep(400);
    check('a click on the pill closes an open card', !(await cardOpen(page)));

    // Enter on the focused pill still opens the card (keyboard).
    await pill.focus();
    await page.keyboard.press('Enter');
    await sleep(400);
    check('Enter on the pill opens the card', await cardOpen(page));
    await page.keyboard.press('Escape');
    await sleep(300);

    // A double click on the pill still opens the console of the switch.
    await page.mouse.dblclick(pb.x + 14, pb.y + pb.height / 2);
    const opened = await until(async() => (await page.locator('.xterm-rows').count()) > 0, 4000);
    check('a double click on the pill opens the console of the switch', !!opened);

    check('no errors in the page', problems.length === 0, problems.join(' | '));
    await context.close();
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
