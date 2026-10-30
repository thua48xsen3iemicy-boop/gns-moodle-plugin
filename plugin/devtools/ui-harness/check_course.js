'use strict';
/**
 * Checks the course page of a teacher (docs/teacher-dashboard.md) in Chromium, end to end, against the stub of the external
 * functions (stubs/ajax.js: five students, two labs): the table and its counters, the figures of the facts in the cells,
 * "no changes for N min", the search, the sorting, the group, the card (address, findings, devices, the link to the lab,
 * the place for the history), the phone width, a lab the manager did not answer for, a failed call and escaping.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_course.js [--min] [--shots <dir>]
 *
 * --min runs the minified modules of plugin/amd/build instead of amd/src. Playwright with Chromium must be installed
 * (require('playwright')). The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18126;
const BASE = `http://127.0.0.1:${PORT}`;
const MIN = process.argv.includes('--min');
const shotArg = process.argv.indexOf('--shots');
const SHOTS = path.resolve(shotArg > 0 ? process.argv[shotArg + 1] : path.join(__dirname, 'dist/shots'));
const URL = (query = '') => `${BASE}/index.en.course.html?${MIN ? 'min&' : ''}${query}`;

let failed = 0;
const check = (name, ok, detail = '') => {
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !detail ? '' : '  ' + detail}`);
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
async function until(fn, ms = 6000) {
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

/**
 * A page with its errors collected.
 *
 * @param {Browser} browser browser
 * @param {string[]} problems where the errors go
 * @param {object} [options] context options
 * @returns {Promise<Page>}
 */
async function newPage(browser, problems, options = {}) {
    const context = await browser.newContext({viewport: {width: 1280, height: 800}, ...options});
    const page = await context.newPage();
    page.on('pageerror', e => problems.push('pageerror: ' + e.message));
    page.on('console', m => {
        if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) {
            problems.push('console: ' + m.text());
        }
    });
    return page;
}

const rowOf = (page, name) => page.locator('tbody tr', {has: page.locator('th .name', {hasText: name})});
const cellOf = (page, name, lab) => rowOf(page, name).locator('td').nth(lab - 1);
const names = page => page.locator('tbody th .name').allInnerTexts();
const calls = page => page.evaluate(() => ({...window.__stub.calls}));

(async() => {
    fs.mkdirSync(SHOTS, {recursive: true});
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch();
    const problems = [];
    try {
        const ready = await until(async() => (await get(`${BASE}/index.en.course.html`)) === 200, 10000);
        if (!ready) {
            throw new Error('server did not start');
        }

        /* ---------------- the table ---------------- */
        const page = await newPage(browser, problems);
        await page.clock.install();
        await page.goto(URL());
        await page.locator('tbody tr').first().waitFor();
        check('five students in rows, sorted by name', JSON.stringify(await names(page))
            === JSON.stringify(['Alice Ivanova', 'Boris Petrov', 'Chen Wei', 'Dana Kim', 'Eve <b>x</b> & Co']), JSON.stringify(await names(page)));
        check('the name that looks like markup is text, not markup', (await page.locator('tbody th b').count()) === 0);
        const heads = await page.locator('thead th').allInnerTexts();
        check('the columns are the labs in the order of the course', heads.join('|').includes('Lab 4') && heads.join('|').includes('Lab 5'));
        const counts = await page.locator('[data-region="counts"]').innerText();
        check('counters per lab: running, errors, not started',
            /Lab 4[^\n]*Running: 2, errors: 1, not started: 2/.test(counts.replace(/\n/g, ' ')) && /Lab 5[^\n]*Running: 1, errors: 0, not started: 2/.test(counts.replace(/\n/g, ' ')),
            JSON.stringify(counts));
        check('the first poll asked the server once, with no group', (await page.evaluate(() => window.__stub.course.calls.length)) === 1);

        const waiting = await until(async() => /\d+/.test(await cellOf(page, 'Boris', 1).locator('.finds .bad').innerText()), 8000);
        check('a student with errors: the number of bad findings in the cell', !!waiting);
        check('...and the number of warnings', /\d+/.test(await cellOf(page, 'Boris', 1).locator('.finds .warn').innerText().catch(() => '')));
        check('...the cell is coloured by the worst', (await cellOf(page, 'Boris', 1).locator('button').getAttribute('data-level')) === 'bad');
        check('a student with a correct configuration: no counts, a tick', (await cellOf(page, 'Alice', 1).locator('.finds .n.ok').count()) === 1
            && (await cellOf(page, 'Alice', 1).locator('.finds .bad').count()) === 0);
        check('...and "no changes since HH:MM" before the first change the page saw',
            /No changes since \d/.test(await cellOf(page, 'Alice', 1).innerText()), await cellOf(page, 'Alice', 1).innerText());
        check('a running session that reports no facts: a grey sign, not "all is well"',
            (await cellOf(page, 'Chen', 2).locator('.sign.unknown').count()) === 1 && (await cellOf(page, 'Chen', 2).locator('.n').count()) === 0);
        check('the time left of a running lab', /left/.test(await cellOf(page, 'Alice', 1).innerText()));
        check('states: error, not started, stopped',
            /Error/.test(await cellOf(page, 'Chen', 1).innerText()) && /Not started/.test(await cellOf(page, 'Dana', 1).innerText())
            && /Stopped/.test(await cellOf(page, 'Alice', 2).innerText()));
        check('a student the teacher cannot observe in a lab: a dash, no button',
            (await cellOf(page, 'Dana', 2).innerText()).trim() === '—' && (await cellOf(page, 'Dana', 2).locator('button').count()) === 0);
        const c = await calls(page);
        check('the topology is read once per session and the facts per session (two running sessions with facts "unknown")',
            c.mod_gns3lab_get_state === 2 && c.mod_gns3lab_get_facts === 2, JSON.stringify(c));
        await page.screenshot({path: path.join(SHOTS, 'course-1-table.png')});

        /* ---------------- no changes for N min: the rev moves ---------------- */
        await page.evaluate(() => window.__stub.course.bump(11));
        await page.clock.fastForward(61000);
        const moved = await until(async() => /No changes for 0 min/.test(await cellOf(page, 'Alice', 1).innerText()), 5000);
        check('the facts changed: "no changes for 0 min" from the change the page saw', !!moved, await cellOf(page, 'Alice', 1).innerText());
        await page.clock.fastForward(60000 * 3);
        const idle = await until(async() => /No changes for [34] min/.test(await cellOf(page, 'Alice', 1).innerText()), 5000);
        check('...and the minutes grow while nothing changes', !!idle, await cellOf(page, 'Alice', 1).innerText());
        check('the table is read again every 15 s (polls after the fast-forward)', (await page.evaluate(() => window.__stub.course.calls.length)) >= 3);

        /* ---------------- search, sort ---------------- */
        await page.locator('[data-region="search"]').fill('ch');
        check('the search finds by name', JSON.stringify(await names(page)) === JSON.stringify(['Chen Wei']), JSON.stringify(await names(page)));
        await page.locator('[data-region="search"]').fill('');
        await page.locator('thead .sortbtn', {hasText: 'Student'}).click();
        check('sorting by name, again: the other way round', (await names(page))[0] === 'Eve <b>x</b> & Co');
        await page.locator('thead .sortbtn', {hasText: 'Lab 4'}).click();
        const byState = await names(page);
        check('sorting by the state in a lab: the error first, then the errors in facts, then the rest',
            byState[0] === 'Chen Wei' && byState[1] === 'Boris Petrov' && byState[2] === 'Alice Ivanova', JSON.stringify(byState));
        await page.locator('thead .sortbtn', {hasText: 'Student'}).click();

        /* ---------------- group ---------------- */
        await page.locator('[data-region="group"]').selectOption('5');
        const grouped = await until(async() => (await names(page)).length === 2, 4000);
        check('the group narrows the table to its members', !!grouped, JSON.stringify(await names(page)));
        check('...and is sent to the server', (await page.evaluate(() => window.__stub.course.calls.at(-1))).groupid === 5);
        await page.locator('[data-region="group"]').selectOption('0');
        await until(async() => (await names(page)).length === 5, 4000);

        /* ---------------- the card ---------------- */
        await cellOf(page, 'Boris', 1).locator('button').click();
        const card = page.locator('[data-region="card"]');
        await card.waitFor({state: 'visible'});
        check('the address carries the card', (await page.evaluate(() => location.hash)) === '#cm=1&user=12');
        check('the card names the student and the lab', /Boris Petrov/.test(await card.innerText()) && /Lab 4/.test(await card.innerText()));
        const link = await card.locator('a.btn').getAttribute('href');
        check('"Open the scheme" leads to the lab of the student, read only', link === 'lab.html?id=1&userid=12&from=course', link);
        const findings = await until(async() => (await card.locator('.cfinds .issues li').count()) > 0, 6000);
        check('findings by layer, texts from the layers', !!findings && (await card.locator('.cfinds h4').count()) >= 2);
        check('...the important ones first', (await card.locator('.cfinds .issues').first().locator('li').first().getAttribute('class')) === 'bad');
        check('...L1 says what is shut down', /Gi0\/2/.test(await card.locator('.cfinds').innerText()));
        const devices = await card.locator('.devs tbody tr').count();
        check('the devices: name, state, interfaces up, addresses', devices === 11 && /R1/.test(await card.locator('.devs').innerText())
            && /\d+ \/ \d+/.test(await card.locator('.devs').innerText()) && /10\.0\.12\.1/.test(await card.locator('.devs').innerText()), String(devices));
        const history = card.locator('[data-region="history"]');
        check('the place for the history of attempts and checks, with no requests behind it',
            /History of attempts and checks/.test(await history.innerText()) && /Will appear with the automatic checks/.test(await history.innerText())
            && (await history.locator('[data-region="historylist"]').count()) === 1);
        await page.screenshot({path: path.join(SHOTS, 'course-2-card.png')});
        await card.locator('.cclose').click();
        await card.waitFor({state: 'hidden'});
        check('closing the card clears the address', (await page.evaluate(() => location.hash)) === '');

        await cellOf(page, 'Dana', 1).locator('button').click();
        await card.waitFor({state: 'visible'});
        const none = await card.innerText();
        check('no session: "has not started the lab yet" and the same place for the history',
            /has not started the lab yet/.test(none) && /History of attempts/.test(none) && (await card.locator('a.btn').isHidden()));
        await page.goBack();
        await card.waitFor({state: 'hidden'});
        check('back in the browser closes the card', true);

        // reload with the address: the card comes back
        await page.goto(URL() + '#cm=1&user=11');
        await card.waitFor({state: 'visible', timeout: 8000});
        check('a card can be reloaded by its address', /Alice Ivanova/.test(await card.innerText()));
        await page.close();

        /* ---------------- phone ---------------- */
        const phone = await newPage(browser, problems, {viewport: {width: 360, height: 740}, hasTouch: true, isMobile: true});
        await phone.goto(URL());
        await phone.locator('tbody tr').first().waitFor();
        const box = await phone.locator('[data-region="tablebox"]').evaluate(e => ({scroll: e.scrollWidth, client: e.clientWidth}));
        check('on a phone the table scrolls inside its box, not the page', box.scroll > box.client
            && (await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)), JSON.stringify(box));
        const button = await cellOf(phone, 'Alice', 1).locator('button').boundingBox();
        check('touch targets are at least 40 px', button.height >= 40, String(button.height));
        await phone.screenshot({path: path.join(SHOTS, 'course-3-phone.png')});
        await cellOf(phone, 'Boris', 1).locator('button').tap();
        const pcard = phone.locator('[data-region="card"]');
        await pcard.waitFor({state: 'visible'});
        const cb = await pcard.boundingBox();
        check('the card takes the whole screen', cb.width >= 359 && cb.height >= 600, JSON.stringify(cb));
        check('...with a button back to the table, and no cross', await pcard.locator('.cback').isVisible() && !(await pcard.locator('.cclose').isVisible()));
        const bb = await pcard.locator('.cback').boundingBox();
        check('...whose touch target is at least 40 px', bb.width >= 40 && bb.height >= 40);
        await phone.screenshot({path: path.join(SHOTS, 'course-4-phone-card.png')});
        await pcard.locator('.cback').tap();
        await pcard.waitFor({state: 'hidden'});
        check('the back button returns to the table', await phone.locator('tbody tr').first().isVisible());
        await phone.close();

        /* ---------------- dark theme ---------------- */
        const dark = await newPage(browser, problems);
        await dark.goto(URL('theme=dark'));
        await dark.locator('tbody tr').first().waitFor();
        await sleep(800);
        await dark.screenshot({path: path.join(SHOTS, 'course-5-dark.png')});
        await dark.close();

        /* ---------------- the manager did not answer for a lab ---------------- */
        const broken = await newPage(browser, problems);
        await broken.goto(URL('laberror'));
        await broken.locator('tbody tr').first().waitFor();
        check('a lab the manager did not answer for: a mark in its header, the rest of the table works',
            (await broken.locator('thead .laberr').count()) === 1 && (await broken.locator('thead th').nth(1).locator('.laberr').count()) === 0
            && /Running: 2/.test(await broken.locator('[data-region="counts"]').innerText()));
        check('...and the counters of that lab say it', /did not answer/i.test(await broken.locator('[data-region="counts"] .has-error').innerText()));
        await broken.close();

        const down = await newPage(browser, problems);
        await down.goto(URL('coursefail'));
        const notice = down.locator('[data-region="notice"]');
        await notice.waitFor({state: 'visible'});
        check('a failed call: a notice, no trace', /could not be loaded/i.test(await notice.innerText()));
        await down.close();

        const known = problems.filter(p => !/The course could not be read/.test(p));
        check('no errors in the page', known.length === 0, known.join(' | '));
    } catch (e) {
        check('the run went to the end', false, String(e.stack || e));
    } finally {
        await browser.close();
        server.kill();
    }
    console.log(failed ? `\n${failed} failed` : '\nall checks passed');
    process.exit(failed);
})();
