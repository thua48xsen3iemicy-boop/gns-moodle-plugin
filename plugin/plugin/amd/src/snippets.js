// This file is part of Moodle - https://moodle.org/
//
// Moodle is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Moodle is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with Moodle.  If not, see <https://www.gnu.org/licenses/>.

/**
 * Snippets under a console: ready-made commands for the kind of the device.
 *
 * A static set for now (docs/SPEC.md, stage 2); sets from the activity settings come later. The commands are
 * grouped (inspect, routing, ...); the menu of mod_gns3lab/snipmenu shows them all, the bar under the terminal
 * keeps the pinned ones. A snippet either runs at once or, when it needs an argument ("ping "), is only typed
 * into the command line. Descriptions are language strings: "snip_<id>" for a snippet, "snipgroup_<id>" for a group.
 *
 * @module     mod_gns3lab/snippets
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/** The groups in the order of a router; a switch puts "switching" second. */
export const GROUPS = ['inspect', 'routing', 'switching', 'tools', 'config'];

const SWITCH_GROUPS = ['inspect', 'switching', 'routing', 'tools', 'config'];

/** Ctrl+U clears a half-typed line, Enter gets a fresh prompt, Ctrl+Z leaves the configuration mode. */
const IOS_PRE = '\x15\r\x1a' + 'enable\r';

/**
 * A snippet of a kind of device.
 *
 * @param {string} group group id
 * @param {string} id unique id, also the key of the description string
 * @param {string} cmd the command; a trailing space marks a command that needs an argument
 * @param {object} [opts]
 * @param {string} [opts.short] shorter caption for the bar
 * @param {boolean|string} [opts.quick] pinned to the bar until the student changes it: true for every device,
 *      'router' or 'switch' for that one only
 * @returns {object}
 */
const sn = (group, id, cmd, opts = {}) => ({group, id, cmd, short: opts.short || cmd.trim(), quick: opts.quick || false});

/**
 * Kinds of devices: what is sent before a command, the prompt end for the preview, the snippets.
 * A command that needs an argument ends with a space: it is typed, not run.
 */
const KINDS = {
    // Cisco IOS (IOU, Dynamips): every snippet starts in the privileged mode, whatever mode the student left.
    ios: {
        pre: IOS_PRE,
        prompt: '#',
        items: [
            sn('inspect', 'ios_ipbrief', 'show ip interface brief', {short: 'show ip int brief', quick: true}),
            sn('inspect', 'ios_run', 'show running-config', {short: 'show run', quick: true}),
            sn('inspect', 'ios_startup', 'show startup-config', {short: 'show start'}),
            sn('inspect', 'ios_version', 'show version'),
            sn('inspect', 'ios_cdp', 'show cdp neighbors'),
            sn('inspect', 'ios_arp', 'show arp'),
            sn('routing', 'ios_route', 'show ip route', {quick: 'router'}),
            sn('routing', 'ios_routeto', 'show ip route '),
            sn('routing', 'ios_routeospf', 'show ip route ospf'),
            sn('routing', 'ios_proto', 'show ip protocols'),
            sn('routing', 'ios_ospfnei', 'show ip ospf neighbor', {quick: 'router'}),
            sn('routing', 'ios_ospfint', 'show ip ospf interface brief'),
            sn('routing', 'ios_ospfdb', 'show ip ospf database'),
            sn('switching', 'ios_vlan', 'show vlan brief', {quick: 'switch'}),
            sn('switching', 'ios_ifstatus', 'show interfaces status'),
            sn('switching', 'ios_trunk', 'show interfaces trunk'),
            sn('switching', 'ios_mac', 'show mac address-table', {quick: 'switch'}),
            sn('switching', 'ios_stp', 'show spanning-tree'),
            sn('switching', 'ios_ether', 'show etherchannel summary'),
            sn('tools', 'ios_ping', 'ping '),
            sn('tools', 'ios_trace', 'traceroute '),
            sn('tools', 'ios_termlen', 'terminal length 0'),
            sn('config', 'ios_conft', 'configure terminal', {short: 'conf t'}),
            sn('config', 'ios_wr', 'write memory', {quick: true}),
        ],
    },
    // Linux hosts and servers (QEMU, Docker).
    linux: {
        pre: '\x15',
        prompt: '$',
        items: [
            sn('inspect', 'lx_addr', 'ip addr', {quick: true}),
            sn('inspect', 'lx_addrbr', 'ip -br addr'),
            sn('inspect', 'lx_route', 'ip route', {quick: true}),
            sn('inspect', 'lx_neigh', 'ip neighbor'),
            sn('inspect', 'lx_hostname', 'hostname', {quick: true}),
            sn('inspect', 'lx_listen', 'ss -tuln'),
            sn('inspect', 'lx_dns', 'cat /etc/resolv.conf'),
            sn('tools', 'lx_pinggw', 'ping -c 3 $(ip route | awk \'/default/ {print $3; exit}\')',
                {short: 'ping gateway', quick: true}),
            sn('tools', 'lx_ping', 'ping -c 3 '),
            sn('tools', 'lx_trace', 'traceroute '),
            sn('tools', 'lx_curl', 'curl -I '),
            sn('tools', 'lx_clear', 'clear'),
        ],
    },
    // VPCS.
    vpcs: {
        pre: '\x15',
        prompt: '>',
        items: [
            sn('inspect', 'vp_showip', 'show ip', {quick: true}),
            sn('inspect', 'vp_show', 'show', {quick: true}),
            sn('inspect', 'vp_arp', 'show arp'),
            sn('tools', 'vp_ping', 'ping '),
            sn('tools', 'vp_trace', 'trace '),
            sn('config', 'vp_ip', 'ip '),
            sn('config', 'vp_dhcp', 'ip dhcp'),
            sn('config', 'vp_save', 'save'),
            sn('config', 'vp_help', 'help', {quick: true}),
        ],
    },
};

// What each snippet sends: whole (run) or only the text of the command (insert, the student presses Enter).
Object.values(KINDS).forEach(kind => kind.items.forEach(item => {
    item.insertOnly = item.cmd.endsWith(' ');
    item.typed = kind.pre + item.cmd;
    item.seq = item.insertOnly ? item.typed : item.typed + '\r';
}));

/** Strings of the snippets: a description of every snippet and a name of every group. */
export const STRING_KEYS = [
    ...GROUPS.map(g => 'snipgroup_' + g),
    ...Object.values(KINDS).flatMap(kind => kind.items.map(item => 'snip_' + item.id)),
];

/**
 * Kind of the device by its node.
 *
 * @param {object} node node of the model
 * @returns {string} ios, linux or vpcs
 */
export const kindOf = node => {
    if (node.type === 'iou' || node.type === 'dynamips') {
        return 'ios';
    }
    if (node.type === 'vpcs') {
        return 'vpcs';
    }
    // A virtual router or switch (QEMU, Docker) is taken for a Cisco-like device, a host for Linux.
    return ['router', 'switch', 'firewall'].includes(node.cat) ? 'ios' : 'linux';
};

/**
 * Snippets of a node in groups.
 *
 * The pinned ones are kept per pinKey: a router and a switch have the same commands but need different ones at hand.
 *
 * @param {object} node node of the model
 * @returns {{kind: string, pinKey: string, defaults: string[], prompt: string, items: object[],
 *      groups: {id: string, items: object[]}[]}}
 */
export const snippetSet = node => {
    const kind = kindOf(node), def = KINDS[kind], isSwitch = kind === 'ios' && node.cat === 'switch';
    const order = isSwitch ? SWITCH_GROUPS : GROUPS, role = isSwitch ? 'switch' : 'router';
    return {
        kind,
        pinKey: isSwitch ? 'ios-switch' : kind,
        defaults: def.items.filter(item => item.quick === true || item.quick === role).map(item => item.id),
        prompt: def.prompt,
        items: def.items,
        groups: order.map(id => ({id, items: def.items.filter(item => item.group === id)})).filter(g => g.items.length),
    };
};
