'use strict';
/** Browser check of derived image authoring against the real development mock manager.
 * Start mock-manager, gateway and ui-harness on 18090, 18080 and 18123 as documented.
 * Moodle capabilities and database concurrency require dev-Moodle and are not emulated here.
 */
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const {chromium} = require('playwright');
const base = process.env.HARNESS_URL || 'http://127.0.0.1:18123';
const min = process.argv.includes('--min') ? '?min' : '';
const shots = process.env.SHOTS || path.join(__dirname, 'dist/derived-shots');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(test, timeout = 20000) {
    const end = Date.now() + timeout;
    while (Date.now() < end) {
        if (await test()) return;
        await sleep(200);
    }
    throw new Error('Timed out waiting for authoring state');
}
async function call(args) {
    const response = await fetch(base + '/authoring-api', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({labid: 1, ...args})});
    const body = await response.json();
    if (!response.ok) throw new Error(body.message);
    return body;
}
(async() => {
    fs.mkdirSync(shots, {recursive: true});
    const executablePath = process.env.CHROMIUM_BIN || '/usr/lib/chromium/chromium';
    const browser = await chromium.launch({executablePath, args: ['--no-sandbox']});
    const errors = [];
    try {
        const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(base + '/editor.ru.authoring.html' + min);
        const start = page.getByRole('button', {name: 'Запустить', exact: true});
        await start.waitFor();
        await until(async() => (await call({action: 'state'})).state !== undefined);
        const current = (await call({action: 'state'})).state.session;
        if (!current || ['stopped', 'failed'].includes(current.status)) {
            await start.click();
            await page.locator('dialog iframe').waitFor();
        }
        await until(async() => (await call({action: 'state'})).state.session?.status === 'running');
        if (await page.locator('dialog iframe').count()) {
            await page.getByRole('button', {name: 'Вернуться в редактор', exact: true}).click();
        }
        // The authoring page uses the same Stage/Consoles/ConsoleConnection modules as a student.
        const popupPromise = page.waitForEvent('popup');
        await page.getByRole('button', {name: 'Открыть консоли', exact: true}).click();
        const popup = await popupPromise;
        popup.on('pageerror', error => errors.push(error.message));
        await popup.locator('.node.st-started').first().waitFor({timeout: 15000});
        const state = (await call({action: 'state'})).state;
        const topology = JSON.parse(state.session.topology);
        const pc = topology.topology.nodes.find(n => n.name === 'PC1');
        const halo = popup.locator(`.node[data-node="${pc.node_id}"] circle.halo`);
        const box = await halo.boundingBox();
        await popup.mouse.dblclick(box.x + box.width / 2, box.y + box.height / 2);
        await popup.locator('.xterm').first().waitFor();
        await until(async() => (await popup.locator('.xterm-screen').innerText()).includes('PC1'));
        await popup.screenshot({path: path.join(shots, 'console.png')});
        await popup.locator('.xterm-helper-textarea').focus();
        await popup.keyboard.type('poweroff');
        await popup.keyboard.press('Enter');
        await until(async() => (await call({action: 'state'})).capturecandidates.some(n => n.name === 'PC1' && n.status === 'stopped'));
        console.log('PASS a guest powered off from its console remains a capture candidate');
        await popup.close();
        console.log('PASS authoring console uses real mock slot through gateway');
        await page.getByRole('button', {name: 'Сохранить с образами', exact: true}).click();
        const dialog = page.locator('.ed-capture-dialog');
        await dialog.waitFor();
        assert.equal(await dialog.locator('fieldset').count(), 2, 'capture:false switch is excluded');
        assert.match(await dialog.innerText(), /machine-id/);
        assert.match(await dialog.innerText(), /PC1 — выключена/);
        assert.match(await dialog.innerText(), /PC2 — работает/);
        await dialog.locator('textarea').first().fill('FRR configured <script>unsafe</script>');
        await page.screenshot({path: path.join(shots, 'capture-dialog.png')});
        await dialog.getByRole('button', {name: 'Сохранить с образами', exact: true}).click();
        await until(async() => (await call({action: 'capturestate'})).capture.state === 'done');
        const firstCapture = (await call({action: 'capturestate'})).capture;
        assert.deepEqual(firstCapture.forced, []);
        await page.locator(`[data-edit-image="${firstCapture.images[0].image}"]`).waitFor({timeout: 15000});
        await until(() => start.isEnabled());
        await page.locator('[data-act="save"]').click();
        const saved = await page.evaluate(() => window.__stub.editor.saved);
        assert(saved.nodes.filter(n => n.name.startsWith('PC')).every(n => /^d-[a-f0-9]{12}$/.test(n.image)));
        assert.equal(saved.nodes.find(n => n.name === 'SW1').image, 'linux-switch');
        assert.equal(await page.locator('.ed-image-entry script').count(), 0);
        console.log('PASS selected images replace only draft nodes; text is escaped');
        // Recommendation is visible immediately and is persisted by the mock API.
        await page.locator('[data-feature-image]').first().click();
        await until(async() => (await call({action: 'images'})).images.some(i => i.featured));
        console.log('PASS featured PATCH updates catalog');
        // Fail after stopping the next authoring run, then retry the same stopped session.
        const before = (await call({action: 'images'})).images.filter(i => i.derived).length;
        await start.click();
        await page.locator('dialog iframe').waitFor();
        await until(async() => (await call({action: 'state'})).state.session?.status === 'running');
        await page.getByRole('button', {name: 'Вернуться в редактор', exact: true}).click();
        await fetch((process.env.MOCK_URL || 'http://127.0.0.1:18090') + '/mock/capture-fail/on', {method: 'POST'});
        await page.getByRole('button', {name: 'Сохранить с образами', exact: true}).click();
        await page.locator('.ed-capture-dialog').getByRole('button', {name: 'Сохранить с образами', exact: true}).click();
        await page.locator('[data-notification]', {hasText: 'CAPTURE_NODE_MISSING'}).waitFor({timeout: 15000});
        assert.equal((await call({action: 'images'})).images.filter(i => i.derived).length, before);
        console.log('PASS failed capture publishes no partial catalog');
        await fetch((process.env.MOCK_URL || 'http://127.0.0.1:18090') + '/mock/capture-fail/off', {method: 'POST'});
        await page.getByRole('button', {name: 'Сохранить с образами', exact: true}).click();
        await page.locator('.ed-capture-dialog').getByRole('button', {name: 'Сохранить с образами', exact: true}).click();
        await until(async() => (await call({action: 'capturestate'})).capture.state === 'done');
        const repeatedCapture = (await call({action: 'capturestate'})).capture;
        await page.locator(`[data-edit-image="${repeatedCapture.images[0].image}"]`).waitFor({timeout: 15000});
        await until(() => start.isEnabled());
        assert.equal(await page.locator('[data-edit-image]').count(), 2);
        assert((await call({action: 'images'})).images.filter(i => i.derived?.supersedes).length >= 2);
        console.log('PASS retry and supersedes hide previous image versions');
        const managedId = repeatedCapture.images[0].image;
        await page.locator(`[data-edit-image="${managedId}"]`).click();
        let editDialog = page.locator('.ed-capture-dialog');
        await editDialog.locator('[name="retired"]').check();
        await editDialog.getByRole('button', {name: 'Сохранить', exact: true}).click();
        await until(async() => (await call({action: 'images'})).images.find(i => i.id === managedId).retired);
        const retiredGroup = page.locator('.ed-palette details').filter({has: page.locator('summary', {hasText: 'Мои образы вне палитры'})});
        await retiredGroup.locator('summary').first().click();
        assert(await page.locator(`[data-edit-image="${managedId}"]`).locator('..').locator('.ed-img').isDisabled());
        await page.locator(`[data-edit-image="${managedId}"]`).click();
        editDialog = page.locator('.ed-capture-dialog');
        await editDialog.locator('[name="retired"]').uncheck();
        await editDialog.getByRole('button', {name: 'Сохранить', exact: true}).click();
        await until(async() => !(await call({action: 'images'})).images.find(i => i.id === managedId).retired);
        console.log('PASS retiring hides a selectable image and can be reversed by its author');
        const mock = process.env.MOCK_URL || 'http://127.0.0.1:18090';
        const beginRun = async() => {
            await start.click();
            await page.locator('dialog iframe').waitFor();
            await until(async() => (await call({action: 'state'})).state.session?.status === 'running');
            await page.getByRole('button', {name: 'Вернуться в редактор', exact: true}).click();
        };
        const chooseCapture = async() => {
            await page.getByRole('button', {name: 'Сохранить с образами', exact: true}).click();
            await page.locator('.ed-capture-dialog').getByRole('button', {name: 'Сохранить с образами', exact: true}).click();
        };
        const waitApplied = async() => {
            await until(async() => (await call({action: 'capturestate'})).capture.state === 'done');
            const result = (await call({action: 'capturestate'})).capture;
            await page.locator(`[data-edit-image="${result.images[0].image}"]`).waitFor({timeout: 15000});
            await until(() => start.isEnabled());
            return result;
        };
        await beginRun();
        await fetch(mock + '/mock/shutdown-fail', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({nodes: {PC2: 'GUEST_AGENT_UNAVAILABLE'}})});
        const beforeBlocked = (await call({action: 'images'})).images.filter(i => i.derived).length;
        await chooseCapture();
        let blocked = page.locator('.ed-shutdown-dialog');
        await blocked.waitFor({timeout: 15000});
        assert.match(await blocked.innerText(), /PC2: Агент гостя недоступен/);
        assert.equal((await call({action: 'state'})).state.session.status, 'running');
        assert.equal((await call({action: 'images'})).images.filter(i => i.derived).length, beforeBlocked);
        await page.screenshot({path: path.join(shots, 'blocked-shutdown.png')});
        await blocked.getByRole('button', {name: 'Повторить', exact: true}).click();
        await page.locator('.ed-shutdown-dialog').waitFor({timeout: 15000});
        assert.equal((await call({action: 'state'})).state.session.status, 'running');
        console.log('PASS partial guest shutdown failure and Retry keep the session running');
        blocked = page.locator('.ed-shutdown-dialog');
        assert.match(await blocked.innerText(), /последние изменения могут потеряться/);
        await blocked.getByRole('button', {name: 'Сохранить как есть', exact: true}).click();
        const forced = await waitApplied();
        assert.deepEqual(forced.forced, ['PC2']);
        const forcedImage = forced.images.find(i => i.name === 'PC2').image;
        assert.equal(await page.locator(`[data-forced-image="${forcedImage}"]`).count(), 1);
        assert.equal(await page.locator(`[data-forced-image="${forced.images.find(i => i.name === 'PC1').image}"]`).count(), 0);
        console.log('PASS Save as is marks only the guest that failed shutdown');
        await fetch(mock + '/mock/shutdown-fail', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{"nodes":{}}'});
        await beginRun();
        await page.getByRole('button', {name: 'Остановить лабу', exact: true}).click();
        await until(async() => (await call({action: 'state'})).state.session.status === 'stopped');
        await chooseCapture();
        blocked = page.locator('.ed-shutdown-dialog');
        await blocked.waitFor();
        assert.match(await blocked.innerText(), /Запустите лабу снова/);
        await blocked.getByRole('button', {name: 'Сохранить как есть', exact: true}).click();
        const stoppedForce = await waitApplied();
        assert.deepEqual(stoppedForce.forced.sort(), ['PC1', 'PC2']);
        console.log('PASS stopped-session capture requires explicit Save as is');
        const search = page.locator('[data-image-search]');
        await search.fill('no matching image 12345');
        assert.equal(await page.locator('.ed-image-entry:visible').count(), 0);
        await search.fill('');
        await page.screenshot({path: path.join(shots, 'saved.png'), fullPage: true});
        await page.setViewportSize({width: 390, height: 844});
        await page.screenshot({path: path.join(shots, 'mobile.png'), fullPage: true});
        assert.deepEqual(errors, []);
        console.log('PASS image search, mobile view, no browser exceptions');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
