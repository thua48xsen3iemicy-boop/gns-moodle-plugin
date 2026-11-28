'use strict';
/**
 * Checks the teacher's setup steps of a lab (docs/lab-setup/README.md §6, §6a; plugin docs/lab-setup.md) in Chromium:
 * the "Setup" block of the lab editor (setupform.js) and its window of the prompt (promptwindow.js): the prompt carries
 * the devices with what they take, the segments with their addresses, the steps and the task; the answer becomes cards
 * with the editor's own problems and, after "Check with the lab manager", the manager's (stub: PC2 has no guest agent);
 * "Replace the setup" and "Add to the existing steps" are one change each, undone by "Undo"; the manual fields and the
 * JSON; a manager without lab-setup shows the steps read only. The lab page: SETUP_FAILED is a general text for the
 * student and the step for an observer. The authoring run (mock manager): a failed step offers "Prompt: fix the step",
 * which opens the window in the editor, and the answer replaces that step only. The checks prompt of an activity names
 * the setup steps of its lab without their code.
 *
 *   cd plugin/devtools/ui-harness && npm install && node check_setup.js [--min] [--shots <dir>]
 *
 * It starts the server of server.js (port 18141) and the mock manager (18142, its slot 18143). Environment: PYTHON with
 * aiohttp (default /tmp/gns-venv/bin/python if it exists, else python3). --min runs the minified modules of
 * plugin/amd/build. Playwright with Chromium must be installed. The exit code is the number of failed checks.
 */
const {execFileSync, spawn} = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');
const {chromium} = require('playwright');

const PORTS = {page: 18141, mock: 18142, slot: 18143};
const BASE = `http://127.0.0.1:${PORTS.page}`;
const MOCK = `http://127.0.0.1:${PORTS.mock}`;
const MIN = process.argv.includes('--min') ? '?min' : '';
const Q = MIN ? MIN + '&' : '?';
const PYTHON = process.env.PYTHON || (fs.existsSync('/tmp/gns-venv/bin/python') ? '/tmp/gns-venv/bin/python' : 'python3');
const shotArg = process.argv.indexOf('--shots');
const SHOTS = path.resolve(shotArg > 0 ? process.argv[shotArg + 1] : path.join(__dirname, 'dist/shots'));

let failed = 0;
const check = (name, ok, detail = '') => {
    console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail && !ok ? '  ' + detail : ''}`);
    if (!ok) {
        failed++;
    }
};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
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
        await sleep(50);
    }
}
const get = url => new Promise((resolve, reject) => {
    http.get(url, res => {
        res.resume();
        resolve(res.statusCode);
    }).on('error', reject);
});
const post = (url, body) => new Promise((resolve, reject) => {
    const req = http.request(url, {method: 'POST', headers: {'Content-Type': 'application/json'}}, res => {
        res.resume();
        resolve(res.statusCode);
    });
    req.on('error', reject);
    req.end(JSON.stringify(body));
});
const text = loc => loc.innerText().then(t => t.replace(/\s+/g, ' ').trim());
// The status pill is drawn in capitals (text-transform): its text, not what is shown.
const raw = loc => loc.textContent().then(t => t.replace(/\s+/g, ' ').trim());
const answer = value => 'Вот шаги:\n```json\n' + JSON.stringify({setup: value}, null, 2) + '\n```\nГотово.';
const saved = page => page.evaluate(() => JSON.parse(JSON.stringify(window.__stub.editor.saved)));

(async() => {
    fs.mkdirSync(SHOTS, {recursive: true});
    execFileSync(process.execPath, [path.join(__dirname, 'build.js')], {cwd: __dirname, stdio: 'ignore'});
    const servers = [
        spawn(PYTHON, [path.join(__dirname, '../mock-manager/mock_manager.py')], {
            env: {...process.env, MOCK_PORT: PORTS.mock, MOCK_SLOT_PORT: PORTS.slot}, stdio: 'ignore',
        }),
        spawn(process.execPath, [path.join(__dirname, 'server.js'), PORTS.page], {
            env: {...process.env, HARNESS_MOCK_URL: MOCK, HARNESS_SLOT_URL: `ws://127.0.0.1:${PORTS.slot}`}, stdio: 'ignore',
        }),
    ];
    const browser = await chromium.launch({executablePath: process.env.CHROMIUM_BIN || undefined});
    const errors = [];
    try {
        const ready = await until(async() => (await get(`${BASE}/editor.ru.setup.html`)) === 200
            && (await get(`${MOCK}/api/v1/info`)) === 401, 15000);
        check('the harness server and the mock manager are up', !!ready, 'is aiohttp installed for ' + PYTHON + '?');
        if (!ready) {
            throw new Error('servers did not start');
        }
        const context = await browser.newContext({viewport: {width: 1440, height: 900}});
        const page = await context.newPage();
        page.on('pageerror', e => errors.push(e.message));

        // ---- The block in the panel of the lab.
        await page.goto(`${BASE}/editor.ru.setup.html${MIN}`);
        const block = page.locator('[data-region="setup"]');
        await block.waitFor();
        check('the block "Преднастрой" shows the step of the lab as a card',
            /Преднастрой/.test(await text(block)) && (await block.locator('[data-setup-step]').count()) === 1
            && /nginx on PC1/.test(await text(block.locator('[data-setup-step]').first())));
        check('its main buttons are the prompt and the answer',
            (await block.locator('[data-act="setupprompt"]').count()) === 1
            && (await block.locator('[data-act="setupanswer"]').count()) === 1);

        // ---- The prompt.
        await block.locator('[data-act="setupprompt"]').click();
        const dialog = page.locator('dialog.ed-prompt-dialog');
        await dialog.waitFor();
        await dialog.locator('[data-prompt-task]').fill('на R1 OSPF area 0, на PC3 DNS 10.0.0.53');
        const prompt = await dialog.locator('[data-prompt-text]').inputValue();
        check('the prompt is the setup prompt without its leading comments and marks',
            /Ты помогаешь преподавателю подготовить лабораторную работу/.test(prompt) && !/<!--/.test(prompt)
            && !/\[\[(LAB|SETUP|TASK)\]\]/.test(prompt), prompt.slice(0, 200));
        const lab = JSON.parse(prompt.slice(prompt.indexOf('## Лаба') + 7, prompt.indexOf('## Уже есть')).trim());
        const node = name => lab.nodes.find(n => n.name === name) || {};
        check('[[LAB]]: a Debian takes "run" in sh, IOL and VPCS take "config", the ISP takes nothing',
            node('PC1').step === 'run' && node('PC1').shell === 'sh' && node('R1').step === 'config'
            && node('PC3').step === 'config' && node('ISP1').step === null && !('shell' in node('R1')), JSON.stringify(lab.nodes));
        check('[[LAB]]: links by ports and the segment with its planned addresses',
            lab.links.some(l => l.join('|') === 'SW1 Ethernet0|PC1 Ethernet0')
            && lab.segments[0].name === 'LAN1' && lab.segments[0].gateway.ip === '192.168.10.254/24'
            && lab.segments[0].members.some(m => m.port === 'PC3 Ethernet0' && m.ip === '192.168.10.20/24'),
        JSON.stringify(lab.segments));
        check('[[SETUP]] has the steps of the lab, [[TASK]] the task', /"id": "web"/.test(prompt)
            && /на R1 OSPF area 0, на PC3 DNS 10\.0\.0\.53/.test(prompt));

        // ---- The answer: cards, local problems, the manager's problems.
        await dialog.locator('[data-prompt-tab="answer"]').first().click();
        const area = dialog.locator('[data-prompt-answer]');
        await area.fill('не JSON');
        check('a text without JSON is not an answer', /не JSON со списком шагов/.test(await text(dialog.locator('[data-answer-status]')))
            && await dialog.locator('[data-prompt-apply="replace"]').isDisabled());
        await area.fill(answer([
            {id: 'ospf', title: 'OSPF на R1', node: 'R1', config: 'router ospf 1\n network 0.0.0.0 255.255.255.255 area 0'},
            {id: 'far', title: 'на R9', node: 'R9', run: 'true'},
            {id: 'motd', title: 'motd на PC2', node: 'PC2', run: 'echo hi > /etc/motd'},
            {id: 'isp', title: 'ISP', node: 'ISP1', run: 'true'},
            {id: 'ospf', title: 'ещё раз', node: 'R1', run: 'true', shell: 'routeros'},
            {id: 'sh', title: 'не та оболочка', node: 'PC1', run: 'true', shell: 'routeros', timeout: 500},
            {id: 'ru', title: 'кириллица', node: 'PC3', config: 'ip dns сервер'},
        ]));
        const cards = dialog.locator('[data-answer-step]');
        const card = i => text(cards.nth(i));
        check('the answer in a ```json block gives seven cards', (await cards.count()) === 7
            && /Шагов в ответе: 7/.test(await text(dialog.locator('[data-answer-status]'))));
        check('the editor sees: a device the lab lacks, a device without steps, a repeated id, a field of another kind',
            /в лабе нет устройства R9/.test(await card(1)) && /на ISP1 шаги преднастроя не выполняются/.test(await card(3))
            && /идентификатор ospf повторяется/.test(await card(4)) && /поле run не для этого устройства/.test(await card(4))
            && !/bad|ошиб/.test(await card(0)), await card(4));
        check('…another shell, a time limit out of range and non-ASCII configuration lines',
            /другая оболочка: sh/.test(await card(5)) && /от 1 до 120/.test(await card(5))
            && /только печатные символы ASCII/.test(await card(6)));
        await dialog.locator('[data-prompt-validate]').click();
        await until(async() => /Ошибок у менеджера/.test(await text(dialog.locator('[data-prompt-checked]'))));
        check('"Check with the lab manager": the problem of PC2 goes onto its card, R9 is said once',
            /node: на этом устройстве шаги преднастроя не выполняются/.test(await card(2))
            && ((await card(1)).match(/нет устройства R9/g) || []).length === 1 && !/node:/.test(await card(1)), await card(1));
        const sent = await page.evaluate(() => window.__stub.editor.setupValidated.slice(-1)[0]);
        check('the manager is asked about the lab with the answer as its setup', Array.isArray(sent) && sent.length === 7);
        await page.screenshot({path: path.join(SHOTS, 'setup-answer.png')});

        // ---- Replace, undo, append.
        const good = [
            {id: 'ospf', title: 'OSPF на R1', node: 'R1', config: 'router ospf 1\n network 0.0.0.0 255.255.255.255 area 0'},
            {id: 'dns', title: 'DNS на PC3', node: 'PC3', config: 'ip dns 10.0.0.53'},
            {id: 'web', title: 'nginx на PC1 (новый)', node: 'PC1', run: 'apt-get install -y nginx'},
        ];
        await area.fill(answer(good));
        check('"Add" is not offered for ids the lab has already, "Replace" is',
            await dialog.locator('[data-prompt-apply="append"]').isDisabled()
            && /web/.test(await text(dialog.locator('[data-prompt-blocked]')))
            && !(await dialog.locator('[data-prompt-apply="replace"]').isDisabled()));
        await dialog.locator('[data-prompt-apply="replace"]').click();
        await until(async() => (await page.locator('dialog.ed-prompt-dialog').count()) === 0);
        check('"Replace the setup": three cards in the block', (await block.locator('[data-setup-step]').count()) === 3
            && /DNS на PC3/.test(await text(block)));
        await page.locator('[data-act="save"]').click();
        await until(async() => (await saved(page)) && (await saved(page)).setup);
        let def = await saved(page);
        check('the draft goes out as lab/2 with the three steps', def.format === 'gns3lab.lab/2'
            && def.setup.map(x => x.id).join() === 'ospf,dns,web', JSON.stringify(def.setup));
        await page.locator('[data-act="undo"]').click();
        check('"Undo" brings back the one step of before', (await block.locator('[data-setup-step]').count()) === 1
            && /nginx on PC1/.test(await text(block)));
        await block.locator('[data-act="setupanswer"]').click();
        await dialog.waitFor();
        check('the answer tab opens with the window', await dialog.locator('[data-pane="answer"]').isVisible());
        await dialog.locator('[data-prompt-answer]').fill(answer([good[1]]));
        await dialog.locator('[data-prompt-apply="append"]').click();
        check('"Add to the existing steps": web, then dns', (await block.locator('[data-setup-step]').count()) === 2
            && /DNS на PC3/.test(await text(block.locator('[data-setup-step]').nth(1))));

        // ---- Manual fields and the JSON.
        await block.locator('[data-fold-name="manual"]').click();
        const title = block.locator('[data-step="1"] [data-stfield="title"]');
        await title.fill('DNS 10.0.0.53 на PC3');
        await title.press('Tab');
        check('a field of a step changes its card', /DNS 10\.0\.0\.53 на PC3/.test(await text(block.locator('[data-setup-step]').nth(1)))
            && await block.locator('[data-setup-fold="manual"]').evaluate(d => d.open));
        const kinds = await block.locator('[data-step="0"] [data-stfield]').evaluateAll(l => l.map(x => x.dataset.stfield));
        const kinds1 = await block.locator('[data-step="1"] [data-stfield]').evaluateAll(l => l.map(x => x.dataset.stfield));
        check('a Debian step has a script, a shell and a time; a VPCS step has configuration lines',
            kinds.includes('run') && kinds.includes('shell') && kinds.includes('timeout') && kinds1.includes('config')
            && !kinds1.includes('run'), kinds1.join());
        await block.locator('[data-act="addstep"]').click();
        check('"Add a step" adds one on the first device that takes steps',
            (await block.locator('[data-setup-step]').count()) === 3);
        await block.locator('[data-step="2"] [data-act="delstep"]').click();
        await block.locator('[data-fold-name="json"]').click();
        const json = block.locator('[data-setupjson]');
        await json.fill('{"not": "a list"}');
        await json.press('Tab');
        check('JSON that is not a list of steps is refused with a message',
            /не список шагов/.test(await text(page.locator('[data-region="setupfielderror"]'))));
        await page.locator('[data-act="check"]').click();
        await page.locator('[data-region="setup"]').waitFor();
        check('"Check" of the lab: no setup problem for steps the manager takes',
            !/Преднастрой ·/.test(await text(page.locator('[data-region="props"]'))));
        await page.evaluate(() => 0);

        // ---- A step on PC2 checked by the whole lab: the problem names the step and lands on its card.
        await page.goto(`${BASE}/editor.ru.setup.html${MIN}`);
        await block.waitFor();
        await block.locator('[data-act="setupanswer"]').click();
        await dialog.locator('[data-prompt-answer]').fill(answer([{id: 'motd', title: 'motd на PC2', node: 'PC2', run: 'true'}]));
        await dialog.locator('[data-prompt-apply="replace"]').click();
        await page.locator('[data-act="check"]').click();
        await until(async() => /Преднастрой · motd на PC2/.test(await text(page.locator('[data-region="props"]'))));
        check('a problem of the manager in the setup names the step in the list and lands on its card',
            /Преднастрой · motd на PC2 · node/.test(await text(page.locator('[data-region="props"]')))
            && /не выполняются/.test(await text(page.locator('[data-setup-step]').first())));

        // ---- A manager without lab-setup: the steps are kept and shown read only.
        await page.goto(`${BASE}/editor.ru.setup-kept.html${MIN}`);
        await block.waitFor();
        check('without lab-setup the steps are shown read only', (await block.locator('[data-setup-step]').count()) === 1
            && (await block.locator('[data-act="setupprompt"]').count()) === 0
            && (await block.locator('[data-setup-fold="manual"]').count()) === 0
            && await block.locator('[data-setupjson]').evaluate(t => t.readOnly)
            && /не выполняет преднастрой/.test(await text(block)));
        await page.locator('[data-act="save"]').click();
        await until(async() => (await saved(page)));
        def = await saved(page);
        check('…and kept in the draft', def.setup && def.setup.length === 1 && def.format === 'gns3lab.lab/2');

        // ---- The checks prompt of an activity names the setup of its lab without the code.
        await page.goto(`${BASE}/checksform.ru.own.html${MIN}`);
        await page.locator('[data-act="checksprompt"]').click();
        const checksPrompt = await until(async() => page.locator('dialog.ed-prompt-dialog [data-prompt-text]').inputValue());
        check('the checks prompt carries the setup steps of the lab: title and device, no code',
            /"setup": \[/.test(checksPrompt || '') && /"title": "nginx on PC1"/.test(checksPrompt || '')
            && !/"run"/.test(checksPrompt.slice(checksPrompt.indexOf('"setup"'), checksPrompt.indexOf('\n## ',
                checksPrompt.indexOf('"setup"')))));

        // ---- The lab page: the student gets the general text, the observer the step.
        await page.goto(`${BASE}/index.ru.html${Q}fast&setupfail`);
        await page.locator('[data-region="start"]').click();
        const status = page.locator('[data-region="status"]');
        await until(async() => /не подготовилась/.test(await raw(status)), 8000);
        check('the student: "the lab was not prepared, tell your teacher", without the step',
            /Лаба не подготовилась: ошибка в преднастрое лабы\. Сообщите преподавателю/.test(await raw(status))
            && await page.locator('[data-region="setuperror"]').isHidden());
        check('…and no "start again"', !/снова/.test(await text(page.locator('[data-region="start"]'))));
        await page.goto(`${BASE}/index.ru.html${Q}setupfail&observer`);
        const detail = page.locator('[data-region="setuperror"]');
        await until(async() => detail.isVisible(), 8000);
        check('the observer sees the step, the device, the code and the line as text',
            /Шаг преднастроя «web» на PC1 вышел с кодом 100: E: Unable to locate package <nginx>/.test(await text(detail))
            && await page.locator('[data-region="setupfix"]').isHidden());

        // ---- The authoring run: the failed step and "Prompt: fix the step" in the editor.
        await page.goto(`${BASE}/editor.ru.setup-authoring.html${MIN}`);
        await page.locator('[data-region="setup"]').waitFor();
        const run = page.getByRole('button', {name: 'Запустить', exact: true});
        await until(async() => !(await run.isDisabled()), 8000);
        check('the mock manager takes the order to fail a setup step',
            (await post(`${MOCK}/mock/setup/fail-next`, {step: 'web', exit_code: 100,
                message: 'E: Unable to locate package nginx'})) === 200);
        await run.click();
        await page.locator('dialog iframe').waitFor();
        const frame = page.frameLocator('dialog iframe');
        const fixButton = frame.locator('[data-region="setupfix"]');
        const shown = await until(async() => fixButton.isVisible(), 20000);
        check('the authoring page shows the failed step and "Prompt: fix the step"', !!shown
            && /«web» на PC1 вышел с кодом 100: E: Unable to locate package nginx/.test(
                await text(frame.locator('[data-region="setuperror"]')))
            && /не подготовилась/.test(await raw(frame.locator('[data-region="status"]'))));
        await page.screenshot({path: path.join(SHOTS, 'setup-authoring-failed.png')});
        await fixButton.click();
        const fix = page.locator('dialog.ed-prompt-dialog');
        await until(async() => (await fix.count()) === 1, 5000);
        const task = await fix.locator('[data-prompt-task]').inputValue().catch(() => '');
        check('the window of the prompt opens in the editor with the step, the error and the code in the task',
            /Шаг web на PC1 вышел с кодом 100: E: Unable to locate package nginx/.test(task)
            && /apt-get install -y nginx/.test(task) && /Верни только его/.test(task)
            && /Исправление шага web/.test(await text(fix.locator('[data-prompt-note]'))), task);
        await fix.locator('[data-prompt-tab="answer"]').first().click();
        await fix.locator('[data-prompt-answer]').fill(answer([{id: 'web', title: 'nginx на PC1, с обновлением', node: 'PC1',
            run: 'apt-get update && apt-get install -y nginx'}, {id: 'extra', title: 'x', node: 'PC1', run: 'true'}]));
        await fix.locator('[data-prompt-apply="replacestep"]').click();
        await until(async() => (await page.locator('dialog.ed-prompt-dialog').count()) === 0);
        await page.getByRole('button', {name: 'Вернуться в редактор', exact: true}).click();
        await page.locator('[data-act="save"]').click();
        await until(async() => (await saved(page)) && (await saved(page)).setup);
        def = await saved(page);
        check('the answer replaces that step only', def.setup.length === 2 && def.setup[0].id === 'web'
            && /apt-get update/.test(def.setup[0].run) && def.setup[1].id === 'motd', JSON.stringify(def.setup));

        check('no errors on the pages', !errors.length, errors.join(' | '));
    } catch (err) {
        check('the run completes', false, err && err.stack ? err.stack : String(err));
    } finally {
        await browser.close();
        servers.forEach(s => s.kill());
    }
    process.exit(failed);
})();
