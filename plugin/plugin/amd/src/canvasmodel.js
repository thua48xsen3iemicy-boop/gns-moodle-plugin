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
 * Display geometry for the student Canvas. Connectivity stays authoritative in facts.js.
 *
 * @module mod_gns3lab/canvasmodel
 * @copyright 2026 mod_gns3lab contributors
 * @license https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
/* eslint-disable curly, max-len, jsdoc/require-jsdoc, no-eq-null */
import {endVertex, switchVlans} from 'mod_gns3lab/facts';

// Both the canvas and the card use the same directed ends: TX always terminates at RX.
export const linkDirections = link => [
    {from: link.a, to: link.b, sign: 1},
    {from: link.b, to: link.a, sign: -1},
];

/** Height of a VLAN pill inside a switch chassis, its gap and the padding of the chassis around the pills. */
export const PILL_H = 26;
const GAP = 8, PAD = 12;

/**
 * Width of a VLAN pill: a colour dot and "VLAN n".
 *
 * @param {number} vlan VLAN id
 * @returns {number}
 */
export const pillWidth = vlan => Math.round(34 + 6.4 * ('VLAN ' + vlan).length);

/**
 * Offsets of the VLAN pills of one switch from its centre for a shape: a row, a column or two columns.
 *
 * @param {number[]} values VLAN ids in order
 * @param {string} shape row, column or grid
 * @returns {{items: Array<{vlan: number, x: number, y: number, w: number}>, hw: number, hh: number}}
 */
const arrange = (values, shape) => {
    const widths = values.map(pillWidth), max = Math.max(...widths);
    const cols = shape === 'row' ? values.length : shape === 'column' ? 1 : 2;
    const rows = Math.ceil(values.length / cols);
    const colW = shape === 'row' ? widths : Array(cols).fill(max);
    const totalW = colW.slice(0, cols).reduce((a, b) => a + b, 0) + GAP * (cols - 1), totalH = rows * PILL_H + GAP * (rows - 1);
    const items = values.map((vlan, i) => {
        const c = i % cols, r = Math.floor(i / cols);
        const left = colW.slice(0, c).reduce((a, b) => a + b, 0) + GAP * c;
        return {vlan, w: widths[i], x: -totalW / 2 + left + colW[c] / 2, y: -totalH / 2 + r * (PILL_H + GAP) + PILL_H / 2};
    });
    return {items, hw: totalW / 2 + PAD, hh: totalH / 2 + PAD};
};

/**
 * Lays out the VLAN pills of every switch so that chassis do not cover each other or the nodes around them:
 * each switch takes the row, column or two-column shape that overlaps its neighbours least.
 *
 * @param {object} model topology model
 * @param {Map<string, Set<number>>} vlans VLAN ids by switch id
 * @returns {Array<{orig: object, items: Array}>}
 */
export const layoutVlans = (model, vlans) => {
    const switches = [...vlans].map(([id, set]) => ({orig: model.nodes.get(id), values: [...set].sort((a, b) => a - b)}))
        .filter(x => x.orig && x.orig.state !== 'silhouette');
    const ids = new Set(switches.map(x => x.orig.id));
    // Other nodes keep their icon and name; the margin leaves room for the chassis outline and its title.
    const fixed = [...model.nodes.values()].filter(n => !ids.has(n.id)).map(n => ({cx: n.cx, cy: n.cy, hw: 40, hh: 42}));
    const overlap = (a, b) => Math.max(0, Math.min(a.cx + a.hw, b.cx + b.hw) - Math.max(a.cx - a.hw, b.cx - b.hw) + 16)
        * Math.max(0, Math.min(a.cy + a.hh, b.cy + b.hh) - Math.max(a.cy - a.hh, b.cy - b.hh) + 20);
    switches.forEach(x => { x.shape = arrange(x.values, 'row'); });
    for (let pass = 0; pass < 3; pass++) {
        switches.forEach(x => {
            const others = [...fixed, ...switches.filter(y => y !== x).map(y => ({cx: y.orig.cx, cy: y.orig.cy, hw: y.shape.hw, hh: y.shape.hh}))];
            const shapes = ['row', 'column', 'grid'].filter(shape => shape !== 'grid' || x.values.length > 2);
            x.shape = shapes.map((shape, rank) => {
                const a = arrange(x.values, shape), box = {cx: x.orig.cx, cy: x.orig.cy, hw: a.hw, hh: a.hh};
                return {a, score: others.reduce((sum, o) => sum + overlap(box, o), 0) + rank};
            }).reduce((best, c) => c.score < best.score ? c : best).a;
        });
    }
    return switches.map(x => ({orig: x.orig, items: x.shape.items}));
};

export const canvasL2 = stage => {
    const {model, fx, der} = stage, virtuals = new Map(), segments = [], vlans = new Map();
    const note = vertex => {
        if (!vertex || !vertex.startsWith('S|')) return;
        const [, id, vlan] = vertex.split('|');
        if (!vlans.has(id)) vlans.set(id, new Set());
        vlans.get(id).add(Number(vlan));
    };
    // Only filtered facts and visible cables enter the display model.
    fx.nodes.forEach(f => switchVlans(f).forEach(v => note(`S|${f.id}|${v}`)));
    model.links.forEach(link => {
        const r = der.l2.links.get(link.id);
        if (!r) return;
        const {wa, wb, ia, ib} = r;
        if ((wa && wa.none) || (wb && wb.none)) return;
        const candidates = new Set([...(wa ? wa.set : []), ...(wb ? wb.set : [])]);
        if (wa && wb && wa.any && wb.any) [link.a, link.b].forEach(e => {
            const f = fx.nodes.get(e.nodeId);
            if (f) switchVlans(f).forEach(v => candidates.add(v));
        });
        if (!candidates.size) candidates.add('u');
        candidates.forEach(tag => {
            const va = wa && endVertex(link.a.nodeId, ia, wa, tag);
            const vb = wb && endVertex(link.b.nodeId, ib, wb, tag);
            const accepted = r.tags.some(t => t.t === tag);
            const sender = va ? wa : wb;
            const sent = sender && (tag !== 'u' || !sender.sw || sender.sp.mode !== 'trunk');
            const blocked = !!(r.known && !accepted && r.issues.length && (!va || !vb) && sent);
            if (!va && !vb && tag !== 'u') return;
            // An unused native VLAN is valid: no peer is expected to accept it.
            if (r.known && (!va || !vb) && !blocked) return;
            note(va); note(vb);
            const vlan = wa && wa.sw ? wa.map(tag) : wb && wb.sw ? wb.map(tag) : null;
            const mismatch = accepted && tag === 'u' && wa.sw && wb.sw && wa.map(tag) !== wb.map(tag);
            const port = (wire, iface) => !iface ? null : !wire || wire.sw || tag === 'u' ? iface
                : (wire.subs.find(i => Number(i.dot1q) === tag) || iface);
            const name = (wire, iface) => port(wire, iface)?.name || '?';
            segments.push({id: link.id + '|' + tag, link, tag, va, vb, vlan, active: accepted,
                blocked, mismatch, unknown: !r.known || der.l1.links.get(link.id).unknown,
                aname: name(wa, ia), bname: name(wb, ib), domain: accepted ? der.l2.domOf.get(va) : null});
        });
    });
    layoutVlans(model, vlans).forEach(({orig, items}) => items.forEach(({vlan, x, y, w}) => {
        const id = `S|${orig.id}|${vlan}`;
        virtuals.set(id, {id, orig, vlan, w, cx: orig.cx + x, cy: orig.cy + y, domain: der.l2.domOf.get(id)});
    }));
    return {virtuals, segments, switches: new Set([...virtuals.values()].map(v => v.orig.id))};
};
