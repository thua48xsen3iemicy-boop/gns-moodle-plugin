'use strict';
/**
 * Checks the auto-check panel of the lab page (docs/autochecks.md) in Chromium, end to end against the stub of core/ajax:
 *
 *   the "Check" button appears for an activity with checks → the panel says to start the lab → the lab runs → "Check the
 *   lab" carries the page id (seat) → progress → mark, counts and findings, a consequence under its root, a forgiven note
 *   → the attempts go down → the last attempt spent switches the button off → a report shown only as counts has no
 *   findings → an observer sees the runs but no button → a phone width and the dark theme.
 *
 * The server side (attempt counting, visibility, grades) is in the PHPUnit tests of the plugin; here stubs/ajax.js
 * plays it. Screenshots go to --shots (default dist/shots).
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_checks.js [--min] [--shots <dir>]
 *
 * Playwright with Chromium must be installed (require('playwright')). The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18131;
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

const region = (page, name) => page.locator(`[data-region="${name}"]`);
const panelText = page => region(page, 'checksBody').innerText();
const startButton = page => page.locator('[data-region="checksBody"] .chk-start button');

(async() => {
    fs.mkdirSync(SHOTS, {recursive: true});
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch();
    try {
        const ready = await until(async() => (await get(`${BASE}/gw/stats`)) === 200, 10000);
        check('the fake gateway is up', !!ready);
        if (!ready) {
            throw new Error('server did not start');
        }
        const problems = [];
        const open = async(url, viewport = {width: 1440, height: 860}) => {
            const page = await (await browser.newContext({viewport})).newPage();
            page.on('pageerror', e => problems.push('pageerror: ' + e.message));
            page.on('console', m => {
                if (m.type() === 'error' && !/WebSocket connection|Failed to load resource/.test(m.text())) {
                    problems.push('console: ' + m.text());
                }
            });
            await page.goto(`${BASE}/${url}${url.includes('?') ? '&' : '?'}${MIN ? 'min&' : ''}fast`);
            return page;
        };

        // ---- an activity without checks has no button and asks nothing
        let page = await open('index.ru.html');
        await sleep(800);
        check('no "Check" button without checks', await region(page, 'checksOpen').isHidden());
        check('...and get_checks is not called', !(await page.evaluate(() => window.__stub.calls.mod_gns3lab_get_checks)));
        await page.context().close();

        // ---- the student: before the lab runs, start, progress, report
        page = await open('index.ru.checks.html?checksmax=2');
        const openBtn = region(page, 'checksOpen');
        check('the "Check" button appears', !!await until(async() => openBtn.isVisible()));
        await openBtn.click();
        check('the panel opens', await region(page, 'checksPanel').isVisible());
        check('attempts are shown', /Осталось попыток: 2 из 2/.test(await region(page, 'checksLead').innerText()));
        check('the button says to start the lab first', await startButton(page).isDisabled()
            && /Запустите лабу/.test(await panelText(page)));
        await region(page, 'start').click();
        check('the button works once the lab runs', !!await until(async() => startButton(page).isEnabled(), 6000));
        await startButton(page).click();
        const gated = await page.evaluate(() => window.__stub.seat.gated.find(g => g.name === 'start_check'));
        check('start_check carries the page id (seat)', !!gated && /^[0-9a-f]{32}$/.test(gated.tabid), JSON.stringify(gated));
        check('progress while the run goes on', !!await until(async() => /Проверка ждёт|Собираю/.test(await panelText(page)), 3000));
        check('...and the button is off meanwhile', await startButton(page).isDisabled());
        check('the mark comes', !!await until(async() => (await page.locator('.chk-mark-value').count()) > 0, 6000));
        check('mark 5 for one minor finding', (await page.locator('.chk-mark-value').innerText()) === '5',
            await page.locator('.chk-mark-value').innerText());
        check('counts by class', /мелких: 1/.test(await panelText(page)));
        check('attempts went down', !!await until(async() => /1 из 2/.test(await region(page, 'checksLead').innerText())));
        check('the mark is on the button', (await region(page, 'checksMark').innerText()) === '5');
        check('a student is not told what was not checked', !/Проверено не всё|Не проверено/.test(await panelText(page)));
        await page.screenshot({path: path.join(SHOTS, 'checks-ok.png')});

        // A broken lab: consequences under the root, a forgiven note, the message as text (no markup from the manager).
        await page.evaluate(() => window.__stub.call('mod_gns3lab_refresh_facts', {cmid: 1}));
        await until(async() => startButton(page).isEnabled(), 3000);
        await startButton(page).click();
        check('second report comes', !!await until(async() => (await page.locator('.chk-consequences').count()) > 0, 6000));
        const order = await page.locator('.chk-findings > li > .chk-line .chk-class').allInnerTexts();
        check('counted findings first, the forgiven one last', order[order.length - 1].toLowerCase() === 'прощено', order.join(','));
        check('a consequence is under its root, labelled', (await page.locator('.chk-consequences .chk-tag').innerText()) === 'следствие');
        await page.locator('.chk-toroot').first().click();
        check('"to the cause" focuses the root', await page.evaluate(() => document.activeElement.classList.contains('chk-finding')));
        check('a forgiven finding says so', /Прощено: при обычной строгости/.test(await panelText(page)));
        check('the message of the manager is text', (await page.locator('.chk-msg b').count()) === 0
            && /<b>/.test(await panelText(page)));
        check('mark 3 for two major findings', (await page.locator('.chk-mark-value').innerText()) === '3');
        check('no attempts left: the button is off and says why', !!await until(async() => startButton(page).isDisabled())
            && /Попытки закончились/.test(await panelText(page)));
        check('earlier runs are listed', (await page.locator('.chk-history li').count()) === 2);
        await page.locator('.chk-history button').nth(1).click();
        check('an earlier run opens', !!await until(async() => (await page.locator('.chk-mark-value').innerText()) === '5'));
        await page.screenshot({path: path.join(SHOTS, 'checks-broken.png')});
        await page.keyboard.press('Escape');
        check('Escape closes the panel', await region(page, 'checksPanel').isHidden());
        await page.context().close();

        // ---- incomplete and failed runs are not attempts
        page = await open('index.en.checks.html?checksmax=1');
        await region(page, 'checksOpen').click();
        await region(page, 'start').click();
        await until(async() => startButton(page).isEnabled(), 6000);
        await page.evaluate(() => window.__stub.checks.next('incomplete'));
        await startButton(page).click();
        check('an incomplete run says to check again', !!await until(async() => /incomplete/.test(await panelText(page)), 6000));
        check('...and keeps the attempt', /Attempts left: 1 of 1/.test(await region(page, 'checksLead').innerText()));
        await until(async() => startButton(page).isEnabled(), 3000);
        await page.evaluate(() => window.__stub.checks.next('failed'));
        await startButton(page).click();
        check('a failed run names the code', !!await until(async() => /SLOT_UNAVAILABLE/.test(await panelText(page)), 6000));
        check('...and keeps the attempt too', /Attempts left: 1 of 1/.test(await region(page, 'checksLead').innerText()));
        await page.context().close();

        // ---- counts only: the server leaves the findings out, the page says why
        page = await open('index.ru.checks.html?checksreport=counts&checksmax=0');
        await region(page, 'checksOpen').click();
        check('unlimited attempts', /не ограничено/.test(await region(page, 'checksLead').innerText()));
        await region(page, 'start').click();
        await until(async() => startButton(page).isEnabled(), 6000);
        await startButton(page).click();
        check('counts-only report: mark, no findings', !!await until(async() => (await page.locator('.chk-mark-value').count()) > 0, 6000)
            && (await page.locator('.chk-findings').count()) === 0 && /только итог/.test(await panelText(page)));
        await page.context().close();

        // ---- a teacher: the gaps in the data, by reason, in the language of the page
        page = await open('index.ru.checks.html?checkteacher&checksmax=0');
        await region(page, 'checksOpen').click();
        await region(page, 'start').click();
        await until(async() => startButton(page).isEnabled(), 6000);
        await startButton(page).click();
        await until(async() => (await page.locator('.chk-mark-value').count()) > 0, 6000);
        const gaps = await page.locator('ul.chk-note li').allInnerTexts();
        check('a teacher is told what was not checked', /Проверено не всё:/.test(await panelText(page)) && gaps.length === 2
            && /не собираются факты.*правил: 2/.test(gaps[0]) && /ошибкой.*правил: 1/.test(gaps[1]), gaps.join(' | '));
        check('...without rule codes', !/no_collector|address_not_saved/.test(await panelText(page)));
        await page.screenshot({path: path.join(SHOTS, 'checks-teacher.png')});
        await page.context().close();

        // ---- an observer: the runs of the student, no button
        page = await open('index.ru.checks.html?observer');
        await region(page, 'checksOpen').click();
        await sleep(300);
        check('observer: no check button', (await startButton(page).count()) === 0);
        check('observer: says whose checks', /Проверки студента/.test(await region(page, 'checksLead').innerText()));
        await page.context().close();

        // ---- a phone in the dark theme: the panel fits the width
        page = await open('index.ru.checks.html?theme=dark', {width: 360, height: 740});
        await region(page, 'checksOpen').click();
        const box = await region(page, 'checksPanel').boundingBox();
        check('phone: the panel fits the screen', !!box && box.x >= 0 && box.x + box.width <= 360, JSON.stringify(box));
        await page.screenshot({path: path.join(SHOTS, 'checks-phone-dark.png')});
        await page.context().close();

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
