'use strict';
/**
 * Checks the texts and boxes a teacher puts on the scheme of a library lab (lab_definition::ANNOTATIONS) in Chromium:
 * in the lab editor a box and a text are added from the bar, moved, resized and styled (size, bold, italic, colours), a
 * right click deletes one and Ctrl+Z brings it back, the saved definition carries them; on the lab page of a student the
 * drawings of the lab (as lab_definition::drawings writes them) are drawn through the sanitizer with their colours and
 * styles, before the start and in the live project, which has no drawings of its own.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_annotations.js [--min] [--shots <dir>]
 *
 * It starts the static server of server.js itself (port 18138). Playwright with Chromium must be installed.
 * The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18138;
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
const saved = page => page.evaluate(() => JSON.parse(JSON.stringify(window.__stub.editor.saved)));

/**
 * Drags from the centre of an element by an offset, with the mouse.
 *
 * @param {Page} page page
 * @param {Locator} locator what to grab
 * @param {number} dx offset in pixels
 * @param {number} dy offset in pixels
 */
async function drag(page, locator, dx, dy) {
    const b = await locator.boundingBox();
    const x = b.x + b.width / 2, y = b.y + b.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + dx / 2, y + dy / 2, {steps: 4});
    await page.mouse.move(x + dx, y + dy, {steps: 4});
    await page.mouse.up();
}

(async() => {
    fs.mkdirSync(SHOTS, {recursive: true});
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch({executablePath: process.env.CHROMIUM_BIN || undefined});
    try {
        const ready = await until(async() => (await get(`${BASE}/editor.ru.html`)) === 200, 10000);
        check('the harness server is up', !!ready);
        if (!ready) {
            throw new Error('server did not start');
        }
        const context = await browser.newContext({viewport: {width: 1440, height: 860}, deviceScaleFactor: 2});
        const page = await context.newPage();
        const problems = [];
        page.on('pageerror', e => problems.push('pageerror: ' + e.message));
        page.on('console', m => {
            if (m.type() === 'error') {
                problems.push('console: ' + m.text());
            }
        });

        // ---- the editor: a box from the bar
        await page.goto(`${BASE}/editor.ru.html${MIN}`);
        await until(async() => (await page.locator('.node').count()) === 5);
        await page.locator('[data-act="addbox"]').click();
        const box = page.locator('.ed-ann.box');
        check('the bar adds a selected box with a caption, under the devices',
            (await box.count()) === 1 && (await box.getAttribute('class')).includes('sel')
            && (await page.locator('.ed-props .eyebrow').textContent()) === 'Область'
            && (await box.locator('text').textContent()) === 'Офис'
            && await page.evaluate(() => document.querySelector('[data-region="notes"]')
                === document.querySelector('[data-region="vp"]').firstElementChild));

        const text = page.locator('[data-nfield="text"]');
        await text.fill('Офис <Москва>');
        check('the caption follows the typing on the scheme', (await box.locator('text').textContent()) === 'Офис <Москва>');
        await text.dispatchEvent('change');
        check('the heading of the panel catches up on change', (await page.locator('.ed-props h2').innerText()) === 'Офис <Москва>');
        await page.locator('.ed-swatch[data-ncolor="fill"][data-value="#388e3c"]').click();
        check('a swatch colours the box and its caption',
            (await box.locator('rect').first().getAttribute('fill')) === '#388e3c'
            && (await box.locator('text').getAttribute('fill')) === '#388e3c');
        const size = page.locator('[data-nfield="size"]');
        await size.fill('18');
        await size.dispatchEvent('change');
        await page.locator('[data-ntoggle="italic"]').click();
        check('size and italic go on the caption',
            (await box.locator('text').getAttribute('font-size')) === '18'
            && (await box.locator('text').getAttribute('font-style')) === 'italic'
            && (await page.locator('[data-ntoggle="italic"]').getAttribute('aria-pressed')) === 'true');
        await size.fill('300');
        await size.dispatchEvent('change');
        check('a size out of range is refused on the spot',
            !(await page.locator('[data-region="fielderror"]').isHidden()) && (await size.inputValue()) === '18');

        const before = await (await page.evaluate(() => JSON.stringify(window.__stub.editor.saved || null)));
        const grip = page.locator('.ed-ann-grip');
        const w0 = +(await box.locator('rect').first().getAttribute('width'));
        await drag(page, grip, 80, 40);
        const w1 = +(await box.locator('rect').first().getAttribute('width'));
        check('the corner handle resizes the box', w1 > w0 + 40, `${w0} → ${w1}`);
        const t0 = await box.getAttribute('transform');
        await drag(page, box.locator('text'), -120, -60);
        const t1 = await box.getAttribute('transform');
        check('dragging moves the box', t0 !== t1, `${t0} → ${t1}`);

        // ---- a text
        await page.locator('[data-act="addtext"]').click();
        const note = page.locator('.ed-ann:not(.box)');
        await page.locator('[data-nfield="text"]').fill('Area 0\nbackbone');
        await page.locator('[data-nfield="text"]').dispatchEvent('change');
        await page.locator('[data-ntoggle="bold"]').click();
        await page.locator('.ed-swatch[data-ncolor="color"][data-value="#d32f2f"]').click();
        check('a text of two lines, bold and red',
            (await note.locator('text').count()) === 2
            && (await note.locator('text').first().getAttribute('font-weight')) === 'bold'
            && (await note.locator('text').first().getAttribute('fill')) === '#d32f2f');
        await page.locator('.ed-swatch[data-ncolor="color"].auto').click();
        check('the theme swatch takes the colour off', (await note.locator('text').first().getAttribute('fill')) === null);
        await page.locator('.ed-swatch[data-ncolor="color"][data-value="#d32f2f"]').click();
        await page.screenshot({path: path.join(SHOTS, 'annotations-editor.png')});

        // ---- a right click deletes, Ctrl+Z brings back
        await note.locator('text').first().click({button: 'right', force: true});
        check('a right click deletes the text', (await note.count()) === 0);
        await page.locator('svg[data-region="svg"]').focus();
        await page.keyboard.press('Control+z');
        check('Ctrl+Z brings it back', (await note.count()) === 1);

        await page.locator('[data-act="save"]').click();
        await until(async() => JSON.stringify(await saved(page)) !== before && (await saved(page))?.annotations);
        const def = await saved(page);
        const [a, b] = (def && def.annotations) || [];
        check('the saved definition carries the box and the text',
            a && a.kind === 'box' && a.text === 'Офис <Москва>' && a.fill === '#388e3c' && a.size === 18 && a.italic === true
            && a.w > 320 && b && b.kind === 'text' && b.text === 'Area 0\nbackbone' && b.bold === true && b.color === '#d32f2f',
            JSON.stringify(def && def.annotations));
        check('a lab without segments stays lab/1', def && def.format === 'gns3lab.lab/1');

        // ---- the lab page of a student
        for (const theme of ['light', 'dark']) {
            await page.goto(`${BASE}/index.ru.notes.html?livenodrawings&fast${MIN ? '&min' : ''}${theme === 'dark' ? '&theme=dark' : ''}`);
            await page.locator('.drawing').first().waitFor();
            const drawn = await page.evaluate(() => [...document.querySelectorAll('.drawing text')].map(t => ({
                text: t.textContent, fill: getComputedStyle(t).fill, style: t.getAttribute('font-style'),
                weight: t.getAttribute('font-weight')})));
            const office = drawn.find(d => d.text === 'Office <Moscow>');
            const plain = drawn.find(d => d.text === 'backbone');
            check(`${theme}: the box and the texts of the lab are drawn with their styles`,
                office && office.weight === 'bold' && office.fill === 'rgb(56, 142, 60)'
                && drawn.some(d => d.text === 'Area 0' && d.style === 'italic' && d.fill === 'rgb(211, 47, 47)'),
                JSON.stringify(drawn));
            const lum = plain ? plain.fill.match(/\d+/g).slice(0, 3).reduce((s, v) => s + +v, 0) / 3 : -1;
            check(`${theme}: a text without a colour follows the theme`, theme === 'light' ? lum >= 0 && lum < 90 : lum > 160,
                plain && plain.fill);
            await page.screenshot({path: path.join(SHOTS, `annotations-student-${theme}.png`)});
        }
        await page.locator('[data-region="start"]').click();
        await page.locator('[data-region="status"]', {hasText: 'Лаба работает'}).waitFor({timeout: 15000});
        await sleep(800);
        check('the live project without drawings keeps those of the lab',
            await page.evaluate(() => [...document.querySelectorAll('.drawing text')].some(t => t.textContent === 'Office <Moscow>')));

        check('no errors in the page', !problems.length, problems.join(' | '));
        await context.close();
    } catch (e) {
        check('the scenario ran to the end', false, e.stack);
    } finally {
        await browser.close();
        server.kill();
    }
    console.log(failed ? `\n${failed} check(s) failed` : '\nall checks passed');
    process.exit(failed);
})();
