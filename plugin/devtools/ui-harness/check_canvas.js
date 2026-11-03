'use strict';
/** Regression checks of the production Canvas, including its Moodle template and real filtered fact models.
 * Run after build.js: node check_canvas.js [--min]. Requires Playwright and Chromium, like check_reveal.js.
 */
const assert = require('assert/strict');
const {spawn} = require('child_process');
const path = require('path');
const {chromium} = require('playwright');
const PORT = 18129;
const base = `http://127.0.0.1:${PORT}`;
const min = process.argv.includes('--min') ? '&min' : '';
const near = (a, b) => Math.abs(a - b) < .1;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function captureStage(page) {
    await page.evaluate(() => new Promise(resolve => require(['mod_gns3lab/stage'], ({Stage}) => {
        const original = Stage.prototype.applyView;
        Stage.prototype.applyView = function(...args) {
            window.canvasStage = this;
            return original.apply(this, args);
        };
        resolve();
    })));
    await page.locator('[data-region="zoomFit"]').click();
    await page.waitForFunction(() => window.canvasStage);
    await sleep(400);
}

(async() => {
    const server = spawn(process.execPath, [path.join(__dirname, 'server.js'), PORT], {stdio: 'ignore'});
    const browser = await chromium.launch({executablePath: process.env.CHROMIUM_BIN || '/usr/bin/chromium',
        args: ['--no-sandbox']});
    const errors = [];
    try {
        for (let i = 0; i < 50; i++) {
            if (await fetch(base + '/gw/stats').then(r => r.ok).catch(() => false)) break;
            await sleep(100);
        }
        const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
        page.on('pageerror', e => errors.push(e.message));
        await page.goto(`${base}/index.en.html?fast${min}`);
        await page.locator('[data-region="start"]').click();
        await page.locator('[data-region="status"]', {hasText: 'The lab is running'}).waitFor();
        await page.waitForFunction(() => document.querySelectorAll('.duplex').length === 11);
        await captureStage(page);

        const selection = await page.evaluate(() => {
            const s = window.canvasStage, n = [...s.model.nodes.values()].find(n => n.name === 'SW1');
            s.select({kind: 'node', id: n.id});
            s.call('details', s.sel);
            return {node: n.id, links: s.model.links.filter(l => l.a.nodeId === n.id || l.b.nodeId === n.id).map(l => l.id).sort()};
        });
        assert.deepEqual(await page.locator('.link.on').evaluateAll(es => es.map(e => e.dataset.link).sort()), selection.links);
        await page.locator('[data-card-layer="l3"]').click();
        assert.equal(await page.locator('[data-layer="l1"]').getAttribute('aria-pressed'), 'true');
        assert.match(await page.locator('.details').innerText(), /10\.1\.99\./);
        await page.locator('.details > .x').click();
        assert.equal(await page.locator('.node.sel').getAttribute('data-node'), selection.node);
        assert.equal(await page.locator('.link.on').count(), selection.links.length);
        assert.equal(await page.locator('.card-lead').evaluate(e => getComputedStyle(e).display), 'none');
        console.log('PASS  independent card tabs; X retains neighbours and hides its tether');

        await page.locator('[data-region="assignmentsOpen"]').click();
        assert.match(await page.locator('.assignments').innerText(), /Explore the ports/);
        assert.equal(await page.locator('[data-region="assignmentsOpen"]').getAttribute('aria-expanded'), 'true');
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('.assignments').isHidden(), true);
        console.log('PASS  activity assignments open on the canvas and close with Escape');

        await page.locator('[data-layer="l2"]').click();
        const l2 = await page.evaluate(() => {
            const s = window.canvasStage, view = s.canvas.l2;
            const v = [...view.virtuals.values()].find(v => v.domain && v.vlan === 10);
            const authoritative = s.der.l2.domOf.get(v.id);
            s.select({kind: 'switchvlan', id: v.id}); s.call('details', s.sel);
            return {same: v.domain === authoritative, members: [...v.domain.nodes],
                marked: [...s.svg.querySelectorAll('[data-node].on')].map(e => e.dataset.node),
                phantomNative: view.segments.some(p => {
                    const r = s.der.l2.links.get(p.link.id);
                    return r.known && !r.issues.length && !p.unknown && (!p.va || !p.vb);
                }),
                paths: view.segments.filter(p => p.domain === v.domain).every(p => p.g.classList.contains('on'))};
        });
        assert.equal(l2.same && l2.paths, true);
        l2.members.forEach(id => assert.ok(l2.marked.includes(id)));
        assert.equal(l2.phantomNative, false);
        await page.locator('[data-card-layer="l1"]').click();
        assert.equal(await page.locator('[data-layer="l2"]').getAttribute('aria-pressed'), 'true');
        await page.locator('.details > .x').click();
        console.log('PASS  virtual VLANs use authoritative domains and highlight their actual members');

        await page.locator('[data-layer="l3"]').click();
        const l3 = await page.evaluate(() => {
            const s = window.canvasStage, subnet = s.der.l3.subnets.find(x => x.members.length > 2);
            s.select({kind: 'subnet', id: subnet.key}); s.call('details', s.sel);
            return {members: [...new Set(subnet.members.map(m => m.nodeId))].sort(),
                marked: [...s.svg.querySelectorAll('.node.on')].map(e => e.dataset.node).sort()};
        });
        assert.deepEqual(l3.marked, l3.members);
        assert.equal(await page.locator('[data-card-layer]').count(), 1);
        await page.locator('.details > .x').click();
        assert.equal(await page.locator('.node.on').count(), l3.members.length);
        await page.locator('[data-layer="l4"]').click();
        assert.equal(await page.locator('[data-region="canvasL4"]').isVisible(), true);
        assert.equal(await page.locator('.node,.link,.subnet').count(), 0);
        console.log('PASS  subnet selection highlights all members; L4 remains reserved');

        await page.locator('[data-layer="l1"]').click();
        // Isolate a cable to verify its physical directions and screen sizes in every quadrant.
        const geometry = await page.evaluate(() => {
            const s = window.canvasStage, cable = s.model.links[0];
            s.model = {...s.model, links: [cable], nodes: new Map([cable.a, cable.b].map(e => [e.nodeId, {...s.model.nodes.get(e.nodeId)}]))};
            const a = s.model.nodes.get(cable.a.nodeId), b = s.model.nodes.get(cable.b.nodeId);
            s.sel = {kind: 'link', id: cable.id};
            const results = [];
            for (const angle of [0, 45, 90, 135, 180, 225, 270, 315]) {
                const rad = angle * Math.PI / 180;
                a.cx = -200 * Math.cos(rad); a.cy = -200 * Math.sin(rad);
                b.cx = 200 * Math.cos(rad); b.cy = 200 * Math.sin(rad);
                s.render();
                for (const k of [.22, .6, 1, 2, 6]) {
                    s.view = {k, tx: 700, ty: 450}; s.applyView();
                    const rails = s.canvas.rails[0].directions;
                    const valid = rails.every(({line, labels}) => {
                        const from = s.model.nodes.get(line.dataset.txNode), to = s.model.nodes.get(line.dataset.rxNode);
                        const dx = line.x2.baseVal.value - line.x1.baseVal.value, dy = line.y2.baseVal.value - line.y1.baseVal.value;
                        return from !== to && dx * (to.cx - from.cx) + dy * (to.cy - from.cy) > 0
                            && labels.every(l => l.g.dataset.nodeEnd === (l.g.dataset.role === 'TX' ? from.id : to.id));
                    });
                    const dot = s.svg.querySelector('.pdot').getBoundingClientRect();
                    const font = parseFloat(getComputedStyle(s.svg.querySelector('.plabel')).fontSize) * k;
                    const stroke = getComputedStyle(rails[0].line);
                    results.push({angle, k, valid, dot: dot.width, font, stroke: stroke.strokeWidth, effect: stroke.vectorEffect});
                }
            }
            return results;
        });
        geometry.forEach(r => {
            assert.ok(r.valid, `TX → RX: ${r.angle}°, zoom ${r.k}`);
            assert.ok(near(r.dot, 6.4) && near(r.font, 11), JSON.stringify(r));
            assert.equal(r.effect, 'non-scaling-stroke');
            assert.ok(parseFloat(r.stroke) <= 1.6);
        });
        console.log('PASS  TX → RX in both directions and fixed port/line sizes in 40 angle/zoom cases');

        await page.setViewportSize({width: 390, height: 844});
        await page.goto(`${base}/index.ru.html?fast&theme=dark${min}`);
        await page.locator('[data-region="start"]').click();
        await page.waitForFunction(() => window.__stub.calls.mod_gns3lab_get_facts >= 1);
        await page.locator('[data-layer="l3"]:not([disabled])').waitFor();
        await page.locator('[data-region="overview"]').click();
        await page.locator('.rowbtn[data-goto-node]').first().click();
        const card = await page.locator('.details').boundingBox(), zoom = await page.locator('.zoom').boundingBox();
        assert.ok(card.x >= 0 && card.x + card.width <= 391 && card.y + card.height < zoom.y);
        await page.locator('.details > .x').click();
        await page.locator('[data-region="assignmentsOpen"]').click();
        assert.match(await page.locator('.assignments').innerText(), /Проверьте VLAN/);
        assert.deepEqual(errors, []);
        console.log('PASS  Russian mobile/dark view leaves zoom controls accessible; no browser errors');
    } finally {
        await browser.close();
        server.kill();
    }
})().catch(e => { console.error(e); process.exitCode = 1; });
