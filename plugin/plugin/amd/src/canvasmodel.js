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
            const name = (wire, iface) => !iface ? '?' : !wire || wire.sw || tag === 'u' ? iface.name
                : (wire.subs.find(i => Number(i.dot1q) === tag) || iface).name;
            segments.push({id: link.id + '|' + tag, link, tag, va, vb, vlan, active: accepted,
                blocked, mismatch, unknown: !r.known || der.l1.links.get(link.id).unknown,
                aname: name(wa, ia), bname: name(wb, ib), domain: accepted ? der.l2.domOf.get(va) : null});
        });
    });
    vlans.forEach((set, id) => {
        const orig = model.nodes.get(id);
        if (!orig || orig.state === 'silhouette') return;
        const values = [...set].sort((a, b) => a - b);
        values.forEach((vlan, index) => {
            const id = `S|${orig.id}|${vlan}`;
            virtuals.set(id, {id, orig, vlan, cx: orig.cx + (index - (values.length - 1) / 2) * 152,
                cy: orig.cy, domain: der.l2.domOf.get(id)});
        });
    });
    return {virtuals, segments, switches: new Set([...virtuals.values()].map(v => v.orig.id))};
};
