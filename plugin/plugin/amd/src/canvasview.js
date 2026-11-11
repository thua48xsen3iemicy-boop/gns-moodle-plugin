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
 * Canvas rendering, screen sized labels and connection selection for the student topology.
 * Session, reveal, facts and console ownership remain in the existing lab controller.
 *
 * @module mod_gns3lab/canvasview
 * @copyright 2026 mod_gns3lab contributors
 * @license https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
/* eslint-disable curly, max-len, jsdoc/require-jsdoc, no-eq-null */
import {svgEl as el} from 'mod_gns3lab/dom';
import {clamp, isHost} from 'mod_gns3lab/model';
import {factsStatus, layerHelp, layerIssues} from 'mod_gns3lab/canvasfacts';
import {PILL_H, canvasL2, linkDirections} from 'mod_gns3lab/canvasmodel';
import {portStp, stpBlocks, stpRoot, stpShort} from 'mod_gns3lab/facts';

export const STRING_KEYS = [
    'ui_canvas_title', 'ui_canvas_l4', 'ui_canvas_duplex',
    'ui_canvas_blocked', 'ui_canvas_stp_blocked', 'ui_canvas_stp_root', 'ui_canvas_stp_self', 'ui_canvas_unknown', 'ui_canvas_disconnected', 'ui_canvas_objects',
    'ui_canvas_card_layers', 'ui_canvas_interface', 'ui_canvas_admin', 'ui_canvas_oper',
    'ui_assignments', 'ui_assignments_empty', 'ui_canvas_practice', 'ui_overview', 'ui_canvas_l1_help', 'ui_canvas_l2_help', 'ui_canvas_l3_help',
    'ui_canvas_collection_error', 'ui_canvas_empty_l2', 'ui_canvas_empty_l3', 'ui_canvas_subnet',
    'ui_canvas_domain', 'ui_canvas_interfaces', 'ui_canvas_node', 'ui_canvas_port',
    'ui_canvas_subnet_foot', 'ui_canvas_logical_link', 'ui_canvas_passing', 'ui_canvas_tag',
    'ui_canvas_untagged', 'ui_canvas_physical', 'ui_canvas_link_foot', 'ui_canvas_connection',
    'ui_canvas_state', 'ui_canvas_config', 'ui_canvas_ipv4', 'ui_canvas_filters',
    'ui_canvas_name', 'ui_canvas_value', 'ui_canvas_exploration', 'ui_canvas_node_foot',
    'ui_canvas_vlan_object', 'ui_canvas_vlan_mixed', 'ui_canvas_vlan_foot', 'ui_canvas_mac_missing',
    'ui_canvas_mac_back', 'ui_canvas_mac_open', 'ui_canvas_gateways', 'ui_canvas_address',
    'ui_canvas_guests', 'ui_canvas_type', 'ui_canvas_all_vlans', 'ui_canvas_disabled',
    'ui_canvas_no_l2', 'ui_canvas_no_layer_data', 'ui_canvas_directions', 'ui_canvas_issues',
    'ui_canvas_related', 'ui_canvas_members', 'ui_canvas_no_objects', 'ui_canvas_no_issues',
    'ui_canvas_no_check', 'ui_canvas_inspect_note',
];

/** Radius of a port dot on L1, in screen pixels. */
export const PORT_DOT = 4.5;

/**
 * From this zoom a cable is drawn as its two directions with TX and RX. It is about two wheel notches (x1.2 each)
 * past the start of the "Addresses" level (zoom 1.6), so that neighbouring cables have room for their labels.
 */
const DUPLEX_K = 2.3;

/** Distance between the control points of fanned L2 lines with the same ends; the curves part by half of it. */
const FAN = 20;

/** Colours of the subnets on L3, in the order of the subnets. */
const NET_COLORS = ['#73c7ed', '#60dfaf', '#f0bd73', '#da9fe8', '#ff9f8f', '#b7e07a', '#8bb5fa', '#f2a5c8', '#7fe0d6', '#e6d27a'];

/** Top of the HUD controls under the layer note, in pixels of the scheme area (styles.css .hud-tools). */
const TOOLS_TOP = 106;

export class CanvasView {
    constructor(stage) {
        this.stage = stage;
        this.rails = [];
        this.subnets = [];
        this.nodeLayer = null;
        this.frame = null;
        document.fonts.ready.then(() => this.scheduleLabels());
        document.fonts.addEventListener('loadingdone', () => this.scheduleLabels());
        ['pointerdown', 'pointerup', 'wheel', 'keydown'].forEach(type => stage.r('canvasUi').addEventListener(type, e => e.stopPropagation()));
    }

    reset() {
        this.rails = [];
        this.l2 = this.stage.layer === 'l2' && this.stage.der ? canvasL2(this.stage) : null;
        this.nodeLayer = null;
        this.subnets = (this.stage.der?.l3.subnets || []).map(x => ({...x, cx: x.x, cy: x.y}));
        this.leads = this.stage.L.leads;
    }

    duplex(link, geometry, group) {
        const stage = this.stage;
        group.classList.add('dimmable');
        group.querySelector('.wire').classList.add('physical-wire');
        const g = el('g', {class: 'duplex', 'aria-hidden': 'true'}, group);
        const directions = linkDirections(link).map(({from, to, sign}) => {
            const line = el('line', {class: 'wire', 'data-tx-node': from.nodeId, 'data-rx-node': to.nodeId}, g);
            const arrow = el('path', {class: 'duplex-arrow'}, g);
            const labels = [[from, 'TX'], [to, 'RX']].filter(([e]) => stage.endShown(e)).map(([end, role]) => {
                const endpointGroup = el('g', {class: 'duplex-end', 'data-role': role, 'data-node-end': end.nodeId}, g);
                const lead = el('line', {class: 'duplex-label-lead'}, endpointGroup);
                const text = el('text', {class: 'duplex-label', 'text-anchor': 'middle', 'dominant-baseline': 'central'}, endpointGroup);
                text.textContent = role;
                return {end, g: endpointGroup, lead, text};
            });
            return {line, arrow, labels, sign};
        });
        el('title', {}, group).textContent = stage.s('ui_canvas_duplex');
        this.rails.push({link, geometry, g, directions, group});
    }

    renderL2() {
        const s = this.stage, {virtuals, segments} = this.l2;
        const zones = el('g', {}, s.L.l2), lines = el('g', {}, s.L.l2), pills = el('g', {}, s.L.l2);
        this.nodeLayer = el('g', {}, s.L.l2);
        const groups = new Map();
        const color = vlan => ({1: '#8ba6b4', 10: '#60dfaf', 20: '#f0bd73', 30: '#8bb5fa', 99: '#da9fe8'}[vlan] || `hsl(${(Number(vlan) * 47) % 360} 65% 70%)`);
        virtuals.forEach(v => {
            if (!groups.has(v.orig.id)) groups.set(v.orig.id, []);
            groups.get(v.orig.id).push(v);
        });
        groups.forEach(vs => {
            // The chassis outline holds the VLANs of one switch; its title is just the switch name.
            const n = vs[0].orig, pad = 12;
            const x0 = Math.min(...vs.map(v => v.cx - v.w / 2)) - pad, x1 = Math.max(...vs.map(v => v.cx + v.w / 2)) + pad;
            const y0 = Math.min(...vs.map(v => v.cy)) - PILL_H / 2 - pad, y1 = Math.max(...vs.map(v => v.cy)) + PILL_H / 2 + pad;
            const g = el('g', {class: 'l2-chassis dimmable', 'data-node': n.id}, zones);
            el('rect', {x: x0, y: y0, width: x1 - x0, height: y1 - y0, rx: 12}, g);
            const label = el('g', {class: 'auto-label', 'data-priority': 1}, g);
            el('text', {x: x0 + 8, y: y0 - 6}, label).textContent = n.name;
            s.grow(x0, y0 - 20, x1, y1);
        });
        // Segments between the same two ends (parallel cables carrying one VLAN) are fanned out, or they cover each other.
        // The side comes from the pair of ends, not from the cable: one stored B→A would otherwise land on one stored A→B.
        const endKey = (vertex, nodeId) => vertex || 'N|' + nodeId;
        const fans = new Map();
        segments.forEach(segment => {
            const ka = endKey(segment.va, segment.link.a.nodeId), kb = endKey(segment.vb, segment.link.b.nodeId);
            const key = [ka, kb].sort().join('#');
            if (!fans.has(key)) fans.set(key, []);
            fans.get(key).push(segment);
            segment.side = ka < kb ? 1 : -1;
        });
        fans.forEach(list => list.forEach((segment, i) => {
            segment.fan = list.length > 1 ? (i - (list.length - 1) / 2) * FAN * segment.side : null;
        }));
        segments.forEach(segment => {
            const {link, va, vb, vlan} = segment;
            const a = virtuals.get(va) || s.model.nodes.get(link.a.nodeId), b = virtuals.get(vb) || s.model.nodes.get(link.b.nodeId);
            const dx = b.cx - a.cx, dy = b.cy - a.cy, length = Math.hypot(dx, dy) || 1;
            const bend = segment.fan ?? (vlan == null ? 0 : segment.tag === 'u' ? -7 : 7);
            const mx = (a.cx + b.cx) / 2 - dy / length * bend, my = (a.cy + b.cy) / 2 + dx / length * bend;
            const d = `M${a.cx} ${a.cy} Q${mx} ${my} ${b.cx} ${b.cy}`;
            // An 802.1Q trunk carries its VLANs on one cable: each of its lines gets the trunk casing.
            const trunk = s.der.l2.links.get(link.id)?.kind === 'trunk';
            segment.trunk = trunk;
            const g = el('g', {class: 'link l2-path dimmable' + (vlan == null ? ' l2-p2p' : '') + (trunk ? ' l2-trunk' : '') + (!segment.active ? ' down' : '') +
                (segment.blocked ? ' problem' : '') + (segment.mismatch ? ' mismatch' : '') + (segment.unknown ? ' unknown' : ''),
                'data-link': link.id, 'data-segment': segment.id, tabindex: '0', role: 'button',
                'aria-label': s.model.nodes.get(link.a.nodeId).name + ' ↔ ' + s.model.nodes.get(link.b.nodeId).name + ' · ' +
                    (vlan == null ? 'p2p' : (trunk ? 'trunk · ' : '') + 'VLAN ' + vlan + (trunk && segment.tag === 'u' ? ' (native)' : ''))}, lines);
            g.style.setProperty('--vlan-color', color(vlan));
            el('path', {d, class: 'hit'}, g);
            if (trunk) el('path', {d, class: 'casing'}, g);
            el('path', {d, class: 'stroke'}, g);
            // The colour tells the VLAN and the pill the details: a line carries a caption only for a problem.
            const caption = segment.mismatch ? `native VLAN ${va.split('|')[2]} ↔ ${vb.split('|')[2]} !`
                : segment.blocked ? `VLAN ${vlan} · ${s.s('ui_canvas_blocked')} !` : '';
            if (caption) {
                const label = el('g', {class: 'auto-label', 'data-priority': 1}, g);
                // On the curve itself: the control point of a fanned line is far from it.
                el('text', {x: (a.cx + 2 * mx + b.cx) / 4, y: (a.cy + 2 * my + b.cy) / 4 - 7, class: 'caption'}, label).textContent = caption;
            }
            this.markStp(segment, g, a, b, {x: mx, y: my});
            segment.g = g;
            segment.ends = [[link.a.nodeId, a, b], [link.b.nodeId, b, a]];
            segment.mid = {x: (a.cx + 2 * mx + b.cx) / 4, y: (a.cy + 2 * my + b.cy) / 4};
        });
        // One "trunk" pill per trunk cable, amid its VLAN lines, from the middle zoom on (class l2). It keeps its size on
        // screen, and the label layout moves it aside or hides it when it would cover another pill.
        const trunks = new Map();
        segments.filter(x => x.trunk).forEach(x => {
            if (!trunks.has(x.link.id)) trunks.set(x.link.id, []);
            trunks.get(x.link.id).push(x);
        });
        trunks.forEach(list => {
            const x = list.reduce((sum, p) => sum + p.mid.x, 0) / list.length, y = list.reduce((sum, p) => sum + p.mid.y, 0) / list.length;
            const first = list.find(p => p.tag !== 'u') || list[0];
            const g = el('g', {class: 'l2-trunk-pill auto-label dimmable l2', 'data-priority': 3, 'data-segment': first.id,
                'data-link': first.link.id, 'aria-hidden': 'true'}, pills);
            g.dataset.fixedX = x;
            g.dataset.fixedY = y;
            el('rect', {x: x - 17, y: y - 7.5, width: 34, height: 15, rx: 7.5}, g);
            el('text', {x, y: y + .5}, g).textContent = 'trunk';
            list.forEach(p => { p.pill = g; });
        });
        virtuals.forEach(v => {
            const mixed = v.domain?.vlans.length > 1;
            const g = el('g', {class: 'l2-virtual dimmable' + (mixed ? ' mixed' : '') + (!v.domain ? ' idle' : '') + (s.factsFailed(v.orig.id) ? ' collection-error' : ''),
                transform: `translate(${v.cx} ${v.cy})`, 'data-virtual': v.id, tabindex: '0', role: 'button', 'aria-label': v.orig.name + ' · VLAN ' + v.vlan}, this.nodeLayer);
            g.style.setProperty('--vlan-color', color(v.vlan));
            el('rect', {x: -v.w / 2, y: -PILL_H / 2, width: v.w, height: PILL_H, rx: PILL_H / 2}, g);
            el('circle', {cx: -v.w / 2 + 13, cy: 0, r: 4, class: 'vmark'}, g);
            el('text', {x: -v.w / 2 + 23, y: 0, class: 'vtitle'}, g).textContent = 'VLAN ' + v.vlan + (mixed ? ' !' : '');
            // The root bridge of the spanning tree of this VLAN, as the console showed it (docs/live-state.md §8.2).
            const root = stpRoot(s.fx && s.fx.nodes.get(v.orig.id), v.vlan) === true;
            if (root) {
                g.classList.add('stp-root');
                const tag = el('g', {class: 'stp-root-tag', transform: `translate(${v.w / 2 - 10} ${-PILL_H / 2})`}, g);
                el('rect', {x: -15, y: -7, width: 30, height: 14, rx: 7}, tag);
                el('text', {x: 0, y: .5}, tag).textContent = 'root';
            }
            // Domain and members are in the tooltip and the card, not on the scheme.
            el('title', {}, g).textContent = (v.domain
                ? mixed ? `VLAN ${v.domain.vlans.join('+')} !` : `${v.domain.members.length} ${s.s('ui_canvas_members')}`
                : s.s('ui_canvas_disconnected')) + (root ? ' · ' + s.s('ui_canvas_stp_root') : '');
            v.g = g;
        });
    }

    /**
     * Marks the ends of an L2 line whose switch port the spanning tree keeps from forwarding in that VLAN: a "cut" sign
     * on the line beside the port, and the line dashed. Only what the facts say: nothing is guessed from the topology.
     *
     * @param {object} segment segment of canvasL2
     * @param {SVGElement} g group of the line
     * @param {object} a start of the line (centre of a VLAN pill or a node)
     * @param {object} b end of the line
     * @param {{x: number, y: number}} m control point of the curve
     */
    markStp(segment, g, a, b, m) {
        const s = this.stage, notes = [];
        const length = Math.hypot(b.cx - a.cx, b.cy - a.cy) || 1;
        [[segment.va, segment.pa, a, 0], [segment.vb, segment.pb, b, 1]].forEach(([vertex, iface, from, side]) => {
            if (!vertex || !vertex.startsWith('S|') || !iface) return;
            const port = portStp(iface, Number(vertex.split('|')[2]));
            if (!port) return;
            notes.push(`${from.orig?.name || from.name || ''} ${iface.name}: ${stpShort(port)}`);
            if (!stpBlocks(port)) return;
            // Just outside the pill (or the icon) of the switch, on the curve.
            const away = Math.min(.42, Math.max(.08, ((from.w ? from.w / 2 : 26) + 14) / length));
            const t = side ? 1 - away : away, u = 1 - t;
            const x = u * u * a.cx + 2 * u * t * m.x + t * t * b.cx, y = u * u * a.cy + 2 * u * t * m.y + t * t * b.cy;
            const angle = Math.atan2(2 * u * (m.y - a.cy) + 2 * t * (b.cy - m.y), 2 * u * (m.x - a.cx) + 2 * t * (b.cx - m.x));
            const mark = el('g', {class: 'stp-block', transform: `translate(${x} ${y}) rotate(${angle * 180 / Math.PI})`}, g);
            el('circle', {r: 7}, mark);
            el('line', {x1: 0, y1: -4.5, x2: 0, y2: 4.5}, mark);
            el('title', {}, mark).textContent = `${iface.name} · ${s.s('ui_canvas_stp_blocked')} (${stpShort(port)})`;
            g.classList.add('stp-blocked');
        });
        if (notes.length) el('title', {}, g).textContent = 'STP · ' + notes.join(' · ');
    }

    renderL3() {
        const s = this.stage;
        this.subnets.forEach((x, index) => {
            x.edges = [];
            // Each subnet has its own colour, as each VLAN on L2: the pill, its lines and its address labels.
            const color = NET_COLORS[index % NET_COLORS.length];
            x.members.forEach(m => {
                const n = s.model.nodes.get(m.nodeId);
                const line = el('line', {x1: n.cx, y1: n.cy, x2: x.cx, y2: x.cy, class: 'subnet-edge dimmable' + (m.down || m.dup ? ' down' : ''),
                    'data-subnet': x.key}, s.L.l3);
                line.style.setProperty('--net-color', color);
                const group = el('g', {class: 'auto-label dimmable', 'data-priority': 2, 'data-subnet': x.key}, s.L.l3);
                const label = el('text', {x: n.cx + (x.cx - n.cx) * .4, y: n.cy + (x.cy - n.cy) * .4 - 5 / s.view.k, class: 'pip'}, group);
                label.textContent = this.portText(n, m.iface) + ' · ' + m.c.ip;
                x.edges.push({line, label, node: n});
            });
            const g = el('g', {class: 'subnet-object dimmable' + (x.issues.length ? ' warn' : ''), transform: `translate(${x.cx} ${x.cy})`,
                'data-subnet': x.key, tabindex: '0', role: 'button', 'aria-label': x.key + ', ' + x.members.length + ' ' + s.s('ui_canvas_interfaces')}, s.L.l3);
            g.style.setProperty('--net-color', color);
            const title = (x.issues.length ? '! ' : '') + x.key;
            // Compact pill in screen pixels: the address and an "i" that opens the card (a tap on the pill only selects).
            x.hw = Math.round((title.length * 6.2 + 12 + 22) / 2); x.hh = 11;
            const pill = el('g', {class: 'subnet-pill', transform: `scale(${1 / s.view.k})`}, g);
            el('rect', {x: -x.hw, y: -x.hh, width: 2 * x.hw, height: 2 * x.hh, rx: x.hh}, pill);
            el('text', {x: -8, y: 1}, pill).textContent = title;
            const info = el('g', {class: 'subnet-info', 'data-subnet-info': x.key, transform: `translate(${x.hw - 12} 0)`}, pill);
            el('circle', {r: 13, class: 'hit'}, info);
            el('circle', {r: 7.5, class: 'dot'}, info);
            el('text', {y: 1}, info).textContent = 'i';
            el('title', {}, info).textContent = s.s('ui_act_info');
            x.g = g; s.grow(x.x - x.hw, x.y - x.hh, x.x + x.hw, x.y + x.hh);
        });
    }

    /**
     * Name of an interface on the scheme: a host shows the name of its cable end (E0, E1, ...), other nodes the device name.
     *
     * @param {object} n node of the model
     * @param {object} iface interface from the facts
     * @returns {string}
     */
    portText(n, iface) {
        if (!isHost(n)) return iface.name;
        // A subinterface keeps its suffix: ens3.10 on the cable end E0 is E0.10.
        const f = this.stage.fx && this.stage.fx.nodes.get(n.id);
        const parent = !iface.port && iface.parent && f && f.byName.get(iface.parent);
        if (parent && iface.name.startsWith(parent.name)) return this.portText(n, parent) + iface.name.slice(parent.name.length);
        const p = iface.port;
        const end = p && this.stage.model.links.flatMap(l => [l.a, l.b])
            .find(e => e.nodeId === n.id && e.adapter === p.adapter && e.port === p.port);
        return end ? end.text : iface.name;
    }

    /**
     * Directions of the lines drawn from each node on the current layer.
     *
     * @returns {Map<string, Array<{x: number, y: number}>>} node id: unit vectors in scene coordinates
     */
    spokes() {
        const s = this.stage, out = new Map();
        const add = (id, from, to) => {
            const dx = to.cx - from.cx, dy = to.cy - from.cy, len = Math.hypot(dx, dy);
            if (!len) return;
            if (!out.has(id)) out.set(id, []);
            out.get(id).push({x: dx / len, y: dy / len});
        };
        if (s.layer === 'l3') {
            this.subnets.forEach(x => {
                if (x.g && x.g.style.display !== 'none') x.edges.forEach(e => add(e.node.id, e.node, x));
            });
        } else if (s.layer === 'l2') {
            (this.l2?.segments || []).forEach(segment => (segment.ends || []).forEach(([id, from, to]) => add(id, from, to)));
        } else if (s.layer !== 'l4') {
            s.model.links.forEach(l => {
                const a = s.model.nodes.get(l.a.nodeId), b = s.model.nodes.get(l.b.nodeId);
                add(a.id, a, b); add(b.id, b, a);
            });
        }
        return out;
    }

    /**
     * Side of a node for its name: below if no line leaves that way, else above, right or left; the freest one at worst.
     *
     * @param {Array<{x: number, y: number}>} dirs directions of the lines of the node
     * @returns {string} bottom, top, right or left
     */
    static labelSide(dirs) {
        const sides = {bottom: [0, 1], top: [0, -1], right: [1, 0], left: [-1, 0]};
        const score = ([x, y]) => Math.max(-1, ...dirs.map(d => d.x * x + d.y * y));
        const order = Object.keys(sides);
        // cos 55°: a line closer than that to the side would cross the name.
        return order.find(side => score(sides[side]) < 0.57)
            || order.reduce((best, side) => score(sides[side]) < score(sides[best]) - 0.01 ? side : best);
    }

    positionSubnets() {
        const s = this.stage, {k} = s.view;
        const busy = [...s.model.nodes.values()].map(n => {
            const p = s.toScreen(n.cx, n.cy), r = Math.max(14, 26 * k); return {x: p.x - r, y: p.y - r, w: 2 * r, h: 2 * r};
        });
        // Pills keep clear of the nodes and of each other only: near the screen edge or under a panel they stay put,
        // so the scheme moves as one picture while it is dragged.
        const intersects = (a, b) => a.x < b.x + b.w + 5 && a.x + a.w + 5 > b.x && a.y < b.y + b.h + 5 && a.y + a.h + 5 > b.y;
        this.subnets.forEach(x => {
            if (!x.g) return;
            const origin = s.toScreen(x.x, x.y), offsets = [[0, 0]];
            for (const r of [24, 48, 80, 120, 176, 240]) for (let a = 0; a < 360; a += 45) offsets.push([r * Math.cos(a * Math.PI / 180), r * Math.sin(a * Math.PI / 180)]);
            const offset = offsets.find(([dx, dy]) => {
                const b = {x: origin.x + dx - x.hw, y: origin.y + dy - x.hh, w: 2 * x.hw, h: 2 * x.hh};
                return !busy.some(o => intersects(b, o));
            });
            x.g.style.display = offset ? '' : 'none'; x.g.setAttribute('tabindex', offset ? '0' : '-1');
            const [dx, dy] = offset || [0, 0]; x.cx = x.x + dx / k; x.cy = x.y + dy / k;
            x.g.setAttribute('transform', `translate(${x.cx} ${x.cy})`);
            x.g.querySelector('.subnet-pill').setAttribute('transform', `scale(${1 / k})`);
            if (offset) busy.push({x: origin.x + dx - x.hw, y: origin.y + dy - x.hh, w: 2 * x.hw, h: 2 * x.hh});
            x.edges.forEach(({line, label, node}) => {
                line.style.display = offset ? '' : 'none'; label.parentNode.style.display = offset ? '' : 'none';
                line.setAttribute('x2', x.cx); line.setAttribute('y2', x.cy);
                label.setAttribute('x', node.cx + (x.cx - node.cx) * .4); label.setAttribute('y', node.cy + (x.cy - node.cy) * .4 - 5 / k);
            });
        });
    }

    renderHud() {
        const s = this.stage, hud = s.r('layerSeg'), tools = s.r('hudTools');
        const maxW = Math.max(220, Math.min(370, s.wrap.clientWidth - 24));
        // The HUD is as wide as its content: compact layer buttons, the layer note and the overview/facts controls.
        let natural = 0;
        if (tools) { tools.style.width = 'max-content'; natural = tools.offsetWidth; }
        const key = [maxW, s.layer, natural, tools ? tools.offsetHeight : 0].join('/');
        if (this.hudKey !== key) {
            this.hudKey = key; hud.textContent = '';
            const bg = el('rect', {x: 14, y: 14, height: 0, rx: 14, class: 'hud-bg'}, hud);
            const text = (cls, x, y, value) => { const t = el('text', {class: cls, x, y}, hud); t.textContent = value; return t; };
            const kicker = text('hud-kicker', 29, 36, s.s('ui_canvas_title'));
            const bw = 36, gap = 6;
            ['l1', 'l2', 'l3', 'l4'].forEach((layer, i) => {
                const g = el('g', {class: 'layer-button' + (s.layer === layer ? ' active' : ''), tabindex: '0', role: 'button',
                    'data-layer': layer, 'aria-label': layerHelp(s, layer), 'aria-pressed': String(s.layer === layer)}, hud);
                el('rect', {x: 29 + i * (bw + gap), y: 46, width: bw, height: 28, rx: 7}, g);
                el('text', {x: 29 + i * (bw + gap) + bw / 2, y: 60}, g).textContent = layer.toUpperCase();
                el('title', {}, g).textContent = layerHelp(s, layer);
                g.onclick = () => s.setLayer(layer);
                g.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); s.setLayer(layer); s.r('layerSeg').querySelector(`[data-layer="${layer}"]`).focus(); } };
            });
            const note = text('hud-note', 29, 94, layerHelp(s, s.layer));
            const measure = t => { try { return t.getComputedTextLength(); } catch (e) { return 0; } };
            this.hudW = clamp(Math.max(4 * bw + 3 * gap, measure(kicker), measure(note), natural) + 30, 200, maxW);
            bg.setAttribute('width', this.hudW);
            this.hudBg = bg;
        }
        if (tools) tools.style.width = (this.hudW - 30) + 'px';
        const height = TOOLS_TOP - 14 + (tools ? tools.offsetHeight + 12 : 0);
        this.hudBg.setAttribute('height', height);
        s.hudBottom = 14 + height;
        s.hudRight = 14 + this.hudW;
    }

    prepareLabels() {
        const s = this.stage;
        s.vp.querySelectorAll('.node').forEach(g => {
            const label = g.querySelector('.auto-label') || el('g', {class: 'auto-label node-caption', 'data-priority': 0}, g);
            g.querySelectorAll(':scope > .nname,:scope > .nsub,:scope > .nadj').forEach(e => label.appendChild(e));
        });
        s.L.links.querySelectorAll('.link:not(.l2-path)').forEach(g => {
            const texts = [...g.querySelectorAll(':scope > .plabel,:scope > .pip')];
            texts.forEach(t => {
                const x = Number(t.getAttribute('x')), y = Number(t.getAttribute('y'));
                const key = x + '/' + y;
                let label = [...g.querySelectorAll('.auto-label')].find(e => e.dataset.anchor === key);
                if (!label) label = el('g', {class: 'auto-label', 'data-priority': 2, 'data-anchor': key}, g);
                label.appendChild(t);
            });
        });
        s.L.labels.querySelectorAll('.ppill,.subnet,.vpill,.bpill,.wpill').forEach(g => {
            if (g.classList.contains('subnet')) {
                g.setAttribute('tabindex', '0');
                g.setAttribute('role', 'button');
                g.setAttribute('aria-label', g.textContent);
            } else g.classList.add('auto-label');
        });
        // Pills are scaled about their anchor, keeping their measured background and type together.
        s.svg.querySelectorAll('.ppill,.subnet,.gpill,.vpill,.bpill,.wpill').forEach(g => {
            const t = g.querySelector('text');
            if (!t) return;
            g.dataset.fixedX = t.getAttribute('x') || 0;
            g.dataset.fixedY = t.getAttribute('y') || 0;
        });
    }

    view() {
        const s = this.stage, inv = 1 / s.view.k;
        s.svg.style.setProperty('--node-ring', clamp(s.view.k, .5, 1.1) + 'px');
        s.svg.dataset.factlayer = s.layer;
        s.svg.querySelectorAll('.pdot').forEach(dot => dot.setAttribute('r', PORT_DOT * inv));
        // A cable is one line until the scheme is zoomed in well; then its two directions with TX and RX.
        s.svg.dataset.duplex = s.view.k >= DUPLEX_K ? 'on' : 'off';
        s.svg.querySelectorAll('[data-fixed-x]').forEach(g => {
            const x = Number(g.dataset.fixedX), y = Number(g.dataset.fixedY);
            g.setAttribute('transform', `translate(${x} ${y}) scale(${inv}) translate(${-x} ${-y})`);
        });
        this.rails.forEach(({link, geometry: g, directions}) => {
            // The two directions run between the port dots on the node rings, as the single wire does.
            const half = Math.max(0, Math.hypot(g.pb.x - g.pa.x, g.pb.y - g.pa.y) / 2);
            const mx = (g.pa.x + g.pb.x) / 2, my = (g.pa.y + g.pb.y) / 2;
            directions.forEach(({line, arrow, labels, sign}) => {
                const point = (x, y) => ({x: mx + g.dx * x - g.dy * y, y: my + g.dy * x + g.dx * y});
                const y = -sign * 3 * inv, a = point(-sign * half, y), b = point(sign * half, y);
                Object.entries({x1: a.x, y1: a.y, x2: b.x, y2: b.y}).forEach(([k, v]) => line.setAttribute(k, v));
                const x = sign * half * .35, tip = point(x, y), p = point(x - sign * 5 * inv, y - 3 * inv), q = point(x - sign * 5 * inv, y + 3 * inv);
                arrow.setAttribute('d', `M${p.x} ${p.y} L${tip.x} ${tip.y} L${q.x} ${q.y}`);
                arrow.style.display = half * 2 * s.view.k < 28 ? 'none' : '';
                labels.forEach(label => {
                    const inset = Math.min(24 * inv, Math.max(0, half - 12 * inv));
                    const lx = (label.end === link.a ? -1 : 1) * (half - inset);
                    const at = point(lx, -sign * 13 * inv), start = point(lx, y), end = point(lx, -sign * 8 * inv);
                    label.g.style.display = half * 2 * s.view.k >= 44 && (s.lod >= 1 || s.sel?.kind === 'link' && s.sel.id === link.id) ? '' : 'none';
                    label.text.setAttribute('x', at.x); label.text.setAttribute('y', at.y);
                    Object.entries({x1: start.x, y1: start.y, x2: end.x, y2: end.y}).forEach(([k, v]) => label.lead.setAttribute(k, v));
                });
            });
        });
        this.scheduleLabels();
        this.renderHud();
        this.positionSubnets();
        const status = factsStatus(s);
        s.svg.dataset.source = status.state;
        s.r('factStatus').dataset.state = status.state;
        s.r('canvasFacts').textContent = status.text;
        s.r('issueCount').textContent = layerIssues(s).length;
        s.r('canvasRefresh').disabled = !s.factsLive || !s.liveDoc;
        let empty = '';
        if (s.layer === 'l4') empty = s.s('ui_canvas_l4');
        else if (s.layer === 'l3' && !this.subnets.length) empty = s.s('ui_canvas_empty_l3');
        else if (s.layer === 'l2' && (!this.l2 || (!this.l2.virtuals.size && !this.l2.segments.length))) empty = s.s('ui_canvas_empty_l2');
        s.r('canvasEmpty').textContent = empty; s.r('canvasEmpty').hidden = !empty;

    }

    select() {
        const s = this.stage, sel = s.layer === 'l4' ? null : s.sel, D = s.der;
        s.svg.classList.toggle('has-sel', !!sel);
        s.vp.querySelectorAll('.on,.sel,.connection-related,.connection-primary,.connection-dim').forEach(e => e.classList.remove('on', 'sel', 'connection-related', 'connection-primary', 'connection-dim'));
        const mark = (attr, id, primary = false) => s.svg.querySelectorAll(`[${attr}="${CSS.escape(String(id))}"]`).forEach(e => e.classList.add(...(primary ? ['on', 'sel'] : ['on'])));
        const nodes = new Set(), links = new Set(), subnets = new Set(), domains = new Set();
        s.svg.dataset.connectionFocus = sel ? 'true' : 'false';
        if (!sel) { this.scheduleLabels(); return; }
        if (sel.kind === 'node') {
            nodes.add(sel.id); mark('data-node', sel.id, true);
            if (s.layer === 'l3' && D) D.l3.subnets.forEach(x => { if (x.members.some(m => m.nodeId === sel.id)) subnets.add(x.key); });
            else if (s.layer === 'l2' && this.l2) {
                this.l2.virtuals.forEach(v => { if (v.orig.id === sel.id && v.domain) domains.add(v.domain); });
                D.l2.domains.forEach(d => { if (d.nodes.has(sel.id)) domains.add(d); });
            } else s.model.links.forEach(l => { if (l.a.nodeId === sel.id || l.b.nodeId === sel.id) links.add(l.id); });
        } else if (sel.kind === 'switchvlan' && this.l2) {
            const v = this.l2.virtuals.get(sel.id);
            mark('data-virtual', sel.id, true);
            if (v && v.domain) domains.add(v.domain);
            // facts.js omits domains without host members. Their accepted switch paths still exist.
            if (v && !v.domain) {
                const vertices = new Set([v.id]);
                const grow = () => {
                    let added = false;
                    this.l2.segments.filter(p => p.active && (vertices.has(p.va) || vertices.has(p.vb))).forEach(p => {
                        p.g.classList.add('on');
                        [p.va, p.vb].filter(id => !vertices.has(id)).forEach(id => {
                            vertices.add(id);
                            added = true;
                        });
                    });
                    return added;
                };
                while (grow()) {
                    // Every round adds the vertices reached through the segments found in the round before.
                }
                vertices.forEach(id => { mark('data-virtual', id); const peer = this.l2.virtuals.get(id); if (peer) mark('data-node', peer.orig.id); });
            }
            if (v) mark('data-node', v.orig.id);
        } else if (sel.kind === 'segment' && this.l2) {
            const p = this.l2.segments.find(p => p.id === sel.id);
            if (p) {
                mark('data-segment', p.id, true); [p.link.a, p.link.b].forEach(e => nodes.add(e.nodeId)); [p.va, p.vb].forEach(id => mark('data-virtual', id));
                // The other VLANs of the same trunk cable are lit with it.
                if (p.trunk) this.l2.segments.filter(q => q.link === p.link && q !== p && q.g).forEach(q => q.g.classList.add('on'));
                if (p.pill) p.pill.classList.add('on');
            }
        } else if (sel.kind === 'link') { links.add(sel.id); mark('data-link', sel.id, true); }
        else if (sel.kind === 'subnet') { subnets.add(sel.id); mark('data-subnet', sel.id, true); }
        else if (sel.kind === 'vlan' && D) { const d = D.l2.domains.find(d => d.id === sel.id); if (d) domains.add(d); }
        if (sel.kind === 'link' && s.layer === 'l3' && D) {
            const link = s.model.links.find(l => l.id === sel.id);
            if (link) D.l3.subnets.forEach(x => { if (x.members.some(m => [link.a.nodeId, link.b.nodeId].includes(m.nodeId))) subnets.add(x.key); });
        }
        subnets.forEach(id => {
            const subnet = D && D.l3.subnets.find(x => x.key === id);
            mark('data-subnet', id);
            if (subnet) subnet.members.forEach(m => nodes.add(m.nodeId));
            if (subnet && s.layer === 'l1') {
                subnet.members.forEach(m => m.dom?.links.forEach(id => links.add(id)));
                const members = new Set(subnet.members.map(m => m.nodeId));
                s.model.links.forEach(l => { if (members.has(l.a.nodeId) && members.has(l.b.nodeId)) links.add(l.id); });
            }
        });
        domains.forEach(d => {
            d.nodes.forEach(id => nodes.add(id));
            if (this.l2) {
                this.l2.virtuals.forEach(v => { if (v.domain === d) mark('data-virtual', v.id); });
                this.l2.segments.forEach(segment => { if (segment.domain === d) segment.g.classList.add('on'); });
            }
        });
        links.forEach(id => {
            mark('data-link', id);
            const l = s.model.links.find(l => l.id === id);
            if (l) { nodes.add(l.a.nodeId); nodes.add(l.b.nodeId); }
        });
        nodes.forEach(id => mark('data-node', id));
        // Never highlight other VLANs of a related chassis.
        if (this.l2 && sel.kind === 'node') this.l2.virtuals.forEach(v => { if (v.orig.id === sel.id) mark('data-virtual', v.id, true); });
        s.vp.querySelectorAll('.node,.link,.l2-virtual,.l2-chassis,.subnet-object,.subnet-edge,[data-subnet].auto-label').forEach(g => {
            g.classList.add(g.classList.contains('on') ? 'connection-related' : 'connection-dim');
            if (g.classList.contains('sel')) g.classList.add('connection-primary');
        });
        this.view();
    }

    scheduleLabels() {
        if (this.frame !== null) return;
        this.frame = requestAnimationFrame(() => { this.frame = null; this.layoutLabels(); });
    }

    layoutLabels() {
        const s = this.stage, svg = s.svg, root = svg.getBoundingClientRect(), inv = 1 / s.view.k;
        const labels = [...svg.querySelectorAll('.auto-label')];
        const visible = e => {
            for (let n = e; n && n !== svg; n = n.parentElement) {
                const style = getComputedStyle(n);
                if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0) return false;
            }
            return true;
        };
        const box = e => { const r = e.getBoundingClientRect(); return {x: r.left - root.left, y: r.top - root.top, w: r.width, h: r.height}; };
        const overlaps = (a, b) => a.x < b.x + b.w + 3 && a.x + a.w + 3 > b.x && a.y < b.y + b.h + 3 && a.y + a.h + 3 > b.y;
        labels.forEach(g => { g.classList.remove('label-hidden'); g.style.translate = ''; });
        // Interface names matter to the student: each sits on its own plate, sized to the text at this zoom.
        svg.querySelectorAll('.plabel').forEach(t => {
            let bg = t.previousElementSibling;
            // The plate shares the level-of-detail classes of its text, so both appear and hide together.
            if (!bg || !bg.classList.contains('plabel-bg')) {
                bg = t.parentNode.insertBefore(el('rect', {class: ['plabel-bg', ...[...t.classList].filter(c => c !== 'plabel')].join(' ')}), t);
            }
            if (!visible(t)) { bg.setAttribute('width', 0); return; }
            const r = t.getBBox(), px = 4 * inv, py = 1.5 * inv;
            Object.entries({x: r.x - px, y: r.y - py, width: r.width + 2 * px, height: r.height + 2 * py, rx: 3 * inv})
                .forEach(([k, v]) => bg.setAttribute(k, v));
        });
        const spokes = this.spokes();
        labels.forEach(g => {
            const name = g.querySelector('.nname'), node = g.closest('.node');
            // Placed even while the layer fades in (opacity 0): the bounding box is already known then.
            if (!name || !node) return;
            // The name goes where no line leaves the node, so it does not cover the connections.
            const side = CanvasView.labelSide(spokes.get(node.dataset.node) || []), gap = 4 * inv;
            const lines = [name, ...[...g.querySelectorAll('.nsub,.nadj')].filter(visible)];
            const heights = lines.map(t => t.getBBox().height), total = heights.reduce((a, b) => a + b, 0) + gap * (lines.length - 1);
            const x = side === 'right' ? 33 : side === 'left' ? -33 : 0;
            let y = side === 'bottom' ? (s.layer === 'l2' ? 23 : 25) : side === 'top' ? -25 - total : -total / 2;
            lines.forEach((t, i) => {
                t.setAttribute('dy', '0'); t.setAttribute('x', x); t.setAttribute('y', y);
                t.style.textAnchor = side === 'right' ? 'start' : side === 'left' ? 'end' : '';
                y += heights[i] + gap;
            });
            g.dataset.side = side;
        });
        if (!this.leads) return;
        this.leads.textContent = '';
        const occupied = [...svg.querySelectorAll('.node .ico,.l2-virtual > rect,.subnet-object rect,.drawing text,.duplex-label')]
            .filter(visible).map(box).filter(r => r.w && r.h);
        // Only objects of the scheme push labels aside: the screen edge and the panels over the scheme do not, so labels
        // do not slide while the scheme is dragged.
        labels.map(g => {
            const texts = [...g.querySelectorAll('text')].filter(visible);
            if (!texts.length) return null;
            const boxes = texts.map(box), x = Math.min(...boxes.map(b => b.x)), y = Math.min(...boxes.map(b => b.y));
            return {g, r: {x, y, w: Math.max(...boxes.map(b => b.x + b.w)) - x, h: Math.max(...boxes.map(b => b.y + b.h)) - y},
                priority: Number(g.dataset.priority || 2) - (g.closest('.sel') ? 20 : g.closest('.on') ? 10 : 0)};
        }).filter(Boolean).sort((a, b) => a.priority - b.priority).forEach(({g, r, priority}) => {
                const offsets = [[0, 0]];
                for (const d of [14, 28, 44, 64, 88]) {
                    offsets.push([0, -d], [0, d], [-d, 0], [d, 0], [-d, -d], [d, -d], [-d, d], [d, d]);
                    if (priority > 1 && d >= 44) break;
                }
                const offset = offsets.find(([dx, dy]) => {
                    const c = {...r, x: r.x + dx, y: r.y + dy};
                    return !occupied.some(b => overlaps(c, b));
                });
                if (!offset) { g.classList.add('label-hidden'); return; }
                const [dx, dy] = offset;
                // Individual translate is applied in the parent scene coordinates, before the SVG transform.
                g.style.translate = `${dx * inv}px ${dy * inv}px`;
                occupied.push({...r, x: r.x + dx, y: r.y + dy});
                if (Math.hypot(dx, dy) >= 14) {
                    const x = r.x + r.w / 2, y = r.y + r.h / 2;
                    el('line', {class: 'label-lead', x1: (x - s.view.tx) * inv, y1: (y - s.view.ty) * inv,
                        x2: (x + dx - s.view.tx) * inv, y2: (y + dy - s.view.ty) * inv}, this.leads);
                }
            });
    }
}
