// Checks the figures of the course page of a teacher (docs/teacher-dashboard.md §4.2, §5.2, §5.3) without Moodle or a
// browser: the summary of a cell and the card of a student from the topology and the facts of a fixture, a document
// without facts, facts that are out of date, and the tracking of the rev ("no changes for N min").
//
//   node plugin/devtools/check_coursefacts.mjs        (Node 22; exit code 1 if a scenario fails)
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {register} from 'node:module';

// amd/src imports its siblings as 'mod_gns3lab/<name>': point that prefix at the source directory.
const srcUrl = new URL('../plugin/amd/src/', import.meta.url).href;
register('data:text/javascript,' + encodeURIComponent(`
const src = ${JSON.stringify(srcUrl)};
export async function resolve(specifier, context, next) {
    if (specifier.startsWith('mod_gns3lab/')) {
        return {url: new URL(specifier.slice('mod_gns3lab/'.length) + '.js', src).href, shortCircuit: true, format: 'module'};
    }
    return next(specifier, context);
}
export async function load(url, context, next) {
    return next(url, url.startsWith(src) && !url.endsWith('/xterm.js') ? {...context, format: 'module'} : context);
}`));
const {buildModel} = await import(srcUrl + 'model.js');
const cf = await import(srcUrl + 'coursefacts.js');

const fixture = name => JSON.parse(readFileSync(new URL('../fixtures/' + name, import.meta.url), 'utf8'));
const model = buildModel(fixture('ospf-multi-area.gns3'));
const good = fixture('ospf-multi-area.facts.json');
const broken = fixture('ospf-multi-area.facts-broken.json');

const failures = [];
const queue = [];
const check = (name, fn) => queue.push([name, fn]);

check('the correct configuration has no bad findings', () => {
    const s = cf.summarize(model, good);
    assert.equal(s.bad, 0);
    assert.notEqual(s.level, 'unknown');
    assert.notEqual(s.level, 'bad');
});

check('the broken configuration counts bad and warn findings over all layers, the worst decides the colour', () => {
    const s = cf.summarize(model, broken);
    assert.equal(s.level, 'bad');
    assert.ok(s.bad >= 3, 'bad: ' + s.bad);
    assert.ok(s.warn >= 1, 'warn: ' + s.warn);
    const total = cf.card(model, broken).layers.reduce((n, l) => n + l.issues.length, 0);
    assert.equal(s.bad + s.warn + s.info, total);
});

check('without a document, or with a document that no device reported on, the state is unknown, not ok', () => {
    assert.equal(cf.summarize(model, null).level, 'unknown');
    assert.equal(cf.summarize(model, {schema: 'gns3lab.facts/1', rev: 1, nodes: {}}).level, 'unknown');
    const empty = {schema: 'gns3lab.facts/1', rev: 1, nodes: {}};
    for (const id of model.nodes.keys()) {
        empty.nodes[id] = {status: 'started', error: {code: 'unreachable', message: 'x'}};
    }
    assert.equal(cf.summarize(model, empty).level, 'unknown');
    assert.equal(cf.summarize(null, broken).level, 'unknown');
});

check('facts past their ttl give the grey state with the last known counts', () => {
    const s = cf.summarize(model, broken, {stale: true});
    assert.equal(s.level, 'stale');
    assert.ok(s.bad > 0);
});

check('the card lists findings by layer, the important ones first', () => {
    const c = cf.card(model, broken);
    assert.equal(c.known, true);
    assert.deepEqual(c.layers.map(l => l.id), ['l1', 'l2', 'l3', 'rt']);
    const l1 = c.layers[0].issues;
    assert.ok(l1.some(x => x.sev === 'bad' && /Gi0\/2/.test(x.text)), 'R1 Gi0/2 shutdown');
    for (const layer of c.layers) {
        const order = layer.issues.map(x => ({bad: 0, warn: 1, info: 2}[x.sev]));
        assert.deepEqual(order, [...order].sort((a, b) => a - b), layer.id);
    }
    assert.ok(c.layers[3].issues.some(x => /OSPF/.test(x.text)));
});

check('the card lists the devices with the interfaces up and the IPv4 addresses', () => {
    const c = cf.card(model, broken);
    assert.equal(c.devices.length, model.nodes.size);
    const r1 = c.devices.find(d => d.name === 'R1');
    assert.ok(r1, 'R1');
    assert.equal(r1.state, 'on');
    assert.ok(r1.total > 0 && r1.up < r1.total, 'one interface of R1 is shut down');
    assert.ok(r1.addresses.some(a => /^\d+\.\d+\.\d+\.\d+\/\d+$/.test(a.address)));
});

check('the state of a device comes from the session first, then from the facts', () => {
    const r1 = [...model.nodes.values()].find(n => n.name === 'R1');
    const stopped = cf.card(model, broken, new Map([[r1.id, 'stopped']])).devices.find(d => d.id === r1.id);
    assert.equal(stopped.state, 'off');
    const none = cf.card(model, null).devices.find(d => d.id === r1.id);
    assert.equal(none.state, 'unknown');
    assert.equal(none.total, null);
});

check('the card without facts has no findings and is not known', () => {
    const c = cf.card(model, null);
    assert.equal(c.known, false);
    assert.ok(c.layers.every(l => l.issues.length === 0));
});

check('the rev is tracked from the first look: no change seen, then minutes since the last change', () => {
    const t0 = Date.parse('2026-10-30T10:00:00Z');
    let t = cf.trackRev(null, 7, t0);
    assert.deepEqual(cf.idleSince(t, t0 + 5 * 60000), {kind: 'first', since: t0, minutes: 5});
    t = cf.trackRev(t, 7, t0 + 60000);
    assert.equal(t.since, t0, 'the same rev keeps the time');
    t = cf.trackRev(t, 8, t0 + 10 * 60000);
    assert.equal(t.changes, 1);
    assert.deepEqual(cf.idleSince(t, t0 + 14 * 60000 + 59000), {kind: 'idle', since: t0 + 10 * 60000, minutes: 4});
});

for (const [name, fn] of queue) {
    try {
        await fn();
        console.log('ok   ' + name);
    } catch (error) {
        failures.push(name);
        console.log('FAIL ' + name + '\n     ' + String(error && error.stack || error).split('\n').slice(0, 6).join('\n     '));
    }
}
console.log(`\n${queue.length - failures.length}/${queue.length} scenarios passed`);
process.exit(failures.length ? 1 : 0);
