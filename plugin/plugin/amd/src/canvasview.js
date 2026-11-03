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
import {clamp} from 'mod_gns3lab/model';
import {vcolor} from 'mod_gns3lab/facts';
import {canvasL2, linkDirections} from 'mod_gns3lab/canvasmodel';

export const STRING_KEYS = ['ui_canvas_title', 'ui_canvas_l4', 'ui_canvas_duplex', 'ui_canvas_chassis',
    'ui_canvas_blocked', 'ui_canvas_unknown', 'ui_canvas_disconnected', 'ui_canvas_objects',
    'ui_canvas_card_layers', 'ui_canvas_interface', 'ui_canvas_admin', 'ui_canvas_oper',
    'ui_assignments', 'ui_assignments_empty'];

export class CanvasView {
    constructor(stage) {
        this.stage = stage;
        this.rails = [];
        this.frame = null;
    }

    reset() {
        this.rails = [];
        this.l2 = this.stage.layer === 'l2' && this.stage.der ? canvasL2(this.stage) : null;
        this.leads = el('g', {class: 'label-leads', 'aria-hidden': 'true'}, this.stage.L.labels);
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
                const g = el('g', {class: 'duplex-end', 'data-role': role, 'data-node-end': end.nodeId}, group);
                const lead = el('line', {class: 'duplex-label-lead'}, g);
                const text = el('text', {class: 'duplex-label', 'text-anchor': 'middle', 'dominant-baseline': 'central'}, g);
                text.textContent = role;
                return {end, g, lead, text};
            });
            return {line, arrow, labels, sign};
        });
        el('title', {}, group).textContent = stage.s('ui_canvas_duplex');
        this.rails.push({link, geometry, g, directions, group});
    }

    renderL2() {
        const s = this.stage, {virtuals, segments} = this.l2;
        const groups = new Map();
        virtuals.forEach(v => {
            if (!groups.has(v.orig.id)) groups.set(v.orig.id, []);
            groups.get(v.orig.id).push(v);
        });
        groups.forEach(vs => {
            const n = vs[0].orig, x = vs[0].cx - 66, width = vs[vs.length - 1].cx - x + 66;
            const g = el('g', {class: 'l2-chassis dimmable', 'data-node': n.id}, s.L.draw);
            el('rect', {x, y: n.cy - 49, width, height: 100, rx: 14}, g);
            const label = el('g', {class: 'auto-label', 'data-priority': 1}, g);
            el('text', {x: x + width / 2, y: n.cy - 60, class: 'caption'}, label).textContent = n.name + ' · ' + s.s('ui_canvas_chassis');
            s.grow(x, n.cy - 70, x + width, n.cy + 54);
        });
        segments.forEach(segment => {
            const {link, va, vb, vlan} = segment;
            const a = virtuals.get(va) || s.model.nodes.get(link.a.nodeId);
            const b = virtuals.get(vb) || s.model.nodes.get(link.b.nodeId);
            const dx = b.cx - a.cx, dy = b.cy - a.cy, length = Math.hypot(dx, dy) || 1;
            const bend = segment.tag === 'u' ? -7 : 7;
            const mx = (a.cx + b.cx) / 2 - dy / length * bend, my = (a.cy + b.cy) / 2 + dx / length * bend;
            const d = `M${a.cx} ${a.cy} Q${mx} ${my} ${b.cx} ${b.cy}`;
            const g = el('g', {class: 'link l2-path dimmable' + (!segment.active ? ' down' : '') +
                (segment.blocked || segment.mismatch ? ' problem' : '') + (segment.unknown ? ' unknown' : '') +
                (s.factsFailed(link.a.nodeId) || s.factsFailed(link.b.nodeId) ? ' fstale' : ''),
                'data-link': link.id, 'data-segment': segment.id, tabindex: '0', role: 'button',
                'aria-label': vlan == null ? 'p2p' : 'VLAN ' + vlan}, s.L.links);
            g.style.setProperty('--vlan-color', vcolor(s.der, vlan));
            el('path', {d, class: 'hit'}, g);
            el('path', {d, class: 'wire'}, g);
            const label = el('g', {class: 'auto-label', 'data-priority': segment.blocked || segment.mismatch ? 1 : 3}, g);
            const caption = segment.mismatch ? `native VLAN ${va.split('|')[2]} ↔ ${vb.split('|')[2]} !`
                : (vlan == null ? 'p2p' : 'VLAN ' + vlan) + (segment.blocked ? ' · ' + s.s('ui_canvas_blocked')
                : segment.unknown ? ' · ' + s.s('ui_canvas_unknown') : '');
            el('text', {x: mx, y: my - 7, class: 'caption'}, label).textContent = caption;
            [[link.a, a, b, segment.aname], [link.b, b, a, segment.bname]].forEach(([end, here, peer, name]) => {
                if (!s.endShown(end)) return;
                const dx = peer.cx - here.cx, dy = peer.cy - here.cy, len = Math.hypot(dx, dy) || 1;
                const d = Math.min(62, len * .32);
                const port = el('g', {class: 'auto-label', 'data-priority': 2}, g);
                el('text', {x: here.cx + dx / len * d, y: here.cy + dy / len * d - 8,
                    class: 'caption l2-port'}, port).textContent = name;
            });
            segment.g = g;
        });
        virtuals.forEach(v => {
            const g = el('g', {class: 'l2-virtual dimmable' + (s.factsFailed(v.orig.id) ? ' fstale' : ''), transform: `translate(${v.cx} ${v.cy})`,
                'data-virtual': v.id, tabindex: '0', role: 'button', 'aria-label': v.orig.name + ' · VLAN ' + v.vlan}, s.L.nodes);
            g.style.setProperty('--vlan-color', vcolor(s.der, v.vlan));
            el('rect', {x: -56, y: -30, width: 112, height: 60, rx: 12}, g);
            const label = el('g', {class: 'virtual-caption'}, g);
            el('text', {x: 0, y: -4, class: 'vtitle'}, label).textContent = 'VLAN ' + v.vlan;
            el('text', {x: 0, y: 16, class: 'vsub'}, label).textContent = v.domain ? v.domain.id + ' · ' + v.domain.members.length : s.s('ui_canvas_disconnected');
            v.g = g;
        });
    }

    prepareLabels() {
        const s = this.stage;
        s.L.nodes.querySelectorAll('.node').forEach(g => {
            const label = el('g', {class: 'auto-label node-caption', 'data-priority': 0}, g);
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
        s.svg.style.setProperty('--ring-width', clamp(s.view.k * .7, .5, 1.1) + 'px');
        s.svg.querySelectorAll('.pdot').forEach(dot => dot.setAttribute('r', 3.2 * inv));
        s.svg.querySelectorAll('[data-fixed-x]').forEach(g => {
            const x = Number(g.dataset.fixedX), y = Number(g.dataset.fixedY);
            g.setAttribute('transform', `translate(${x} ${y}) scale(${inv}) translate(${-x} ${-y})`);
        });
        this.rails.forEach(({link, geometry: g, directions, group}) => {
            const half = Math.max(0, g.len / 2 - Math.min(31, g.len / 4));
            const mx = (g.ax + g.bx) / 2, my = (g.ay + g.by) / 2;
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
                    label.g.style.display = half * 2 * s.view.k >= 60 && (s.lod >= 1 || group.classList.contains('on')) ? '' : 'none';
                    label.text.setAttribute('x', at.x); label.text.setAttribute('y', at.y);
                    Object.entries({x1: start.x, y1: start.y, x2: end.x, y2: end.y}).forEach(([k, v]) => label.lead.setAttribute(k, v));
                });
            });
        });
        this.scheduleLabels();
        const meta = s.r('canvasMeta');
        if (meta && s.model) meta.textContent = s.model.nodes.size + ' · ' + s.s('ui_canvas_objects') + ' / ' + s.model.links.length + ' · ' + s.s('ui_link');
        const status = s.r('canvasFacts');
        if (status) status.textContent = !s.der ? s.s('ui_facts_nodata') : s.factsMeta.stale ? s.s('ui_facts_stale_mark')
            : s.factsMeta.at ? new Date(s.factsMeta.at).toLocaleTimeString() : s.s('ui_facts_ago_now');
        const placeholder = s.r('canvasL4');
        if (placeholder) placeholder.hidden = s.layer !== 'l4';
    }

    select() {
        const s = this.stage, sel = s.sel, D = s.der;
        s.svg.classList.toggle('has-sel', !!sel);
        s.svg.querySelectorAll('.on,.sel').forEach(e => e.classList.remove('on', 'sel'));
        const mark = (attr, id, primary = false) => s.svg.querySelectorAll(`[${attr}="${CSS.escape(String(id))}"]`).forEach(e => e.classList.add(...(primary ? ['on', 'sel'] : ['on'])));
        const nodes = new Set(), links = new Set(), subnets = new Set(), domains = new Set();
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
                let added;
                do {
                    added = false;
                    this.l2.segments.filter(p => p.active && (vertices.has(p.va) || vertices.has(p.vb))).forEach(p => {
                        p.g.classList.add('on');
                        [p.va, p.vb].forEach(id => { if (!vertices.has(id)) { vertices.add(id); added = true; } });
                    });
                } while (added);
                vertices.forEach(id => { mark('data-virtual', id); const peer = this.l2.virtuals.get(id); if (peer) mark('data-node', peer.orig.id); });
            }
            if (v) mark('data-node', v.orig.id);
        } else if (sel.kind === 'link') { links.add(sel.id); mark('data-link', sel.id, true); }
        else if (sel.kind === 'subnet') { subnets.add(sel.id); mark('data-subnet', sel.id, true); }
        else if (sel.kind === 'vlan' && D) { const d = D.l2.domains.find(d => d.id === sel.id); if (d) domains.add(d); }
        subnets.forEach(id => {
            const subnet = D && D.l3.subnets.find(x => x.key === id);
            mark('data-subnet', id);
            if (subnet) subnet.members.forEach(m => nodes.add(m.nodeId));
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
        labels.forEach(g => {
            const name = g.querySelector('.nname');
            if (!name || !visible(name)) return;
            const r = name.getBBox();
            let y = r.y + r.height + 4 * inv;
            g.querySelectorAll('.nsub,.nadj').forEach(t => {
                t.setAttribute('dy', '0'); t.setAttribute('y', y);
                if (visible(t)) y += t.getBBox().height + 4 * inv;
            });
        });
        if (!this.leads) return;
        this.leads.textContent = '';
        const occupied = [...svg.querySelectorAll('.node .ico,.l2-virtual > rect,.virtual-caption,.subnet rect,.duplex-label,.card')]
            .filter(visible).map(box).filter(r => r.w && r.h);
        s.wrap.querySelectorAll('.canvas-hud,.canvas-actions,.details:not([hidden]),.assignments:not([hidden]),.zoom').forEach(e => occupied.push(box(e)));
        labels.map(g => ({g, r: box(g), priority: Number(g.dataset.priority || 2) - (g.closest('.sel') ? 20 : g.closest('.on') ? 10 : 0)}))
            .filter(x => visible(x.g) && x.r.w && x.r.h).sort((a, b) => a.priority - b.priority).forEach(({g, r, priority}) => {
                const offsets = [[0, 0]];
                for (const d of [14, 28, 44, 64, 88]) {
                    offsets.push([0, -d], [0, d], [-d, 0], [d, 0], [-d, -d], [d, -d], [-d, d], [d, d]);
                    if (priority > 1 && d >= 44) break;
                }
                const offset = offsets.find(([dx, dy]) => {
                    const c = {...r, x: r.x + dx, y: r.y + dy};
                    return c.x >= 5 && c.y >= 5 && c.x + c.w <= root.width - 5 && c.y + c.h <= root.height - 5 && !occupied.some(b => overlaps(c, b));
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
