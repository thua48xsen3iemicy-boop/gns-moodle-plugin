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
 * Model of a lab: the .gns3 topology as plain objects, and small helpers shared by the student interface modules.
 *
 * @module     mod_gns3lab/model
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/**
 * Escapes text for HTML. Everything that comes from a .gns3 file or from the lab manager goes through it.
 *
 * @param {*} s value
 * @returns {string}
 */
export const esc = s => String(s === null || s === undefined ? '' : s)
    .replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

/**
 * Natural order: Gi0/2 before Gi0/10.
 *
 * @param {*} a first
 * @param {*} b second
 * @returns {number}
 */
export const natSort = (a, b) => String(a).localeCompare(String(b), undefined, {numeric: true});

/**
 * Limits a number to a range.
 *
 * @param {number} v value
 * @param {number} a lowest
 * @param {number} b highest
 * @returns {number}
 */
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

/**
 * Category of a node, it chooses the icon: router, switch, pc, server, cloud, firewall, hypervisor.
 *
 * @param {object} n node of the model
 * @returns {string}
 */
export const catOf = n => {
    const s = (n.symbol || '').toLowerCase(), t = n.type;
    const img = String((n.props && (n.props.hda_disk_image || n.props.path || n.props.image)) || '');
    if (/proxmox|\bpve/i.test(s + ' ' + img)) {
        return 'hypervisor';
    }
    if (t === 'cloud' || t === 'nat' || /cloud|nat\b|nat\./.test(s)) {
        return 'cloud';
    }
    if (/firewall|asa/.test(s)) {
        return 'firewall';
    }
    if (['ethernet_switch', 'ethernet_hub', 'frame_relay_switch', 'atm_switch'].includes(t) || /switch|hub/.test(s)) {
        return 'switch';
    }
    if (/router/.test(s)) {
        return 'router';
    }
    if (t === 'vpcs' || /vpcs|computer|\bpc|laptop|workstation/.test(s)) {
        return 'pc';
    }
    return 'server';
};

/**
 * Name of a port for a link label.
 *
 * In the live topology of a session the properties are empty, but GNS3 returns ports with the device port names.
 *
 * @param {object} node node of the model
 * @param {number} adapter adapter number
 * @param {number} port port number
 * @returns {string}
 */
export const portName = (node, adapter, port) => {
    const gp = node.ports && node.ports.find(x => x.adapter_number === adapter && x.port_number === port);
    if (gp && (gp.short_name || gp.name)) {
        return gp.short_name || gp.name;
    }
    const pm = node.props && node.props.ports_mapping;
    if (Array.isArray(pm)) {
        const p = pm.find(x => x.port_number === port);
        if (p && p.name) {
            return p.name;
        }
    }
    if (node.type === 'ethernet_switch' || node.type === 'ethernet_hub') {
        return 'Ethernet' + port;
    }
    return adapter + '/' + port;
};

/**
 * Link filters of GNS3 as a list for the details card: {k: title, v: value, s: short form for a label}.
 *
 * @param {object} f filters object of a link
 * @returns {Array}
 */
export const fmtFilters = f => {
    const out = [];
    if (!f) {
        return out;
    }
    if (f.delay && f.delay.length) {
        const jitter = f.delay[1];
        out.push({
            k: 'Задержка', v: f.delay[0] + ' мс' + (jitter ? ' ± ' + jitter : ''),
            s: f.delay[0] + (jitter ? '±' + jitter : '') + ' мс',
        });
    }
    if (f.packet_loss && f.packet_loss.length) {
        out.push({k: 'Потери', v: f.packet_loss[0] + ' %', s: 'loss ' + f.packet_loss[0] + '%'});
    }
    if (f.corrupt && f.corrupt.length) {
        out.push({k: 'Порча пакетов', v: f.corrupt[0] + ' %', s: 'corrupt ' + f.corrupt[0] + '%'});
    }
    if (f.frequency_drop && f.frequency_drop.length) {
        out.push({k: 'Дроп', v: 'каждый ' + f.frequency_drop[0] + '-й пакет', s: 'drop 1/' + f.frequency_drop[0]});
    }
    if (f.bpf && f.bpf.length) {
        out.push({k: 'BPF-фильтр', v: String(f.bpf[0]), s: 'bpf'});
    }
    return out;
};

/**
 * Builds the model of a lab from a .gns3 document.
 *
 * Coordinates: x/y of a GNS3 node is its top left corner, the centre is x + w/2, y + h/2.
 * Links to unknown nodes are dropped.
 *
 * @param {object} doc .gns3 document: name, version, topology.nodes/links/drawings
 * @returns {{name: string, version: string, nodes: Map, links: Array, drawings: Array}}
 */
export const buildModel = doc => {
    const t = doc.topology || {};
    const nodes = new Map();
    for (const n of (t.nodes || [])) {
        if (!n || !n.node_id) {
            continue;
        }
        const w = +n.width || 60, h = +n.height || 45, x = +n.x || 0, y = +n.y || 0;
        const node = {
            id: n.node_id, name: n.name || '—', type: n.node_type || '', symbol: n.symbol || '',
            x, y, w, h, cx: x + w / 2, cy: y + h / 2, props: n.properties || {}, ports: Array.isArray(n.ports) ? n.ports : null,
            consoleType: n.console_type || (n.properties && n.properties.console_type) || null,
            status: n.status || null,
        };
        node.cat = catOf(node);
        nodes.set(n.node_id, node);
    }
    const links = [];
    for (const l of (t.links || [])) {
        const ends = (l.nodes || []).slice(0, 2).map(e => ({
            nodeId: e.node_id, adapter: e.adapter_number, port: e.port_number,
            text: e.label && e.label.text ? String(e.label.text) : null,
        }));
        if (ends.length < 2 || !nodes.has(ends[0].nodeId) || !nodes.has(ends[1].nodeId)) {
            continue;
        }
        ends.forEach(e => {
            if (!e.text) {
                e.text = portName(nodes.get(e.nodeId), e.adapter, e.port);
            }
        });
        links.push({
            id: l.link_id || ('link-' + links.length), a: ends[0], b: ends[1], filters: l.filters || {},
            suspend: !!l.suspend, type: l.link_type || 'ethernet',
        });
    }
    return {
        name: doc.name || '—', version: doc.version || '', nodes, links,
        drawings: Array.isArray(t.drawings) ? t.drawings : [],
    };
};
