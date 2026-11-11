'use strict';
/**
 * Checks the maintenance notice of the lab page (manager CONTRACTS §2.11) in Chromium.
 *
 *   announced, no lab → the notice counts down, the start button is off → a running lab sees the notice and its time
 *   left runs to the start of the maintenance → at the start the lab stops and the notice says maintenance is running →
 *   the maintenance ends → the notice goes, the lab can be started again.
 *
 * The stub of core/ajax plays the manager (stubs/ajax.js, `?maintenance=<seconds>`, window.__stub.maintenance). The page
 * polls a running lab every 15 s, so the whole check takes about a minute.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_maintenance.js [--min]
 *
 * --min runs the minified modules of plugin/amd/build instead of amd/src. The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18131;
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
        await sleep(100);
    }
}

const get = url => new Promise((resolve, reject) => {
    http.get(url, res => {
        res.resume();
        resolve(res.statusCode);
    }).on('error', reject);
});

/** Seconds of an mm:ss or h:mm:ss text, or null. */
const seconds = text => {
    const m = /(?:(\d+):)?(\d\d):(\d\d)/.exec(text || '');
    return m ? (+(m[1] || 0)) * 3600 + (+m[2]) * 60 + (+m[3]) : null;
};

(async() => {
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch();
    try {
        const ready = await until(async() => (await get(`${BASE}/gw/stats`)) === 200, 10000);
        if (!ready) {
            throw new Error('server did not start');
        }
        const problems = [];
        const open = async url => {
            const page = await (await browser.newContext({viewport: {width: 1440, height: 860}})).newPage();
            page.on('pageerror', e => problems.push('pageerror: ' + e.message));
            await page.goto(`${BASE}/${url}${url.includes('?') ? '&' : '?'}${MIN ? 'min&' : ''}fast`);
            return page;
        };
        const notice = page => page.locator('[data-region="maintenance"]');
        const start = page => page.locator('[data-region="start"]');

        // ---- announced, no lab yet: a countdown, and nothing to start
        let page = await open('index.en.html?maintenance=600');
        await notice(page).waitFor({state: 'visible', timeout: 8000});
        let text = await notice(page).innerText();
        check('the notice counts down to the maintenance', /all labs will be stopped in (09:5\d|10:00)/.test(text), text);
        check('...and shows the note of the administrator', text.includes('Administrator: Image update'), text);
        check('the start button is off while a maintenance is announced', await start(page).isDisabled());
        await page.context().close();

        // ---- a running lab: the notice appears with the next poll, the time left ends at the maintenance
        page = await open('index.ru.html');
        await start(page).click();
        await page.locator('[data-region="status"]', {hasText: 'Лаба работает'}).waitFor({timeout: 15000});
        check('no notice without a maintenance', await notice(page).isHidden());
        await page.evaluate(() => window.__stub.maintenance(25, 'Обновление образов'));
        check('the running lab sees the announcement with its next poll',
            !!(await until(async() => await notice(page).isVisible(), 17000)));
        text = await notice(page).innerText();
        check('...in Russian, with a countdown', /будут остановлены через 00:[0-2]\d/.test(text), text);
        const left = seconds(await page.locator('[data-region="timeleft"]').innerText());
        check('the time left runs to the start of the maintenance, not to the lab limit', left !== null && left <= 25, String(left));

        // ---- the maintenance starts: the lab is stopped, the notice says so
        const stopped = await until(async() =>
            (await page.locator('[data-region="status"]').innerText()).toLowerCase().includes('остановлена'), 50000);
        check('the lab is stopped when the maintenance starts', !!stopped,
            await page.locator('[data-region="status"]').innerText());
        text = await notice(page).innerText();
        check('...and the notice says the maintenance is running', text.includes('Идёт техническое обслуживание'), text);
        check('...the start button stays off', await start(page).isDisabled());

        // ---- the maintenance ends: the page notices it by itself
        await page.evaluate(() => window.__stub.maintenance(null));
        check('the notice goes when the maintenance ends', !!(await until(async() => await notice(page).isHidden(), 17000)));
        check('...and the lab can be started again', await start(page).isEnabled());
        check('no page errors', !problems.length, problems.join('; '));
    } catch (e) {
        check('the check ran to the end', false, e.message);
    } finally {
        await browser.close();
        server.kill();
    }
    process.exit(failed);
})();
