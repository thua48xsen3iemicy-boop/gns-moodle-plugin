// Checks the modes `seen` and `fog` (docs/lab-constructor/README.md §2, §12) without Moodle or a browser: the groups a
// command uncovers, the node, its interfaces, the cables and the neighbours that the facts uncover, the starting
// nodes, a device without a console, the filtered facts, the findings that must not name what is hidden, and the mask
// that only grows.
//
//   node plugin/devtools/check_reveal.mjs        (Node 22; exit code 1 if a scenario fails)
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
    return next(url, url.startsWith(src) && !url.endsWith('/xterm.js') ? {...context, format: 'module'} : context);
}`));
const {buildModel} = await import(srcUrl + 'model.js');
const {derive} = await import(srcUrl + 'facts.js');
const {addSeen, interpret, mergeSeen} = await import(srcUrl + 'consolefacts.js');
const rv = await import(srcUrl + 'reveal.js');

const failures = [];
const queue = [];
const check = (name, fn) => queue.push([name, fn]);

/* ---------------- the lab: a router, a switch, two hosts, a switch without a console ---------------- */

const iou = (id, name, symbol, ports) => ({
    node_id: id, name, node_type: 'iou', symbol, console_type: 'telnet', x: 0, y: 0, width: 60, height: 45, properties: {},
    ports: ports.map(([a, p]) => ({adapter_number: a, port_number: p, name: `Ethernet${a}/${p}`, short_name: `e${a}/${p}`})),
});
const host = (id, name, mac) => ({
    node_id: id, name, node_type: 'qemu', symbol: ':/symbols/classic/computer.svg', console_type: 'telnet',
    x: 0, y: 0, width: 60, height: 45, properties: {mac_address: mac},
    ports: [0, 1].map(a => ({adapter_number: a, port_number: 0, name: `Ethernet${a}`, short_name: `e${a}`})),
});
const R = 'r-1', S = 's-1', A = 'a-1', B = 'b-1', X = 'x-1';
const end = (node_id, adapter_number, port_number) => ({node_id, adapter_number, port_number});
const DOC = {
    name: 'lab',
    topology: {
        nodes: [
            iou(R, 'R1', ':/symbols/classic/router.svg', [[0, 0], [0, 1]]),
            iou(S, 'SW1', ':/symbols/classic/ethernet_switch.svg', [[0, 0], [0, 1], [0, 2]]),
            host(A, 'PC-A', '50:5b:3c:02:a2:00'),
            host(B, 'PC-B', '50:5b:3c:02:b2:00'),
            {
                node_id: X, name: 'LSW', node_type: 'qemu', symbol: ':/symbols/classic/ethernet_switch.svg',
                console_type: 'none', x: 0, y: 0, width: 60, height: 45, properties: {},
                ports: [0, 1].map(a => ({adapter_number: a, port_number: 0, name: `p${a}`, short_name: `p${a}`})),
            },
        ],
        links: [
            {link_id: 'l-rs', nodes: [end(R, 0, 0), end(S, 0, 0)]},
            {link_id: 'l-as', nodes: [end(A, 0, 0), end(S, 0, 1)]},
            {link_id: 'l-bs', nodes: [end(B, 0, 0), end(S, 0, 2)]},
            {link_id: 'l-rx', nodes: [end(R, 0, 1), end(X, 0, 0)]},
            {link_id: 'l-xb', nodes: [end(X, 1, 0), end(B, 1, 0)]},
        ],
        drawings: [],
    },
};

const iface = (name, a, p, extra = {}) => ({name, kind: 'physical', port: {adapter: a, port: p}, ...extra});
const facts = (over = {}) => ({
    schema: 'gns3lab.facts/1', rev: 1, generated_at: '2026-10-27T10:00:00Z', ttl: 90,
    nodes: {
        [R]: {status: 'started', router_id: '1.1.1.1', interfaces: [
            iface('Ethernet0/0', 0, 0, {admin: 'up', ipv4: ['10.0.12.1/24']}),
            iface('Ethernet0/1', 0, 1, {admin: 'down', ipv4: ['10.0.13.1/24']}),
        ]},
        [S]: {status: 'started', vlans: [{id: 10, name: 'USERS'}], interfaces: [
            iface('Ethernet0/0', 0, 0, {admin: 'up', switchport: {mode: 'access', vlan: 10}}),
            iface('Ethernet0/1', 0, 1, {admin: 'up', switchport: {mode: 'access', vlan: 10}}),
            iface('Ethernet0/2', 0, 2, {admin: 'up', switchport: {mode: 'access', vlan: 10}}),
        ]},
        [A]: {status: 'started', interfaces: [
            iface('ens3', 0, 0, {mac: '50:5b:3c:02:a2:00', admin: 'up', oper: 'up', ipv4: ['10.0.12.10/24']}),
        ]},
        [B]: {status: 'started', interfaces: [
            iface('ens3', 0, 0, {mac: '50:5b:3c:02:b2:00', admin: 'up', oper: 'up', ipv4: ['10.0.12.20/24']}),
            iface('ens4', 1, 0, {mac: '50:5b:3c:02:b2:01', admin: 'up', oper: 'up', ipv4: ['10.0.13.20/24']}),
        ]},
        [X]: {status: 'started', interfaces: [
            iface('p0', 0, 0, {admin: 'up', oper: 'up', mirror: true, switchport: {mode: 'access', vlan: 10}}),
            iface('p1', 1, 0, {admin: 'up', oper: 'up', mirror: true, switchport: {mode: 'access', vlan: 10}}),
        ]},
    },
    ...over,
});

const lines = text => text.split('\n').map(l => l.replace(/\s+$/, ''));
const IP_A = `
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
2: ens3: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000
    link/ether 50:5b:3c:02:a2:00 brd ff:ff:ff:ff:ff:ff
    inet 10.0.12.10/24 brd 10.0.12.255 scope global ens3
`;
const IP_A_DOWN = `
2: ens3: <BROADCAST,MULTICAST> mtu 1500 qdisc fq_codel state DOWN group default qlen 1000
    link/ether 50:5b:3c:02:a2:00 brd ff:ff:ff:ff:ff:ff
    inet 10.0.12.10/24 brd 10.0.12.255 scope global ens3
`;
const IP_ROUTE = `
default via 10.0.12.1 dev ens3
10.0.12.0/24 dev ens3 proto kernel scope link src 10.0.12.10
`;
const IPBRIEF = `
Interface                  IP-Address      OK? Method Status                Protocol
Ethernet0/0                10.0.12.1       YES manual up                    up
Ethernet0/1                10.0.13.1       YES manual administratively down down
`;
const IPBRIEF_UP = `
Interface                  IP-Address      OK? Method Status                Protocol
Ethernet0/0                10.0.12.1       YES manual up                    up
Ethernet0/1                10.0.13.1       YES manual up                    up
`;
const STATUS = `
Port      Name               Status       Vlan       Duplex  Speed Type
Et0/0                        connected    trunk      auto    auto  unknown
Et0/1                        connected    10         auto    auto  unknown
Et0/2                        connected    10         auto    auto  unknown
`;

/**
 * The page in miniature: the store of what was seen, the mask, and the steps lab.js takes after a command or a snapshot.
 */
class Page {
    constructor(mode, {start = [], factsDoc = facts()} = {}) {
        this.mode = mode;
        this.start = start;
        this.model = buildModel(DOC);
        this.factsDoc = factsDoc;
        this.seen = new Map();
        this.mask = rv.emptyMask();
        this.at = 0;
        this.settle();
    }

    settle() {
        const step = rv.settleReveal({mode: this.mode, model: this.model, factsDoc: this.factsDoc, seen: this.seen, mask: this.mask});
        this.mask = step.mask;
    }

    run(nodeId, kind, command, text) {
        const result = interpret(kind, command, lines(text));
        if (result && !result.save) {
            this.seen = addSeen(this.seen, nodeId, result, {at: ++this.at, fp: null});
        }
        const items = rv.revealByCommand({
            mode: this.mode, model: this.model, factsDoc: this.factsDoc, nodeId, result: result && !result.save ? result : null,
        });
        this.mask = rv.maskAdd(this.mask, items).mask;
        this.settle();
    }

    snapshot(factsDoc) {
        this.factsDoc = factsDoc;
        this.settle();
    }

    get merged() {
        return mergeSeen(this.model, this.factsDoc, this.seen);
    }

    get scene() {
        return rv.applyReveal({mode: this.mode, model: this.model, doc: this.merged, mask: this.mask, start: this.start});
    }

    state(id) {
        return this.scene.view.states.get(id);
    }

    ifaces(id) {
        const node = this.scene.doc.nodes[id];
        return node ? (node.interfaces || []) : null;
    }
}
const states = page => Object.fromEntries([...page.scene.view.states].map(([id, st]) => [id, st]));
const opened = page => [...page.scene.view.links].sort();

/* ---------------- the mask ---------------- */

check('the mask only grows, takes each item once and ignores what is not an item', () => {
    const base = rv.emptyMask();
    const one = rv.maskAdd(base, [
        {nodeid: R, kind: 'node', item: ''}, {nodeid: R, kind: 'node', item: ''},
        {nodeid: R, kind: 'iface', item: 'Ethernet0/0'}, {nodeid: R, kind: 'group', item: 'l1'},
        {nodeid: '', kind: 'link', item: 'l-rs'}, {nodeid: S, kind: 'known', item: ''},
        {nodeid: R, kind: 'group', item: 'bogus'}, {nodeid: '', kind: 'node', item: ''}, {nodeid: R, kind: 'link', item: 'x'},
        {nodeid: R, kind: 'iface', item: ''}, {nodeid: R, kind: 'what', item: ''}, null,
    ]);
    assert.equal(one.added.length, 5);
    assert.equal(rv.maskItems(base).length, 0, 'the old mask is not changed');
    const again = rv.maskAdd(one.mask, one.added);
    assert.equal(again.added.length, 0);
    assert.equal(again.mask, one.mask, 'nothing new: the same mask comes back');
    assert.deepEqual(rv.maskItems(rv.maskFromItems(rv.maskItems(one.mask))).length, 5);
});

check('an interface name the server would refuse is not sendable', () => {
    assert.ok(rv.sendable({nodeid: R, kind: 'iface', item: 'Ethernet0/0.10'}));
    assert.ok(rv.sendable({nodeid: R, kind: 'iface', item: 'ens3.10'}));
    assert.ok(!rv.sendable({nodeid: R, kind: 'iface', item: 'bad name'}));
    assert.ok(!rv.sendable({nodeid: R, kind: 'iface', item: 'a'.repeat(101)}));
    assert.ok(rv.sendable({nodeid: R, kind: 'node', item: ''}));
});

/* ---------------- seen ---------------- */

check('seen: groups of data are uncovered by the command that shows them, cables are there from the start', () => {
    const page = new Page('seen');
    assert.deepEqual(Object.values(states(page)), Array(5).fill('open'));
    assert.equal(page.scene.view.links.size, 5);
    // Nothing was run: the interfaces are there, the data is not.
    assert.deepEqual(Object.keys(page.ifaces(A)[0]).sort(), ['kind', 'name', 'port']);
    page.run(A, 'linux', 'ip a', IP_A);
    const ens3 = page.ifaces(A).find(i => i.name === 'ens3');
    assert.equal(ens3.admin, 'up');
    assert.deepEqual(ens3.ipv4, ['10.0.12.10/24']);
    assert.ok(!('switchport' in ens3));
    assert.deepEqual([...page.mask.groups.get(A)].sort(), ['l1', 'l3']);
    // The addresses of the router stay closed until its console shows them.
    assert.ok(!('ipv4' in page.ifaces(R)[0]));
    page.run(R, 'ios', 'sh ip int br', IPBRIEF);
    assert.equal(page.ifaces(R)[0].ipv4[0], '10.0.12.1/24');
    page.run(A, 'linux', 'ip route', IP_ROUTE);
    assert.ok([...page.mask.groups.get(A)].includes('routes'));
    assert.ok(page.scene.doc.nodes[A].routes.length, 'routes are uncovered by ip route');
    assert.ok(!('routes' in page.scene.doc.nodes[B]));
    assert.equal(page.mask.nodes.size, 0, 'seen has no open nodes: everything is there');
});

check('seen: the vlans of a switch come with show vlan brief and the switchports with the status', () => {
    const page = new Page('seen');
    assert.ok(!('vlans' in page.scene.doc.nodes[S]));
    page.run(S, 'ios', 'sh int status', STATUS);
    assert.deepEqual(page.ifaces(S)[1].switchport, {mode: 'access', vlan: 10});
    assert.deepEqual([...page.mask.groups.get(S)].sort(), ['l1', 'l2']);
});

check('live: nothing is filtered', () => {
    const page = new Page('live');
    const {view, doc} = page.scene;
    assert.equal(view, null);
    assert.equal(doc, page.merged);
    assert.equal(rv.drawModel(page.model, view), page.model);
    assert.equal(rv.layerModel(page.model, view), page.model);
});

/* ---------------- fog ---------------- */

check('fog: every node starts as a silhouette, no cable, no facts', () => {
    const page = new Page('fog');
    assert.deepEqual(Object.values(states(page)), Array(5).fill('silhouette'));
    assert.equal(page.scene.view.links.size, 0);
    assert.deepEqual(page.scene.doc.nodes, {});
    assert.equal(rv.drawModel(page.model, page.scene.view).nodes.size, 5);
    assert.equal(rv.layerModel(page.model, page.scene.view).nodes.size, 0);
});

check('fog: the first output opens a node, ip a opens its interfaces, a cable that works shows the neighbour', () => {
    const page = new Page('fog');
    // Whatever the student typed, the node opens: its console printed something.
    page.run(A, 'linux', 'whoami', '');
    assert.equal(page.state(A), 'open');
    assert.equal(page.state(S), 'silhouette');
    assert.deepEqual(page.ifaces(A), [], 'no interface of PC-A is known yet');

    page.run(A, 'linux', 'ip a', IP_A);
    assert.deepEqual([...page.mask.ifaces.get(A)].sort(), ['ens3', 'lo']);
    assert.equal(page.state(S), 'known', 'the cable PC-A - SW1 works, SW1 is found');
    assert.deepEqual(opened(page), ['l-as']);
    assert.equal(page.state(R), 'silhouette');
    const ens3 = page.ifaces(A).find(i => i.name === 'ens3');
    assert.deepEqual(ens3.ipv4, ['10.0.12.10/24']);
    // The neighbour is known by name and type only: no interfaces, no facts, and its end of the cable has no port name.
    assert.deepEqual(page.scene.doc.nodes[S].interfaces, []);
    assert.ok(page.scene.view.ends.has(`${A}|0/0`));
    assert.ok(!page.scene.view.ends.has(`${S}|0/1`));
    assert.ok(![...page.scene.view.ends].some(k => k.startsWith(S)), 'no port of SW1 is shown');
    assert.equal([...page.scene.view.groups.get(S)].length, 0);
});

check('fog: a router interface that is down uncovers no cable; raising it does, by the deciding facts as well', () => {
    const page = new Page('fog');
    page.run(R, 'ios', 'sh ip int br', IPBRIEF);
    assert.deepEqual([...page.mask.ifaces.get(R)].sort(), ['Ethernet0/0', 'Ethernet0/1']);
    assert.deepEqual(opened(page), ['l-rs'], 'Ethernet0/1 is shut down: its cable stays hidden');
    assert.equal(page.state(S), 'known');
    assert.equal(page.state(X), 'silhouette');

    // no shutdown, the student repeats the command: the seen layer says up.
    page.run(R, 'ios', 'sh ip int br', IPBRIEF_UP);
    assert.deepEqual(opened(page), ['l-rs', 'l-rx']);
    // The device without a console is found by the cable and its port on this end comes with it.
    assert.equal(page.state(X), 'known');
    assert.deepEqual([...page.mask.ifaces.get(X)], ['p0']);
    assert.ok(page.scene.view.ends.has(`${X}|0/0`));
    assert.ok(page.scene.view.ends.has(`${X}|1/0`) === false, 'its other port is not on an uncovered cable');
});

check('fog: a raised interface the student did not look at again uncovers the cable once the deciding facts show it', () => {
    const page = new Page('fog');
    page.run(R, 'ios', 'sh ip int br', IPBRIEF);
    assert.deepEqual(opened(page), ['l-rs']);
    // The saved configuration changed: the new snapshot says Ethernet0/1 is up. The seen layer still says down.
    const up = facts();
    up.nodes[R].interfaces[1].admin = 'up';
    page.snapshot(up);
    assert.deepEqual(opened(page), ['l-rs', 'l-rx']);
});

check('fog: an interface that is down on the device does not open a cable', () => {
    const down = facts();
    down.nodes[A].interfaces[0].oper = 'down';
    const page = new Page('fog', {factsDoc: down});
    page.run(A, 'linux', 'ip a', IP_A_DOWN);
    assert.deepEqual(opened(page), []);
    assert.equal(page.state(S), 'silhouette');
    // The student raises it, the deciding facts follow.
    page.snapshot(facts());
    assert.deepEqual(opened(page), ['l-as']);
});

check('fog: a stopped node uncovers nothing', () => {
    const stopped = facts();
    stopped.nodes[A].status = 'stopped';
    const page = new Page('fog', {factsDoc: stopped});
    page.mask = rv.maskAdd(page.mask, [{nodeid: A, kind: 'node', item: ''}, {nodeid: A, kind: 'iface', item: 'ens3'}]).mask;
    page.settle();
    assert.deepEqual(opened(page), []);
});

check('fog: starting nodes - the rest are hidden and are found by cables only', () => {
    const page = new Page('fog', {start: ['PC-A']});
    assert.deepEqual(states(page), {[R]: 'hidden', [S]: 'hidden', [A]: 'silhouette', [B]: 'hidden', [X]: 'hidden'});
    assert.equal(rv.drawModel(page.model, page.scene.view).nodes.size, 1);
    page.run(A, 'linux', 'ip a', IP_A);
    assert.deepEqual(states(page), {[R]: 'hidden', [S]: 'known', [A]: 'open', [B]: 'hidden', [X]: 'hidden'});
    assert.deepEqual([...rv.drawModel(page.model, page.scene.view).nodes.keys()].sort(), [A, S].sort());
    // SW1 is known now, but it does not open anything else by itself: PC-B is behind its console.
    assert.equal(page.state(B), 'hidden');
});

check('fog: starting names that match nothing are ignored: every node is a silhouette', () => {
    const page = new Page('fog', {start: ['nobody']});
    assert.deepEqual(Object.values(states(page)), Array(5).fill('silhouette'));
    assert.equal(rv.startIds(page.model, ['nobody']), null);
    assert.equal(rv.startIds(page.model, []), null);
    assert.deepEqual([...rv.startIds(page.model, ['PC-A', 'R1'])].sort(), [A, R].sort());
});

check('fog: the filtered facts carry the open groups of the open interfaces only', () => {
    const page = new Page('fog');
    page.run(S, 'ios', 'sh ip int br', IPBRIEF.replace(/Ethernet0\/1.*\n/, ''));
    const nodes = page.scene.doc.nodes;
    // `show ip interface brief` uncovers l1 and l3: the state and the addresses, not the switchport.
    assert.deepEqual([...page.mask.groups.get(S)].sort(), ['l1', 'l3']);
    const e00 = nodes[S].interfaces.find(i => i.name === 'Ethernet0/0');
    assert.equal(e00.admin, 'up');
    assert.ok(!('switchport' in e00));
    assert.ok(!('vlans' in nodes[S]));
    // The interface the command did not show is not there.
    assert.ok(!nodes[S].interfaces.some(i => i.name === 'Ethernet0/2'));
    page.run(S, 'ios', 'sh int status', STATUS);
    assert.deepEqual(page.scene.doc.nodes[S].interfaces.find(i => i.name === 'Ethernet0/1').switchport, {mode: 'access', vlan: 10});
});

check('fog: a VPCS-like interface found by its port keeps the name of the deciding facts', () => {
    const page = new Page('fog');
    const items = rv.revealByCommand({
        mode: 'fog', model: page.model, factsDoc: page.factsDoc, nodeId: A,
        result: interpret('linux', 'ip a', lines(IP_A)),
    });
    assert.deepEqual(items.filter(i => i.kind === 'iface').map(i => i.item).sort(), ['ens3', 'lo']);
});

check('fog: a screen opens its node whole', () => {
    const page = new Page('fog');
    page.mask = rv.maskAdd(page.mask, [{nodeid: B, kind: 'node', item: ''}]).mask;
    assert.equal(page.state(B), 'open');
});

check('fog: the findings do not name a node that is not found or a cable that is not uncovered', () => {
    const page = new Page('fog');
    // PC-B has the address of the router's second interface in a subnet that makes a finding; R1 has one down.
    page.run(R, 'ios', 'sh ip int br', IPBRIEF);
    const {view, doc} = page.scene;
    const names = ['PC-B', 'LSW'];
    const model = rv.layerModel(page.model, view);
    assert.deepEqual([...model.nodes.keys()].sort(), [R, S].sort());
    const result = derive(model, doc);
    const text = JSON.stringify(result.der.l1.issues) + JSON.stringify(result.der.l2.issues)
        + JSON.stringify(result.der.l3.issues) + JSON.stringify(result.der.rt.issues);
    names.forEach(name => assert.ok(!text.includes(name), `a finding names ${name}: ${text}`));
    // The unfiltered lab does have a finding about the hidden cable of Ethernet0/1.
    const full = derive(page.model, page.merged);
    assert.ok(JSON.stringify(full.der.l1.issues).includes('Ethernet0/1'), 'the finding exists on the full lab');
    assert.ok(!text.includes('Ethernet0/1'), 'and it is not on the filtered one');
});

check('fog: what is uncovered stays uncovered when a new snapshot comes or the interface goes down', () => {
    const page = new Page('fog');
    page.run(A, 'linux', 'ip a', IP_A);
    const before = rv.maskItems(page.mask).length;
    const down = facts();
    down.nodes[A].interfaces[0].oper = 'down';
    page.snapshot(down);
    page.snapshot(facts({nodes: {}}));
    assert.ok(rv.maskItems(page.mask).length >= before);
    assert.equal(page.state(S), 'known');
    assert.deepEqual(opened(page), ['l-as']);
    // The cable stays uncovered; with no facts there is nothing more to say about its far end.
    assert.ok(!page.scene.doc.nodes[S]);
});

check('fog: the sub-interface finds its port through its parent, which comes along as a bare place', () => {
    const doc = facts();
    doc.nodes[R].interfaces.push({name: 'Ethernet0/0.10', kind: 'subif', parent: 'Ethernet0/0', dot1q: 10, admin: 'up', ipv4: ['10.1.10.1/24']});
    const page = new Page('fog', {factsDoc: doc});
    page.mask = rv.maskAdd(page.mask, [
        {nodeid: R, kind: 'node', item: ''}, {nodeid: R, kind: 'iface', item: 'Ethernet0/0.10'}, {nodeid: R, kind: 'group', item: 'l3'},
    ]).mask;
    const list = page.ifaces(R);
    const parent = list.find(i => i.name === 'Ethernet0/0');
    assert.deepEqual(Object.keys(parent).sort(), ['kind', 'name', 'port'], 'the parent has no data');
    assert.deepEqual(list.find(i => i.name === 'Ethernet0/0.10').ipv4, ['10.1.10.1/24']);
    assert.ok(!list.some(i => i.name === 'Ethernet0/1'));
});

check('the groups of unknown commands follow the fields their output has', () => {
    assert.deepEqual(rv.groupsOf({label: 'something new', fragment: {interfaces: [{name: 'a', admin: 'up', ipv4: []}]}}), ['l1', 'l3']);
    assert.deepEqual(rv.groupsOf({label: 'something new', fragment: {interfaces: [], routes: [{dst: 'default'}]}}), ['routes']);
    assert.deepEqual(rv.groupsOf({label: 'show ip', fragment: {interfaces: [{name: 'eth0', mac: 'x', ipv4: []}]}}), ['l3']);
});

check('fog: live L1 exposes only ports of opened interfaces and the opened L1 group', () => {
    const model = buildModel(DOC), doc = facts();
    doc.nodes[R].l1_ports = {ok: true, observed_at: new Date().toISOString(), ttl: 15,
        ports: [0, 1].map(port => ({port: {adapter: 0, port}, state: 'up'}))};
    const mask = rv.maskFromItems([{nodeid: R, kind: 'node', item: ''},
        {nodeid: R, kind: 'iface', item: 'Ethernet0/0'}, {nodeid: R, kind: 'group', item: 'l1'}]);
    const visible = rv.applyReveal({mode: 'fog', model, doc, mask}).doc;
    assert.equal(visible.nodes[R].l1_ports.ports.length, 1);
    assert.equal(visible.nodes[R].l1_ports.ports[0].port.port, 0);
    const hiddenMask = rv.maskFromItems([{nodeid: R, kind: 'node', item: ''}]);
    const hidden = rv.applyReveal({mode: 'fog', model, doc, mask: hiddenMask}).doc;
    assert.equal(hidden.nodes[R].l1_ports, undefined);
});

/* ---------------- run ---------------- */

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
