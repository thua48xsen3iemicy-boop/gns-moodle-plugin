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
 * Command sets of the console keyboard, by node type.
 *
 * A command is sent at once, unless it has placeholders: {ip}, {mask}, {port}… become "<IP>", "<маска>"…
 * in the input line and the student fills them in. For Cisco IOS every command has the mode it needs:
 * exec (show, ping), config (global configuration), if (interface), router (routing process).
 * The keyboard reads the prompt of the device and enters the mode itself where it can.
 * Group names and placeholders are language strings: kbg_<group>, ph_<placeholder>.
 *
 * @module     mod_gns3lab/commands
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/** Placeholders a command may contain. */
export const PLACEHOLDERS = ['ip', 'mask', 'prefix', 'gateway', 'net', 'wildcard', 'area', 'n', 'port', 'name', 'text', 'vlans'];

const CISCO = {
    id: 'cisco',
    types: ['iou', 'dynamips'],
    prompt: true,
    groups: [
        {key: 'mode', items: [
            {cmd: 'enable', mode: 'any'},
            {cmd: 'configure terminal', mode: 'priv'},
            {cmd: 'interface {port}', mode: 'config'},
            {cmd: 'exit', mode: 'any'},
            {cmd: 'end', mode: 'any'},
            {cmd: 'disable', mode: 'any'},
        ]},
        {key: 'show', items: [
            {cmd: 'show ip interface brief', mode: 'exec'},
            {cmd: 'show running-config', mode: 'exec', priv: true},
            {cmd: 'show interfaces {port}', mode: 'exec'},
            {cmd: 'show ip route', mode: 'exec'},
            {cmd: 'show vlan brief', mode: 'exec'},
            {cmd: 'show interfaces trunk', mode: 'exec'},
            {cmd: 'show cdp neighbors', mode: 'exec'},
            {cmd: 'show version', mode: 'exec'},
        ]},
        {key: 'if', items: [
            {cmd: 'ip address {ip} {mask}', mode: 'if'},
            {cmd: 'no shutdown', mode: 'if'},
            {cmd: 'shutdown', mode: 'if'},
            {cmd: 'description {text}', mode: 'if'},
            {cmd: 'no switchport', mode: 'if'},
            {cmd: 'switchport mode access', mode: 'if'},
            {cmd: 'switchport access vlan {n}', mode: 'if'},
            {cmd: 'switchport trunk encapsulation dot1q', mode: 'if'},
            {cmd: 'switchport mode trunk', mode: 'if'},
            {cmd: 'switchport trunk allowed vlan {vlans}', mode: 'if'},
        ]},
        {key: 'routing', items: [
            {cmd: 'hostname {name}', mode: 'config'},
            {cmd: 'ip routing', mode: 'config'},
            {cmd: 'ip route {net} {mask} {gateway}', mode: 'config'},
            {cmd: 'router ospf {n}', mode: 'config'},
            {cmd: 'network {net} {wildcard} area {area}', mode: 'router'},
            {cmd: 'passive-interface {port}', mode: 'router'},
            {cmd: 'vlan {n}', mode: 'config'},
            {cmd: 'name {name}', mode: 'any'},
        ]},
        {key: 'diag', items: [
            {cmd: 'ping {ip}', mode: 'exec'},
            {cmd: 'traceroute {ip}', mode: 'exec'},
            {cmd: 'show ip ospf neighbor', mode: 'exec'},
            {cmd: 'show ip protocols', mode: 'exec'},
            {cmd: 'show spanning-tree', mode: 'exec'},
            {cmd: 'show mac address-table', mode: 'exec'},
            {cmd: 'show arp', mode: 'exec'},
        ]},
        {key: 'save', items: [
            {cmd: 'write memory', mode: 'exec', priv: true},
            {cmd: 'show startup-config', mode: 'exec', priv: true},
            {cmd: 'reload', mode: 'exec', priv: true},
        ]},
    ],
};

const LINUX = {
    id: 'linux',
    types: ['qemu', 'docker'],
    prompt: false,
    groups: [
        {key: 'net', items: [
            {cmd: 'ip -br address'},
            {cmd: 'ip -br link'},
            {cmd: 'sudo ip address add {ip}/{prefix} dev {port}'},
            {cmd: 'sudo ip link set {port} up'},
            {cmd: 'ip route'},
            {cmd: 'sudo ip route add default via {gateway}'},
            {cmd: 'cat /etc/resolv.conf'},
        ]},
        {key: 'diag', items: [
            {cmd: 'ping -c 4 {ip}'},
            {cmd: 'traceroute {ip}'},
            {cmd: 'ip neigh'},
            {cmd: 'ss -tulpn'},
            {cmd: 'sudo tcpdump -ni {port}'},
        ]},
        {key: 'system', items: [
            {cmd: 'hostname'},
            {cmd: 'cat /etc/os-release'},
            {cmd: 'df -h'},
            {cmd: 'free -m'},
            {cmd: 'sudo -i'},
            {cmd: 'clear'},
        ]},
    ],
};

const VPCS = {
    id: 'vpcs',
    types: ['vpcs'],
    prompt: false,
    groups: [
        {key: 'net', items: [
            {cmd: 'ip {ip}/{prefix} {gateway}'},
            {cmd: 'ip dhcp'},
            {cmd: 'show ip'},
            {cmd: 'save'},
        ]},
        {key: 'diag', items: [
            {cmd: 'ping {ip}'},
            {cmd: 'trace {ip}'},
            {cmd: 'arp'},
            {cmd: 'clear ip'},
        ]},
    ],
};

/** Every set, the first one whose types include the node type is used. */
export const SETS = [CISCO, LINUX, VPCS];

/**
 * The command set for a node type, or null if there is none.
 *
 * @param {string} nodeType GNS3 node type
 * @returns {object|null}
 */
export const setFor = nodeType => SETS.find(set => set.types.includes(nodeType)) || null;

/**
 * Reads the Cisco IOS prompt at the end of a terminal line: "R1>", "R1#", "R1(config-if)#".
 *
 * @param {string} line text of the cursor line
 * @returns {object|null} {text, mode: user|priv|config|if|router|sub} or null when it is not a prompt
 */
export const parsePrompt = line => {
    const m = /([A-Za-z0-9][A-Za-z0-9_.-]{0,62})(?:\(([a-z0-9-]+)\))?([>#])\s*$/.exec(line || '');
    if (!m || (m[3] === '>' && m[2])) {
        return null;
    }
    const sub = m[2] || '';
    let mode = m[3] === '>' ? 'user' : 'priv';
    if (sub === 'config') {
        mode = 'config';
    } else if (sub === 'config-if' || sub.startsWith('config-subif')) {
        mode = 'if';
    } else if (sub === 'config-router') {
        mode = 'router';
    } else if (sub.startsWith('config')) {
        mode = 'sub';
    }
    return {text: m[0].trim(), mode};
};

/**
 * What to send for a Cisco command in the current mode, so the student does not have to switch modes first.
 *
 * @param {object} item command {cmd, mode, priv}
 * @param {string} text command text with the placeholders filled in
 * @param {object|null} prompt parsed prompt, null if unknown
 * @returns {object} {seq} to send, or {need: 'if'|'router'} when the student must enter that mode first
 */
export const ciscoSequence = (item, text, prompt) => {
    // Ctrl+U clears whatever was typed on the device line before.
    const clear = '\x15';
    if (!prompt || item.mode === 'any') {
        return {seq: clear + text + '\r'};
    }
    const mode = prompt.mode;
    const inConfig = ['config', 'if', 'router', 'sub'].includes(mode);
    switch (item.mode) {
        case 'exec':
            if (inConfig) {
                return {seq: clear + 'do ' + text + '\r'};
            }
            return {seq: clear + (mode === 'user' && item.priv ? 'enable\r' : '') + text + '\r'};
        case 'priv':
            if (inConfig) {
                return {seq: clear + 'end\r' + text + '\r'};
            }
            return {seq: clear + (mode === 'user' ? 'enable\r' : '') + text + '\r'};
        case 'config':
            if (inConfig) {
                return {seq: clear + text + '\r'};
            }
            return {seq: clear + (mode === 'user' ? 'enable\r' : '') + 'configure terminal\r' + text + '\r'};
        default:
            // Interface and routing commands make sense only inside their mode.
            if (mode !== item.mode) {
                return {need: item.mode};
            }
            return {seq: clear + text + '\r'};
    }
};
