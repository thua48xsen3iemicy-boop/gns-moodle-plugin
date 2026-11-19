'use strict';
/**
 * Checks the tab bar and the hiding of the console panel (consoles.js, panel mode), in Chromium:
 *
 *   eight consoles open in a narrow panel → the wheel over the tabs scrolls them sideways; Shift+arrows scroll the bar
 *   to the chosen tab, round both ways; a tab dragged to the first
 *   place moves there and Shift+Right follows the new order; the button of the bar pushes the panel beyond the edge
 *   (the scheme gets the width, a tongue with the number of consoles stays), new output marks the tongue, a click on
 *   the tongue brings the panel back; opening a console from the scheme or choosing the panel mode brings it back too.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_dock.js [--min]
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
const TERMS = demo.nodes.filter(n => n.console_type === 'telnet');

const node = (page, id) => page.locator(`.node[data-node="${id}"]`);

async function openConsole(page, id) {
    let box = await node(page, id).locator('circle.halo').boundingBox();
    const area = await page.locator('[data-region="wrap"]').boundingBox();
    if (box.x + box.width / 2 < area.x || box.x + box.width / 2 > area.x + area.width
        || box.y + box.height / 2 < area.y || box.y + box.height / 2 > area.y + area.height) {
        await page.locator('[data-region="zoomFit"]').click();
        await sleep(400);
        box = await node(page, id).locator('circle.halo').boundingBox();
    }
    await page.mouse.dblclick(box.x + box.width / 2, box.y + box.height / 2);
}

const tabNames = page => page.locator('[data-region="tabs"] .tab').evaluateAll(ts => ts.map(t => t.textContent.trim()));
const selected = page => page.locator('[data-region="tabs"] .tab[aria-selected="true"]').evaluate(t => t.textContent.trim());

async function scenario(browser) {
    const context = await browser.newContext({viewport: {width: 1440, height: 860}});
    await context.addInitScript(() => {
        localStorage.setItem('gns3lab.mode', JSON.stringify('panel'));
        localStorage.setItem('gns3lab.dockw', JSON.stringify('420px'));
    });
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
    await sleep(300);
    for (const n of TERMS) {
        await page.locator(`.node[data-node="${n.node_id}"]`).waitFor({state: 'attached'});
        await openConsole(page, n.node_id);
        await until(async() => (await selected(page)) === n.name, 3000);
    }
    const tabs = page.locator('[data-region="tabs"]');
    const count = (await tabNames(page)).length;
    check('the open consoles have tabs', count >= 6, JSON.stringify(await tabNames(page)));
    const overflow = await tabs.evaluate(t => t.scrollWidth > t.clientWidth);
    check('the tabs do not fit the narrow panel', overflow);

    const order0 = await tabNames(page);
    // The wheel over the bar scrolls it sideways.
    await tabs.evaluate(t => { t.scrollLeft = 0; });
    const tb = await tabs.boundingBox();
    await page.mouse.move(tb.x + tb.width / 2, tb.y + tb.height / 2);
    await page.mouse.wheel(0, 200);
    const left = await until(async() => (await tabs.evaluate(t => t.scrollLeft)) || null, 2000);
    check('the wheel over the tabs scrolls them sideways', left > 0, `scrollLeft ${left}`);
    await page.mouse.wheel(0, -1000);
    await until(async() => (await tabs.evaluate(t => t.scrollLeft)) === 0, 2000);
    check('...and back', (await tabs.evaluate(t => t.scrollLeft)) === 0);

    // Shift+arrows walk round the tabs: the bar follows the chosen tab, it never stays out of sight.
    const inView = () => tabs.evaluate(t => {
        const a = t.querySelector('[aria-selected="true"]').getBoundingClientRect(), b = t.getBoundingClientRect();
        return a.left >= b.left - 1 && a.right <= b.right + 1;
    });
    await tabs.locator('.tab').nth(0).click();
    await tabs.evaluate(t => { t.scrollLeft = 0; });
    let walked = true;
    for (let i = 0; i < count * 2; i++) {
        await page.keyboard.press(i < count ? 'Shift+ArrowRight' : 'Shift+ArrowLeft');
        await sleep(120);
        if (!(await inView())) {
            walked = false;
            break;
        }
    }
    check('Shift+arrows scroll the bar to the chosen tab', walked, await selected(page));
    await page.keyboard.press('Shift+ArrowLeft');
    await sleep(120);
    check('...also round from the first tab to the last', (await inView()) && (await selected(page)) === order0[count - 1],
        await selected(page));
    await tabs.locator('.tab').nth(count - 1).click();
    await tabs.evaluate(t => { t.scrollLeft = 0; });

    // The third tab is dragged to the first place.
    const order = await tabNames(page);
    const third = tabs.locator('.tab').nth(2), first = tabs.locator('.tab').nth(0);
    const b3 = await third.boundingBox(), b1 = await first.boundingBox();
    await page.mouse.move(b3.x + b3.width / 2, b3.y + b3.height / 2);
    await page.mouse.down();
    for (let i = 1; i <= 10; i++) {
        await page.mouse.move(b3.x + b3.width / 2 + (b1.x + 4 - b3.x - b3.width / 2) * i / 10, b3.y + b3.height / 2);
        await sleep(20);
    }
    await page.mouse.up();
    await sleep(200);
    const moved = await tabNames(page);
    const want = [order[2], order[0], order[1], ...order.slice(3)];
    check('a tab dragged to the first place moves there', JSON.stringify(moved) === JSON.stringify(want), JSON.stringify(moved));
    check('...the drop does not choose that tab', (await selected(page)) === order[order.length - 1], await selected(page));
    // A plain click still chooses a tab.
    await tabs.locator('.tab').nth(0).click();
    check('a click chooses a tab', (await selected(page)) === order[2]);
    await page.keyboard.press('Shift+ArrowRight');
    check('Shift+Right follows the new order', !!(await until(async() => (await selected(page)) === order[0], 2000)), await selected(page));

    // Every console is looked at, so that none of them has unread output.
    for (let i = count - 1; i >= 0; i--) {
        await tabs.locator('.tab').nth(i).click();
        await sleep(150);
    }
    await sleep(300);
    // The panel is pushed beyond the edge.
    const wrap = page.locator('[data-region="wrap"]');
    const w0 = (await wrap.boundingBox()).width;
    await page.locator('[data-region="dockHide"]').click();
    await sleep(400);
    const stage = page.locator('[data-region="stagebox"]');
    check('the hide button hides the panel', await stage.evaluate(s => s.classList.contains('dock-off')));
    check('...the dock is out of sight', await page.locator('[data-region="dock"]').evaluate(d => {
        const r = d.getBoundingClientRect(), s = d.parentNode.getBoundingClientRect();
        return getComputedStyle(d).visibility === 'hidden' && r.left >= s.right - 1;
    }));
    const w1 = (await wrap.boundingBox()).width;
    check('...the scheme takes the width', w1 > w0 + 300, `${w0} → ${w1}`);
    const tongue = page.locator('[data-region="dockTongue"]');
    check('...a tongue shows the number of consoles', await tongue.isVisible()
        && (await page.locator('[data-region="dockCount"]').textContent()) === String(count));
    check('...the tongue has the keyboard', await tongue.evaluate(t => document.activeElement === t));
    check('...no new output yet', !(await page.locator('[data-region="dockUnread"]').isVisible()));

    await tongue.click();
    await sleep(400);
    check('a click on the tongue brings the panel back', !(await stage.evaluate(s => s.classList.contains('dock-off'))));
    check('...the scheme gives the width back', Math.abs((await wrap.boundingBox()).width - w0) < 2);
    check('...the active console has the keyboard', await page.evaluate(() => !!document.activeElement?.closest('.dockbody')));
    check('...the tab order is kept', JSON.stringify(await tabNames(page)) === JSON.stringify(want));

    // A command typed in the active console, the panel hidden, Enter sent to the terminal: its output prints behind the edge.
    await page.keyboard.type('show version');
    await page.locator('[data-region="dockHide"]').click();
    await page.locator('.dockbody .xterm-helper-textarea').evaluate(t => t.focus());
    await page.keyboard.press('Enter');
    const marked = await until(async() => page.locator('[data-region="dockUnread"]').isVisible(), 3000);
    check('output behind the edge marks the tongue', !!marked);

    // Opening a console from the scheme brings the panel back.
    await openConsole(page, TERMS[0].node_id);
    const back = await until(async() => !(await stage.evaluate(s => s.classList.contains('dock-off'))), 2000);
    check('opening a console brings a hidden panel back', !!back);
    check('...with that console chosen', (await selected(page)) === TERMS[0].name);
    check('hidden state is kept in the browser', (await page.evaluate(() => localStorage.getItem('gns3lab.dockoff'))) === 'false');

    // Hidden, then another mode and the panel again: choosing the panel shows it.
    await page.locator('[data-region="dockHide"]').click();
    await sleep(300);
    await page.locator('[data-region="modes"] button[data-mode="windows"]').click();
    await page.locator('[data-region="modes"] button[data-mode="panel"]').click();
    await sleep(300);
    check('choosing the panel mode shows a hidden panel', !(await stage.evaluate(s => s.classList.contains('dock-off'))));

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
