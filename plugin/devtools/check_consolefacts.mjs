// Checks the facts "seen in the console" (docs/live-state.md §8) without Moodle or a browser: recognizing a command,
// parsing the output of IOS (an IOL switch and router), Debian and VPCS, merging with the deciding facts, the
// fingerprint that drops what a new snapshot has outdated, and the watch that takes commands off the buffer of a
// real xterm.js terminal.
//
//   node plugin/devtools/check_consolefacts.mjs        (Node 22; exit code 1 if a scenario fails)
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
    return next(url, url.startsWith(src) && !url.endsWith('/xterm.js') ? {...context, format: 'module'} : context);
}`));
const {buildModel} = await import(srcUrl + 'model.js');
const {derive} = await import(srcUrl + 'facts.js');
const {kindOf} = await import(srcUrl + 'snippets.js');
const cf = await import(srcUrl + 'consolefacts.js');
const {ConsoleWatch} = await import(srcUrl + 'consolewatch.js');
const {interpret, recognize, addSeen, mergeSeen, pruneSeen, fingerprint, parseVlanList, iosName} = cf;

// The real terminal of the console. Its file is a UMD build: the module is its `default` export.
const xterm = (await import(srcUrl + 'xterm.js')).default;

const failures = [];
const queue = [];
const check = (name, fn) => queue.push([name, fn]);

const lines = text => text.split('\n').map(l => l.replace(/\s+$/, ''));
const seen = (kind, command, text, paged) => interpret(kind, command, lines(text), paged);
const names = res => res.fragment.interfaces.map(i => i.name);
const ifc = (res, name) => res.fragment.interfaces.find(i => i.name === name);

/* ---------------- what the devices print ---------------- */

const IOL_STATUS = `
Port      Name               Status       Vlan       Duplex  Speed Type
Et0/0                        connected    trunk      auto    auto  unknown
Et0/1     PC1                connected    10         auto    auto  unknown
Et0/2                        disabled     10         auto    auto  unknown
Et0/3                        notconnect   1          auto    auto  unknown
Et1/0                        err-disabled 10         auto    auto  unknown
Et1/1                        connected    routed     auto    auto  unknown
Po1                          connected    trunk      auto    auto  unknown
`;

const IOL_TRUNK = `
Port        Mode             Encapsulation  Status        Native vlan
Et0/0       on               802.1q         trunking      1
Et1/2       on               802.1q         trunking      99

Port        Vlans allowed on trunk
Et0/0       10,99
Et1/2       1-4094

Port        Vlans allowed and active in management domain
Et0/0       10,99
Et1/2       1,10,99

Port        Vlans in spanning tree forwarding state and not pruned
Et0/0       10,99
Et1/2       1,10,99
`;

const IOL_VLANS = `
VLAN Name                             Status    Ports
---- -------------------------------- --------- -------------------------------
1    default                          active    Et0/3, Et1/0, Et1/1
10   USERS                            active    Et0/1, Et0/2
99   MGMT                             active
                                                Et1/3,
                                                Et2/0
1002 fddi-default                     act/unsup
1003 token-ring-default               act/unsup
1004 fddinet-default                  act/unsup
1005 trnet-default                    act/unsup
`;

const IOL_IPBRIEF = `
Interface                  IP-Address      OK? Method Status                Protocol
Ethernet0/0                10.0.12.1       YES manual up                    up
Ethernet0/1                unassigned      YES unset  administratively down down
Ethernet0/2                10.0.13.1       YES manual up                    down
Ethernet0/2.10             10.1.10.1       YES manual up                    up
Vlan99                     10.1.99.2       YES manual up                    up
Loopback0                  1.1.1.1         YES manual up                    up
Tunnel0                    unassigned      YES unset  up                    down
`;

const IOL_IPINT = `
Ethernet0/0 is up, line protocol is up
  Internet address is 10.0.12.1/30
  Broadcast address is 255.255.255.255
  Address determined by setup command
  MTU is 1500 bytes
  Helper address is not set
Ethernet0/1 is administratively down, line protocol is down
  Internet protocol processing disabled
Ethernet0/2 is up, line protocol is up
  Internet address is 10.0.13.1/24
  Secondary address 10.0.14.1/24
  MTU is 1500 bytes
Loopback0 is up, line protocol is up
  Internet address is 1.1.1.1/32
`;

const DEBIAN_ADDR = `
1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    inet 127.0.0.1/8 scope host lo
       valid_lft forever preferred_lft forever
    inet6 ::1/128 scope host noprefixroute
       valid_lft forever preferred_lft forever
2: ens3: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000
    link/ether 50:5b:3c:02:a2:00 brd ff:ff:ff:ff:ff:ff
    altname enp0s3
    inet 10.0.10.99/24 brd 10.0.10.255 scope global dynamic ens3
       valid_lft 86368sec preferred_lft 86368sec
    inet 10.0.11.99/24 scope global secondary ens3
       valid_lft forever preferred_lft forever
    inet6 fe80::525b:3cff:fe02:a200/64 scope link
       valid_lft forever preferred_lft forever
3: ens4: <BROADCAST,MULTICAST> mtu 1500 qdisc noop state DOWN group default qlen 1000
    link/ether 50:5b:3c:02:a2:01 brd ff:ff:ff:ff:ff:ff
4: ens3.10@ens3: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default qlen 1000
    link/ether 50:5b:3c:02:a2:00 brd ff:ff:ff:ff:ff:ff
    inet 10.0.20.12/24 brd 10.0.20.255 scope global ens3.10
`;

const DEBIAN_BRIEF = `
lo               UNKNOWN        127.0.0.1/8 ::1/128
ens3             UP             10.0.10.99/24 fe80::525b:3cff:fe02:a200/64
ens4             DOWN
ens3.10@ens3     UP             10.0.20.12/24
`;

const DEBIAN_ROUTE = `
default via 10.0.10.1 dev ens3 proto dhcp src 10.0.10.99 metric 100
10.0.10.0/24 dev ens3 proto kernel scope link src 10.0.10.99 metric 100
10.0.20.0/24 via 10.0.10.254 dev ens3
10.9.9.9 dev ens4 scope link
unreachable 10.8.0.0/16 metric 1024
`;

const VPCS_SHOW = `
NAME        : PC1[1]
IP/MASK     : 10.0.10.11/24
GATEWAY     : 10.0.10.1
DNS         :
MAC         : 00:50:79:66:68:00
LPORT       : 20000
RHOST:PORT  : 127.0.0.1:20001
MTU         : 1500
`;

const VPCS_ALL = `
NAME   IP/MASK              GATEWAY           MAC                LPORT  RHOST:PORT
PC1    10.0.10.11/24        10.0.10.1         00:50:79:66:68:00  20000  127.0.0.1:20001
`;

/* ---------------- recognizing a command ---------------- */

const rec = (kind, cmd) => {
    const r = recognize(kind, cmd);
    return r ? r.label + (r.partial ? ' (partial)' : '') : null;
};

check('recognize IOS: abbreviations, case, do', () => {
    for (const cmd of ['show ip interface brief', 'sh ip int b', 'sh ip int br', 'SH IP INT BRIEF', 'do show ip int br', 'Show Ip Interface Brief  ']) {
        assert.equal(rec('ios', cmd), 'show ip interface brief', cmd);
    }
    for (const cmd of ['show interfaces status', 'sh int status', 'sh interface stat', 'do sh int status']) {
        assert.equal(rec('ios', cmd), 'show interfaces status', cmd);
    }
    for (const cmd of ['sh vl br', 'sh vlan brief', 'show vlan b']) {
        assert.equal(rec('ios', cmd), 'show vlan brief', cmd);
    }
    assert.equal(rec('ios', 'sh int trunk'), 'show interfaces trunk');
    assert.equal(rec('ios', 'sh ip int'), 'show ip interface');
    assert.equal(rec('ios', 'show ip interface'), 'show ip interface');
});
check('recognize IOS: a word shorter than its minimum, or another command, is not one', () => {
    for (const cmd of ['s ip int b', 'sh i int b', 'show ip route', 'show ip', 'show running-config', 'show vlan', 'show vlan id 10',
        'shutdown', 'sh int', '', 'conf t', 'enable', 'ping 10.0.0.1']) {
        assert.equal(rec('ios', cmd), null, cmd);
    }
});
check('recognize IOS: a filter or one interface makes the output partial', () => {
    assert.equal(rec('ios', 'sh ip int br | include up'), 'show ip interface brief (partial)');
    assert.equal(rec('ios', 'sh ip int br | begin Loop'), 'show ip interface brief (partial)');
    assert.equal(rec('ios', 'show interfaces Ethernet0/1 status'), 'show interfaces status (partial)');
    assert.equal(rec('ios', 'show interfaces Et0/1 trunk'), 'show interfaces trunk (partial)');
    assert.equal(rec('ios', 'sh int e 0/1 status'), 'show interfaces status (partial)');
    assert.equal(rec('ios', 'show ip interface Ethernet0/1'), 'show ip interface (partial)');
    assert.equal(rec('ios', 'sh vlan brief | i USERS'), 'show vlan brief (partial)');
});
check('recognize IOS: saving the configuration', () => {
    for (const cmd of ['write', 'wr', 'write memory', 'wr mem', 'copy running-config startup-config', 'copy run start', 'do write', 'do wr mem']) {
        assert.ok(recognize('ios', cmd).save, cmd);
    }
    for (const cmd of ['write erase', 'wr term', 'copy run tftp:', 'copy start run', 'write memory now']) {
        assert.ok(!(recognize('ios', cmd) || {}).save, cmd);
    }
});
check('recognize Linux: ip addr and ip route in their spellings', () => {
    for (const cmd of ['ip a', 'ip addr', 'ip address', 'ip addr show', 'ip a s', 'ip -c a', 'ip -4 a', 'ip -br a', 'ip -4 -br a',
        'ip -c -br addr', 'sudo ip a', 'ip --color a', 'ip address list']) {
        assert.equal(rec('linux', cmd), 'ip addr', cmd);
    }
    for (const cmd of ['ip a show dev ens3', 'ip addr show ens3', 'ip -br a show up']) {
        assert.equal(rec('linux', cmd), 'ip addr (partial)', cmd);
    }
    for (const cmd of ['ip route', 'ip r', 'ip route show', 'ip -4 r', 'ip r s', 'ip route list']) {
        assert.equal(rec('linux', cmd), 'ip route', cmd);
    }
    for (const cmd of ['ip r show dev ens3', 'ip route show default', 'ip route 10.0.0.0/8', 'ip -6 route']) {
        assert.equal(rec('linux', cmd), 'ip route (partial)', cmd);
    }
});
check('recognize Linux: other commands, changes and pipelines are not read', () => {
    for (const cmd of ['ip link', 'ip neigh', 'ip a add 10.0.0.1/24 dev ens3', 'ip addr flush dev ens3', 'ip route add default via 1.1.1.1',
        'ip route get 8.8.8.8', 'ip route del default', 'ip a | grep inet', 'ip a; ip r', 'ls', 'ifconfig', 'ip', 'echo ip a']) {
        assert.equal(rec('linux', cmd), null, cmd);
    }
});
check('recognize VPCS', () => {
    assert.equal(rec('vpcs', 'show ip'), 'show ip');
    assert.equal(rec('vpcs', 'show ip all'), 'show ip');
    assert.equal(rec('vpcs', 'sh ip'), 'show ip');
    assert.equal(rec('vpcs', 'ip 10.0.0.1/24'), null);
    assert.equal(rec('vpcs', 'show'), null);
    assert.equal(rec('ios', 'show ip'), null);
});

/* ---------------- parsing the output ---------------- */

check('IOS show interfaces status: states, VLANs, routed, trunk; no Port-channel', () => {
    const res = seen('ios', 'sh int status', IOL_STATUS);
    assert.deepEqual(names(res), ['Ethernet0/0', 'Ethernet0/1', 'Ethernet0/2', 'Ethernet0/3', 'Ethernet1/0', 'Ethernet1/1']);
    const f = n => ifc(res, n);
    assert.deepEqual(f('Ethernet0/0'), {name: 'Ethernet0/0', admin: 'up', oper: 'up', switchport: {mode: 'trunk'}});
    assert.deepEqual(f('Ethernet0/1'), {name: 'Ethernet0/1', admin: 'up', oper: 'up', switchport: {mode: 'access', vlan: 10}});
    assert.deepEqual(f('Ethernet0/2'), {name: 'Ethernet0/2', admin: 'down', oper: 'down', switchport: {mode: 'access', vlan: 10}});
    assert.deepEqual(f('Ethernet0/3'), {name: 'Ethernet0/3', admin: 'up', oper: 'down', switchport: {mode: 'access', vlan: 1}});
    assert.deepEqual(f('Ethernet1/0'), {name: 'Ethernet1/0', admin: 'up', oper: 'down', switchport: {mode: 'access', vlan: 10}});
    assert.deepEqual(f('Ethernet1/1'), {name: 'Ethernet1/1', admin: 'up', oper: 'up', switchport: null});
    assert.equal(res.partial, false);
});
check('IOS show interfaces status: a filtered output without the header is read too, as partial', () => {
    const res = seen('ios', 'sh int status | include disabled', 'Et0/2                        disabled     10         auto    auto  unknown\nEt1/0                        err-disabled 10         auto    auto  unknown');
    assert.equal(res.partial, true);
    assert.deepEqual(names(res), ['Ethernet0/2', 'Ethernet1/0']);
});
check('IOS show interfaces trunk: native, allowed, "all" for 1-4094, wrapped list', () => {
    const res = seen('ios', 'show interfaces trunk', IOL_TRUNK);
    assert.deepEqual(ifc(res, 'Ethernet0/0').switchport, {mode: 'trunk', native: 1, allowed: [10, 99]});
    assert.deepEqual(ifc(res, 'Ethernet1/2').switchport, {mode: 'trunk', native: 99, allowed: 'all'});
    const wrapped = seen('ios', 'show interfaces trunk', IOL_TRUNK.replace('Et0/0       10,99\nEt1/2       1-4094', 'Et0/0       1,10,20,\n            99\nEt1/2       1-4094'));
    assert.deepEqual(ifc(wrapped, 'Ethernet0/0').switchport.allowed, [1, 10, 20, 99]);
});
check('parseVlanList', () => {
    assert.deepEqual(parseVlanList('1,10,20-22'), [1, 10, 20, 21, 22]);
    assert.equal(parseVlanList('1-4094'), 'all');
    assert.deepEqual(parseVlanList('none'), []);
    assert.equal(parseVlanList('x'), null);
    assert.deepEqual(parseVlanList('10,'), [10]);
});
check('IOS show vlan brief: vlans without the legacy ones, access ports, wrapped port list', () => {
    const res = seen('ios', 'sh vl br', IOL_VLANS);
    assert.deepEqual(res.fragment.vlans, [{id: 1, name: 'default'}, {id: 10, name: 'USERS'}, {id: 99, name: 'MGMT'}]);
    const port = n => ifc(res, n).switchport;
    assert.deepEqual(port('Ethernet0/1'), {mode: 'access', vlan: 10});
    assert.deepEqual(port('Ethernet1/0'), {mode: 'access', vlan: 1});
    assert.deepEqual(port('Ethernet1/3'), {mode: 'access', vlan: 99});
    assert.deepEqual(port('Ethernet2/0'), {mode: 'access', vlan: 99});
});
check('IOS show vlan brief: with a filter the VLAN list is not replaced', () => {
    const res = seen('ios', 'sh vlan brief | include USERS', '10   USERS                            active    Et0/1, Et0/2');
    assert.equal(res.partial, true);
    assert.equal(res.fragment.vlans, undefined);
    assert.deepEqual(names(res), ['Ethernet0/1', 'Ethernet0/2']);
});
check('IOS show vlan brief: with --More-- the VLAN list is not replaced either', () => {
    const res = seen('ios', 'sh vlan brief', IOL_VLANS, true);
    assert.equal(res.partial, true);
    assert.equal(res.fragment.vlans, undefined);
});
check('IOS show ip interface brief: states, unassigned, kinds', () => {
    const res = seen('ios', 'sh ip int br', IOL_IPBRIEF);
    assert.deepEqual(names(res), ['Ethernet0/0', 'Ethernet0/1', 'Ethernet0/2', 'Ethernet0/2.10', 'Vlan99', 'Loopback0']);
    assert.deepEqual(ifc(res, 'Ethernet0/0'), {name: 'Ethernet0/0', admin: 'up', oper: 'up'});
    assert.deepEqual(ifc(res, 'Ethernet0/1'), {name: 'Ethernet0/1', admin: 'down', oper: 'down', ipv4: []});
    assert.deepEqual(ifc(res, 'Ethernet0/2'), {name: 'Ethernet0/2', admin: 'up', oper: 'down'});
});
check('IOS show ip interface: addresses with masks, secondary, none', () => {
    const res = seen('ios', 'show ip interface', IOL_IPINT);
    assert.deepEqual(ifc(res, 'Ethernet0/0'), {name: 'Ethernet0/0', admin: 'up', oper: 'up', ipv4: ['10.0.12.1/30']});
    assert.deepEqual(ifc(res, 'Ethernet0/1'), {name: 'Ethernet0/1', admin: 'down', oper: 'down', ipv4: []});
    assert.deepEqual(ifc(res, 'Ethernet0/2').ipv4, ['10.0.13.1/24', '10.0.14.1/24']);
    assert.deepEqual(ifc(res, 'Loopback0').ipv4, ['1.1.1.1/32']);
});
check('IOS show ip interface: a block cut off before its address says nothing about the address', () => {
    const res = seen('ios', 'show ip interface', 'Ethernet0/0 is up, line protocol is up\n', true);
    assert.deepEqual(ifc(res, 'Ethernet0/0'), {name: 'Ethernet0/0', admin: 'up', oper: 'up'});
});
check('IOS: garbage and errors give nothing', () => {
    assert.equal(seen('ios', 'sh int status', '% Invalid input detected at \'^\' marker.'), null);
    assert.equal(seen('ios', 'sh ip int br', ''), null);
    assert.equal(seen('ios', 'enable', 'Password:'), null);
});
check('iosName', () => {
    for (const [short, full] of [['Et0/1', 'Ethernet0/1'], ['e0/1', 'Ethernet0/1'], ['Gi0/0/1', 'GigabitEthernet0/0/1'], ['Fa0/1', 'FastEthernet0/1'],
        ['Vl10', 'Vlan10'], ['Lo0', 'Loopback0'], ['Se2/0', 'Serial2/0'], ['Po1', 'Port-channel1'], ['Ethernet0/1.10', 'Ethernet0/1.10'],
        ['Te1/0/1', 'TenGigabitEthernet1/0/1'], ['ens3', 'ens3']]) {
        assert.equal(iosName(short), full);
    }
});
check('IOS save: [OK] makes the refresh, an error does not', () => {
    assert.deepEqual(interpret('ios', 'wr', ['Building configuration...', '[OK]']), {save: true, ok: true});
    assert.deepEqual(interpret('ios', 'copy run start', ['Destination filename [startup-config]?', 'Building configuration...', '[OK]']),
        {save: true, ok: true});
    assert.deepEqual(interpret('ios', 'wr', ['% Please answer \'yes\' or \'no\'.']), {save: true, ok: false});
});
check('Debian ip a: flags, MAC, addresses incl. secondary, subinterface, down', () => {
    const res = seen('linux', 'ip a', DEBIAN_ADDR);
    assert.deepEqual(names(res), ['lo', 'ens3', 'ens4', 'ens3.10']);
    assert.deepEqual(ifc(res, 'lo'), {name: 'lo', admin: 'up', oper: 'up', ipv4: ['127.0.0.1/8']});
    assert.deepEqual(ifc(res, 'ens3'), {name: 'ens3', admin: 'up', oper: 'up', mac: '50:5b:3c:02:a2:00', ipv4: ['10.0.10.99/24', '10.0.11.99/24']});
    assert.deepEqual(ifc(res, 'ens4'), {name: 'ens4', admin: 'down', oper: 'down', mac: '50:5b:3c:02:a2:01', ipv4: []});
    assert.equal(ifc(res, 'ens3.10').parent, 'ens3');
    assert.equal(res.partial, false);
});
check('Debian ip -br a: states and addresses', () => {
    const res = seen('linux', 'ip -br a', DEBIAN_BRIEF);
    assert.deepEqual(names(res), ['lo', 'ens3', 'ens4', 'ens3.10']);
    assert.deepEqual(ifc(res, 'ens3'), {name: 'ens3', admin: 'up', oper: 'up', ipv4: ['10.0.10.99/24']});
    assert.deepEqual(ifc(res, 'ens4'), {name: 'ens4', oper: 'down', ipv4: []});
    assert.deepEqual(ifc(res, 'lo'), {name: 'lo', admin: 'up', ipv4: ['127.0.0.1/8']});
});
check('Debian ip -6 a gives no IPv4', () => {
    const res = seen('linux', 'ip -6 a', DEBIAN_ADDR);
    assert.ok(res.fragment.interfaces.every(i => !('ipv4' in i)));
});
check('Debian ip a show dev X is partial', () => {
    const res = seen('linux', 'ip a show dev ens3', DEBIAN_ADDR.split('\n').slice(7, 14).join('\n'));
    assert.equal(res.partial, true);
    assert.deepEqual(names(res), ['ens3']);
});
check('Debian ip route: routes in §3.3, host routes, no unreachable; full only', () => {
    const res = seen('linux', 'ip r', DEBIAN_ROUTE);
    assert.deepEqual(res.fragment.routes, [
        {dst: 'default', via: '10.0.10.1', dev: 'ens3', proto: 'dhcp', metric: 100},
        {dst: '10.0.10.0/24', dev: 'ens3', proto: 'kernel', metric: 100},
        {dst: '10.0.20.0/24', via: '10.0.10.254', dev: 'ens3', proto: 'boot'},
        {dst: '10.9.9.9/32', dev: 'ens4', proto: 'boot'},
    ]);
    const part = seen('linux', 'ip r show dev ens3', DEBIAN_ROUTE);
    assert.equal(part, null, 'a partial route list gives nothing: it has no interface fields');
});
check('VPCS show ip: address, gateway, MAC; unset address; show ip all', () => {
    assert.deepEqual(seen('vpcs', 'show ip', VPCS_SHOW).fragment.interfaces,
        [{name: 'eth0', ipv4: ['10.0.10.11/24'], gateway: '10.0.10.1', mac: '00:50:79:66:68:00'}]);
    assert.deepEqual(seen('vpcs', 'show ip', VPCS_SHOW.replace('10.0.10.11/24', '0.0.0.0/0').replace('10.0.10.1\n', '0.0.0.0\n')).fragment.interfaces,
        [{name: 'eth0', ipv4: [], mac: '00:50:79:66:68:00'}]);
    assert.deepEqual(seen('vpcs', 'show ip all', VPCS_ALL).fragment.interfaces,
        [{name: 'eth0', ipv4: ['10.0.10.11/24'], gateway: '10.0.10.1', mac: '00:50:79:66:68:00'}]);
    assert.equal(seen('vpcs', 'show ip', 'PC1> show ip\n*** Unknown command'), null);
});

/* ---------------- the store and the merge ---------------- */

const nid = n => `00000000-0000-4000-8000-0000000000${String(n).padStart(2, '0')}`;
const [SW, R, PC, DEB, VP] = [nid(1), nid(2), nid(3), nid(4), nid(5)];
const node = (id, name, type, extra = {}) => ({node_id: id, name, node_type: type, x: +id.slice(-2) * 100, y: 0, properties: {}, ...extra});
const port = (name, adapter, p = 0) => ({name, short_name: name, adapter_number: adapter, port_number: p});
const iouPorts = n => Array.from({length: n}, (_, k) => port(`Ethernet${Math.floor(k / 4)}/${k % 4}`, Math.floor(k / 4), k % 4));
const model = buildModel({name: 'check', topology: {
    nodes: [
        node(SW, 'SW1', 'iou', {symbol: ':/symbols/classic/multilayer_switch.svg', ports: iouPorts(12)}),
        node(R, 'R1', 'iou', {symbol: ':/symbols/classic/router.svg', ports: iouPorts(8)}),
        node(PC, 'PC1', 'vpcs', {symbol: ':/symbols/classic/vpcs_guest.svg', ports: [port('Ethernet0', 0)]}),
        node(DEB, 'DEB1', 'qemu', {symbol: ':/symbols/classic/qemu_guest.svg', properties: {mac_address: '50:5b:3c:02:a2:00'},
            ports: [port('Ethernet0', 0), port('Ethernet1', 1)]}),
        node(VP, 'VP2', 'vpcs', {symbol: ':/symbols/classic/vpcs_guest.svg'}),
    ],
    links: [],
}});

const sw = (extra = {}) => ({
    status: 'started', vlans: [{id: 10, name: 'USERS'}],
    interfaces: [
        {name: 'Ethernet0/0', kind: 'physical', port: {adapter: 0, port: 0}, admin: 'up', switchport: {mode: 'trunk', allowed: [10, 99], native: 1}},
        {name: 'Ethernet0/1', kind: 'physical', port: {adapter: 0, port: 1}, admin: 'up', switchport: {mode: 'access', vlan: 10}},
        {name: 'Ethernet0/2', kind: 'physical', port: {adapter: 0, port: 2}, admin: 'up', switchport: {mode: 'access', vlan: 10}},
        {name: 'Vlan99', kind: 'svi', vlan: 99, admin: 'up', ipv4: ['10.1.99.2/24']},
    ],
    ...extra,
});
const doc = nodes => ({schema: 'gns3lab.facts/1', rev: 7, generated_at: '2026-10-26T10:00:00Z', ttl: 90, nodes});
const decisive = doc({[SW]: sw()});

const feed = (docIn, nodeId, kind, command, text, at = 1000) => {
    const res = seen(kind, command, text);
    assert.ok(res, command);
    return addSeen(new Map(), nodeId, res, {at, fp: fingerprint(docIn, nodeId)});
};
const ifOf = (d, nodeId, name) => d.nodes[nodeId].interfaces.find(i => i.name === name);

check('merge: the fields a command gave replace the same fields, the rest and the other interfaces stay', () => {
    const merged = mergeSeen(model, decisive, feed(decisive, SW, 'ios', 'sh int status', IOL_STATUS));
    const e02 = ifOf(merged, SW, 'Ethernet0/2');
    assert.deepEqual([e02.admin, e02.oper, e02.switchport, e02.port, e02.kind], ['down', 'down', {mode: 'access', vlan: 10}, {adapter: 0, port: 2}, 'physical']);
    assert.deepEqual(e02.seen, ['admin', 'oper', 'switchport']);
    assert.deepEqual(ifOf(merged, SW, 'Vlan99'), decisive.nodes[SW].interfaces[3], 'an interface the output does not show is untouched');
    assert.equal(merged.rev, 7);
    assert.equal(merged.nodes[SW].seen.command, 'show interfaces status');
    assert.deepEqual(merged.nodes[SW].vlans, [{id: 10, name: 'USERS'}], 'no VLAN list in this output');
    assert.equal(decisive.nodes[SW].interfaces[2].admin, 'up', 'the snapshot itself is not changed');
});
check('merge: the interface is matched through the full IOS name (the snapshot may say e0/2)', () => {
    const short = doc({[SW]: sw({interfaces: sw().interfaces.map(i => ({...i, name: i.name.replace('Ethernet', 'e')}))})});
    const merged = mergeSeen(model, short, feed(short, SW, 'ios', 'sh int status', IOL_STATUS));
    assert.equal(ifOf(merged, SW, 'e0/2').admin, 'down');
    assert.equal(merged.nodes[SW].interfaces.filter(i => /0\/2$/.test(i.name)).length, 1, 'not added a second time');
});
check('merge: a trunk named by show interfaces status takes allowed/native from the snapshot, then from the defaults', () => {
    const merged = mergeSeen(model, decisive, feed(decisive, SW, 'ios', 'sh int status', IOL_STATUS));
    assert.deepEqual(ifOf(merged, SW, 'Ethernet0/0').switchport, {mode: 'trunk', allowed: [10, 99], native: 1});
    const bare = doc({[SW]: sw({interfaces: []})});
    const m2 = mergeSeen(model, bare, feed(bare, SW, 'ios', 'sh int status', IOL_STATUS));
    assert.deepEqual(ifOf(m2, SW, 'Ethernet0/0').switchport, {mode: 'trunk', allowed: 'all', native: 1});
});
check('merge: show interfaces trunk then show interfaces status keep the trunk details', () => {
    let store = feed(decisive, SW, 'ios', 'show interfaces trunk', IOL_TRUNK, 1);
    store = addSeen(store, SW, seen('ios', 'sh int status', IOL_STATUS), {at: 2, fp: fingerprint(decisive, SW)});
    const merged = mergeSeen(model, decisive, store);
    assert.deepEqual(ifOf(merged, SW, 'Ethernet0/0').switchport, {mode: 'trunk', allowed: [10, 99], native: 1});
    assert.equal(merged.nodes[SW].seen.at, 2);
});
check('merge: routed port loses its switchport', () => {
    const d = doc({[SW]: sw({interfaces: [{name: 'Ethernet1/1', kind: 'physical', port: {adapter: 1, port: 1}, switchport: {mode: 'access', vlan: 1}}]})});
    const merged = mergeSeen(model, d, feed(d, SW, 'ios', 'sh int status', IOL_STATUS));
    assert.ok(!('switchport' in ifOf(merged, SW, 'Ethernet1/1')));
});
check('merge: an interface the snapshot does not have is added with a kind and the port of GNS3 by name', () => {
    const merged = mergeSeen(model, decisive, feed(decisive, SW, 'ios', 'sh int status', IOL_STATUS));
    const e11 = ifOf(merged, SW, 'Ethernet1/1');
    assert.deepEqual([e11.kind, e11.port], ['physical', {adapter: 1, port: 1}]);
    const r = mergeSeen(model, null, feed(null, R, 'ios', 'sh ip int br', IOL_IPBRIEF));
    assert.deepEqual(r.nodes[R].interfaces.map(i => [i.name, i.kind]), [
        ['Ethernet0/0', 'physical'], ['Ethernet0/1', 'physical'], ['Ethernet0/2', 'physical'], ['Vlan99', 'svi'], ['Loopback0', 'loopback']]);
    assert.ok(!r.nodes[R].interfaces.some(i => /\./.test(i.name)), 'a sub-interface without its tag is not made up');
    assert.equal(r.nodes[R].status, 'started');
});
check('merge: show ip interface brief leaves the snapshot address when the same address is there; unassigned clears it', () => {
    const d = doc({[R]: {status: 'started', interfaces: [
        {name: 'Ethernet0/0', kind: 'physical', port: {adapter: 0, port: 0}, ipv4: ['10.0.12.1/30']},
        {name: 'Ethernet0/1', kind: 'physical', port: {adapter: 0, port: 1}, ipv4: ['10.0.13.1/24']},
    ]}});
    const merged = mergeSeen(model, d, feed(d, R, 'ios', 'sh ip int br', IOL_IPBRIEF));
    assert.deepEqual(ifOf(merged, R, 'Ethernet0/0').ipv4, ['10.0.12.1/30']);
    assert.deepEqual(ifOf(merged, R, 'Ethernet0/1').ipv4, []);
    assert.deepEqual(ifOf(merged, R, 'Ethernet0/1').seen, ['admin', 'oper', 'ipv4']);
});
check('merge: VLANs and routes are replaced by a full output only', () => {
    const full = mergeSeen(model, decisive, feed(decisive, SW, 'ios', 'sh vlan brief', IOL_VLANS));
    assert.deepEqual(full.nodes[SW].vlans.map(v => v.id), [1, 10, 99]);
    assert.deepEqual(ifOf(full, SW, 'Ethernet0/1').switchport, {mode: 'access', vlan: 10});
    const part = mergeSeen(model, decisive, feed(decisive, SW, 'ios', 'sh vlan brief | i USERS', '10   USERS                            active    Et0/1, Et0/2'));
    assert.deepEqual(part.nodes[SW].vlans, [{id: 10, name: 'USERS'}]);
    const d = doc({[DEB]: {status: 'started', routes: [{dst: 'default', via: '1.1.1.1', dev: 'ens3'}], interfaces: []}});
    const r = mergeSeen(model, d, feed(d, DEB, 'linux', 'ip r', DEBIAN_ROUTE));
    assert.equal(r.nodes[DEB].routes.length, 4);
});
check('merge: Linux interfaces get the port by the MAC of the adapter, never by order; a shared MAC gives none', () => {
    const merged = mergeSeen(model, null, feed(null, DEB, 'linux', 'ip a', DEBIAN_ADDR));
    const by = n => ifOf(merged, DEB, n);
    assert.deepEqual([by('ens3').kind, by('ens3').port], ['physical', {adapter: 0, port: 0}]);
    assert.deepEqual([by('ens4').kind, by('ens4').port], ['physical', {adapter: 1, port: 0}]);
    assert.deepEqual([by('lo').kind, by('lo').port], ['loopback', undefined]);
    assert.equal(by('ens3.10'), undefined, 'a VLAN sub-interface needs its tag');
    const brief = mergeSeen(model, null, feed(null, DEB, 'linux', 'ip -br a', DEBIAN_BRIEF));
    assert.equal(ifOf(brief, DEB, 'ens3').port, undefined, '-br has no MAC: no port');
    const dup = DEBIAN_ADDR.replace('50:5b:3c:02:a2:01', '50:5b:3c:02:a2:00');
    assert.equal(ifOf(mergeSeen(model, null, feed(null, DEB, 'linux', 'ip a', dup)), DEB, 'ens3').port, undefined);
});
check('merge: a decisive interface keeps its kind and port, the seen fields replace the others', () => {
    const d = doc({[DEB]: {status: 'started', interfaces: [
        {name: 'ens3', kind: 'physical', port: {adapter: 0, port: 0}, mac: '50:5b:3c:02:a2:00', admin: 'up', oper: 'up', ipv4: ['10.0.10.11/24'], gateway: '10.0.10.1'},
        {name: 'ens3.10', kind: 'subif', parent: 'ens3', dot1q: 10, ipv4: ['10.0.20.99/24']},
    ]}});
    const merged = mergeSeen(model, d, feed(d, DEB, 'linux', 'ip a', DEBIAN_ADDR));
    assert.deepEqual(ifOf(merged, DEB, 'ens3'), {
        name: 'ens3', kind: 'physical', port: {adapter: 0, port: 0}, mac: '50:5b:3c:02:a2:00', admin: 'up', oper: 'up',
        ipv4: ['10.0.10.99/24', '10.0.11.99/24'], gateway: '10.0.10.1', seen: ['admin', 'oper', 'ipv4', 'mac'],
    });
    assert.equal(ifOf(merged, DEB, 'ens3.10').dot1q, 10);
    assert.deepEqual(ifOf(merged, DEB, 'ens3.10').ipv4, ['10.0.20.12/24']);
});
check('merge: VPCS gets its only port', () => {
    const merged = mergeSeen(model, null, feed(null, PC, 'vpcs', 'show ip', VPCS_SHOW));
    assert.deepEqual(ifOf(merged, PC, 'eth0'), {
        name: 'eth0', kind: 'physical', port: {adapter: 0, port: 0}, ipv4: ['10.0.10.11/24'], gateway: '10.0.10.1',
        mac: '00:50:79:66:68:00', seen: ['ipv4', 'mac', 'gateway'],
    });
    assert.deepEqual(ifOf(mergeSeen(model, null, feed(null, VP, 'vpcs', 'show ip', VPCS_SHOW)), VP, 'eth0').port, {adapter: 0, port: 0});
});
check('merge: nothing seen gives the snapshot itself; a node that is not in the lab is ignored', () => {
    assert.equal(mergeSeen(model, decisive, new Map()), decisive);
    const store = feed(decisive, SW, 'ios', 'sh int status', IOL_STATUS);
    assert.equal(mergeSeen(model, decisive, new Map([[nid(99), store.get(SW)]])), decisive);
});
check('merge: the layers see it at once (a disabled port drops the link, a new address moves the subnet)', () => {
    const demo = JSON.parse(fs.readFileSync(new URL('../plugin/demo/ospf-multi-area.gns3', import.meta.url), 'utf8'));
    const facts = JSON.parse(fs.readFileSync(new URL('../plugin/demo/ospf-multi-area.facts.json', import.meta.url), 'utf8'));
    const m = buildModel(demo);
    const id = name => [...m.nodes.values()].find(n => n.name === name).id;
    const link = [...m.links].find(l => l.a.nodeId === id('SW1') && l.a.port === 2 && l.b.nodeId === id('PC2'));
    assert.equal(derive(m, facts).der.l1.links.get(link.id).down, false);
    const store = feed(facts, id('SW1'), 'ios', 'sh int status', IOL_STATUS);
    const after = derive(m, mergeSeen(m, facts, store)).der.l1.links.get(link.id);
    assert.equal(after.down, true);
    assert.match(after.why.join(' '), /SW1 e0\/2: shutdown/);
    const pc = feed(facts, id('PC1'), 'vpcs', 'show ip', VPCS_SHOW.replace('10.0.10.11/24', '10.1.10.77/24').replace('10.0.10.1', '10.1.10.1'));
    const merged = mergeSeen(m, facts, pc);
    assert.deepEqual(merged.nodes[id('PC1')].interfaces.find(i => i.name === 'e0').ipv4, ['10.1.10.77/24'], 'matched by the port, not made up twice');
    assert.equal(merged.nodes[id('PC1')].interfaces.length, 1);
});

check('fingerprint: collected_at and error do not count, key order does not count, an absent node is null', () => {
    const a = {nodes: {x: {status: 'started', interfaces: [{name: 'e0', admin: 'up'}], collected_at: 't1'}}};
    const b = {nodes: {x: {collected_at: 't2', interfaces: [{admin: 'up', name: 'e0'}], status: 'started', error: {code: 'timeout'}}}};
    const c = {nodes: {x: {status: 'started', interfaces: [{name: 'e0', admin: 'down'}]}}};
    assert.equal(fingerprint(a, 'x'), fingerprint(b, 'x'));
    assert.notEqual(fingerprint(a, 'x'), fingerprint(c, 'x'));
    assert.equal(fingerprint(a, 'y'), null);
    assert.equal(fingerprint(null, 'x'), null);
});
check('prune: a snapshot with other facts of a node drops what was seen on it, only on it', () => {
    const d1 = doc({[SW]: sw(), [R]: {status: 'started', interfaces: []}});
    let store = feed(d1, SW, 'ios', 'sh int status', IOL_STATUS);
    store = addSeen(store, R, seen('ios', 'sh ip int br', IOL_IPBRIEF), {at: 2, fp: fingerprint(d1, R)});
    assert.equal(pruneSeen(store, d1), store, 'the same snapshot keeps everything (and the same object)');
    const sameCollected = doc({[SW]: {...sw(), collected_at: 'later'}, [R]: {status: 'started', interfaces: []}});
    assert.equal(pruneSeen(store, sameCollected), store);
    const d2 = doc({[SW]: sw({vlans: [{id: 10, name: 'USERS'}, {id: 20, name: 'X'}]}), [R]: {status: 'started', interfaces: []}});
    const next = pruneSeen(store, d2);
    assert.deepEqual([...next.keys()], [R]);
    assert.deepEqual([...pruneSeen(store, null).keys()], [], 'the snapshot is gone: nothing seen is kept against it');
    assert.deepEqual([...pruneSeen(feed(null, SW, 'ios', 'sh int status', IOL_STATUS), null).keys()], [SW], 'seen before any snapshot, still none');
});
check('addSeen: a command after the snapshot changed starts the node over', () => {
    const d2 = doc({[SW]: sw({vlans: []})});
    let store = feed(decisive, SW, 'ios', 'sh int status', IOL_STATUS);
    store = addSeen(store, SW, seen('ios', 'sh ip int br', IOL_IPBRIEF), {at: 5, fp: fingerprint(d2, SW)});
    assert.deepEqual(store.get(SW).order.every(k => /vlan99|loopback0|ethernet0\/[012]/.test(k)), true);
    assert.equal(store.get(SW).ifs['ethernet0/3'], undefined, 'what the old snapshot saw is gone');
});

/* ---------------- the watch on a real terminal ---------------- */

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const write = (term, data) => new Promise(resolve => term.write(data, resolve));
const QUIET = 25;
const PAUSE = {pause: QUIET * 3};

/** Plays a session on a terminal and returns what the watch told. `PAUSE` is a silence longer than the quiet time. */
const play = async(hint, parts, {cols = 80, rows = 24, scrollback = 3000, afterwards = QUIET * 4} = {}) => {
    const term = new xterm.Terminal({cols, rows, scrollback, allowProposedApi: true});
    const events = [];
    const watch = new ConsoleWatch(term, {hint, quiet: QUIET, onCommand: e => events.push(e)});
    for (const part of parts) {
        await (part === PAUSE ? pause(QUIET * 3) : write(term, part));
    }
    await pause(afterwards);
    watch.dispose();
    term.dispose();
    return events;
};
const crlf = text => text.replace(/\r?\n/g, '\r\n');

check('watch IOS: the command and its output between two prompts', async() => {
    const events = await play('ios', ['SW1#', PAUSE, 'sh int status\r\n', crlf(IOL_STATUS), 'SW1#']);
    assert.equal(events.length, 1);
    assert.equal(events[0].kind, 'ios');
    assert.equal(events[0].command, 'sh int status');
    assert.equal(events[0].partial, false);
    assert.equal(events[0].lines[0].trim(), '');
    assert.match(events[0].lines.join('\n'), /Et0\/2 +disabled +10/);
    const res = interpret(events[0].kind, events[0].command, events[0].lines, events[0].partial);
    assert.equal(res.fragment.interfaces.length, 6);
});
check('watch IOS: typing with Backspace leaves the command as it stands on the line', () => {
    return play('ios', ['SW1#', PAUSE, 'sh ip intx\b \b', 'erfacz\b \b', 'e brief\r\n', 'Interface IP-Address\r\n', 'SW1#']).then(events => {
        assert.deepEqual(events.map(e => e.command), ['sh ip interface brief']);
    });
});
check('watch IOS: a long command that wraps is one command', async() => {
    const long = 'show ip interface brief | include Ethernet0/1|Ethernet0/2|Ethernet0/3|Loopback0';
    const events = await play('ios', ['SW1#', PAUSE, long + '\r\n', 'Ethernet0/1 10.0.0.1 YES manual up up\r\n', 'SW1#'], {cols: 30});
    assert.equal(events.length, 1);
    assert.equal(events[0].command, long);
    assert.deepEqual(events[0].lines, ['Ethernet0/1 10.0.0.1 YES manual up up']);
});
check('watch IOS: a long output line that wraps is one line of output', async() => {
    const row = 'Ethernet0/0                10.0.12.1       YES manual up                    up';
    const events = await play('ios', ['R1#', PAUSE, 'sh ip int br\r\n', row + '\r\n', 'R1#'], {cols: 40});
    assert.deepEqual(events[0].lines, [row]);
});
check('watch IOS: the pager makes the output partial, the line of the pager is gone from the lines', async() => {
    const events = await play('ios', ['SW1#', PAUSE, 'sh vlan brief\r\n', 'VLAN Name  Status\r\n1 default active\r\n --More-- ', PAUSE,
        '\b\b\b\b\b\b\b\b\b\b          \b\b\b\b\b\b\b\b\b\b10 USERS active\r\n', 'SW1#']);
    assert.equal(events.length, 1);
    assert.equal(events[0].partial, true);
    assert.deepEqual(events[0].lines, ['VLAN Name  Status', '1 default active', '10 USERS active']);
});
check('watch IOS: configuration mode prompt, the "do" form, and the prompt of another mode ends a command', async() => {
    const events = await play('ios', ['SW1#', PAUSE, 'conf t\r\nEnter configuration commands, one per line.  End with CNTL/Z.\r\nSW1(config)#', PAUSE,
        'do sh int status\r\n' + crlf(IOL_STATUS) + 'SW1(config)#', PAUSE, 'end\r\nSW1#']);
    assert.deepEqual(events.map(e => e.command), ['conf t', 'do sh int status', 'end']);
    assert.ok(interpret('ios', events[1].command, events[1].lines, events[1].partial));
});
check('watch IOS: two commands that came faster than the silence are two commands', async() => {
    const events = await play('ios', ['SW1#', PAUSE, 'sh vlan brief\r\n', 'VLAN Name  Status\r\n1 default active\r\nSW1#', 'wr\r\nBuilding configuration...\r\n[OK]\r\nSW1#']);
    assert.deepEqual(events.map(e => e.command), ['sh vlan brief', 'wr']);
    assert.deepEqual(events[0].lines, ['VLAN Name  Status', '1 default active']);
    assert.deepEqual(events[1].lines, ['Building configuration...', '[OK]']);
});
check('watch IOS: nothing before the silence, nothing for an empty command, nothing while the command runs', async() => {
    const term = new xterm.Terminal({cols: 80, rows: 24, allowProposedApi: true});
    const events = [];
    const watch = new ConsoleWatch(term, {hint: 'ios', quiet: QUIET, onCommand: e => events.push(e)});
    await write(term, 'SW1#');
    await pause(QUIET * 3);
    await write(term, '\r\nSW1#');
    await pause(QUIET * 3);
    assert.equal(events.length, 0, 'an empty command is no command');
    await write(term, 'ping 10.0.0.1\r\n!!!!!\r\n');
    await pause(QUIET * 3);
    assert.equal(events.length, 0, 'the command has not ended: the cursor is not on a prompt');
    await write(term, 'Success rate is 100 percent\r\nSW1#');
    assert.equal(events.length, 0, 'the silence has not come yet');
    await pause(QUIET * 3);
    assert.deepEqual(events.map(e => e.command), ['ping 10.0.0.1']);
    watch.dispose();
    term.dispose();
});
check('watch IOS: a password prompt is not a command and its answer is not read', async() => {
    const events = await play('ios', ['SW1>', PAUSE, 'enable\r\nPassword: \r\nSW1#']);
    assert.deepEqual(events.map(e => e.command), ['enable']);
    assert.equal(interpret(events[0].kind, events[0].command, events[0].lines), null);
});
check('watch: lines that have left the scrollback are skipped, a longer scrollback is fine', async() => {
    const body = Array.from({length: 120}, (_, k) => `Et0/${k} line`).join('\r\n') + '\r\n';
    const lost = await play('ios', ['SW1#', PAUSE, 'sh int status\r\n', body, 'SW1#'], {rows: 10, scrollback: 20});
    assert.equal(lost.length, 0);
    const kept = await play('ios', ['SW1#', PAUSE, 'sh int status\r\n', body, 'SW1#'], {rows: 10, scrollback: 500});
    assert.equal(kept.length, 1);
    assert.equal(kept[0].lines.length, 120);
});
check('watch: an output of more than 2000 lines is skipped', async() => {
    const body = Array.from({length: 2100}, (_, k) => `line ${k}`).join('\r\n') + '\r\n';
    const events = await play('ios', ['SW1#', PAUSE, 'show tech\r\n', body, 'SW1#'], {scrollback: 5000});
    assert.equal(events.length, 0);
});
check('watch: the next command is read after a skipped one', async() => {
    const body = Array.from({length: 120}, (_, k) => `line ${k}`).join('\r\n') + '\r\n';
    const events = await play('ios', ['SW1#', PAUSE, 'show tech\r\n', body, 'SW1#', PAUSE, 'sh ip int br\r\nEthernet0/0 10.0.0.1 YES manual up up\r\nSW1#'],
        {rows: 10, scrollback: 20});
    assert.deepEqual(events.map(e => e.command), ['sh ip int br']);
});
check('watch Linux: the prompt with a trailing space, ip a, the family follows the prompt, not the category', async() => {
    const prompt = 'user@deb:~$ ';
    const events = await play('linux', [prompt, PAUSE, 'ip -br a\r\n' + crlf(DEBIAN_BRIEF) + prompt, PAUSE, 'ip r\r\n' + crlf(DEBIAN_ROUTE) + prompt]);
    assert.deepEqual(events.map(e => [e.kind, e.command]), [['linux', 'ip -br a'], ['linux', 'ip r']]);
    assert.equal(interpret('linux', events[1].command, events[1].lines).fragment.routes.length, 4);
    const asSwitch = await play('ios', ['root@sw:~# ', PAUSE, 'ip a\r\n' + crlf(DEBIAN_ADDR) + 'root@sw:~# ']);
    assert.deepEqual(asSwitch.map(e => [e.kind, e.command]), [['linux', 'ip a']]);
});
check('watch Linux: a line of output ending in "$" is not a prompt', async() => {
    const events = await play('linux', ['user@deb:~$ ', PAUSE, 'echo hi\r\n', 'costs 5$', PAUSE]);
    assert.equal(events.length, 0);
});
check('watch VPCS: the prompt looks like the user mode of IOS but the node tells the family', async() => {
    const events = await play('vpcs', ['PC1> ', PAUSE, 'show ip\r\n' + crlf(VPCS_SHOW) + 'PC1> ']);
    assert.deepEqual(events.map(e => [e.kind, e.command]), [['vpcs', 'show ip']]);
    assert.equal(interpret('vpcs', 'show ip', events[0].lines).fragment.interfaces[0].ipv4[0], '10.0.10.11/24');
    const none = await play('vpcs', ['SW1#', PAUSE, 'sh ip int br\r\nx\r\nSW1#']);
    assert.equal(none.length, 0, 'an IOS prompt on a VPCS node is nothing');
});
check('watch: the full-screen mode (alternate buffer) is not read; the commands before and after are', async() => {
    const events = await play('linux', ['u@h:~$ ', PAUSE, 'less x\r\n', '\x1b[?1049h\x1b[H', 'u@h:~$ ', PAUSE, '\x1b[?1049l', 'u@h:~$ ']);
    assert.deepEqual(events.map(e => e.command).filter(c => c !== 'less x'), []);
});
check('watch: reading nothing after dispose', async() => {
    const term = new xterm.Terminal({cols: 80, rows: 24, allowProposedApi: true});
    const events = [];
    const watch = new ConsoleWatch(term, {hint: 'ios', quiet: QUIET, onCommand: e => events.push(e)});
    await write(term, 'SW1#');
    await pause(QUIET * 3);
    await write(term, 'sh ip int br\r\nx\r\nSW1#');
    watch.dispose();
    await pause(QUIET * 3);
    assert.equal(events.length, 0);
    term.dispose();
});
check('watch to scheme: a console session of the IOL switch changes the layers at once', async() => {
    const demo = JSON.parse(fs.readFileSync(new URL('../plugin/demo/ospf-multi-area.gns3', import.meta.url), 'utf8'));
    const facts = JSON.parse(fs.readFileSync(new URL('../plugin/demo/ospf-multi-area.facts.json', import.meta.url), 'utf8'));
    const m = buildModel(demo);
    const sw1 = [...m.nodes.values()].find(n => n.name === 'SW1');
    const events = await play(kindOf(sw1), ['SW1#', PAUSE, 'sh int status\r\n' + crlf(IOL_STATUS) + 'SW1#']);
    const res = interpret(events[0].kind, events[0].command, events[0].lines, events[0].partial);
    const store = addSeen(new Map(), sw1.id, res, {at: 1, fp: fingerprint(facts, sw1.id)});
    const e02 = mergeSeen(m, facts, store).nodes[sw1.id].interfaces.find(i => i.name === 'e0/2');
    assert.deepEqual([e02.admin, e02.oper], ['down', 'down']);
});

for (const [name, fn] of queue) {
    try {
        await fn();
        console.log('ok    ' + name);
    } catch (e) {
        failures.push(name);
        console.log('FAIL  ' + name + '\n      ' + String(e.stack || e).split('\n').slice(0, 9).join('\n      '));
    }
}

check('fingerprint: independent L1 does not erase console IP and VLAN observations', () => {
    const base = {nodes: {x: {interfaces: [{name: 'Ethernet0/0', ipv4: ['10.0.0.1/24']}]}}};
    const heartbeat = structuredClone(base);
    heartbeat.nodes.x.l1_ports = {ok: true, observed_at: new Date().toISOString(), ttl: 15,
        ports: [{port: {adapter: 0, port: 0}, state: 'up'}]};
    assert.equal(fingerprint(base, 'x'), fingerprint(heartbeat, 'x'));
    heartbeat.nodes.x.l1_ports.ports[0].state = 'down';
    assert.equal(fingerprint(base, 'x'), fingerprint(heartbeat, 'x'));
});

if (failures.length) {
    console.log(`\n${failures.length} failed`);
    process.exit(1);
}

console.log('\nall scenarios passed');
