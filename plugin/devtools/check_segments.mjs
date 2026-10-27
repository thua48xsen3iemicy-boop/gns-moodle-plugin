// Checks the pure functions of plugin/amd/src/segments.js without Moodle or a browser: the addresses the segments give
// (the same rules as the lab manager, manager/lab_manager/segments.py), the expected facts and what facts.js derives from
// them for the preview of the lab editor, and the mapping of the findings of the lab manager to segments and members.
//
//   node plugin/devtools/check_segments.mjs        (Node 22; exit code 1 if a scenario fails)
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
const {derive} = await import(srcUrl + 'facts.js');
const S = await import(srcUrl + 'segments.js');

const image = (id, kind, category, extra = {}) => ({
    id, kind, category, symbol: ':/symbols/x.svg', console_type: 'telnet', port_name: kind === 'iou' ? 'Ethernet{adapter}/{port}'
        : 'Ethernet{adapter}', ports_per_adapter: kind === 'iou' ? 4 : 1, adapters: {default: 1, min: 1, max: 8},
    preconfig: kind === 'vpcs' || id === 'debian12', ...extra,
});
const images = new Map([
    image('debian12', 'qemu', 'host'),
    image('vpcs', 'vpcs', 'host', {adapters: null, port_name: 'Ethernet0'}),
    image('linux-switch', 'qemu', 'switch', {adapters: {default: 8, min: 1, max: 16}, console_type: 'none'}),
    image('l3', 'iou', 'router'),
].map(i => [i.id, i]));

const node = (name, img, x = 0, y = 0, extra = {}) => ({name, image: img, x, y, ...extra});
const link = (a, ap, b, bp = 0) => ({a: {node: a, adapter: ap, port: 0}, b: {node: b, adapter: bp, port: 0}});
const seg = (name, subnet, members, extra = {}) => ({
    name, subnet: {fixed: subnet}, members: members.map(m => typeof m === 'string' ? {node: m, adapter: 0, port: 0} : m), ...extra});
const lab = (segments = []) => ({
    nodes: [node('SW1', 'linux-switch', 0, 0), node('PC1', 'debian12', -100, 100), node('PC2', 'vpcs', 100, 100),
        node('R1', 'l3', 0, -100, {adapters: 1})],
    links: [link('SW1', 0, 'PC1'), link('SW1', 1, 'PC2'), link('SW1', 2, 'R1')],
    segments,
});

let failed = 0;
const check = (name, fn) => {
    try {
        fn();
        console.log('PASS', name);
    } catch (e) {
        failed++;
        console.log('FAIL', name, '\n  ', e.message.split('\n').slice(0, 6).join('\n   '));
    }
};
const ips = l => Object.fromEntries(S.assignments(l).map(a => [`${a.node}`, `${a.ip}/${a.prefix}` + (a.gateway ? ` via ${a.gateway}` : '')]));

check('subnets are parsed like the lab manager does', () => {
    assert.deepEqual(S.parseSubnet('192.168.10.0/24'), {base: 3232238080, prefix: 24, size: 256, usable: 254});
    for (const bad of ['192.168.10.1/24', '192.168.10.0', '192.168.010.0/24', '192.168.10.0/31', '10.0.0.0/7', '0.1.0.0/16',
        '127.0.0.0/8', '169.254.0.0/16', '224.0.0.0/8', '240.0.0.0/8', '300.0.0.0/8', '192.168.10.0/255.255.255.0', 24, null]) {
        assert.equal(S.parseSubnet(bad), null, String(bad));
    }
    assert.ok(S.parseSubnet('10.0.0.0/8') && S.parseSubnet('192.168.10.4/30'));
});

check('the gateway takes the last working address, the members the lowest free numbers', () => {
    const l = lab([seg('LAN1', '192.168.10.0/24', ['PC1', 'PC2'], {gateway: {node: 'R1', adapter: 0, port: 0}})]);
    assert.deepEqual(ips(l), {PC1: '192.168.10.1/24 via 192.168.10.254', PC2: '192.168.10.2/24 via 192.168.10.254'});
    const small = lab([seg('S', '10.0.0.0/29', ['PC1', 'PC2'], {gateway: {node: 'R1', adapter: 0, port: 0}})]);
    assert.equal(ips(small).PC1, '10.0.0.1/29 via 10.0.0.6');
});

check('explicit hosts keep their numbers and the others skip them', () => {
    const l = lab([seg('LAN1', '192.168.10.0/24', [{node: 'PC1', adapter: 0, port: 0, host: 1}, 'PC2'])]);
    assert.deepEqual(ips(l), {PC1: '192.168.10.1/24', PC2: '192.168.10.2/24'});
    const swap = lab([seg('LAN1', '192.168.10.0/24', ['PC1', {node: 'PC2', adapter: 0, port: 0, host: 1}])]);
    assert.deepEqual(ips(swap), {PC1: '192.168.10.2/24', PC2: '192.168.10.1/24'});
});

check('plan reports what the lab manager reports', () => {
    const net = S.parseSubnet('192.168.10.0/24');
    const codes = (gw, members) => S.plan(net, gw, members).problems.map(p => `${p.where}:${p.code}`);
    assert.deepEqual(codes(null, [{host: 0}, {host: 255}, {host: 3}, {host: 3}]), ['0:host_out_of_range', '1:host_out_of_range', '3:host_in_use']);
    assert.deepEqual(codes({}, [{host: 254}]), ['0:host_in_use']);
    assert.deepEqual(codes({host: 300}, []), ['gateway:host_out_of_range']);
    assert.deepEqual(S.plan(S.parseSubnet('10.0.0.0/30'), {}, [{}, {}]).problems, [{where: 'members', code: 'subnet_too_small'}]);
});

check('expected facts: interfaces with addresses and the default gateway of the member', () => {
    const l = lab([seg('LAN1', '192.168.10.0/24', ['PC1', 'PC2'], {gateway: {node: 'R1', adapter: 0, port: 0}})]);
    const f = S.expectedFacts(l, images);
    assert.equal(f.schema, 'gns3lab.facts/1');
    assert.deepEqual(Object.keys(f.nodes).sort(), ['PC1', 'PC2']);
    assert.deepEqual(f.nodes.PC1.interfaces, [{name: 'Ethernet0', kind: 'physical', port: {adapter: 0, port: 0}, admin: 'up',
        oper: 'up', ipv4: ['192.168.10.1/24'], gateway: '192.168.10.254'}]);
});

const derived = l => {
    const m = buildModel(S.previewDocument(l, images));
    return {m, ...derive(m, S.previewFacts(l, images))};
};

check('the preview derives one subnet with both members and no findings', () => {
    const {der} = derived(lab([seg('LAN1', '192.168.10.0/24', ['PC1', 'PC2'])]));
    assert.deepEqual(der.l3.issues, []);
    assert.equal(der.l3.subnets.length, 1);
    assert.deepEqual(der.l3.subnets[0].members.map(m => m.nodeId).sort(), ['PC1', 'PC2']);
});

check('the preview finds a repeated address and a broken L2 path', () => {
    // One subnet in two segments: both members are given .5. (Inside one segment the lab manager refuses the second.)
    const dup = lab([seg('A', '192.168.10.0/24', [{node: 'PC1', adapter: 0, port: 0, host: 5}]),
        seg('B', '192.168.10.0/24', [{node: 'PC2', adapter: 0, port: 0, host: 5}])]);
    assert.ok(derived(dup).der.l3.issues.some(i => i.sev === 'bad' && i.text.includes('192.168.10.5')));
    // PC1 and PC2 behind a switch in different VLANs of two segments of one subnet: torn apart on L2.
    const split = lab([seg('A', '192.168.10.0/24', ['PC1'], {vlan: {fixed: 10}}), seg('B', '192.168.10.0/24', ['PC2'], {vlan: {fixed: 20}})]);
    assert.ok(derived(split).der.l3.issues.some(i => i.text.includes('разорвана на L2')));
});

check('a segment on a switch without VLANs stays one L2 domain', () => {
    const {der} = derived(lab([seg('LAN1', '192.168.10.0/24', ['PC1', 'PC2', {node: 'R1', adapter: 0, port: 0}].slice(0, 2))]));
    assert.equal(der.l2.domains.length, 1);
});

check('findings of the lab manager are mapped to segments, members, the gateway and nodes', () => {
    assert.deepEqual(S.placeOf('segments[2].members[1]'), {kind: 'segment', i: 2, part: 'members', j: 1});
    assert.deepEqual(S.placeOf('segments[0].members[3].node'), {kind: 'segment', i: 0, part: 'members', j: 3});
    assert.deepEqual(S.placeOf('segments[1].gateway'), {kind: 'segment', i: 1, part: 'gateway'});
    assert.deepEqual(S.placeOf('segments[1].subnet.fixed'), {kind: 'segment', i: 1, part: 'subnet'});
    assert.deepEqual(S.placeOf('segments[1].vlan.fixed'), {kind: 'segment', i: 1, part: 'vlan'});
    assert.deepEqual(S.placeOf('segments[4]'), {kind: 'segment', i: 4});
    assert.deepEqual(S.placeOf('nodes[3].adapters'), {kind: 'node', i: 3});
    assert.deepEqual(S.placeOf('links[0].a'), {kind: 'link', i: 0});
    assert.equal(S.placeOf('segments'), null);
    assert.equal(S.placeOf(''), null);
});

check('taken ports and the preconfig flag', () => {
    const l = lab([seg('LAN1', '192.168.10.0/24', ['PC1'], {gateway: {node: 'R1', adapter: 0, port: 0}})]);
    const t = S.takenPorts(l);
    assert.deepEqual([...t.entries()], [['R1|0/0', {segment: 0, member: -1}], ['PC1|0/0', {segment: 0, member: 0}]]);
    assert.ok(S.isPreconfig(images.get('vpcs')) && !S.isPreconfig(images.get('linux-switch')) && !S.isPreconfig(null));
});

check('why a node cannot join a segment depends on the category of its image', () => {
    assert.equal(S.notMemberKey(images.get('l3')), 'ed_nopreconfig_router');
    assert.equal(S.notMemberKey(images.get('linux-switch')), 'ed_nopreconfig_switch');
    assert.equal(S.notMemberKey(image('other', 'qemu', 'host')), 'ed_nopreconfig');
    assert.equal(S.notMemberKey(image('x', 'qemu', '')), 'ed_nopreconfig');
    assert.equal(S.notMemberKey(null), 'ed_nopreconfig');
});

// Stage 3: IOL switches and routers are set up by the lab manager (lab constructor README §15).
const iolImages = new Map([...images, ...[image('l2', 'iou', 'switch'), image('l3', 'iou', 'router')].map(i => [i.id, i])]);
const cable = (a, ap, b, bp) => ({a: {node: a, adapter: Math.floor(ap / 4), port: ap % 4}, b: {node: b, adapter: Math.floor(bp / 4), port: bp % 4}});
const stick = segments => ({
    nodes: [node('SW1', 'l2'), node('SW2', 'l2'), node('SW3', 'l2'), node('LSW', 'linux-switch'), node('PC1', 'debian12'),
        node('PC2', 'vpcs'), node('R1', 'l3', 0, 0, {adapters: 1})],
    // PC1 - SW1 - SW2 - PC2, the router on SW2, SW3 is a spur behind SW2 (a Linux switch behind that), nobody is on it
    links: [cable('PC1', 0, 'SW1', 0), cable('SW1', 3, 'SW2', 3), cable('PC2', 0, 'SW2', 0), cable('R1', 0, 'SW2', 2),
        cable('SW2', 1, 'SW3', 0), cable('SW3', 1, 'LSW', 0)],
    segments,
});
const gw = {node: 'R1', adapter: 0, port: 0};
const users = seg('USERS', '192.168.10.0/24', ['PC1', {node: 'PC2', adapter: 0, port: 0}], {vlan: {fixed: 10}, gateway: gw});

check('the lab manager sets the IOL switches on the path and the IOL gateway, not a spur', () => {
    const l = stick([users]);
    assert.deepEqual(S.managedNodes(l, iolImages, 0), ['SW1', 'SW2', 'R1']);
    assert.deepEqual([...S.pathSwitches(l, iolImages, users)].sort(), ['SW1', 'SW2']);
    assert.deepEqual(S.managedNodes(l, iolImages, 1), []);
});

check('a switch that is not IOL stays the student\'s, and so does a router that is not IOL', () => {
    const l = stick([users]);
    l.nodes.find(n => n.name === 'SW1').image = 'linux-switch';
    l.links[0] = {a: {node: 'PC1', adapter: 0, port: 0}, b: {node: 'SW1', adapter: 0, port: 0}};
    l.links[1] = {a: {node: 'SW1', adapter: 1, port: 0}, b: {node: 'SW2', adapter: 0, port: 3}};
    assert.deepEqual(S.managedNodes(l, iolImages, 0), ['SW2', 'R1']);
    l.nodes.find(n => n.name === 'R1').image = 'debian12';
    assert.deepEqual(S.managedNodes(l, iolImages, 0), ['SW2']);
});

check('a ring puts every switch of the ring on the path', () => {
    const l = {nodes: [node('SW1', 'l2'), node('SW2', 'l2'), node('SW3', 'l2'), node('PC1', 'vpcs'), node('PC2', 'vpcs')],
        links: [cable('PC1', 0, 'SW1', 0), cable('PC2', 0, 'SW2', 0), cable('SW1', 1, 'SW2', 1), cable('SW2', 2, 'SW3', 2),
            cable('SW3', 1, 'SW1', 2)],
        segments: [seg('RING', '10.0.0.0/24', ['PC1', 'PC2'])]};
    assert.deepEqual(S.managedNodes(l, iolImages, 0), ['SW1', 'SW2', 'SW3']);
});

check('the preview gives the gateway its address on a subinterface and trunks the switch port towards it', () => {
    const l = stick([users]);
    const facts = S.previewFacts(l, iolImages);
    const router = facts.nodes.R1.interfaces;
    assert.deepEqual(router.map(i => i.name), ['Ethernet0/0', 'Ethernet0/0.10']);
    assert.equal(router[0].ipv4, undefined);
    assert.deepEqual([router[1].kind, router[1].parent, router[1].dot1q, router[1].ipv4], ['subif', 'Ethernet0/0', 10, ['192.168.10.254/24']]);
    const toward = facts.nodes.SW2.interfaces.find(i => i.name === 'Ethernet0/2');
    assert.deepEqual(toward.switchport, {mode: 'trunk', allowed: [10], native: 1});
    assert.deepEqual(facts.nodes.SW2.interfaces.find(i => i.name === 'Ethernet0/0').switchport, {mode: 'access', vlan: 10});
    const {der} = derive(buildModel(S.previewDocument(l, iolImages)), facts);
    assert.deepEqual(der.l3.issues, []);
    assert.deepEqual(der.l3.subnets[0].members.map(m => m.nodeId).sort(), ['PC1', 'PC2', 'R1']);
});

check('a segment without VLAN puts the gateway address on the port itself, and a shared port gets both', () => {
    const plain = seg('PLAIN', '192.168.30.0/24', [], {gateway: gw});
    const facts = S.previewFacts(stick([plain]), iolImages);
    assert.deepEqual(facts.nodes.R1.interfaces.map(i => [i.name, i.ipv4]), [['Ethernet0/0', ['192.168.30.254/24']]]);
    assert.deepEqual(facts.nodes.SW2.interfaces.find(i => i.name === 'Ethernet0/2').switchport, {mode: 'access', vlan: 1});
    const both = S.previewFacts(stick([plain, users]), iolImages);
    assert.deepEqual(both.nodes.R1.interfaces.map(i => i.name), ['Ethernet0/0', 'Ethernet0/0.10']);
    assert.deepEqual(both.nodes.SW2.interfaces.find(i => i.name === 'Ethernet0/2').switchport,
        {mode: 'trunk', allowed: [1, 10], native: 1});
    assert.deepEqual([...S.gatewayUse(stick([plain, users])).get('R1|0/0')], [{segment: 0, vlan: 0}, {segment: 1, vlan: 10}]);
});

console.log(failed ? `${failed} failed` : 'all passed');
process.exit(failed ? 1 : 0);
