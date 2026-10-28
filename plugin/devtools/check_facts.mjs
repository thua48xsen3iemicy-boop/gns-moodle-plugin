// Checks the pure functions of plugin/amd/src/facts.js without Moodle or a browser: the state of the link ends
// (docs/live-state.md §4, L1: a switch port repeats its link end), the L2 modes `none` and `native: null` (§3.1) and
// the rows of the MAC table of a switch (§3.4).
//
//   node plugin/devtools/check_facts.mjs        (Node 22; exit code 1 if a scenario fails)
import assert from 'node:assert/strict';
import fs from 'node:fs';
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
const {derive, endState, macTable} = await import(srcUrl + 'facts.js');

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
// An appliance switch port (the Linux switch image): `mirror` says the student does not manage it.
const sw = (extra, sp = {mode: 'access', vlan: 10}) => node([ifc('e1', 1, {switchport: sp, mirror: true, ...extra})]);
// A switch whose ports the student manages (a Cisco switch): `switchport` without `mirror`.
const managed = extra => node([ifc('e1', 1, {switchport: {mode: 'access', vlan: 10}, ...extra})]);

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
        {[SW1]: node([ifc('p0', 0, {oper: 'down', switchport: {mode: 'access', vlan: 10}, mirror: true})]), [SW2]: sw({oper: 'down'})}, 'up', 'up', false],
    ['8b switch - switch, one switch stopped', link.swsw,
        {[SW1]: node([ifc('p0', 0, {switchport: {mode: 'access', vlan: 10}, mirror: true})]), [SW2]: node([], 'stopped')}, 'down', 'down', true, /SW2 остановлен/],
    ['9 link suspended', link.suspended, {[PC1]: host(), [SW1]: sw()}, 'down', 'down', true, /приостановлен/],
    // A switch the student manages is not a mirror: its own shutdown / carrier still drop the link, as before.
    ['10 managed switch port admin down', link.host, {[PC1]: host(), [SW1]: managed({admin: 'down'})}, 'up', 'down', true, /SW1 e1: shutdown/],
    ['10b managed switch port oper down', link.host, {[PC1]: host(), [SW1]: managed({oper: 'down'})}, 'up', 'down', true, /SW1 e1: нет несущей/],
    ['10c managed switch - appliance switch, managed port admin down', link.swsw,
        {[SW1]: node([ifc('p0', 0, {admin: 'down', switchport: {mode: 'access', vlan: 10}})]), [SW2]: sw()}, 'down', 'down', true, /SW1 p0: shutdown/],
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
    const m = lab(SW1, SW2, true), {der} = derive(m, doc({[SW1]: node([ifc('p0', 0, {switchport: {mode: 'access', vlan: 10}, mirror: true})]), [SW2]: sw()}));
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

// The same on the real demo lab: a student shuts down a cabled port of the Cisco switch SW1 of the OSPF lab.
check('L1 OSPF demo: shutdown on a cabled Cisco switch port drops its link with the reason', () => {
    const read = f => JSON.parse(fs.readFileSync(new URL('../fixtures/' + f, import.meta.url), 'utf8'));
    const gns3 = read('ospf-multi-area.gns3'), facts = read('ospf-multi-area.facts.json');
    const id = gns3.topology.nodes.find(n => n.name === 'SW1').node_id;
    const port = facts.nodes[id].interfaces.find(i => i.name === 'e0/1');
    assert.ok(port.switchport && !port.mirror);
    port.admin = 'down';
    const m = buildModel({name: 'x', topology: gns3.topology}), {der} = derive(m, facts);
    const l = m.links.find(x => [x.a, x.b].some(e => e.nodeId === id && e.adapter === 0 && e.port === 1));
    assert.equal(der.l1.links.get(l.id).down, true);
    assert.match(der.l1.links.get(l.id).why.join(' '), /SW1 e0\/1: shutdown/);
    assert.equal(endState(der, [l.a, l.b].find(e => e.nodeId === id)).down, true);
});

// The MAC table of a switch (§3.4): display rows, never a source of findings.
// PC1 e0 (adapter 0 port 0) is cabled to SW1 e1 (adapter 0 port 1) by the link l1 of lab(PC1, SW1).
const MAC_PC1 = '50:5b:3c:02:a2:00', MAC_PC2 = '50:5b:3c:02:b3:00';
const swWith = table => ({...sw(), mac_table: table});
const rowsOf = (facts, m = lab(PC1, SW1)) => macTable(m, derive(m, doc(facts)).fx, SW1);
check('MAC table: no mac_table is null (not collected), an empty list is an empty table', () => {
    assert.equal(rowsOf({[PC1]: host(), [SW1]: sw()}), null);
    assert.deepEqual(rowsOf({[PC1]: host(), [SW1]: swWith({entries: []})}), {rows: [], truncated: false});
    assert.equal(rowsOf({[PC1]: host()}), null, 'a switch without any facts');
});
check('MAC table: a table without a list of entries is "not collected", not an empty table', () => {
    assert.equal(rowsOf({[PC1]: host(), [SW1]: swWith({truncated: false})}), null);
    assert.equal(rowsOf({[PC1]: host(), [SW1]: swWith({entries: 'x'})}), null);
});
check('MAC table: truncated is passed on only when it is true', () => {
    const t = truncated => rowsOf({[PC1]: host(), [SW1]: swWith({entries: [], ...truncated})}).truncated;
    assert.deepEqual([t({truncated: true}), t({truncated: false}), t({})], [true, false, false]);
});
check('MAC table: the owner is the other node with this MAC, matched case-insensitively', () => {
    const t = swWith({entries: [{mac: MAC_PC1, iface: 'e1', vlan: 10}, {mac: MAC_PC2, iface: 'e1', vlan: 20}]});
    const {rows} = rowsOf({[PC1]: host({mac: MAC_PC1.toUpperCase()}), [SW1]: t});
    assert.deepEqual(rows[0].owner, {nodeId: PC1, node: 'PC1', iface: 'e0'});
    assert.equal(rows[0].mac, MAC_PC1);
    assert.deepEqual([rows[0].vlan, rows[1].vlan], [10, 20]);
    assert.equal(rows[1].owner, null, 'an address nobody here has is kept, without a name');
});
check('MAC table: an upper-case MAC of the entry and the dashed form still find the owner', () => {
    const t = swWith({entries: [{mac: MAC_PC1.toUpperCase(), iface: 'e1', vlan: 10}]});
    const {rows} = rowsOf({[PC1]: host({mac: MAC_PC1.replaceAll(':', '-')}), [SW1]: t});
    assert.deepEqual([rows[0].mac, rows[0].owner && rows[0].owner.node], [MAC_PC1, 'PC1']);
});
check('MAC table: the switch itself is not the owner of what it learned', () => {
    const t = swWith({entries: [{mac: MAC_PC1, iface: 'e1', vlan: 10}]});
    t.interfaces[0].mac = MAC_PC1;
    assert.equal(rowsOf({[PC1]: host(), [SW1]: t}).rows[0].owner, null);
});
check('MAC table: the port of the switch and the cable on it', () => {
    const t = swWith({entries: [{mac: MAC_PC1, iface: 'e1', vlan: 10}, {mac: MAC_PC2, iface: 'nope', vlan: 10}]});
    const {rows} = rowsOf({[PC1]: host(), [SW1]: t});
    assert.deepEqual([rows[0].port, rows[0].link, rows[0].label], [{adapter: 0, port: 1}, 'l1', 'p1']);
    assert.deepEqual([rows[1].port, rows[1].link, rows[1].label], [null, null, null], 'an interface the switch did not report');
});
check('MAC table: an interface with a port that is not on any cable has no link', () => {
    const t = {...node([ifc('e9', 9)]), mac_table: {entries: [{mac: MAC_PC1, iface: 'e9', vlan: 1}]}};
    const {rows} = rowsOf({[PC1]: host(), [SW1]: t});
    assert.deepEqual([rows[0].port, rows[0].link, rows[0].label], [{adapter: 0, port: 9}, null, null]);
});
check('MAC table: of several interfaces of one node with the same MAC the physical one is named', () => {
    const sub = {name: 'e0.10', kind: 'subif', parent: 'e0', dot1q: 10, admin: 'up', oper: 'up', mac: MAC_PC1};
    const pc = node([sub, ifc('e0', 0, {mac: MAC_PC1})]);
    const t = swWith({entries: [{mac: MAC_PC1, iface: 'e1', vlan: 10}]});
    assert.deepEqual(rowsOf({[PC1]: pc, [SW1]: t}).rows[0].owner, {nodeId: PC1, node: 'PC1', iface: 'e0'});
});
check('MAC table: the same MAC on two other nodes names nobody', () => {
    const m = lab(PC1, SW1), pc2 = host({mac: MAC_PC1});
    m.nodes.set(SW2, {id: SW2, name: 'SW2'});
    const t = swWith({entries: [{mac: MAC_PC1, iface: 'e1', vlan: 10}]});
    assert.equal(rowsOf({[PC1]: host({mac: MAC_PC1}), [SW2]: pc2, [SW1]: t}, m).rows[0].owner, null);
});
check('MAC table: it never changes the layers or their findings', () => {
    const t = swWith({entries: [{mac: MAC_PC1, iface: 'e1', vlan: 10}, {mac: MAC_PC2, iface: 'e1', vlan: 10}], truncated: true});
    const layers = nodes => {
        const {der} = derive(lab(PC1, SW1), doc(nodes));
        return JSON.stringify([der.l1.issues, der.l2.issues, der.l3.issues, der.rt.issues, [...der.l1.ends], [...der.l2.links]]);
    };
    assert.equal(layers({[PC1]: host({mac: MAC_PC1}), [SW1]: t}), layers({[PC1]: host({mac: MAC_PC1}), [SW1]: sw()}));
});

// The demo lab `linux-switch-vlan`: PC1 and PC2 are learned on their access ports, SRV1 on the trunk in both VLANs,
// one address is nobody's. The broken snapshot has the port of PC1 down: nothing is learned behind it.
const demo = (facts) => {
    const read = f => JSON.parse(fs.readFileSync(new URL('../fixtures/' + f, import.meta.url), 'utf8'));
    const gns3 = read('linux-switch-vlan.gns3'), m = buildModel({name: 'x', topology: gns3.topology});
    const id = gns3.topology.nodes.find(n => n.name === 'SW1').node_id;
    return {m, T: macTable(m, derive(m, read(facts)).fx, id)};
};
check('MAC table: demo lab rows, owners and cables', () => {
    const {m, T} = demo('linux-switch-vlan.facts.json');
    const seen = T.rows.map(r => `${r.vlan} ${r.iface} ${r.owner ? r.owner.node + ' ' + r.owner.iface : '-'}`);
    assert.deepEqual(seen, ['10 ens4 -', '10 ens4 PC1 ens3', '10 ens6 SRV1 ens3', '20 ens5 PC2 ens3', '20 ens6 SRV1 ens3']);
    assert.ok(T.rows.every(r => r.link && m.links.some(l => l.id === r.link)), 'every row points at a cable');
    assert.deepEqual(T.rows.map(r => r.label), ['e1', 'e1', 'e3', 'e2', 'e3'], 'the port as the scheme names it');
    assert.equal(T.truncated, false);
});
check('MAC table: demo lab with the port of PC1 down has no entries behind that port', () => {
    const {T} = demo('linux-switch-vlan.facts-broken.json');
    assert.deepEqual(T.rows.map(r => r.iface), ['ens6', 'ens5', 'ens6']);
});

// Live IOL L1 overrides saved/console admin only on Cisco-Cisco physical pairs.
check('IOL L1: fresh, stale, failed and unknown measurements preserve configuration', () => {
    const model = lab(SW1, SW2);
    model.nodes.forEach(n => { n.type = 'iou'; });
    const live = (port, state, extra = {}) => ({ok: true, observed_at: new Date().toISOString(), ttl: 15,
        ports: [{port: {adapter: 0, port}, state, reason: 'both_responding'}], ...extra});
    const a = node([ifc('Ethernet0/0', 0, {admin: 'down', ipv4: ['10.0.0.1/24']})]);
    const b = node([ifc('Ethernet0/1', 1, {admin: 'down'})]);
    a.l1_ports = live(0, 'up'); b.l1_ports = live(1, 'up');
    const nodes = {[SW1]: a, [SW2]: b};
    const current = () => derive(model, doc(nodes)).der.l1.links.get('l1');
    assert.equal(current().down, false);
    assert.equal(current().unknown, false);
    assert.equal(a.interfaces[0].admin, 'down'); // live state never rewrites NVRAM
    a.l1_ports = live(0, 'down');
    assert.equal(current().down, true);
    a.l1_ports = live(0, 'up', {observed_at: new Date(Date.now() - 60000).toISOString()});
    assert.equal(current().down, false);
    assert.equal(current().unknown, true); // stale is not shutdown and not green
    a.l1_ports = live(0, 'up', {ok: false});
    assert.equal(current().unknown, true);
    a.l1_ports = live(0, 'unknown');
    assert.equal(current().unknown, true);
    a.l1_ports = live(0, 'up');
    model.nodes.get(SW2).type = 'qemu';
    assert.equal(current().down, true); // Cisco-QEMU retains the old policy
    model.nodes.get(SW2).type = 'iou';
    b.status = 'stopped';
    assert.equal(current().down, true); // node/link lifecycle remains authoritative
});

check('IOL L1: ports work without a saved configuration', () => {
    const model = lab(SW1, SW2);
    model.nodes.forEach(n => { n.type = 'iou'; });
    const nodes = {};
    [SW1, SW2].forEach((id, port) => { nodes[id] = {status: 'started', l1_ports: {ok: true,
        observed_at: new Date().toISOString(), ttl: 15, ports: [{port: {adapter: 0, port}, state: 'up'}]}}; });
    const result = derive(model, doc(nodes));
    assert.equal(result.der.l1.links.get('l1').down, false);
    assert.equal(endState(result.der, model.links[0].a).live, true);
});

if (failures.length) {
    console.log(`\n${failures.length} failed`);
    process.exit(1);
}

console.log('\nall scenarios passed');
