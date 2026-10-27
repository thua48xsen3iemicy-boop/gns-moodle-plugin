'use strict';
/**
 * Checks the segments of the lab editor (docs/lab-constructor/README.md §6, §13.2) in Chromium, end to end: a segment is
 * made on the canvas from two Debian and a VPCS, the addresses appear in the panel and in the preview (facts.js, layer L3),
 * the stub of the lab manager (stubs/ajax.js) returns a finding and notes, the definition goes out as lab/2 and, without
 * segments, as lab/1. A manager without the lab constructor shows no segments.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_editor.js [--min] [--shots <dir>]
 *
 * It starts the static server of server.js itself (port 18126). --min runs the minified modules of plugin/amd/build.
 * Playwright with Chromium must be installed (require('playwright')). The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18126;
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

// Node indexes of the editor lab (build.js EDITOR_LAB): SW1, PC1, PC2, PC3, R1.
const [SW1, PC1, PC2, PC3, R1] = [0, 1, 2, 3, 4];
const tap = (page, i) => page.locator(`.node[data-node="${i}"] .halo`).click();
const act = (page, name) => page.locator(`[data-act="${name}"]`).first();
const texts = (page, selector) => page.locator(selector).allInnerTexts().then(list => list.map(t => t.replace(/\s+/g, ' ').trim()));
// SVG elements have no innerText.
const svgTexts = (page, selector) => page.locator(selector).allTextContents().then(list => list.map(t => t.trim()));
const saved = page => page.evaluate(() => JSON.parse(JSON.stringify(window.__stub.editor.saved)));

(async() => {
    fs.mkdirSync(SHOTS, {recursive: true});
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch();
    try {
        const ready = await until(async() => (await get(`${BASE}/editor.en.html`)) === 200, 10000);
        check('the harness server is up', !!ready);
        if (!ready) {
            throw new Error('server did not start');
        }
        const context = await browser.newContext({viewport: {width: 1440, height: 860}});
        const page = await context.newPage();
        const problems = [];
        page.on('pageerror', e => problems.push('pageerror: ' + e.message));
        page.on('console', m => {
            if (m.type() === 'error') {
                problems.push('console: ' + m.text());
            }
        });

        // ---- a manager without the lab constructor: no segments, no preview
        await page.goto(`${BASE}/editor.en.plain.html${MIN}`);
        await until(async() => (await page.locator('.node').count()) === 5);
        check('without the lab constructor there is no segments panel and no preview button',
            (await page.locator('[data-act="addsegment"]').count()) === 0 && (await page.locator('[data-act="preview"]').count()) === 0);

        // ---- make a segment on the canvas
        await page.goto(`${BASE}/editor.en.html${MIN}`);
        await until(async() => (await page.locator('.node').count()) === 5);
        check('the editor opens the lab with the segments panel', (await page.locator('[data-act="addsegment"]').count()) === 1);
        check('the preview button waits for a segment', await act(page, 'preview').isDisabled());
        await act(page, 'addsegment').click();
        check('a segment LAN1 is selected, with a default subnet', (await page.locator('.ed-props h2').innerText()) === 'LAN1'
            && (await page.locator('[data-seg="subnet"]').inputValue()) === '192.168.10.0/24');
        check('the hint asks to tap devices', /Tap devices/.test(await page.locator('[data-region="hint"]').innerText()));

        await tap(page, PC1);
        await tap(page, SW1);
        check('a switch cannot join: the image has no preconfig, the toast says why',
            /cannot join a segment/.test(await page.locator('[data-region="toast"]').innerText()));
        await tap(page, R1);
        check('...nor a router as a member', (await page.locator('.ed-members tr').count()) === 1);
        await tap(page, PC2);
        const pick = await until(async() => (await page.locator('[data-pick] option').count()) === 2);
        check('PC2 has two free ports: the panel asks for one', !!pick);
        await page.locator('[data-pick]').selectOption('0/0');
        await act(page, 'pickadd').click();
        await tap(page, PC3);
        const rows = await texts(page, '.ed-members tr');
        check('three members with .1 .2 .3 of the subnet', rows.length === 3 && rows[0].includes('PC1') && rows[0].includes('192.168.10.1/24')
            && rows[1].includes('PC2') && rows[1].includes('Ethernet0') && rows[1].includes('192.168.10.2/24')
            && rows[2].includes('PC3') && rows[2].includes('192.168.10.3/24'), JSON.stringify(rows));
        check('the members are marked on the canvas', (await page.locator('.node.member').count()) === 3);

        // ---- VLAN, gateway
        await page.locator('[data-seg="vlan"]').fill('10');
        await page.locator('[data-seg="vlan"]').dispatchEvent('change');
        const options = await texts(page, '[data-seg="gateway"] option');
        check('the gateway can be a free port of a router only', options.length === 9 && options.slice(1).every(o => o.startsWith('R1 · Ethernet')),
            JSON.stringify(options));
        await page.locator('[data-seg="gateway"]').selectOption({label: 'R1 · Ethernet0/0'});
        check('the gateway is marked on the canvas', (await page.locator('.node.gw').count()) === 1);
        check('the gateway is the last working address', await page.locator('[data-seg="gwhost"]').getAttribute('placeholder') === '192.168.10.254');
        await page.locator('[data-seg="subnet"]').fill('192.168.10.5/24');
        await page.locator('[data-seg="subnet"]').dispatchEvent('change');
        check('a subnet with host bits is refused on the spot', /Enter a network/.test(await page.locator('[data-region="fielderror"]').innerText())
            && (await page.locator('[data-seg="subnet"]').inputValue()) === '192.168.10.0/24');

        // ---- the preview
        await act(page, 'preview').click();
        const ips = await until(async() => {
            const list = await svgTexts(page, '.pv-ip');
            return list.length === 3 && list;
        });
        check('the preview shows the host part of every address', JSON.stringify(ips && ips.sort()) === JSON.stringify(['.1', '.2', '.3']),
            JSON.stringify(ips));
        check('...and the subnet with the name of the segment', (await svgTexts(page, '.pv-subnet')).join() === 'LAN1 192.168.10.0/24');
        check('...and the panel says there are no conflicts', /no address conflicts/.test(await page.locator('.ed-props').innerText()));
        await page.screenshot({path: path.join(SHOTS, 'editor-1-preview.png')});

        // ---- the manager (stub) finds a repeated address
        await page.locator('[data-mhost="0"]').fill('5');
        await page.locator('[data-mhost="0"]').dispatchEvent('change');
        await page.locator('[data-mhost="1"]').fill('5');
        await page.locator('[data-mhost="1"]').dispatchEvent('change');
        check('the preview already says the address is used twice, before the lab manager is asked', await until(async() =>
            /PC2\s*: the address is used twice/.test(await page.locator('.ed-props').innerText())
            && (await page.locator('.ed-members tr.bad').count()) === 1), '');
        await act(page, 'check').click();
        const finding = await until(async() => (await texts(page, '.ed-props .ed-problems:not(.warn):not(.seglist) li'))
            .find(t => t.includes('used twice')));
        check('the finding names the segment and the member', /^LAN1 · PC2\s*: the address is used twice$/.test(finding), String(finding));
        await page.locator('.ed-props .ed-problems:not(.warn):not(.seglist) .rowbtn').first().click();
        check('...it leads to the segment, where the row of the member is marked', (await page.locator('.ed-members tr.bad').count()) === 1
            && (await texts(page, '.ed-members tr.bad'))[0].startsWith('PC2'));
        await page.screenshot({path: path.join(SHOTS, 'editor-2-finding.png')});
        await page.locator('[data-mhost="1"]').fill('');
        await page.locator('[data-mhost="1"]').dispatchEvent('change');
        check('a change clears the findings', (await page.locator('.ed-members tr.bad').count()) === 0);
        await act(page, 'check').click();
        const notes = await until(async() => {
            const list = await texts(page, '.ed-props .ed-problems.warn li');
            return list.length === 2 && list;
        });
        check('a valid lab brings the notes of the manager apart from the findings', !!notes
            && notes.some(t => t.startsWith('LAN1 · Gateway') && t.includes('the student gives it this address'))
            && notes.some(t => /^LAN1\s*:/.test(t) && t.includes('the student sets the VLAN')), JSON.stringify(notes));
        check('...and the resources', /needs 3 GB of memory/.test(await page.locator('.ed-props').innerText()));

        // ---- save as lab/2
        await act(page, 'save').click();
        const out = await until(async() => saved(page));
        check('the definition goes out as lab/2 with the segment', !!out && out.format === 'gns3lab.lab/2' && out.segments.length === 1
            && out.segments[0].name === 'LAN1' && out.segments[0].vlan.fixed === 10 && out.segments[0].subnet.fixed === '192.168.10.0/24'
            && out.segments[0].gateway.node === 'R1' && out.segments[0].members.map(m => m.node).join() === 'PC1,PC2,PC3'
            && out.segments[0].members[0].host === 5, JSON.stringify(out && out.segments));

        // ---- renaming and deleting devices keeps the segments sound
        await tap(page, PC3);
        await page.locator('[data-field="name"]').fill('HOST3');
        await page.locator('[data-field="name"]').dispatchEvent('change');
        await act(page, 'save').click();
        await sleep(150);
        check('a renamed device is renamed in its segment', (await saved(page)).segments[0].members[2].node === 'HOST3');
        await page.locator('[data-act="delete"]').first().click();
        await act(page, 'save').click();
        await sleep(150);
        const after = await saved(page);
        check('a deleted device leaves its segment', after.segments[0].members.map(m => m.node).join() === 'PC1,PC2'
            && after.nodes.length === 4);

        // ---- no segments: lab/1 as before
        await page.locator('.node[data-node="0"] .halo').click({position: {x: 1, y: 1}}).catch(() => null);
        await page.keyboard.press('Escape');
        await page.locator('.seglist .rowbtn').first().click();
        await page.locator('[data-act="delete"]').first().click();
        await act(page, 'save').click();
        await sleep(150);
        const plain = await saved(page);
        check('without segments the definition goes out as lab/1, as before', plain.format === 'gns3lab.lab/1' && !('segments' in plain),
            JSON.stringify(Object.keys(plain)));
        check('...and the preview button waits again', await act(page, 'preview').isDisabled());
        await act(page, 'undo').click();
        check('undo brings the segment back', (await page.locator('.seglist .rowbtn').count()) === 1);

        // ---- a lab that already has a segment, in Russian and in the dark theme
        await page.goto(`${BASE}/editor.ru.segments.html${MIN}${MIN ? '&' : '?'}theme=dark`);
        await until(async() => (await page.locator('.node').count()) === 5);
        const list = await texts(page, '.seglist .rowbtn');
        check('the stored segment is listed (ru)', list.length === 1 && list[0].startsWith('LAN1 192.168.10.0/24 · VLAN 10'), JSON.stringify(list));
        check('the strings are Russian', (await act(page, 'addsegment').innerText()) === 'Добавить сегмент');
        await act(page, 'preview').click();
        await sleep(150);
        check('the preview works on an opened lab', (await svgTexts(page, '.pv-ip')).length === 2);
        await page.screenshot({path: path.join(SHOTS, 'editor-3-dark-ru.png')});

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
