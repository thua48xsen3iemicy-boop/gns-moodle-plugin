'use strict';
/**
 * Checks the course page of a teacher (docs/teacher-dashboard.md) in Chromium, end to end, against the stub of the external
 * functions (stubs/ajax.js: five students, three labs): the summary (grades of the gradebook, the mark of a grade changed by
 * hand, the total per student and per lab, a teacher who may not see grades), the view of one lab (the address, the counters,
 * the figures of the facts, "no changes for N min", the checks, the grade, the sorting), the way between them (tabs, the
 * header of a lab, back in the browser), the search, the group, the card (address, grade, findings, devices, the link to the
 * lab, the history of the checks), the phone width, a lab the manager did not answer for, a failed call and escaping.
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

const URLQ = (query = '') => `${BASE}/index.en.course.html?id=2&${MIN ? 'min&' : ''}${query}`;
const rowOf = (page, name) => page.locator('tbody tr', {has: page.locator('th .name', {hasText: name})});
const cellOf = (page, name, lab) => rowOf(page, name).locator('td').nth(lab - 1);
const colOf = (page, name, cls) => rowOf(page, name).locator('td.' + cls);
const names = page => page.locator('tbody th .name').allInnerTexts();
const calls = page => page.evaluate(() => ({...window.__stub.calls}));
const lastCall = page => page.evaluate(() => window.__stub.course.calls.at(-1));
const view = page => page.locator('[data-region="views"] [aria-current="page"]').innerText();

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

        /* ---------------- the summary ---------------- */
        const page = await newPage(browser, problems);
        await page.clock.install();
        await page.goto(URLQ());
        await page.locator('tbody tr').first().waitFor();
        check('the summary is the view the page opens with', (await view(page)) === 'Summary');
        check('...asked for all labs (cmid 0)', (await lastCall(page)).cmid === 0, JSON.stringify(await lastCall(page)));
        check('five students in rows, sorted by name', JSON.stringify(await names(page))
            === JSON.stringify(['Alice Ivanova', 'Boris Petrov', 'Chen Wei', 'Dana Kim', 'Eve <b>x</b> & Co']), JSON.stringify(await names(page)));
        check('the name that looks like markup is text, not markup', (await page.locator('tbody th b').count()) === 0);
        const heads = await page.locator('thead th').allInnerTexts();
        check('the columns are the labs in the order of the course and the total',
            /Lab 4.*Lab 5.*Lab 6.*Passed/s.test(heads.join('|')), JSON.stringify(heads));
        const alice4 = cellOf(page, 'Alice', 1).locator('button');
        check('a grade of the gradebook in the cell', (await alice4.locator('.grade').innerText()) === '5.00');
        check('...passed against the grade to pass', (await alice4.getAttribute('data-pass')) === 'yes');
        check('...with a dot for a lab that runs now', (await alice4.locator('.live').count()) === 1);
        const boris4 = cellOf(page, 'Boris', 1).locator('button');
        check('a grade below the grade to pass', (await boris4.getAttribute('data-pass')) === 'no'
            && (await boris4.locator('.grade').innerText()) === '2.00');
        check('...changed by hand in the gradebook: a mark with the reason', (await boris4.locator('.over').getAttribute('title'))
            === 'Changed by hand in the gradebook');
        check('a lab without a pass grade: the grade, no pass mark', (await cellOf(page, 'Alice', 2).locator('.grade').innerText()) === '85.00'
            && (await cellOf(page, 'Alice', 2).locator('button').getAttribute('data-pass')) === null);
        check('no grade: how far the student got (error, running, not started, started)',
            /Error/.test(await cellOf(page, 'Chen', 1).innerText()) && /Running/.test(await cellOf(page, 'Chen', 2).innerText())
            && /Not started/.test(await cellOf(page, 'Dana', 1).innerText()) && /Started/.test(await cellOf(page, 'Alice', 3).innerText()));
        check('a lab that runs without a grade yet says it runs',
            (await cellOf(page, 'Chen', 2).locator('button').getAttribute('data-kind')) === 'running');
        await page.evaluate(() => window.__stub.course.set(2, 12, {status: 'stopped', startedat: 1, expiresat: 0, facts: 'unknown'}));
        await page.clock.fastForward(15000);
        const nograde = await until(async() => /No grade/.test(await cellOf(page, 'Boris', 2).innerText()), 4000);
        check('a stopped lab without a grade in a graded lab: "No grade"', !!nograde, await cellOf(page, 'Boris', 2).innerText());
        check('a student the teacher cannot observe in a lab: a dash, no button',
            (await cellOf(page, 'Dana', 2).innerText()).trim() === '—' && (await cellOf(page, 'Dana', 2).locator('button').count()) === 0);
        check('the total: labs passed out of the graded labs the student can be observed in',
            (await rowOf(page, 'Alice').locator('td.total').innerText()) === '2 / 2'
            && (await rowOf(page, 'Boris').locator('td.total').innerText()) === '0 / 2'
            && (await rowOf(page, 'Dana').locator('td.total').innerText()) === '0 / 1');
        const foot = await page.locator('tfoot td.labsum').allInnerTexts();
        check('per lab: the average of the gradebook and how many passed or were graded, a lab without a grade says so',
            /Average 3\.50/.test(foot[0]) && /Passed 1 of 5/.test(foot[0]) && /Average 85\.00/.test(foot[1])
            && /Graded 1 of 4/.test(foot[1]) && /Not graded/.test(foot[2]), JSON.stringify(foot));
        check('the link to the gradebook', (await page.locator('[data-region="gradebook"]').getAttribute('href')) === '#gradebook');
        let c = await calls(page);
        check('the summary reads no facts', !c.mod_gns3lab_get_state && !c.mod_gns3lab_get_facts, JSON.stringify(c));
        await page.screenshot({path: path.join(SHOTS, 'course-1-summary.png')});

        await page.locator('thead .sortbtn', {hasText: 'Passed'}).click();
        check('sorting by the total: the most passed first', (await names(page))[0] === 'Alice Ivanova', JSON.stringify(await names(page)));
        await page.locator('thead .sortbtn', {hasText: 'Student'}).click();
        check('sorting by name again', (await names(page))[0] === 'Alice Ivanova' && (await names(page))[1] === 'Boris Petrov');

        /* ---------------- the card from the summary ---------------- */
        await boris4.click();
        const card = page.locator('[data-region="card"]');
        await card.waitFor({state: 'visible'});
        check('the address carries the card', (await page.evaluate(() => location.hash)) === '#cm=1&user=12');
        const grade = card.locator('.cgrade');
        check('the card shows the grade of the gradebook, out of what, changed by hand, the grade to pass',
            /2\.00/.test(await grade.innerText()) && /out of 5\.00/.test(await grade.innerText())
            && /Changed by hand/.test(await grade.innerText()) && /to pass: 3\.00/.test(await grade.innerText()), await grade.innerText());
        const history = card.locator('[data-region="history"]');
        const runs = await until(async() => (await history.locator('li').count()) === 2, 4000);
        check('the history of the checks: a line per run, the newest first', !!runs
            && /in progress/.test(await history.locator('li').first().innerText()) && /mark 2/.test(await history.locator('li').nth(1).innerText()),
            await history.innerText());
        check('...with the findings counted', /critical: 1, major: 1/.test(await history.innerText()));
        check('...read from get_checks for that student and lab',
            JSON.stringify(await page.evaluate(() => window.__stub.course.historyCalls.at(-1))) === '{"cmid":1,"userid":12}');
        const findings = await until(async() => (await card.locator('.cfinds .issues li').count()) > 0, 6000);
        check('the card of a running lab reads its facts in the summary too', !!findings);
        await page.screenshot({path: path.join(SHOTS, 'course-2-summary-card.png')});
        await card.locator('.cclose').click();
        await card.waitFor({state: 'hidden'});
        check('closing the card clears the address', (await page.evaluate(() => location.hash)) === '');

        await cellOf(page, 'Alice', 3).locator('button').click();
        await card.waitFor({state: 'visible'});
        await until(async() => /switched off/.test(await card.innerText()), 4000);
        check('a lab without checks: the history says they are off, no grade line', /Auto-checks are switched off/.test(await card.innerText())
            && (await card.locator('.cgrade').count()) === 0);
        await page.goBack();
        await card.waitFor({state: 'hidden'});

        /* ---------------- one lab ---------------- */
        const before = await calls(page);
        await page.locator('[data-region="views"] [data-view="1"]').click();
        await until(async() => (await lastCall(page)).cmid === 1, 4000);
        await page.locator('table.lab tbody tr').first().waitFor();
        check('a tab opens the lab and puts it into the address', (await page.evaluate(() => location.search)).includes('lab=1')
            && /Lab 4/.test(await view(page)));
        check('...asking the server for that lab only', (await lastCall(page)).cmid === 1);
        const labHeads = await page.locator('thead th').allInnerTexts();
        check('the columns: student, session, scheme, checks, grade', labHeads.join('|') === 'Student|Session|Scheme|Checks|Grade',
            JSON.stringify(labHeads));
        const byState = await names(page);
        check('what needs a look comes first: the error, then the errors in facts, then the rest',
            byState[0] === 'Chen Wei' && byState[1] === 'Boris Petrov' && byState[2] === 'Alice Ivanova', JSON.stringify(byState));
        const counts = (await page.locator('[data-region="counts"]').innerText()).replace(/\n/g, ' ');
        check('counters of the lab: running, errors, not started, passed and the average',
            /Running: 2, errors: 1, not started: 2/.test(counts) && /Passed: 1 of 5/.test(counts) && /Average 3\.50/.test(counts), counts);

        const waiting = await until(async() => /\d+/.test(await colOf(page, 'Boris', 'facts').locator('.finds .bad').innerText()), 8000);
        check('a student with errors: the number of bad findings', !!waiting);
        check('...and the number of warnings', /\d+/.test(await colOf(page, 'Boris', 'facts').locator('.finds .warn').innerText().catch(() => '')));
        check('...the session is coloured by the worst', (await colOf(page, 'Boris', 'state').getAttribute('data-level')) === 'bad');
        check('a student with a correct configuration: no counts, a tick', (await colOf(page, 'Alice', 'facts').locator('.finds .n.ok').count()) === 1
            && (await colOf(page, 'Alice', 'facts').locator('.finds .bad').count()) === 0);
        check('...and "no changes since HH:MM" before the first change the page saw',
            /No changes since \d/.test(await colOf(page, 'Alice', 'facts').innerText()), await colOf(page, 'Alice', 'facts').innerText());
        check('the time left of a running lab', /left/.test(await colOf(page, 'Alice', 'state').innerText()));
        check('states: error, not started', /Error/.test(await colOf(page, 'Chen', 'state').innerText())
            && /Not started/.test(await colOf(page, 'Dana', 'state').innerText()));
        check('the checks: a run in progress, graded attempts, none',
            /Check in progress/.test(await colOf(page, 'Boris', 'checks').innerText())
            && /Attempts: 2/.test(await colOf(page, 'Alice', 'checks').innerText())
            && /No checks/.test(await colOf(page, 'Dana', 'checks').innerText()));
        check('the grade of the gradebook in its column', (await colOf(page, 'Alice', 'gradecell').innerText()) === '5.00'
            && (await colOf(page, 'Dana', 'gradecell').innerText()) === '—');
        c = await calls(page);
        const since = name => (c[name] || 0) - (before[name] || 0);
        check('the topology is read once per session and the facts per session (two running sessions with facts "unknown")',
            since('mod_gns3lab_get_state') === 2 && since('mod_gns3lab_get_facts') >= 2, JSON.stringify(c));
        await page.screenshot({path: path.join(SHOTS, 'course-3-lab.png')});

        /* ---------------- no changes for N min: the rev moves ---------------- */
        await page.evaluate(() => window.__stub.course.bump(11));
        await page.clock.fastForward(61000);
        const moved = await until(async() => /No changes for 0 min/.test(await colOf(page, 'Alice', 'facts').innerText()), 5000);
        check('the facts changed: "no changes for 0 min" from the change the page saw', !!moved, await colOf(page, 'Alice', 'facts').innerText());
        await page.clock.fastForward(60000 * 3);
        const idle = await until(async() => /No changes for [34] min/.test(await colOf(page, 'Alice', 'facts').innerText()), 5000);
        check('...and the minutes grow while nothing changes', !!idle, await colOf(page, 'Alice', 'facts').innerText());
        check('the table is read again every 15 s (polls after the fast-forward)', (await page.evaluate(() => window.__stub.course.calls.length)) >= 4);

        /* ---------------- search, sort, group ---------------- */
        await page.locator('[data-region="search"]').fill('ch');
        check('the search finds by name', JSON.stringify(await names(page)) === JSON.stringify(['Chen Wei']), JSON.stringify(await names(page)));
        await page.locator('[data-region="search"]').fill('');
        await page.locator('thead .sortbtn', {hasText: 'Grade'}).click();
        check('sorting by grade: the best first', (await names(page))[0] === 'Alice Ivanova' && (await names(page))[1] === 'Boris Petrov',
            JSON.stringify(await names(page)));
        await page.locator('thead .sortbtn', {hasText: 'Student'}).click();
        check('sorting by name', (await names(page))[0] === 'Alice Ivanova');
        await page.locator('[data-region="group"]').selectOption('5');
        const grouped = await until(async() => (await names(page)).length === 2, 4000);
        check('the group narrows the table to its members', !!grouped, JSON.stringify(await names(page)));
        check('...and is sent to the server with the lab', (await lastCall(page)).groupid === 5 && (await lastCall(page)).cmid === 1);
        await page.locator('[data-region="group"]').selectOption('0');
        await until(async() => (await names(page)).length === 5, 4000);

        /* ---------------- the card of a lab ---------------- */
        await colOf(page, 'Boris', 'checks').click();
        await card.waitFor({state: 'visible'});
        check('a click in the row opens the card', (await page.evaluate(() => location.hash)) === '#cm=1&user=12');
        check('the row of the open card is marked', (await rowOf(page, 'Boris').getAttribute('aria-current')) === 'true');
        check('the card names the student and the lab', /Boris Petrov/.test(await card.innerText()) && /Lab 4/.test(await card.innerText()));
        const link = await card.locator('a.btn').getAttribute('href');
        check('"Open the scheme" leads to the lab of the student, read only', link === 'lab.html?id=1&userid=12&from=course', link);
        await until(async() => (await card.locator('.cfinds .issues li').count()) > 0, 6000);
        check('findings by layer, texts from the layers', (await card.locator('.cfinds h4').count()) >= 2);
        check('...the important ones first', (await card.locator('.cfinds .issues').first().locator('li').first().getAttribute('class')) === 'bad');
        check('...L1 says what is shut down', /Gi0\/2/.test(await card.locator('.cfinds').innerText()));
        const devices = await card.locator('.devs tbody tr').count();
        check('the devices: name, state, interfaces up, addresses', devices === 11 && /R1/.test(await card.locator('.devs').innerText())
            && /\d+ \/ \d+/.test(await card.locator('.devs').innerText()) && /10\.0\.12\.1/.test(await card.locator('.devs').innerText()), String(devices));
        await page.screenshot({path: path.join(SHOTS, 'course-4-lab-card.png')});
        await card.locator('.cclose').click();
        await card.waitFor({state: 'hidden'});

        await rowOf(page, 'Dana').locator('.namebtn').click();
        await card.waitFor({state: 'visible'});
        await until(async() => /No checks yet/.test(await card.innerText()), 4000);
        const none = await card.innerText();
        check('no session: "has not started the lab yet", no grade yet, no checks yet',
            /has not started the lab yet/.test(none) && /Gradebook grade: none yet/.test(none) && /No checks yet/.test(none)
            && (await card.locator('a.btn').isHidden()), none);
        await page.goBack();
        await card.waitFor({state: 'hidden'});
        check('back in the browser closes the card and stays in the lab', (await page.evaluate(() => location.search)).includes('lab=1'));

        await page.goBack();
        await until(async() => (await view(page)) === 'Summary', 4000);
        await page.locator('table.summary tbody tr').first().waitFor();
        check('back again returns to the summary', (await lastCall(page)).cmid === 0 && !(await page.evaluate(() => location.search)).includes('lab='));
        await page.locator('thead .labbtn', {hasText: 'Lab 5'}).click();
        await until(async() => (await lastCall(page)).cmid === 2, 4000);
        await page.locator('table.lab tbody tr').first().waitFor();
        check('the header of a lab in the summary opens that lab', /Lab 5/.test(await view(page)));
        check('...a lab without a pass grade counts the graded', /Graded: 1 of 4/.test(await page.locator('[data-region="counts"]').innerText()));
        check('...students who cannot be observed in it are not rows', !(await names(page)).includes('Dana Kim'), JSON.stringify(await names(page)));
        const nofacts = await until(async() => (await colOf(page, 'Chen', 'facts').locator('.sign.unknown').count()) === 1, 4000);
        check('a running session that reports no facts: a grey sign, not "all is well"', !!nofacts
            && (await colOf(page, 'Chen', 'facts').locator('.n').count()) === 0);
        check('a failed last run', /last/.test(await colOf(page, 'Chen', 'checks').innerText())
            && /Attempts: 0/.test(await colOf(page, 'Chen', 'checks').innerText()));

        // reload with the address: the lab and the card come back
        await page.goto(URLQ('lab=1') + '#cm=1&user=11');
        await card.waitFor({state: 'visible', timeout: 8000});
        check('a lab and a card can be reloaded by their address', /Alice Ivanova/.test(await card.innerText()) && /Lab 4/.test(await view(page)));
        await page.goto(URLQ('lab=99'));
        await page.locator('tbody tr').first().waitFor().catch(() => null);
        check('a lab that is not on the page: the server refuses, the page says so', await page.locator('[data-region="notice"]').isVisible());
        await page.close();

        /* ---------------- a teacher who may not see grades ---------------- */
        const blind = await newPage(browser, problems);
        await blind.goto(URLQ('nogrades'));
        await blind.locator('tbody tr').first().waitFor();
        check('without grades: no total, no grades in the cells, the states instead',
            (await blind.locator('th.total').count()) === 0 && (await blind.locator('.sumbtn .grade').count()) === 0
            && /Started/.test(await cellOf(blind, 'Alice', 1).innerText()) === false && /Running/.test(await cellOf(blind, 'Alice', 1).innerText()));
        await cellOf(blind, 'Alice', 2).locator('button').click();
        await blind.locator('[data-region="card"]').waitFor({state: 'visible'});
        check('...and no grade in the card', (await blind.locator('.cgrade').count()) === 0);
        await blind.close();

        /* ---------------- phone ---------------- */
        const phone = await newPage(browser, problems, {viewport: {width: 360, height: 740}, hasTouch: true, isMobile: true});
        await phone.goto(URLQ());
        await phone.locator('tbody tr').first().waitFor();
        const box = await phone.locator('[data-region="tablebox"]').evaluate(e => ({scroll: e.scrollWidth, client: e.clientWidth}));
        check('on a phone the table scrolls inside its box, not the page', box.scroll > box.client
            && (await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)), JSON.stringify(box));
        const tabs = await phone.locator('[data-region="views"]').evaluate(e => ({scroll: e.scrollWidth, client: e.clientWidth}));
        check('...and so do the tabs', tabs.scroll > tabs.client, JSON.stringify(tabs));
        const button = await cellOf(phone, 'Alice', 1).locator('button').boundingBox();
        const tab = await phone.locator('[data-region="views"] a').first().boundingBox();
        check('touch targets are at least 40 px', button.height >= 40 && tab.height >= 40, `${button.height} ${tab.height}`);
        await phone.screenshot({path: path.join(SHOTS, 'course-5-phone.png')});
        await phone.goto(URLQ('lab=1'));
        await phone.locator('table.lab tbody tr').first().waitFor();
        await rowOf(phone, 'Boris').locator('.namebtn').tap();
        const pcard = phone.locator('[data-region="card"]');
        await pcard.waitFor({state: 'visible'});
        const cb = await pcard.boundingBox();
        check('the card takes the whole screen', cb.width >= 359 && cb.height >= 600, JSON.stringify(cb));
        check('...with a button back to the table, and no cross', await pcard.locator('.cback').isVisible() && !(await pcard.locator('.cclose').isVisible()));
        const bb = await pcard.locator('.cback').boundingBox();
        check('...whose touch target is at least 40 px', bb.width >= 40 && bb.height >= 40);
        await phone.screenshot({path: path.join(SHOTS, 'course-6-phone-card.png')});
        await pcard.locator('.cback').tap();
        await pcard.waitFor({state: 'hidden'});
        check('the back button returns to the table', await phone.locator('tbody tr').first().isVisible());
        await phone.close();

        /* ---------------- dark theme ---------------- */
        const dark = await newPage(browser, problems);
        await dark.goto(URLQ('theme=dark'));
        await dark.locator('tbody tr').first().waitFor();
        await sleep(500);
        await dark.screenshot({path: path.join(SHOTS, 'course-7-dark-summary.png')});
        await dark.goto(URLQ('theme=dark&lab=1'));
        await dark.locator('tbody tr').first().waitFor();
        await sleep(1500);
        await dark.screenshot({path: path.join(SHOTS, 'course-8-dark-lab.png')});
        await dark.close();

        /* ---------------- the manager did not answer for a lab ---------------- */
        const broken = await newPage(browser, problems);
        await broken.goto(URLQ('laberror'));
        await broken.locator('tbody tr').first().waitFor();
        check('a lab the manager did not answer for: a mark in its header in the summary, the rest works',
            (await broken.locator('thead .laberr').count()) === 1 && (await broken.locator('thead th').nth(2).locator('.laberr').count()) === 1);
        await broken.goto(URLQ('laberror&lab=2'));
        await broken.locator('tbody tr').first().waitFor();
        check('...and in that lab the counters say it', /did not answer/i.test(await broken.locator('[data-region="counts"] .has-error').innerText()));
        await broken.close();

        const down = await newPage(browser, problems);
        await down.goto(URLQ('coursefail'));
        const notice = down.locator('[data-region="notice"]');
        await notice.waitFor({state: 'visible'});
        check('a failed call: a notice, no trace', /could not be loaded/i.test(await notice.innerText()));
        await down.close();

        const known = problems.filter(p => !/The course could not be read|The lab is not available/.test(p));
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
