'use strict';
/**
 * Checks the auto-checks block of the activity form (checksform.js, docs/autochecks.md §12) in Chromium: an activity on
 * the checks of its lab takes them as its own, edits a script, the lab manager (stubs/ajax.js) checks the document
 * against the chosen lab, a move to a version without checks keeps those of the earlier one, the activity goes back to
 * the checks of its lab. Then the lab editor with activitychecks: no checks block, a note about the checks of the draft.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_checksform.js [--min]
 *
 * It starts the static server of server.js itself (port 18140). --min runs the minified modules of plugin/amd/build.
 * Playwright with Chromium must be installed (require('playwright')). The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORT = 18140;
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

const act = (page, name) => page.locator(`[data-act="${name}"]`).first();
const texts = (page, selector) => page.locator(selector).allInnerTexts().then(list => list.map(t => t.replace(/\s+/g, ' ').trim()));
const hidden = page => page.locator('input[name="checksdef"]').inputValue();
const doc = async page => {
    const v = await hidden(page);
    return v ? JSON.parse(v) : null;
};
const stub = (page, key) => page.evaluate(k => JSON.parse(JSON.stringify(window.__stub.checksform[k])), key);

(async() => {
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch({executablePath: process.env.CHROMIUM_BIN || undefined});
    try {
        const ready = await until(async() => (await get(`${BASE}/checksform.en.html`)) === 200, 10000);
        check('the harness server is up', !!ready);
        if (!ready) {
            throw new Error('server did not start');
        }
        const context = await browser.newContext({viewport: {width: 1280, height: 900}});
        const page = await context.newPage();
        const problems = [];
        page.on('pageerror', e => problems.push('pageerror: ' + e.message));
        page.on('console', m => {
            if (m.type() === 'error') {
                problems.push('console: ' + m.text());
            }
        });

        // ---- An activity on the checks of its lab.
        await page.goto(`${BASE}/checksform.en.html${MIN}`);
        await page.locator('[data-region="checkssource"]').waitFor();
        check('the activity uses the checks of its lab', /checks of the lab are used.*scripts: 1/
            .test(await page.locator('[data-region="checkssource"]').innerText()) && (await hidden(page)) === '');
        check('the block does not ask for the lab it was drawn for', (await stub(page, 'labs')).length === 0);

        await page.locator('[data-act="own"]').click();
        const own = await until(() => doc(page));
        check('own checks start from those of the lab', !!own && own.network === 'off' && own.scripts[0].id === 'ssh',
            JSON.stringify(own));
        check('...as fields of the script', await page.locator('fieldset.ed-script').count() === 1
            && (await page.locator('fieldset.ed-script [data-sfield="node"]').inputValue()) === 'PC1');
        const nodes = await page.locator('fieldset.ed-script [data-sfield="node"] option').allInnerTexts();
        check('a script is offered only the devices that take scripts', JSON.stringify(nodes) === '["PC1"]', JSON.stringify(nodes));

        await page.locator('fieldset.ed-script [data-sfield="title"]').fill('SSH server');
        await page.locator('fieldset.ed-script [data-sfield="title"]').press('Tab');
        const titled = await until(async() => {
            const d = await doc(page);
            return d && d.scripts[0].title === 'SSH server' && d;
        });
        check('a field of the script changes the document in the hidden field', !!titled);
        await page.locator('[data-network]').selectOption('auto');
        const net = await until(async() => {
            const d = await doc(page);
            return d && !('network' in d) && d;
        });
        check('the network check switch changes the document', !!net, JSON.stringify(net));

        await page.locator('[data-act="validate"]').click();
        await until(async() => /fit the lab/.test(await page.locator('[data-region="checksstatus"]').innerText()));
        const asked = (await stub(page, 'validated')).pop();
        check('the lab manager checks the document against the chosen version',
            asked && asked.labversionid === 11 && asked.courseid === 2 && JSON.parse(asked.checks).scripts[0].node === 'PC1',
            JSON.stringify(asked));

        // ---- Another version where PC1 is called PC9: the manager names the script.
        await page.locator('select[name="labversionid"]').selectOption('12');
        await until(async() => (await stub(page, 'labs')).length === 1);
        await page.locator('[data-act="validate"]').click();
        const bad = await until(async() => (await page.locator('.ed-problems li').count()) > 0
            && page.locator('.ed-problems li').first().innerText());
        check('a script on a device the new version lacks is a problem with its path',
            !!bad && /checks\.scripts\[0\]\.node/.test(bad), String(bad));
        const offered = await page.locator('fieldset.ed-script [data-sfield="node"] option').allInnerTexts();
        check('...and the devices of the new version are offered', offered.includes('PC9'), JSON.stringify(offered));

        // ---- Back to the checks of the lab; a move to a version without checks keeps those of the earlier one.
        await page.locator('select[name="labversionid"]').selectOption('11');
        await until(async() => (await stub(page, 'labs')).length === 2);
        await page.locator('[data-act="labchecks"]').click();
        check('"Use the checks of the lab" empties the hidden field', (await hidden(page)) === '');
        await page.locator('select[name="labversionid"]').selectOption('12');
        const kept = await until(() => doc(page));
        check('a version without checks takes those of the earlier version into the activity',
            !!kept && kept.scripts && kept.scripts[0].id === 'ssh', JSON.stringify(kept));
        check('...and says so', /taken into the activity/.test(await page.locator('#gns3lab-checksform').innerText()));

        // ---- The manager does not answer.
        await page.evaluate(() => {
            window.__stub.checksform.down = true;
        });
        await page.locator('[data-act="validate"]').click();
        await until(async() => /did not answer/.test(await page.locator('[data-region="checksstatus"]').innerText()));
        check('a manager that does not answer is named, not taken for a good document',
            /did not answer: unreachable/.test(await page.locator('[data-region="checksstatus"]').innerText()));

        // ---- An activity with its own checks, in Russian; the prompt window opens with the devices of the lab.
        await page.goto(`${BASE}/checksform.ru.own.html${MIN}`);
        await page.locator('fieldset.ed-script').waitFor();
        check('own checks of the activity are shown as they are', (await page.locator('fieldset.ed-script legend').innerText())
            === 'nginx' && /Свои проверки этой активности/.test(await page.locator('[data-region="checkssource"]').innerText()));
        await page.locator('[data-act="checksprompt"]').click();
        const prompt = await until(async() => page.locator('dialog.ed-prompt-dialog [data-prompt-text]').inputValue());
        check('the prompt for a neural network carries the devices of the lab', !!prompt && prompt.includes('"PC1"')
            && prompt.includes('"scripts": true') && !prompt.includes('[[LAB]]'));
        await page.locator('dialog.ed-prompt-dialog [data-cancel]').click();

        // ---- The lab editor when the checks belong to the activities.
        await page.goto(`${BASE}/editor.ru.activitychecks.html${MIN}`);
        await page.locator('[data-region="checksmoved"]').waitFor();
        check('the lab editor has no checks block, only a note about the checks of the draft',
            await page.locator('[data-checks]').count() === 0 && await page.locator('[data-network]').count() === 0
            && /настройках активности/.test(await page.locator('[data-region="checksmoved"]').innerText()));
        await page.locator('[data-act="save"]').first().click();
        const saved = await until(() => page.evaluate(() => window.__stub.editor.saved));
        check('...and the draft is saved without them', !!saved && !('checks' in saved), JSON.stringify(saved && Object.keys(saved)));

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
