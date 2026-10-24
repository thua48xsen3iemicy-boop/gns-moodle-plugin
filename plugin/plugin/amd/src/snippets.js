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
 * A static set for now (docs/SPEC.md, stage 2); sets from the activity settings come later.
 * A press sends the command at once, the full text is in the tooltip of the button.
 *
 * @module     mod_gns3lab/snippets
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/** Ctrl+U clears a half-typed line, Enter gets a fresh prompt, Ctrl+Z leaves the configuration mode. */
const IOS_PRE = '\x15\r\x1a' + 'enable\r';

/** Cisco IOS (IOU, Dynamips): every snippet starts in the privileged mode, whatever mode the student left. */
const IOS = [
    {label: 'show ip int brief', cmd: 'show ip interface brief'},
    {label: 'show ip route', cmd: 'show ip route'},
    {label: 'show run', cmd: 'show running-config'},
    {label: 'show ip ospf neighbor', cmd: 'show ip ospf neighbor'},
    {label: 'show vlan brief', cmd: 'show vlan brief'},
    {label: 'write memory', cmd: 'write memory'},
].map(s => ({label: s.label, title: s.cmd, seq: IOS_PRE + s.cmd + '\r'}));

/** Linux hosts and servers (QEMU, Docker). */
const LINUX = [
    {label: 'ip addr', cmd: 'ip addr'},
    {label: 'ip route', cmd: 'ip route'},
    {label: 'hostname', cmd: 'hostname'},
    {label: 'ping gateway', cmd: 'ping -c 3 $(ip route | awk \'/default/ {print $3; exit}\')'},
].map(s => ({label: s.label, title: s.cmd, seq: '\x15' + s.cmd + '\r'}));

/** VPCS. */
const VPCS = [
    {label: 'show ip', cmd: 'show ip'},
    {label: 'show', cmd: 'show'},
    {label: 'help', cmd: 'help'},
].map(s => ({label: s.label, title: s.cmd, seq: '\x15' + s.cmd + '\r'}));

/**
 * Snippets of a node.
 *
 * @param {object} node node of the model
 * @returns {Array<{label: string, title: string, seq: string}>}
 */
export const snippetsFor = node => {
    if (node.type === 'iou' || node.type === 'dynamips') {
        return IOS;
    }
    if (node.type === 'vpcs') {
        return VPCS;
    }
    // A virtual router or switch (QEMU, Docker) is taken for a Cisco-like device, a host for Linux.
    return ['router', 'switch', 'firewall'].includes(node.cat) ? IOS : LINUX;
};
