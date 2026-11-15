'use strict';
/**
 * Checks the assignment window of the lab page (amd/src/assignment.js) in Chromium:
 *
 *   the toolbar button opens it → a click on the scheme, a node card and the overview leave it open → it is dragged by
 *   its title and keeps inside the scheme → it folds to its title and unfolds → after a reload it is where it was,
 *   folded as it was, scrolled as it was → a console window and the assignment come to the front by a click on them
 *   → Escape closes it → on a phone it is a sheet above the zoom controls that folds by a tap on its title.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_assignment.js [--min] [--shots <dir>]
 *
 * Playwright with Chromium must be installed (require('playwright')); CHROMIUM_BIN may name the browser.
 * The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18133;
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
const region = (page, name) => page.locator(`[data-region="${name}"]`);
const win = page => region(page, 'assignments');
const box = async loc => {
    const b = await loc.boundingBox();
    return b && {x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height)};
};

(async() => {
    fs.mkdirSync(SHOTS, {recursive: true});
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch(process.env.CHROMIUM_BIN ? {executablePath: process.env.CHROMIUM_BIN} : {});
    try {
        const ready = await until(async() => (await get(`${BASE}/gw/stats`)) === 200, 10000);
        check('the fake gateway is up', !!ready);
        if (!ready) {
            throw new Error('server did not start');
        }
        const problems = [];
        const watch = page => {
            page.on('pageerror', e => problems.push('pageerror: ' + e.message));
            page.on('console', m => {
                if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) {
                    problems.push('console: ' + m.text());
                }
            });
            return page;
        };
        const url = `${BASE}/index.ru.html?fast${MIN ? '&min' : ''}`;
        const context = await browser.newContext({viewport: {width: 1440, height: 900}});
        const page = watch(await context.newPage());
        await page.goto(url);
        await region(page, 'layerSeg').waitFor();

        // ---- opens, and stays open while the student works with the scheme
        check('closed at first', await win(page).isHidden());
        await region(page, 'assignmentsOpen').click();
        check('the button opens it', await win(page).isVisible());
        check('...and says so', (await region(page, 'assignmentsOpen').getAttribute('aria-expanded')) === 'true');
        check('the activity description is in it', /Проверьте VLAN/.test(await win(page).innerText()));
        const area = await box(region(page, 'wins'));
        let b = await box(win(page));
        check('it opens inside the scheme', b.x >= area.x && b.y >= area.y && b.x + b.w <= area.x + area.w
            && b.y + b.h <= area.y + area.h, JSON.stringify({b, area}));
        const sw = page.locator(`.node[data-node="${SW1}"] circle.halo`);
        await page.locator('[data-region="zoomFit"]').click();
        await sleep(300);
        await page.mouse.click(area.x + 40, area.y + area.h - 120);
        check('a click on the empty scheme leaves it open', await win(page).isVisible());
        const n = await sw.boundingBox();
        await page.mouse.click(n.x + n.width / 2, n.y + n.height / 2);
        await page.locator(`.node[data-node="${SW1}"]`).focus();
        await page.keyboard.press('ContextMenu');
        await page.locator('[data-region="radial"] .wedge').nth(1).click();
        await page.locator('.object-card').waitFor();
        check('the object card opens beside it', await win(page).isVisible());
        await page.locator('.object-card [data-act="close"]').click();
        await region(page, 'overview').click();
        check('the overview opens beside it', await win(page).isVisible() && await region(page, 'overviewPanel').isVisible());
        await region(page, 'overviewClose').click();

        // ---- dragged by the title, kept inside the scheme
        const head = region(page, 'assignmentsHead');
        let h = await box(head);
        await page.mouse.move(h.x + 60, h.y + 20);
        await page.mouse.down();
        await page.mouse.move(h.x - 300, h.y + 160, {steps: 6});
        await page.mouse.up();
        const moved = await box(win(page));
        check('the title drags it', Math.abs(moved.x - (b.x - 360)) <= 2 && Math.abs(moved.y - (b.y + 140)) <= 2,
            JSON.stringify({b, moved}));
        h = await box(head);
        await page.mouse.move(h.x + 60, h.y + 20);
        await page.mouse.down();
        await page.mouse.move(h.x + 60, h.y + 2000, {steps: 4});
        await page.mouse.up();
        h = await box(head);
        check('the title cannot leave the scheme', h.y + h.h <= area.y + area.h + 1, JSON.stringify({h, area}));
        await page.mouse.move(h.x + 60, h.y + 20);
        await page.mouse.down();
        await page.mouse.move(moved.x + 60, moved.y + 20, {steps: 4});
        await page.mouse.up();

        // ---- resize by the corner is remembered
        b = await box(win(page));
        await page.mouse.move(b.x + b.w - 3, b.y + b.h - 3);
        await page.mouse.down();
        await page.mouse.move(b.x + b.w + 77, b.y + b.h - 63, {steps: 5});
        await page.mouse.up();
        const sized = await box(win(page));
        check('the corner resizes it', Math.abs(sized.w - (b.w + 80)) <= 3 && Math.abs(sized.h - (b.h - 60)) <= 3,
            JSON.stringify({b, sized}));

        // ---- folds to the title and back
        await region(page, 'assignmentsFold').click();
        b = await box(win(page));
        check('folded to its title', b.h <= 42 && await region(page, 'assignmentsBody').isHidden(), JSON.stringify(b));
        check('the fold button offers to unfold', (await region(page, 'assignmentsFold').getAttribute('aria-expanded')) === 'false');
        await page.screenshot({path: path.join(SHOTS, 'assignment-folded.png')});
        await region(page, 'assignmentsOpen').click();
        check('the toolbar button unfolds a folded window', (await box(win(page))).h > 100);
        await head.dblclick({position: {x: 60, y: 20}});
        check('a double click on the title folds it', await region(page, 'assignmentsBody').isHidden());
        await head.dblclick({position: {x: 60, y: 20}});
        check('...and unfolds it', await region(page, 'assignmentsBody').isVisible());

        // ---- a long assignment keeps its scroll, the place and the fold survive a reload
        await region(page, 'assignmentsBody').evaluate(body => {
            const extra = document.createElement('div');
            extra.style.height = '3000px';
            body.appendChild(extra);
            body.scrollTop = 400;
        });
        await sleep(400);
        const before = await box(win(page));
        await page.reload();
        await region(page, 'layerSeg').waitFor();
        await sleep(300);
        check('open after a reload', await win(page).isVisible());
        const after = await box(win(page));
        check('...at the same place and size', JSON.stringify(after) === JSON.stringify(before), JSON.stringify({before, after}));
        await region(page, 'assignmentsBody').evaluate(body => {
            const extra = document.createElement('div');
            extra.style.height = '3000px';
            body.appendChild(extra);
        });
        await region(page, 'assignmentsFold').click();
        await region(page, 'assignmentsFold').click();
        check('...scrolled where it was', Math.abs(await until(() => region(page, 'assignmentsBody')
            .evaluate(body => body.scrollTop || null), 2000) - 400) <= 2);
        await region(page, 'assignmentsFold').click();
        await page.reload();
        await region(page, 'layerSeg').waitFor();
        check('folded after a reload when it was folded', await win(page).isVisible()
            && await region(page, 'assignmentsBody').isHidden());
        await region(page, 'assignmentsFold').click();

        // ---- one order with the console windows
        // The window may cover the node: closed while the console opens, it comes back where it was.
        await region(page, 'assignmentsOpen').click();
        await region(page, 'start').click();
        await page.locator(`.node[data-node="${SW1}"].st-started`).waitFor({timeout: 15000});
        await page.locator('[data-region="zoomFit"]').click();
        await sleep(300);
        const s = await sw.boundingBox();
        await page.locator('[data-region="modes"] button[data-mode="windows"]').click();
        const con = page.locator('.wins .win').first();
        // Consoles open once the lab runs, a moment after the nodes are started.
        await until(async() => {
            await page.mouse.dblclick(s.x + s.width / 2, s.y + s.height / 2);
            await sleep(400);
            return await con.count() > 0;
        }, 20000);
        await con.waitFor();
        await sleep(300);
        await region(page, 'assignmentsOpen').click();
        check('reopened where it was', JSON.stringify(await box(win(page))) === JSON.stringify(after));
        // Put the console window over the assignment.
        const a = await box(win(page));
        await con.evaluate((w, p) => Object.assign(w.style, {left: (p.x - w.parentElement.getBoundingClientRect().left - 200) + 'px',
            top: (p.y - w.parentElement.getBoundingClientRect().top + 60) + 'px'}), a);
        const top = (x, y) => page.evaluate(([x, y]) => {
            const e = document.elementFromPoint(x, y);
            return e.closest('.assign-win') ? 'assignment' : e.closest('.win') ? 'console' : 'other';
        }, [x, y]);
        const cb = await box(con);
        const overlap = [Math.max(a.x, cb.x) + 20, Math.max(a.y, cb.y) + 50];
        await page.mouse.click(cb.x + 30, cb.y + 10);
        check('a click brings the console window to the front', (await top(...overlap)) === 'console');
        await head.click({position: {x: 30, y: 10}});
        check('a click brings the assignment to the front', (await top(...overlap)) === 'assignment');
        await page.screenshot({path: path.join(SHOTS, 'assignment-console.png')});
        const hud = await box(region(page, 'canvasActions'));
        check('the HUD buttons stay below the windows', ['console', 'assignment'].includes(await top(hud.x + 10, hud.y + 10))
            || !(a.x < hud.x + 10 && a.x + a.w > hud.x + 10), 'not covered: the window is elsewhere');

        // ---- Escape closes it
        await region(page, 'assignmentsBody').click({position: {x: 10, y: 10}});
        await page.keyboard.press('Escape');
        check('Escape closes it', await win(page).isHidden());
        check('...and the button says so', (await region(page, 'assignmentsOpen').getAttribute('aria-expanded')) === 'false');
        await page.reload();
        await region(page, 'layerSeg').waitFor();
        check('closed after a reload when it was closed', await win(page).isHidden());

        // ---- a phone: a sheet above the zoom controls
        const phone = watch(await (await browser.newContext({viewport: {width: 390, height: 844}, hasTouch: true})).newPage());
        await phone.goto(url + '&theme=dark');
        await region(phone, 'layerSeg').waitFor();
        await region(phone, 'assignmentsOpen').click();
        await sleep(300);
        const sheet = await box(win(phone)), zoom = await box(phone.locator('.zoom'));
        check('phone: a sheet across the screen above the zoom controls', sheet.x >= 0 && sheet.x + sheet.w <= 390
            && sheet.y + sheet.h <= zoom.y, JSON.stringify({sheet, zoom}));
        await phone.screenshot({path: path.join(SHOTS, 'assignment-phone.png')});
        await region(phone, 'assignmentsHead').tap({position: {x: 60, y: 20}});
        const folded = await box(win(phone));
        check('phone: a tap on the title folds it to a bar', folded.h <= 42 && folded.y + folded.h <= zoom.y, JSON.stringify(folded));
        await region(phone, 'assignmentsHead').tap({position: {x: 60, y: 20}});
        check('phone: another tap unfolds it', (await box(win(phone))).h > 100);

        check('no errors in the page', problems.length === 0, problems.join(' | '));
    } catch (e) {
        check('the run finished', false, e.stack || String(e));
    } finally {
        await browser.close();
        server.kill();
    }
    process.exitCode = failed;
})();
