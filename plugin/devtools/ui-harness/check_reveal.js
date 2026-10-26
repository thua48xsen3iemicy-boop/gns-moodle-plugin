'use strict';
/**
 * Checks the modes `seen` and `fog` of the scheme (docs/lab-constructor/README.md §2, §12) in Chromium, end to end:
 *
 *   keys in the terminal → fake gateway of server.js → xterm.js → the watch of the page → the mask (reveal.js) →
 *   the scheme and the cards, and the mask on its way to the stub of mod_gns3lab_reveal and back after a reload.
 *
 * It starts the fake gateway itself and drives the page with Playwright. Screenshots go to --shots (default dist/shots).
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_reveal.js [--min] [--shots <dir>]
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

const PORT = 18125;
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
const NAMES = demo.nodes.map(n => n.name);
const [R1, SW1, PC1, PC2] = ['R1', 'SW1', 'PC1', 'PC2'].map(idOf);
const LINK_PC1 = 'l-06';
const URL = (name, query = '') => `${BASE}/index.en.${name}.html?fast${MIN ? '&min' : ''}${query}`;

const node = (page, id) => page.locator(`.node[data-node="${id}"]`);
const silhouettes = page => page.locator('.node.silhouette').count();
const drawnNodes = page => page.locator('.node').count();
const drawnLinks = page => page.locator('.link').count();
const calls = page => page.evaluate(() => ({...window.__stub.calls}));
const uncovered = page => page.evaluate(() => window.__stub.reveal.items());
const sceneText = page => page.locator('[data-region="svg"]').evaluate(e => e.textContent);
const nameOf = (page, id) => node(page, id).locator('.nname').evaluate(e => e.textContent).catch(() => null);

/**
 * Opens the console of a node the way a student does: a double click on it.
 *
 * @param {Page} page page
 * @param {string} id node id
 */
async function openConsole(page, id) {
    const box = await node(page, id).locator('circle.halo').boundingBox();
    await page.mouse.dblclick(box.x + box.width / 2, box.y + box.height / 2);
}

const screenText = page => page.evaluate(() => Array.from(document.querySelectorAll('.xterm-rows')).map(e => e.innerText).join('\n'));

/**
 * Opens the card of a node from its wheel (the keyboard way: the key ContextMenu on the focused node).
 *
 * @param {Page} page page
 * @param {string} id node id
 * @param {number} [wedges] how many actions the wheel must have
 */
async function openCard(page, id, wedges = 0) {
    await node(page, id).focus();
    await page.keyboard.press('ContextMenu');
    const wheel = page.locator('[data-region="radial"] .wedge');
    await wheel.first().waitFor();
    if (wedges) {
        check('the wheel of a silhouette has the console and the card only', (await wheel.count()) === wedges, String(await wheel.count()));
    }
    await wheel.nth(1).click();
    await page.locator('[data-region="details"]:not([hidden])').waitFor();
}

/**
 * Types a command into the terminal that has the focus and waits for the prompt after it.
 *
 * @param {Page} page page
 * @param {string} command command
 * @param {RegExp} prompt what the screen ends with when the command is done
 */
async function run(page, command, prompt) {
    await page.keyboard.type(command);
    await page.keyboard.press('Enter');
    const done = await until(async() => prompt.test((await screenText(page)).trimEnd()));
    check(`the device answers ${command}`, !!done);
}

/**
 * Starts the lab of the page and waits until the scheme is the live one.
 *
 * @param {Page} page page
 */
async function start(page) {
    await page.locator('[data-region="start"]').click();
    await page.locator('[data-region="status"]', {hasText: 'The lab is running'}).waitFor({timeout: 15000});
    await page.locator(`.node[data-node="${PC1}"].st-started, .node[data-node="${PC1}"].silhouette`).first().waitFor();
    await sleep(500);
}

/**
 * A page with its errors collected.
 *
 * @param {Browser} browser browser
 * @param {string[]} problems where the errors go
 * @param {object} [options] context options
 * @returns {Promise<Page>}
 */
async function newPage(browser, problems, options = {}) {
    const context = await browser.newContext({viewport: {width: 1440, height: 860}, ...options});
    const page = await context.newPage();
    page.on('pageerror', e => problems.push('pageerror: ' + e.message));
    page.on('console', m => {
        if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) {
            problems.push('console: ' + m.text());
        }
    });
    return page;
}

(async() => {
    fs.mkdirSync(SHOTS, {recursive: true});
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch();
    const problems = [];
    try {
        const ready = await until(async() => (await get(`${BASE}/gw/stats`)) === 200, 10000);
        check('the fake gateway is up', !!ready);
        if (!ready) {
            throw new Error('server did not start');
        }

        /* ---------------- fog, every node a silhouette ---------------- */
        const page = await newPage(browser, problems);
        await page.goto(URL('fog'));
        await page.locator('.node').first().waitFor();
        check('before the lab runs the reference scheme is in fog too: every node a silhouette',
            (await silhouettes(page)) === NAMES.length, String(await silhouettes(page)));
        await start(page);
        check('the live scheme: all ' + NAMES.length + ' nodes are silhouettes', (await silhouettes(page)) === NAMES.length);
        check('...with no cable', (await drawnLinks(page)) === 0);
        const text = await sceneText(page);
        check('...and no name on the scheme', NAMES.every(n => !text.includes(n)), JSON.stringify(text.slice(0, 80)));
        check('the mode is announced in the title bar', (await page.locator('.demo', {hasText: 'Fog'}).count()) === 1);
        await page.screenshot({path: path.join(SHOTS, 'reveal-1-fog.png')});

        // the overview does not name them either
        await page.locator('[data-region="overview"]').click();
        const overview = await page.locator('[data-region="details"]').innerText();
        check('the overview names no silhouette', NAMES.every(n => !overview.includes(n)));
        check('...and counts them as not explored', /Not explored yet\s*\n?\s*11/.test(overview), JSON.stringify(overview.slice(0, 200)));
        await page.keyboard.press('Escape');

                // the card of a silhouette, from the wheel (the key ContextMenu opens it): console and card, nothing else
        await openCard(page, R1, 2);
        const card = await page.locator('[data-region="details"]').innerText();
        check('the card of a silhouette says "Unknown device" and offers the console',
            /unknown device/i.test(card) && /open (the )?console/i.test(card) && !card.includes('R1'), JSON.stringify(card.slice(0, 160)));
        await page.keyboard.press('Escape');

        // ---- the first output lifts the fog from the node, ip shows its interface, the cable shows the neighbour
        await openConsole(page, PC1);
        check('the console of a silhouette opens and shows its prompt',
            !!(await until(async() => /PC1>$/.test((await screenText(page)).trimEnd()))));
        const tab = await page.locator('.tab').first().innerText();
        check('the tab of a silhouette is a question mark', tab.trim() === '?', JSON.stringify(tab));
        await sleep(400);
        await run(page, 'show ip', /PC1>$/);
        const opened = await until(async() => (await nameOf(page, PC1)) === 'PC1', 3000);
        check('the command lifts the fog from PC1: its name is there', !!opened);
        check('...the tab of its console has the name now',
            !!(await until(async() => (await page.locator('.tab').first().innerText()).trim() === 'PC1', 3000)));
        check('the cable PC1 - SW1 is uncovered', (await page.locator(`.link[data-link="${LINK_PC1}"]`).count()) === 1);
        check('SW1 is found: it has its name', (await nameOf(page, SW1)) === 'SW1');
        check('...the other nodes are still silhouettes', (await silhouettes(page)) === NAMES.length - 2);
        check('...and there is one cable', (await drawnLinks(page)) === 1);
        const ports = await page.locator('.plabel').allInnerTexts();
        check('the port name is shown at the PC1 end only', ports.length === 1, JSON.stringify(ports));
        await page.screenshot({path: path.join(SHOTS, 'reveal-2-pc1.png')});

        // the card of the found node
        await page.keyboard.press('Escape');
        await openCard(page, SW1);
        const found = await page.locator('[data-region="details"]').innerText();
        check('the card of a found node says it was found by a cable', /Found by a cable/.test(found), JSON.stringify(found.slice(0, 200)));

        // the mask reaches the server in one call, with the right items
        const sent = await until(async() => (await calls(page)).mod_gns3lab_reveal >= 1, 4000);
        check('the page wrote the mask to the server', !!sent, JSON.stringify(await calls(page)));
        const items = await uncovered(page);
        const has = (kind, nodeid, item = '') => items.some(i => i.kind === kind && i.nodeid === nodeid && i.item === item);
        check('...node PC1, found SW1, interface e0, group l3 and the cable', has('node', PC1) && has('known', SW1)
            && has('iface', PC1, 'e0') && has('group', PC1, 'l3') && has('link', '', LINK_PC1), JSON.stringify(items));
        check('...and nothing that was not uncovered', !items.some(i => i.nodeid === R1));
        await page.screenshot({path: path.join(SHOTS, 'reveal-3-card.png')});

        // ---- the page is reloaded: what is uncovered stays
        await page.reload();
        check('after a reload the preview keeps PC1 and SW1 uncovered',
            !!(await until(async() => (await nameOf(page, PC1)) === 'PC1' && (await nameOf(page, SW1)) === 'SW1', 4000)));
        await start(page);
        check('...and so does the live scheme', (await nameOf(page, PC1)) === 'PC1' && (await drawnLinks(page)) === 1
            && (await silhouettes(page)) === NAMES.length - 2);

        // ---- a router: show ip interface brief uncovers its interfaces, the cables that work, the neighbours
        await page.keyboard.press('Escape');
        await openConsole(page, R1);
        check('the console of R1 shows a prompt', !!(await until(async() => /R1#$/.test((await screenText(page)).trimEnd()))));
        await sleep(400);
        await run(page, 'show ip interface brief', /R1#$/);
        const router = await until(async() => (await nameOf(page, R1)) === 'R1', 3000);
        check('R1 is uncovered by its output', !!router);
        const r1 = await until(async() => (await drawnLinks(page)) >= 3, 3000);
        check('its cables to the NAT cloud and R2 are uncovered', !!r1, String(await drawnLinks(page)));
        const nat = idOf('NAT1');
        check('NAT1 has no console: it is found, and its port on the cable is public',
            (await nameOf(page, nat)) === 'NAT1' && (await page.locator(`.node[data-node="${nat}"].silhouette`).count()) === 0);
        check('the nodes behind are still hidden from the plan (silhouettes)', (await silhouettes(page)) === NAMES.length - 5,
            String(await silhouettes(page)));
        await page.screenshot({path: path.join(SHOTS, 'reveal-4-router.png')});
        await page.close();

        /* ---------------- fog: an interface that is down, then raised ---------------- */
        const down = await newPage(browser, problems);
        await down.goto(URL('fog'));
        await down.locator('.node').first().waitFor();
        await down.evaluate(() => window.__stub.reveal.clear());
        await down.reload();
        await start(down);
        await down.evaluate(([n]) => window.__stub.facts.patch(n, 'e0', {admin: 'down'}), [PC1]);
        await down.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
        await sleep(600);
        await openConsole(down, PC1);
        await until(async() => /PC1>$/.test((await screenText(down)).trimEnd()));
        await sleep(400);
        await run(down, 'show ip', /PC1>$/);
        await until(async() => (await nameOf(down, PC1)) === 'PC1', 3000);
        await sleep(600);
        check('the interface of PC1 is shut down: the node is open, no cable is uncovered',
            (await nameOf(down, PC1)) === 'PC1' && (await drawnLinks(down)) === 0 && (await nameOf(down, SW1)) !== 'SW1');
        await down.evaluate(([n]) => window.__stub.facts.patch(n, 'e0', {admin: 'up'}), [PC1]);
        await down.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
        const raised = await until(async() => (await drawnLinks(down)) === 1, 5000);
        check('the interface is raised (the deciding facts say so): the cable is uncovered without another command', !!raised);
        check('...and the neighbour is found', (await nameOf(down, SW1)) === 'SW1');
        await down.close();

        /* ---------------- the consoles as a grid: the tiles are drawn again when a node is uncovered ---------------- */
        const grid = await newPage(browser, problems);
        await grid.goto(URL('fog'));
        await grid.locator('.node').first().waitFor();
        await grid.evaluate(() => window.__stub.reveal.clear());
        await grid.reload();
        await start(grid);
        await grid.locator('[data-region="modes"] [data-mode="grid"]').click();
        await openConsole(grid, PC1);
        await until(async() => /PC1>$/.test((await screenText(grid)).trimEnd()));
        await sleep(400);
        const tile = grid.locator(`.tile[data-con="${PC1}"] .nm`);
        check('in the grid the tile of a silhouette is a question mark', (await tile.innerText()).startsWith('?'));
        await run(grid, 'show ip', /PC1>$/);
        check('...and gets the name when the node is uncovered', !!(await until(async() => (await tile.innerText()).startsWith('PC1'), 3000)));
        // The tile was drawn again: the keyboard types into the same terminal.
        await run(grid, 'show ip', /PC1>$/);
        await grid.close();

        /* ---------------- fog with a starting node ---------------- */
        const only = await newPage(browser, problems);
        await only.goto(URL('fog-start'));
        await start(only);
        check('a starting node: only PC1 is drawn, as a silhouette', (await drawnNodes(only)) === 1 && (await silhouettes(only)) === 1,
            String(await drawnNodes(only)));
        check('...no hidden node is anywhere on the scheme', (await only.locator(`.node[data-node="${R1}"]`).count()) === 0);
        await openConsole(only, PC1);
        await until(async() => /PC1>$/.test((await screenText(only)).trimEnd()));
        await sleep(400);
        await run(only, 'show ip', /PC1>$/);
        const second = await until(async() => (await drawnNodes(only)) === 2, 3000);
        check('the cable of PC1 shows SW1: two nodes now, SW1 by name', !!second && (await nameOf(only, SW1)) === 'SW1');
        check('...the rest is still hidden', (await only.locator(`.node[data-node="${R1}"]`).count()) === 0);
        // A node that is not found has no ticket (classes/external/get_console_ticket.php).
        const refused = await only.evaluate(async id => {
            try {
                await window.__stub.call('mod_gns3lab_get_console_ticket', {nodeid: id});
                return 'ticket';
            } catch (e) {
                return e.errorcode || String(e);
            }
        }, R1).catch(() => 'n/a');
        check('...and the server gives no ticket for a node that is not found (stub of the rule)', refused === 'n/a' || refused === 'nodenotfound', refused);
        await only.screenshot({path: path.join(SHOTS, 'reveal-5-fogstart.png')});
        await only.close();

        /* ---------------- seen ---------------- */
        const seen = await newPage(browser, problems);
        await seen.goto(URL('seen'));
        await start(seen);
        check('seen: every node is there with its name, every cable is there', (await silhouettes(seen)) === 0
            && (await nameOf(seen, R1)) === 'R1' && (await drawnLinks(seen)) === demo.links.length, String(await drawnLinks(seen)));
        await seen.locator('[data-region="layerSeg"]:not([hidden])').waitFor({timeout: 5000}).catch(() => null);
        await seen.locator('[data-region="overview"]').click();
        await seen.locator(`.rowbtn[data-goto-node="${PC1}"]`).click();
        const layerButton = seen.locator('[data-layer="l3"]');
        check('seen: the layers are offered', (await layerButton.count()) === 1 && !(await layerButton.isDisabled()));
        await layerButton.click();
        const before = await seen.locator('[data-region="details"]').innerText();
        check('seen: before any command the addresses of PC1 are not explored', /Not explored yet/.test(before)
            && !/10\.1\.10\./.test(before), JSON.stringify(before.slice(0, 160)));
        await openConsole(seen, PC1);
        await until(async() => /PC1>$/.test((await screenText(seen)).trimEnd()));
        await sleep(400);
        await run(seen, 'show ip', /PC1>$/);
        // The overview of a layer lists its subnets, not the nodes: back to L1 for the list, then to the layer again.
        await seen.keyboard.press('Escape');
        await seen.locator('[data-layer="l1"]').click();
        await seen.locator('[data-region="overview"]').click();
        await seen.locator(`.rowbtn[data-goto-node="${PC1}"]`).click();
        await seen.locator('[data-layer="l3"]').click();
        await sleep(300);
        const after = await seen.locator('[data-region="details"]').innerText();
        check('seen: show ip uncovers the addresses of PC1', /10\.1\.10\.77/.test(after) && !/Not explored yet/.test(after),
            JSON.stringify(after.slice(0, 200)));
        await seen.locator('[data-layer="l2"]').click();
        check('seen: ...and only the addresses: L2 of PC1 is still not explored',
            /Not explored yet/.test(await seen.locator('[data-region="details"]').innerText()));
        const sent2 = await until(async() => (await uncovered(seen)).some(i => i.kind === 'group' && i.item === 'l3'), 4000);
        check('seen: the group l3 of PC1 is on the server', !!sent2);
        check('seen: a node is not "opened" by seen - no node items', !(await uncovered(seen)).some(i => i.kind === 'node'));
        await seen.screenshot({path: path.join(SHOTS, 'reveal-6-seen.png')});
        await seen.close();

        /* ---------------- a teacher ---------------- */
        const teacher = await newPage(browser, problems);
        await teacher.goto(URL('fog', '&observer'));
        await teacher.locator('.node').first().waitFor();
        await teacher.evaluate(([a, b, link]) => sessionStorage.setItem('gns3lab-stub-reveal', JSON.stringify([
            {nodeid: a, kind: 'node', item: ''}, {nodeid: b, kind: 'known', item: ''}, {nodeid: '', kind: 'link', item: link},
        ])), [PC1, SW1, LINK_PC1]);
        await teacher.reload();
        await teacher.locator(`.node[data-node="${PC1}"]`).waitFor();
        await sleep(600);
        check('a teacher sees everything: no silhouette, all names, all cables', (await silhouettes(teacher)) === 0
            && (await nameOf(teacher, R1)) === 'R1' && (await drawnLinks(teacher)) === demo.links.length);
        const asStudent = teacher.locator('[data-region="asStudent"]');
        check('...and has the switch "As the student sees it"', await asStudent.isVisible());
        check('...which asks the server for the mask of the student, not before', (await calls(teacher)).mod_gns3lab_get_reveal === undefined);
        await asStudent.click();
        const masked = await until(async() => (await silhouettes(teacher)) === NAMES.length - 2, 4000);
        check('the switch shows what the student sees: PC1 and SW1 uncovered, the rest silhouettes', !!masked);
        check('...with the one cable the student has', (await drawnLinks(teacher)) === 1);
        check('...and no write to the server by the teacher', (await calls(teacher)).mod_gns3lab_reveal === undefined);
        await asStudent.click();
        check('the switch off: everything again', !!(await until(async() => (await silhouettes(teacher)) === 0
            && (await drawnLinks(teacher)) === demo.links.length, 3000)));
        await teacher.close();

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
