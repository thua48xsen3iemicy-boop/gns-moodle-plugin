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
import {factsStatus, layerHelp, layerIssues} from 'mod_gns3lab/canvasfacts';
import {canvasL2, linkDirections} from 'mod_gns3lab/canvasmodel';

export const STRING_KEYS = [
    'ui_canvas_title', 'ui_canvas_l4', 'ui_canvas_duplex', 'ui_canvas_chassis',
    'ui_canvas_blocked', 'ui_canvas_unknown', 'ui_canvas_disconnected', 'ui_canvas_objects',
    'ui_canvas_card_layers', 'ui_canvas_interface', 'ui_canvas_admin', 'ui_canvas_oper',
    'ui_assignments', 'ui_assignments_empty', 'ui_canvas_practice', 'ui_canvas_nodes',
    'ui_canvas_links', 'ui_canvas_l1_help', 'ui_canvas_l2_help', 'ui_canvas_l3_help',
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
        const zones = el('g', {}, s.L.l2), lines = el('g', {}, s.L.l2);
        this.nodeLayer = el('g', {}, s.L.l2);
        const groups = new Map();
        const color = vlan => ({1: '#8ba6b4', 10: '#60dfaf', 20: '#f0bd73', 30: '#8bb5fa', 99: '#da9fe8'}[vlan] || `hsl(${(Number(vlan) * 47) % 360} 65% 70%)`);
        virtuals.forEach(v => {
            if (!groups.has(v.orig.id)) groups.set(v.orig.id, []);
            groups.get(v.orig.id).push(v);
        });
        groups.forEach(vs => {
            const n = vs[0].orig, x = vs[0].cx - 66, width = vs[vs.length - 1].cx - x + 66;
            const g = el('g', {class: 'l2-chassis dimmable', 'data-node': n.id}, zones);
            el('rect', {x, y: n.cy - 51, width, height: 102, rx: 16}, g);
            const label = el('g', {class: 'auto-label', 'data-priority': 1}, g);
            el('text', {x: x + 11, y: n.cy - 60}, label).textContent = n.name + ' · ' + s.s('ui_canvas_chassis') + ' / ' + vs.length + ' VLAN';
            s.grow(x, n.cy - 70, x + width, n.cy + 54);
        });
        segments.forEach(segment => {
            const {link, va, vb, vlan} = segment;
            const a = virtuals.get(va) || s.model.nodes.get(link.a.nodeId), b = virtuals.get(vb) || s.model.nodes.get(link.b.nodeId);
            const dx = b.cx - a.cx, dy = b.cy - a.cy, length = Math.hypot(dx, dy) || 1;
            const bend = vlan == null ? 0 : segment.tag === 'u' ? -7 : 7;
            const mx = (a.cx + b.cx) / 2 - dy / length * bend, my = (a.cy + b.cy) / 2 + dx / length * bend;
            const d = `M${a.cx} ${a.cy} Q${mx} ${my} ${b.cx} ${b.cy}`;
            const g = el('g', {class: 'link l2-path dimmable' + (vlan == null ? ' l2-p2p' : '') + (!segment.active ? ' down' : '') +
                (segment.blocked ? ' problem' : '') + (segment.mismatch ? ' mismatch' : '') + (segment.unknown ? ' unknown' : ''),
                'data-link': link.id, 'data-segment': segment.id, tabindex: '0', role: 'button',
                'aria-label': s.model.nodes.get(link.a.nodeId).name + ' ↔ ' + s.model.nodes.get(link.b.nodeId).name + ' · ' + (vlan == null ? 'p2p' : 'VLAN ' + vlan)}, lines);
            g.style.setProperty('--vlan-color', color(vlan));
            el('path', {d, class: 'hit'}, g); el('path', {d, class: 'stroke'}, g);
            const label = el('g', {class: 'auto-label', 'data-priority': segment.blocked || segment.mismatch ? 1 : 3}, g);
            const aname = s.endShown(link.a) ? segment.aname : '?', bname = s.endShown(link.b) ? segment.bname : '?';
            const caption = segment.mismatch ? `native VLAN ${va.split('|')[2]} ↔ ${vb.split('|')[2]} !`
                : segment.blocked ? `VLAN ${vlan} · ${s.s('ui_canvas_blocked')} !` : vlan == null ? 'p2p'
                : segment.unknown ? `VLAN ${vlan} · ${s.s('ui_canvas_unknown')}` : aname.includes('.') ? `${aname} · ${vlan}`
                : bname.includes('.') ? `${bname} · ${vlan}` : 'VLAN ' + vlan;
            el('text', {x: mx, y: my - 7, class: 'caption'}, label).textContent = caption;
            segment.g = g;
        });
        virtuals.forEach(v => {
            const g = el('g', {class: 'l2-virtual dimmable' + (v.domain?.vlans.length > 1 ? ' mixed' : '') + (s.factsFailed(v.orig.id) ? ' collection-error' : ''),
                transform: `translate(${v.cx} ${v.cy})`, 'data-virtual': v.id, tabindex: '0', role: 'button', 'aria-label': v.orig.name + ' · VLAN ' + v.vlan}, this.nodeLayer);
            g.style.setProperty('--vlan-color', color(v.vlan));
            el('rect', {x: -56, y: -30, width: 112, height: 60, rx: 12}, g);
            el('circle', {cx: -38, cy: -8, r: 5, class: 'vmark'}, g);
            el('text', {x: -27, y: -4, class: 'vtitle'}, g).textContent = 'VLAN ' + v.vlan;
            el('text', {x: -43, y: 18, class: 'vsub'}, g).textContent = v.domain
                ? v.domain.vlans.length > 1 ? `${v.domain.id} · VLAN ${v.domain.vlans.join('+')} !` : `${v.domain.id} · ${v.domain.members.length} ${s.s('ui_canvas_members')}`
                : s.s('ui_canvas_disconnected');
            v.g = g;
        });
    }

    renderL3() {
        const s = this.stage;
        this.subnets.forEach(x => {
            x.edges = [];
            x.members.forEach(m => {
                const n = s.model.nodes.get(m.nodeId);
                const line = el('line', {x1: n.cx, y1: n.cy, x2: x.cx, y2: x.cy, class: 'subnet-edge dimmable' + (m.down || m.dup ? ' down' : ''),
                    'data-subnet': x.key}, s.L.l3);
                const group = el('g', {class: 'auto-label dimmable', 'data-priority': 2, 'data-subnet': x.key}, s.L.l3);
                const label = el('text', {x: n.cx + (x.cx - n.cx) * .4, y: n.cy + (x.cy - n.cy) * .4 - 5 / s.view.k, class: 'pip'}, group);
                label.textContent = m.iface.name + ' · ' + m.c.ip;
                x.edges.push({line, label, node: n});
            });
            const g = el('g', {class: 'subnet-object dimmable' + (x.issues.length ? ' warn' : ''), transform: `translate(${x.cx} ${x.cy})`,
                'data-subnet': x.key, tabindex: '0', role: 'button', 'aria-label': x.key + ', ' + x.members.length + ' ' + s.s('ui_canvas_interfaces')}, s.L.l3);
            const pill = el('g', {class: 'subnet-pill', transform: `scale(${1 / s.view.k})`}, g);
            el('rect', {x: -78, y: -16, width: 156, height: 32, rx: 9}, pill);
            el('text', {y: 1}, pill).textContent = (x.issues.length ? '! ' : '') + x.key;
            x.g = g; s.grow(x.x - 78, x.y - 16, x.x + 78, x.y + 16);
        });
    }

    positionSubnets() {
        const s = this.stage, {k} = s.view, w = s.wrap.clientWidth, h = s.wrap.clientHeight;
        const busy = [...s.model.nodes.values()].map(n => {
            const p = s.toScreen(n.cx, n.cy), r = Math.max(14, 26 * k); return {x: p.x - r, y: p.y - r, w: 2 * r, h: 2 * r};
        });
        busy.push({x: 0, y: 0, w: Math.min(396, w), h: 168}, {x: 0, y: h - 112, w, h: 112});
        const intersects = (a, b) => a.x < b.x + b.w + 5 && a.x + a.w + 5 > b.x && a.y < b.y + b.h + 5 && a.y + a.h + 5 > b.y;
        this.subnets.forEach(x => {
            if (!x.g) return;
            const origin = s.toScreen(x.x, x.y), offsets = [[0, 0]];
            for (const r of [24, 48, 80, 120, 176, 240]) for (let a = 0; a < 360; a += 45) offsets.push([r * Math.cos(a * Math.PI / 180), r * Math.sin(a * Math.PI / 180)]);
            const offscreen = origin.x < -80 || origin.x > w + 80 || origin.y < -20 || origin.y > h + 20;
            const offset = offscreen ? [0, 0] : offsets.find(([dx, dy]) => {
                const b = {x: origin.x + dx - 78, y: origin.y + dy - 16, w: 156, h: 32};
                return b.x >= 8 && b.y >= 8 && b.x + b.w <= w - 8 && b.y + b.h <= h - 8 && !busy.some(o => intersects(b, o));
            });
            x.g.style.display = offset ? '' : 'none'; x.g.setAttribute('tabindex', offset ? '0' : '-1');
            const [dx, dy] = offset || [0, 0]; x.cx = x.x + dx / k; x.cy = x.y + dy / k;
            x.g.setAttribute('transform', `translate(${x.cx} ${x.cy})`);
            x.g.querySelector('.subnet-pill').setAttribute('transform', `scale(${1 / k})`);
            if (offset && !offscreen) busy.push({x: origin.x + dx - 78, y: origin.y + dy - 16, w: 156, h: 32});
            x.edges.forEach(({line, label, node}) => {
                line.style.display = offset ? '' : 'none'; label.parentNode.style.display = offset ? '' : 'none';
                line.setAttribute('x2', x.cx); line.setAttribute('y2', x.cy);
                label.setAttribute('x', node.cx + (x.cx - node.cx) * .4); label.setAttribute('y', node.cy + (x.cy - node.cy) * .4 - 5 / k);
            });
        });
    }

    renderHud() {
        const s = this.stage, hud = s.r('layerSeg'), w = Math.max(220, Math.min(370, s.wrap.clientWidth - 24));
        const nodes = s.model?.nodes.size || 0, links = s.model?.links.length || 0;
        const key = [w, s.layer, nodes, links].join('/');
        if (this.hudKey === key) return;
        this.hudKey = key; hud.textContent = '';
        el('rect', {x: 14, y: 14, width: w, height: 100, rx: 17, class: 'hud-bg'}, hud);
        const text = (cls, x, y, value) => { const t = el('text', {class: cls, x, y}, hud); t.textContent = value; return t; };
        text('hud-kicker', 29, 36, s.s('ui_canvas_title'));
        text('hud-meta', w - 1, 36, w < 340 ? nodes + ' / ' + links : `${nodes} ${s.s('ui_canvas_nodes')} · ${links} ${s.s('ui_canvas_links')}`).setAttribute('text-anchor', 'end');
        const bw = (w - 38) / 4;
        ['l1', 'l2', 'l3', 'l4'].forEach((layer, i) => {
            const g = el('g', {class: 'layer-button' + (s.layer === layer ? ' active' : ''), tabindex: '0', role: 'button',
                'data-layer': layer, 'aria-label': layerHelp(s, layer), 'aria-pressed': String(s.layer === layer)}, hud);
            el('rect', {x: 29 + i * (bw + 3), y: 46, width: bw, height: 29, rx: 8}, g);
            el('text', {x: 29 + i * (bw + 3) + bw / 2, y: 61}, g).textContent = layer.toUpperCase();
            el('title', {}, g).textContent = layerHelp(s, layer);
            g.onclick = () => s.setLayer(layer);
            g.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); s.setLayer(layer); s.r('layerSeg').querySelector(`[data-layer="${layer}"]`).focus(); } };
        });
        text('hud-note', 29, 95, layerHelp(s, s.layer));
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
        } else if (sel.kind === 'segment' && this.l2) {
            const p = this.l2.segments.find(p => p.id === sel.id);
            if (p) { mark('data-segment', p.id, true); [p.link.a, p.link.b].forEach(e => nodes.add(e.nodeId)); [p.va, p.vb].forEach(id => mark('data-virtual', id)); }
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
        const occupied = [...svg.querySelectorAll('.node .ico,.l2-virtual > rect,.subnet-object rect,.drawing text,.duplex-label,.card')]
            .filter(visible).map(box).filter(r => r.w && r.h);
        s.wrap.querySelectorAll('[data-region=layerSeg],.canvas-actions,[data-region=details] foreignObject,.canvas-panel:not([hidden]),.tasks-panel:not([hidden]),.fact-status,.zoom').forEach(e => occupied.push(box(e)));
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
