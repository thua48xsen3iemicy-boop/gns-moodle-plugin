'use strict';
/** Regression checks of the production Canvas, including its Moodle template and real filtered fact models.
 * Run after build.js: node check_canvas.js [--min]. Requires Playwright and Chromium, like check_reveal.js.
 */
const assert = require('assert/strict');
const {spawn} = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');
const {chromium} = require('playwright');
const PORT = 18129;
const base = `http://127.0.0.1:${PORT}`;
const min = process.argv.includes('--min') ? '&min' : '';
const near = (a, b) => Math.abs(a - b) < .1;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function prototypeParity(browser, page) {
    const names = new Set(['student-ui-canvas.html', 'student-ui-canvas-core.js', 'student-ui-canvas-workbench.js']);
    const fonts = new Set(fs.readdirSync(path.resolve(__dirname, '../../plugin/fonts')).filter(n => n.endsWith('.woff2')).map(n => 'fonts/' + n));
    const referenceServer = http.createServer((req, res) => {
        const name = req.url.slice(1);
        if (!names.has(name) && !fonts.has(name)) { res.writeHead(404).end(); return; }
        res.setHeader('Content-Type', fonts.has(name) ? 'font/woff2' : name.endsWith('.html') ? 'text/html; charset=utf-8' : 'text/javascript');
        res.end(fs.readFileSync(path.resolve(__dirname, fonts.has(name) ? '../../plugin' : '../../prototypes', name)));
    });
    await new Promise(resolve => referenceServer.listen(0, '127.0.0.1', resolve));
    const reference = await browser.newPage({viewport: {width: 1440, height: 1000}});
    try {
        await reference.route('https://**', route => route.abort());
        await reference.goto(`http://127.0.0.1:${referenceServer.address().port}/student-ui-canvas.html`);
        await reference.waitForFunction(() => window.CanvasPrototype);
        const fontFaces = fs.readFileSync(path.resolve(__dirname, '../../plugin/styles.css'), 'utf8').match(/@font-face\s*\{[^}]+\}/g).join('\n')
            .replace(/\[\[font:mod_gns3lab\|([^\]]+)\]\]/g, 'fonts/$1');
        await reference.addStyleTag({content: fontFaces});
        await reference.evaluate(() => CanvasPrototype.navigate('node', nodes.find(n => n.name === 'SW1').id));
        await Promise.all([reference.evaluate(() => document.fonts.ready.then(() => {})), page.evaluate(() => document.fonts.ready.then(() => {}))]);
        assert.equal(await page.evaluate(() => document.fonts.check('500 12px "IBM Plex Mono"') && document.fonts.check('600 25px "IBM Plex Sans Condensed"')), true);
        await reference.waitForTimeout(400);
        const appearance = async(p, prototype) => p.evaluate(isPrototype => {
            const root = document.querySelector(isPrototype ? ':root' : '.gns3lab-student');
            const card = document.querySelector('.object-card'), style = getComputedStyle(card), box = card.getBoundingClientRect();
            const title = getComputedStyle(card.querySelector('h2')), tabs = getComputedStyle(card.querySelector('.object-tabs button[aria-selected="true"]'));
            const hud = document.querySelector(isPrototype ? '#canvasHud .hud-bg' : '[data-region="layerSeg"] .hud-bg');
            return {palette: ['--canvas', '--panel', '--fg', '--accent', '--ico', '--line'].map(k => getComputedStyle(root).getPropertyValue(k).trim()),
                card: {width: box.width, height: box.height, background: style.backgroundColor, border: style.borderTopColor, radius: style.borderTopLeftRadius,
                    font: style.fontFamily, titleSize: title.fontSize, titleFont: title.fontFamily, tabFill: tabs.backgroundColor, tabHeight: tabs.minHeight},
                hud: ['width', 'height', 'rx'].map(k => hud.getAttribute(k)),
                mounted: !!card.closest('foreignObject')?.closest(isPrototype ? '#canvasUi' : '[data-region="canvasUi"]')};
        }, prototype);
        assert.deepEqual(await appearance(page, false), await appearance(reference, true));
        assert.equal(await page.locator('[data-region="factsSel"],#demoPanel,#viewMode,#demoToggle').count(), 0);
        console.log('PASS  live Canvas palette, SVG HUD, card geometry and typography match the original prototype; no prototype mode controls');
    } finally {
        await reference.close();
        await new Promise(resolve => referenceServer.close(resolve));
    }
}

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
        assert.match(await page.locator('.object-card').innerText(), /10\.1\.99\./);
        await prototypeParity(browser, page);
        await page.locator('.object-card [data-act="close"]').click();
        assert.equal(await page.locator('.node.sel').getAttribute('data-node'), selection.node);
        assert.equal(await page.locator('.link.on').count(), selection.links.length);
        assert.equal(await page.locator('.card-lead').count(), 0);
        console.log('PASS  independent card tabs; X retains neighbours and hides its tether');

        await page.locator('[data-region="assignmentsOpen"]').click();
        assert.match(await page.locator('[data-region="assignments"]').innerText(), /Explore the ports/);
        assert.equal(await page.locator('[data-region="assignmentsOpen"]').getAttribute('aria-expanded'), 'true');
        await page.keyboard.press('Escape');
        assert.equal(await page.locator('[data-region="assignments"]').isHidden(), true);
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
        await page.locator('.mac-toggle').click();
        assert.match(await page.locator('.object-card').innerText(), /not been collected/);
        await page.evaluate(() => {
            const s = window.canvasStage, v = s.canvas.l2.virtuals.get(s.sel.id), f = s.fx.nodes.get(v.orig.id);
            f.macs = {entries: [], truncated: false};
            s.call('details', s.sel);
        });
        await page.locator('.mac-toggle').click();
        assert.match(await page.locator('.object-card').innerText(), /not learned/);
        await page.evaluate(() => {
            const s = window.canvasStage, v = s.canvas.l2.virtuals.get(s.sel.id), f = s.fx.nodes.get(v.orig.id);
            f.macs = {entries: [{mac: '02:00:00:00:00:01', iface: f.ifs.find(i => i.switchport?.mode === 'access').name, vlan: 10}], truncated: true};
            s.call('details', s.sel);
        });
        await page.locator('.mac-toggle').click();
        assert.match(await page.locator('.object-card').innerText(), /incomplete/);
        assert.equal(await page.locator('.object-card tr[data-go-kind="link"]').count(), 1);
        console.log('PASS  missing, empty and truncated MAC tables remain distinct, with navigation to the actual cable');
        await page.locator('[data-card-layer="l1"]').click();
        assert.equal(await page.locator('[data-layer="l2"]').getAttribute('aria-pressed'), 'true');
        await page.locator('.object-card [data-act="close"]').click();
        console.log('PASS  virtual VLANs use authoritative domains and highlight their actual members');

        const guestPage = await browser.newPage({viewport: {width: 1440, height: 1000}});
        guestPage.on('pageerror', e => errors.push(e.message));
        await guestPage.goto(`${base}/index.viewer.html?fast${min}`);
        await guestPage.locator('.node').first().waitFor();
        await captureStage(guestPage);
        const guests = await guestPage.evaluate(() => {
            const s = window.canvasStage, n = [...s.model.nodes.values()].find(n => n.cat === 'hypervisor');
            s.setLayer('l1'); s.select({kind: 'node', id: n.id}); s.call('details', s.sel);
            return s.guests[n.id].items.map(g => g.name);
        });
        const guestCard = await guestPage.locator('.object-card').innerText();
        assert.ok(guests.length > 0); guests.forEach(name => assert.ok(guestCard.includes(name)));
        await guestPage.close();
        console.log('PASS  the Proxmox guest snapshot is included in the prototype card');

        const observer = await browser.newPage({viewport: {width: 1440, height: 1000}});
        observer.on('pageerror', e => errors.push(e.message));
        await observer.goto(`${base}/index.en.html?observer&fast${min}`);
        await observer.locator('.node.st-started').first().waitFor();
        await captureStage(observer);
        await observer.evaluate(() => {
            const s = window.canvasStage, n = [...s.model.nodes.values()].find(n => n.name === 'PC1');
            s.select({kind: 'node', id: n.id}); s.call('details', s.sel);
        });
        assert.equal(await observer.locator('[data-inspect]').count(), 0);
        await observer.close();
        console.log('PASS  observers have no command execution controls');

        await page.locator('[data-layer="l3"]').click();
        const l3 = await page.evaluate(() => {
            const s = window.canvasStage, subnet = s.der.l3.subnets.find(x => x.members.length > 2);
            s.select({kind: 'subnet', id: subnet.key}); s.call('details', s.sel);
            return {members: [...new Set(subnet.members.map(m => m.nodeId))].sort(),
                marked: [...s.svg.querySelectorAll('.node.on')].map(e => e.dataset.node).sort()};
        });
        assert.deepEqual(l3.marked, l3.members);
        assert.equal(await page.locator('[data-card-layer]').count(), 1);
        await page.locator('.object-card [data-act="close"]').click();
        assert.equal(await page.locator('.node.on').count(), l3.members.length);
        await page.evaluate(() => {
            const s = window.canvasStage, n = [...s.model.nodes.values()].find(n => n.name === 'PC1');
            s.call('console', n);
        });
        await page.waitForFunction(() => [...document.querySelectorAll('.xterm-rows')].some(e => /PC1>/.test(e.textContent)));
        await sleep(400); // The console watcher commits the prompt after a short idle period.
        await page.evaluate(() => {
            const s = window.canvasStage, n = [...s.model.nodes.values()].find(n => n.name === 'PC1');
            s.select({kind: 'node', id: n.id}); s.call('details', s.sel);
        });
        assert.equal(await page.locator('[data-inspect="vp_showip"]').isEnabled(), true);
        await page.locator('[data-inspect="vp_showip"]').click();
        await page.waitForFunction(() => document.querySelector('.object-card')?.textContent.includes('10.1.10.77'));
        await page.locator('.object-card [data-act="close"]').click();
        console.log('PASS  inspection buttons run real console snippets and feed the existing console-fact pipeline');
        await page.locator('[data-layer="l4"]').click();
        assert.equal(await page.locator('[data-region="canvasEmpty"]').isVisible(), true);
        assert.match(await page.locator('[data-region="canvasEmpty"]').innerText(), /reserved/);
        console.log('PASS  subnet selection highlights all members; L4 remains reserved');

        await page.locator('[data-layer="l1"]').click();
        // Isolate a cable to verify its physical directions and screen sizes in every quadrant.
        const geometry = await page.evaluate(() => {
            const s = window.canvasStage, cable = s.model.links.find(l => Object.keys(l.filters || {}).length) || s.model.links[0];
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
                    results.push({angle, k, valid, dot: dot.width, font, stroke: stroke.strokeWidth, effect: stroke.vectorEffect,
                        filter: s.svg.querySelector('.filt').getBoundingClientRect().width});
                }
            }
            return results;
        });
        geometry.forEach(r => {
            assert.ok(r.valid, `TX → RX: ${r.angle}°, zoom ${r.k}`);
            assert.ok(near(r.dot, 6.4) && near(r.font, 11), JSON.stringify(r));
            assert.ok(near(r.filter, 16), JSON.stringify(r));
            assert.equal(r.effect, 'non-scaling-stroke');
            assert.ok(parseFloat(r.stroke) <= 1.6);
        });
        console.log('PASS  TX → RX in both directions and fixed port/line sizes in 40 angle/zoom cases');

        await page.setViewportSize({width: 390, height: 844});
        await page.goto(`${base}/index.ru.html?fast&theme=dark${min}`);
        await page.locator('[data-region="start"]').click();
        await page.waitForFunction(() => window.__stub.calls.mod_gns3lab_get_facts >= 1);
        await page.locator('[data-layer="l3"]:not([disabled])').waitFor();
        await page.locator('.node').first().focus();
        await page.keyboard.press('ContextMenu');
        await page.locator('[data-region="radial"] .wedge').nth(0).click();
        const card = await page.locator('.object-card').boundingBox(), zoom = await page.locator('.zoom').boundingBox();
        assert.ok(card.x >= 0 && card.x + card.width <= 391 && card.y + card.height < zoom.y);
        await page.locator('.object-card [data-act="close"]').click();
        await page.locator('[data-region="assignmentsOpen"]').click();
        assert.match(await page.locator('[data-region="assignments"]').innerText(), /Проверьте VLAN/);
        assert.deepEqual(errors, []);
        console.log('PASS  Russian mobile/dark view leaves zoom controls accessible; no browser errors');
    } finally {
        await browser.close();
        server.kill();
    }
})().catch(e => { console.error(e); process.exitCode = 1; });
