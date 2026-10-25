// Checks the pure functions of plugin/amd/src/facts.js without Moodle or a browser: the state of the link ends
// (docs/live-state.md §4, L1: a switch port repeats its link end) and the L2 modes `none` and `native: null` (§3.1).
//
//   node plugin/devtools/check_facts.mjs        (Node 22; exit code 1 if a scenario fails)
import assert from 'node:assert/strict';
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
    return next(url, url.startsWith(src) ? {...context, format: 'module'} : context);
}`));
const {buildModel} = await import(srcUrl + 'model.js');
const {derive, endState} = await import(srcUrl + 'facts.js');

const nid = n => `00000000-0000-4000-8000-0000000000${String(n).padStart(2, '0')}`;
const PC1 = nid(1), SW1 = nid(2), SW2 = nid(3);
const NAMES = {[PC1]: 'PC1', [SW1]: 'SW1', [SW2]: 'SW2'};

/** A one-link lab: end a is adapter 0 port 0 of node a, end b is adapter 0 port 1 of node b. */
const lab = (a, b, suspend = false) => buildModel({name: 'check', topology: {
    nodes: [a, b].map((id, k) => ({node_id: id, name: NAMES[id], node_type: 'qemu', x: k * 200, y: 0, properties: {}})),
    links: [{link_id: 'l1', suspend, nodes: [
        {node_id: a, adapter_number: 0, port_number: 0, label: {text: 'p0'}},
        {node_id: b, adapter_number: 0, port_number: 1, label: {text: 'p1'}},
    ]}],
}});
const ifc = (name, port, extra = {}) => ({name, kind: 'physical', port: {adapter: 0, port}, admin: 'up', oper: 'up', ...extra});
const node = (interfaces, status = 'started') => ({status, interfaces});
const doc = nodes => ({schema: 'gns3lab.facts/1', generated_at: '2026-10-25T10:00:00Z', ttl: 90, nodes});
const host = extra => node([ifc('e0', 0, {ipv4: ['10.0.10.11/24'], ...extra})]);
const sw = (extra, sp = {mode: 'access', vlan: 10}) => node([ifc('e1', 1, {switchport: sp, ...extra})]);

const state = (der, e) => {
    const s = endState(der, e);
    return !s ? '-' : s.down ? 'down' : 'up';
};
const failures = [];
const check = (name, fn) => {
    try {
        fn();
        console.log('ok    ' + name);
    } catch (e) {
        failures.push(name);
        console.log('FAIL  ' + name + '\n      ' + e.message.split('\n').join('\n      '));
    }
};

// The nine scenarios of docs/linux-switch-integration/PLAN.md (task B): the state of both ends and of the link.
// `a` is the host end (a switch for scenario 8), `b` the switch end. Scenario 6 has no facts at the host end ("—"
// in the plan): the end exists, but nothing draws it down.
const link = {
    host: lab(PC1, SW1), swsw: lab(SW1, SW2), suspended: lab(PC1, SW1, true),
};
const scenarios = [
    ['1 both ports are fine', link.host, {[PC1]: host(), [SW1]: sw()}, 'up', 'up', false],
    ['2 host admin down', link.host, {[PC1]: host({admin: 'down'}), [SW1]: sw()}, 'down', 'down', true, /PC1 e0: shutdown/],
    ['3 host oper down', link.host, {[PC1]: host({oper: 'down'}), [SW1]: sw()}, 'down', 'down', true, /^Линк .*PC1 e0: нет несущей\.$/],
    ['4 host stopped', link.host, {[PC1]: node([], 'stopped'), [SW1]: sw()}, 'down', 'down', true, /PC1 остановлен/],
    ['5 switch stopped', link.host, {[PC1]: host(), [SW1]: node([], 'stopped')}, 'down', 'down', true, /SW1 остановлен/],
    ['6 no facts about the host', link.host, {[SW1]: sw()}, 'up', 'up', false],
    ['7 switch port has its own oper down', link.host, {[PC1]: host(), [SW1]: sw({oper: 'down'})}, 'up', 'up', false],
    ['7b switch port has its own admin down', link.host, {[PC1]: host(), [SW1]: sw({admin: 'down'})}, 'up', 'up', false],
    ['8 switch - switch, own oper down at both ends', link.swsw,
        {[SW1]: node([ifc('p0', 0, {oper: 'down', switchport: {mode: 'access', vlan: 10}})]), [SW2]: sw({oper: 'down'})}, 'up', 'up', false],
    ['8b switch - switch, one switch stopped', link.swsw,
        {[SW1]: node([ifc('p0', 0, {switchport: {mode: 'access', vlan: 10}})]), [SW2]: node([], 'stopped')}, 'down', 'down', true, /SW2 остановлен/],
    ['9 link suspended', link.suspended, {[PC1]: host(), [SW1]: sw()}, 'down', 'down', true, /приостановлен/],
];
for (const [name, m, nodes, a, b, down, why] of scenarios) {
    check('L1 ' + name, () => {
        const {der} = derive(m, doc(nodes));
        const l = m.links[0], res = der.l1.links.get('l1');
        assert.deepEqual([state(der, l.a), state(der, l.b), res.down], [a, b, down]);
        assert.equal(der.l1.issues.length, down ? 1 : 0);
        if (why) {
            assert.match(der.l1.issues[0].text, why);
        }
    });
}
check('L1 8c switch - switch, suspended', () => {
    const m = lab(SW1, SW2, true), {der} = derive(m, doc({[SW1]: node([ifc('p0', 0, {switchport: {mode: 'access', vlan: 10}})]), [SW2]: sw()}));
    assert.deepEqual([state(der, m.links[0].a), state(der, m.links[0].b), der.l1.links.get('l1').down], ['down', 'down', true]);
});
check('L1 a switch port that is not on a link has no end state', () => {
    const m = lab(PC1, SW1), {der} = derive(m, doc({[PC1]: host(), [SW1]: node([ifc('e9', 9, {switchport: {mode: 'none'}})])}));
    assert.equal(endState(der, {nodeId: SW1, adapter: 0, port: 9}), null);
});
check('L1 two hosts: only the end with the failure is down (as before)', () => {
    const m = lab(PC1, SW1), {der} = derive(m, doc({[PC1]: host({oper: 'down'}), [SW1]: node([ifc('e0', 1, {ipv4: ['10.0.10.12/24']})])}));
    assert.deepEqual([state(der, m.links[0].a), state(der, m.links[0].b), der.l1.links.get('l1').down], ['down', 'up', true]);
});

// L2: mode none, trunk without native.
const l2 = (a, b, facts) => {
    const m = lab(a, b), {der} = derive(m, doc(facts));
    return {rec: der.l2.links.get('l1'), der};
};
const subifs = [10, 20].map(v => ({name: 'e0.' + v, kind: 'subif', parent: 'e0', dot1q: v, admin: 'up', oper: 'up', ipv4: [`10.0.${v}.12/24`]}));
const tagged = node([ifc('e0', 0), ...subifs]);
const trunk = (native, allowed = [10, 20]) => sw({}, native === undefined ? {mode: 'trunk', allowed} : {mode: 'trunk', allowed, native});
check('L2 mode none: nothing passes, no L2 is built, no finding', () => {
    const {rec, der} = l2(PC1, SW1, {[PC1]: host(), [SW1]: sw({}, {mode: 'none'})});
    assert.ok(rec.known);
    assert.deepEqual([rec.tags, rec.vlans, rec.issues, der.l2.issues], [[], [], [], []]);
    assert.equal(der.l2.domains.filter(d => d.switches.length).length, 0);
});
check('L2 trunk with native null: tagged VLANs pass, there is no untagged', () => {
    const {rec, der} = l2(PC1, SW1, {[PC1]: tagged, [SW1]: trunk(null)});
    assert.deepEqual([rec.vlans, rec.issues, rec.tags.map(t => t.t)], [[10, 20], [], [10, 20]]);
    assert.equal(rec.wb.map('u'), null);
    assert.equal(rec.wb.set.has('u'), false);
    assert.deepEqual(der.l2.issues.map(x => x.text).filter(t => !/один в своём/.test(t)), []);
});
check('L2 trunk with native null: untagged frames of a host are dropped, and this is a finding', () => {
    const {rec} = l2(PC1, SW1, {[PC1]: host(), [SW1]: trunk(null)});
    assert.deepEqual([rec.vlans, rec.tags], [[], []]);
    assert.ok(rec.issues.some(t => /PC1 p0 шлёт кадры без тега, а на транке SW1 p1 native нет/.test(t)), rec.issues.join('\n'));
});
check('L2 trunk with native null against an access port: no "VLAN null" glue', () => {
    const {rec} = l2(SW1, SW2, {[SW1]: node([ifc('p0', 0, {switchport: {mode: 'access', vlan: 10}})]), [SW2]: trunk(null)});
    assert.ok(rec.issues.some(t => /SW1 p0 шлёт кадры без тега, а на транке SW2 p1 native нет/.test(t)), rec.issues.join('\n'));
    assert.ok(!rec.issues.some(t => t.includes('null')), rec.issues.join('\n'));
});
check('L2 trunk with native null against a trunk with native: no finding', () => {
    const {rec} = l2(SW1, SW2, {[SW1]: node([ifc('p0', 0, {switchport: {mode: 'trunk', allowed: [10, 20], native: 1}})]), [SW2]: trunk(null)});
    assert.deepEqual([rec.vlans, rec.issues], [[10, 20], []]);
});
check('L2 trunk without the native field is still native 1', () => {
    const {rec} = l2(PC1, SW1, {[PC1]: host(), [SW1]: trunk(undefined, [])});
    assert.deepEqual([rec.vlans, rec.issues], [[1], []]);
    assert.equal(rec.wb.map('u'), 1);
});

if (failures.length) {
    console.log(`\n${failures.length} failed`);
    process.exit(1);
}
console.log('\nall scenarios passed');
