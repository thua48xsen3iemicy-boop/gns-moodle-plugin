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
 * The stage of the student interface: the lab topology as SVG with levels of detail, layers, gestures and selection.
 *
 * Ported from prototypes/student-ui.html (levels of detail by zoom, gestures, console previews on the scheme) and
 * from the first viewer (layers L1/L2/L3/OSPF, drawings). The stage knows nothing about sessions and consoles:
 * the lab controller connects them through `stage.hooks`.
 *
 * Hooks (all optional):
 * - select(sel)            the user changed the selection; sel is {kind, id} or null;
 * - details(sel)           the user asked for the details of a link, subnet or adjacency;
 * - console(node)          badge, preview or double click: open the console of the node;
 * - nodeAction(node)       Enter on a node;
 * - diveCandidate(node)    pinch on a phone reached a node that has a console (null: no longer);
 * - diveRelease(node)      the pinch ended over such a node;
 * - closeOverlays()        a tap on the empty scheme: close the details card;
 * - viewChanged()          the view moved or zoomed;
 * - lodChanged(lod)        the level of detail changed;
 * - mobileChanged(mobile)  the page became phone sized or not;
 * - toast(text)            a short message for the user.
 *
 * @module     mod_gns3lab/stage
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

// Straight port of the prototypes; these style rules stay relaxed.
/* eslint-disable curly, max-len, jsdoc/require-jsdoc, no-eq-null */

import {buildModel, clamp, esc, fmtFilters} from 'mod_gns3lab/model';
import {svgEl as el, store, fill} from 'mod_gns3lab/dom';
import {acolor, derive, endIf, endState, hostPart, vcolor} from 'mod_gns3lab/facts';

const KMIN = 0.22, KMAX = 6;

/** A node icon this big on the screen (px) is "deep enough" to dive into its console on a phone. */
const DIVE_PX = 140;

/** On which layers an object can be seen; selecting it from a list switches to the first one. */
const LAYER_OF = {subnet: ['l3'], vlan: ['l2'], adj: ['rt'], link: ['l1', 'l2']};

const LAYERS = ['l1', 'l2', 'l3', 'rt'];

const OK_EL = new Set(['rect', 'ellipse', 'circle', 'line', 'text']);
const OK_AT = new Set(['x', 'y', 'width', 'height', 'rx', 'ry', 'cx', 'cy', 'r', 'x1', 'y1', 'x2', 'y2', 'fill', 'fill-opacity',
    'stroke', 'stroke-opacity', 'stroke-width', 'stroke-dasharray', 'font-size', 'font-weight', 'opacity']);

/**
 * Whitelist sanitizer of a .gns3 drawing: only these shapes and attributes, never innerHTML (the file is untrusted).
 *
 * @param {string} src SVG source of the drawing
 * @returns {{g: SVGElement, w: number, h: number}|null}
 */
const sanitizeDrawing = src => {
    let doc;
    try {
        doc = new DOMParser().parseFromString(String(src || ''), 'image/svg+xml');
    } catch (e) {
        return null;
    }
    const root = doc.documentElement;
    if (!root || root.nodeName !== 'svg' || doc.getElementsByTagName('parsererror').length) {
        return null;
    }
    const g = el('g', {class: 'drawing'});
    for (const c of Array.from(root.children)) {
        if (!OK_EL.has(c.nodeName)) continue;
        const e = el(c.nodeName, {}, g);
        for (const a of Array.from(c.attributes)) {
            if (!OK_AT.has(a.name)) continue;
            if ((a.name === 'fill' || a.name === 'stroke') && !/^(#[0-9a-fA-F]{3,8}|none|[a-z]+)$/.test(a.value)) continue;
            e.setAttribute(a.name, a.value);
        }
        if (c.nodeName === 'text') {
            e.textContent = c.textContent;
            e.setAttribute('class', 'dtext');
            if (!e.hasAttribute('y')) e.setAttribute('y', c.getAttribute('font-size') || 12);
        }
    }
    return {g, w: parseFloat(root.getAttribute('width')) || 0, h: parseFloat(root.getAttribute('height')) || 0};
};

/**
 * Parallel links between one pair of nodes are spread by 16 px.
 *
 * @param {object} m model
 * @returns {Map} link id → {ax, ay, bx, by, dx, dy, len}
 */
const linkGeometry = m => {
    const groups = {}, out = new Map();
    m.links.forEach(l => {
        const key = [l.a.nodeId, l.b.nodeId].sort().join('|');
        (groups[key] = groups[key] || []).push(l);
    });
    Object.values(groups).forEach(list => list.forEach((l, i) => {
        const A = m.nodes.get(l.a.nodeId), B = m.nodes.get(l.b.nodeId);
        let dx = B.cx - A.cx, dy = B.cy - A.cy;
        const len = Math.hypot(dx, dy) || 1;
        dx /= len;
        dy /= len;
        const off = (i - (list.length - 1) / 2) * 16, nx = -dy * off, ny = dx * off;
        out.set(l.id, {ax: A.cx + nx, ay: A.cy + ny, bx: B.cx + nx, by: B.cy + ny, dx, dy, len});
    }));
    return out;
};

/**
 * Text label on the scheme with a background (pill); the background is measured when the label is drawn.
 *
 * @param {Element} parent layer
 * @param {number} x centre x
 * @param {number} y centre y
 * @param {string} text text
 * @param {string} cls class names
 * @param {string|null} linkId link the label belongs to
 * @returns {SVGElement}
 */
const pill = (parent, x, y, text, cls, linkId) => {
    const g = el('g', {class: cls, 'data-link': linkId}, parent);
    const r = el('rect', {rx: 4}, g);
    const t = el('text', {x, y, 'text-anchor': 'middle', 'dominant-baseline': 'central'}, g);
    t.textContent = text;
    let w = 0, h = 14;
    try {
        const b = t.getBBox();
        w = b.width;
        h = b.height;
    } catch (e) {
        w = text.length * 6.4;
    }
    r.setAttribute('x', x - w / 2 - 5);
    r.setAttribute('y', y - h / 2 - 2);
    r.setAttribute('width', w + 10);
    r.setAttribute('height', h + 4);
    return g;
};

/**
 * Interface name as the user types it in a console → a comparable key: letter group and number path.
 *
 * @param {string} v port name
 * @returns {{k: string, num: string}|null}
 */
const portKey = v => {
    const m = /^([a-z]*)\s*(\d+(?:\/\d+)*)$/i.exec(String(v).trim());
    if (!m) return null;
    const p = m[1].toLowerCase();
    return {k: ['g', 'f', 'e', 's', 'l'].find(c => p.startsWith(c)) || p, num: m[2]};
};

export class Stage {
    /**
     * Constructor.
     *
     * @param {HTMLElement} root the application element
     * @param {object} options
     * @param {string} options.prefix id prefix of the template ({{uniqid}})
     * @param {object} options.strings language strings
     */
    constructor(root, {prefix, strings}) {
        this.root = root;
        this.prefix = prefix;
        this.strings = strings;
        this.hooks = {};
        this.wheel = null;
        const r = name => root.querySelector(`[data-region="${name}"]`);
        this.r = r;
        this.wrap = r('wrap');
        this.svg = r('svg');
        this.vp = r('vp');
        this.dots = r('dots');
        this.ov = r('ov');
        this.L = {draw: r('lyrDraw'), links: r('lyrLinks'), labels: r('lyrLabels'), nodes: r('lyrNodes'), cards: r('lyrCards')};
        this.model = null;
        this.fx = null;
        this.der = null;
        this.facts = null;
        this.factsKey = 'ok';
        this.factsLive = false;
        this.liveDoc = null;
        this.factsMeta = {at: null, stale: false};
        this.layer = 'l1';
        this.sel = null;
        this.view = {k: 1, tx: 0, ty: 0};
        this.lod = -1;
        this.anim = null;
        this.statuses = new Map();
        this.statusKnown = false;
        this.guests = null;
        this.badges = new Map();
        this.cards = new Map();
        this.cardTimers = new Map();
        this.bounds = {x: -200, y: -150, w: 400, h: 300};
        this.reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
        this.mq = matchMedia('(max-width: 760px)');
        this.mobile = this.mq.matches;
        this.mq.addEventListener('change', () => {
            this.mobile = this.mq.matches;
            this.setHint();
            this.call('mobileChanged', this.mobile);
        });
        this.ptrs = new Map();
        this.gest = null;
        this.pinch = null;
        this.lpTimer = null;
        this.lastTap = null;
        this.diveCand = null;
        this.chipT = null;
        this.hintT = null;
        this.lastSize = null;
        this.needFit = false;
        this.bindControls();
        this.bindGestures();
        this.bindKeys();
        new ResizeObserver(() => this.resized()).observe(this.wrap);
    }

    /**
     * Calls a hook if the controller set it.
     *
     * @param {string} name hook name
     * @param {...*} args arguments
     * @returns {*}
     */
    call(name, ...args) {
        return this.hooks[name] ? this.hooks[name](...args) : undefined;
    }

    /**
     * Language string.
     *
     * @param {string} key key
     * @returns {string}
     */
    s(key) {
        return this.strings[key] || key;
    }

    /**
     * Status of a node on the scheme, null while nothing is known (the preview of a lab that is not running).
     *
     * @param {object} n node
     * @returns {string|null}
     */
    nodeStatus(n) {
        return this.statusKnown ? (this.statuses.get(n.id) || n.status || 'stopped') : null;
    }

    /* ---------------- loading ---------------- */

    /**
     * Shows a lab.
     *
     * @param {object} doc .gns3 document
     * @param {object} [options]
     * @param {Map} [options.statuses] node id → started|stopped|suspended; absent: statuses are unknown
     * @param {object} [options.guests] Proxmox guests by node id (demo)
     * @param {object} [options.facts] {ok, broken} facts documents (demo); live facts come later with setFacts()
     * @param {boolean} [options.keepView] keep zoom and position
     */
    load(doc, {statuses = null, guests = null, facts = null, keepView = false} = {}) {
        if (!doc || typeof doc !== 'object' || !doc.topology) {
            throw new Error('Not a GNS3 topology');
        }
        this.model = buildModel(doc);
        this.statuses = statuses || new Map();
        this.statusKnown = !!statuses;
        this.guests = guests;
        this.facts = facts && facts.ok ? facts : null;
        this.factsKey = 'ok';
        this.factsLive = false;
        this.liveDoc = null;
        this.setFactsMeta({at: null, stale: false});
        this.sel = null;
        this.derive();
        if (!this.facts) this.layer = 'l1';
        this.syncLayerUI();
        this.render();
        const box = this.wrap.getBoundingClientRect();
        this.needFit = !box.width || !box.height;
        if (!keepView && !this.needFit) {
            this.fit(false);
        }
        this.setHint();
    }

    /**
     * Computes the layers from the facts of the current key.
     */
    derive() {
        const doc = this.facts ? this.facts[this.factsKey] || this.facts.ok : null;
        const res = doc ? derive(this.model, this.factsLive ? this.withLiveStatuses(doc) : doc) : null;
        this.fx = res ? res.fx : null;
        this.der = res ? res.der : null;
    }

    /**
     * Live facts with the node statuses of the session: the status in a snapshot is for reference only
     * and may be old (CONTRACTS §2.7).
     *
     * @param {object} doc facts document
     * @returns {object} a copy with the statuses replaced
     */
    withLiveStatuses(doc) {
        if (!this.statusKnown) return doc;
        const nodes = {};
        Object.entries(doc.nodes || {}).forEach(([id, f]) => {
            nodes[id] = this.statuses.has(id) ? Object.assign({}, f, {status: this.statuses.get(id)}) : f;
        });
        return Object.assign({}, doc, {nodes});
    }

    /**
     * Updates the node statuses without redrawing the scheme; with live facts the layers follow them.
     *
     * @param {Map} statuses node id → status
     */
    setStatuses(statuses) {
        const before = this.statuses;
        this.statuses = statuses;
        this.statusKnown = true;
        if (!this.model) return;
        const same = before.size === statuses.size && [...statuses].every(([id, st]) => before.get(id) === st);
        if (this.factsLive && this.facts && !same) {
            this.factsChanged();
            return;
        }
        this.model.nodes.forEach(n => this.paintStatus(n));
    }

    paintStatus(n) {
        if (!n.g) return;
        const st = this.nodeStatus(n);
        n.g.setAttribute('class', n.g.getAttribute('class').replace(/st-\w+/, 'st-' + (st || 'none')));
        n.g.setAttribute('aria-label', this.nodeLabel(n));
    }

    nodeLabel(n) {
        const st = this.nodeStatus(n);
        return [n.name, this.s('ui_cat_' + n.cat).toLowerCase(), st ? this.s('ui_st_' + st) : null].filter(Boolean).join(', ');
    }

    /* ---------------- layers ---------------- */

    /**
     * Switches the layer.
     *
     * @param {string} layer l1, l2, l3 or rt
     */
    setLayer(layer) {
        if (!LAYERS.includes(layer) || (layer !== 'l1' && !this.der) || layer === this.layer) return;
        this.layer = layer;
        this.syncLayerUI();
        const s = this.sel;
        if (s && LAYER_OF[s.kind] && !LAYER_OF[s.kind].includes(layer)) this.sel = null;
        this.render();
        this.call('select', this.sel);
        this.call('layerChanged', layer);
    }

    /**
     * Changes the demo facts document (config without and with mistakes).
     *
     * @param {string} key ok or broken
     */
    setFactsKey(key) {
        this.factsKey = key;
        this.factsChanged();
    }

    /**
     * Shows the live facts of the session, keeping the model and the view (docs/live-state.md).
     *
     * The layers L2, L3 and OSPF are offered as soon as a snapshot has nodes; an empty one (rev 0) means "no data".
     *
     * @param {object|null} doc facts document gns3lab.facts/1, null while there is none
     */
    setFacts(doc) {
        this.factsLive = true;
        this.liveDoc = doc || null;
        this.facts = doc && doc.nodes && Object.keys(doc.nodes).length ? {ok: doc} : null;
        this.factsKey = 'ok';
        if (!this.facts) this.layer = 'l1';
        this.factsChanged();
    }

    /**
     * When the live facts were collected and whether they are out of date; the data is shown all the same.
     *
     * @param {object} meta {at: ms or null, stale: boolean}
     */
    setFactsMeta(meta) {
        this.factsMeta = meta;
        const seg = this.r('layerSeg');
        if (!seg) return;
        seg.classList.toggle('stale', !!meta.stale);
        if (meta.stale) seg.title = this.s('ui_facts_stale_mark');
        else seg.removeAttribute('title');
    }

    /**
     * Derives the layers again after the facts changed and drops a selection that is gone.
     */
    factsChanged() {
        this.derive();
        this.syncLayerUI();
        const s = this.sel, D = this.der;
        if (s && ['subnet', 'vlan', 'adj'].includes(s.kind) && (!D || (s.kind === 'subnet' && !D.l3.subnets.some(x => x.key === s.id)) || (s.kind === 'vlan' && !D.l2.domains.some(x => x.id === s.id)) || (s.kind === 'adj' && !D.rt.edges.some(x => x.id === s.id)))) this.sel = null;
        this.render();
        this.call('select', this.sel);
        this.call('layerChanged', this.layer);
    }

    /**
     * Whether the last collection of the facts of a node failed: its facts are drawn pale.
     *
     * @param {string} nodeId node id
     * @returns {boolean}
     */
    factsFailed(nodeId) {
        const f = this.fx && this.fx.nodes.get(nodeId);
        return !!(f && f.err);
    }

    syncLayerUI() {
        const seg = this.r('layerSeg');
        if (!seg) return;
        const has = !!this.der;
        seg.hidden = !has;
        seg.querySelectorAll('button').forEach(b => {
            b.setAttribute('aria-pressed', String(b.dataset.layer === this.layer));
            b.disabled = b.dataset.layer !== 'l1' && !has;
        });
        const fs = this.r('factsSel');
        if (fs) {
            // The demo switch between the two fixtures; live facts are what they are.
            fs.hidden = !(has && !this.factsLive && this.facts && this.facts.broken);
            fs.value = this.factsKey;
        }
    }

    /* ---------------- rendering ---------------- */

    render() {
        const m = this.model;
        if (!m) return;
        for (const k in this.L) this.L[k].textContent = '';
        const b = {x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity};
        this.grow = (x0, y0, x1, y1) => {
            b.x0 = Math.min(b.x0, x0);
            b.y0 = Math.min(b.y0, y0);
            b.x1 = Math.max(b.x1, x1);
            b.y1 = Math.max(b.y1, y1);
        };
        this.renderDrawings();
        const geo = linkGeometry(m), L = this.layer, D = this.der;
        if (L === 'l3' || L === 'rt') geo.forEach(g => el('line', {class: 'wire ghost', x1: g.ax, y1: g.ay, x2: g.bx, y2: g.by}, this.L.links));
        else m.links.forEach(l => (L === 'l2' && D ? this.drawLinkL2(l, geo.get(l.id)) : this.drawLink(l, geo.get(l.id))));
        if (L === 'l3' && D) this.drawL3();
        if (L === 'rt' && D) this.drawRT();
        this.renderNodes();
        this.bounds = isFinite(b.x0) ? {x: b.x0, y: b.y0, w: b.x1 - b.x0, h: b.y1 - b.y0} : {x: -200, y: -150, w: 400, h: 300};
        this.applySel();
        this.view.k = this.view.k || 1;
        this.applyView();
        this.cards.forEach((_, id) => this.scheduleCard(id));
    }

    renderDrawings() {
        [...this.model.drawings].sort((a, b) => (a.z || 0) - (b.z || 0)).forEach(d => {
            const r = sanitizeDrawing(d.svg);
            if (!r) return;
            const x = +d.x || 0, y = +d.y || 0;
            r.g.setAttribute('transform', `translate(${x} ${y})` + (d.rotation ? ` rotate(${+d.rotation})` : ''));
            this.L.draw.appendChild(r.g);
            if (r.w && r.h) this.grow(x, y, x + r.w, y + r.h);
        });
    }

    /**
     * Whether the port at a link end is drawn down: the state comes from L1, a switch port repeats its link end.
     *
     * @param {object} e end of a link of the model
     * @returns {boolean}
     */
    endDown(e) {
        const end = endState(this.der, e);
        return !!(end && end.down);
    }

    /**
     * Layer L1: wires with port names (level 1) and addresses (level 2).
     *
     * @param {object} l Link of the topology model.
     * @param {object} g Calculated link geometry.
     */
    drawLink(l, g) {
        const m = this.model, fx = this.fx, a = l.a, b = l.b;
        const ia = endIf(fx, a), ib = endIf(fx, b);
        const f1 = this.der && this.der.l1.links.get(l.id);
        const down = !!(f1 && f1.down) && !l.suspend;
        const name = `${m.nodes.get(a.nodeId).name} ${a.text} — ${m.nodes.get(b.nodeId).name} ${b.text}`;
        const G = el('g', {class: 'link' + (l.suspend ? ' suspended' : '') + (down ? ' down' : ''), 'data-link': l.id, tabindex: '0', role: 'button',
            'aria-label': fill(this.s('ui_link_aria'), name)}, this.L.links);
        el('line', {x1: g.ax, y1: g.ay, x2: g.bx, y2: g.by, class: 'hit'}, G);
        el('line', {x1: g.ax, y1: g.ay, x2: g.bx, y2: g.by, class: 'wire'}, G);
        const fl = fmtFilters(l.filters);
        if (fl.length) {
            const mx = (g.ax + g.bx) / 2, my = (g.ay + g.by) / 2, f = el('g', {class: 'l1'}, G);
            el('circle', {cx: mx, cy: my, r: 8, class: 'filt'}, f);
            el('text', {x: mx, y: my + 2.8, class: 'filt-t'}, f).textContent = 'ms';
            el('title', {}, f).textContent = fill(this.s('ui_filter_on'), fl.map(x => x.k).join(', '));
        }
        const d = Math.min(58, g.len * 0.36);
        [[a, ia, g.ax, g.ay, g.dx, g.dy], [b, ib, g.bx, g.by, -g.dx, -g.dy]].forEach(([e, ifc, x, y, ux, uy]) => {
            const bad = this.endDown(e), old = this.factsFailed(e.nodeId) ? ' fstale' : '';
            el('circle', {cx: x + ux * 31, cy: y + uy * 31, r: 3.2, class: 'pdot l1' + (bad ? ' down' : '') + old}, G);
            const px = x + ux * d, py = y + uy * d;
            el('text', {x: px, y: py + 3, class: 'plabel l1'}, G).textContent = e.text;
            if (ifc && ifc.ipv4 && ifc.ipv4.length) el('text', {x: px, y: py + 3, dy: '1.35em', class: 'pip l2' + old}, G).textContent = ifc.ipv4[0];
        });
    }

    /**
     * Layer L2: the wire has the colour of the VLAN that really passes it; several stripes mean a trunk.
     *
     * @param {object} l Link of the topology model.
     * @param {object} g Calculated link geometry.
     */
    drawLinkL2(l, g) {
        const D = this.der, f1 = D.l1.links.get(l.id), down = !!(f1 && f1.down);
        const r2 = D.l2.links.get(l.id);
        let cls = 'link dimmable' + (l.suspend ? ' suspended' : '') + (down && !l.suspend ? ' down' : '');
        if (r2 && !down) cls += ' ' + r2.kind + (r2.known && !r2.tags.length ? ' dead' : '');
        const G = el('g', {class: cls, 'data-link': l.id}, this.L.links);
        const line = (o, extra) => el('line', Object.assign({x1: g.ax - g.dy * o, y1: g.ay + g.dx * o, x2: g.bx - g.dy * o, y2: g.by + g.dx * o}, extra), G);
        line(0, {class: 'hit'});
        if (r2 && !down && r2.vlans.length && r2.kind !== 'p2p') {
            const k = r2.vlans.length, step = k > 1 ? 3.4 : 0;
            r2.vlans.forEach((v, j) => line((j - (k - 1) / 2) * step, {class: 'wire vl', style: `stroke:${vcolor(D, v)}`}));
        } else line(0, {class: 'wire'});
        const mx = (g.ax + g.bx) / 2, my = (g.ay + g.by) / 2;
        if (down) pill(this.L.labels, mx, my, 'down', 'bpill dimmable', l.id);
        else if (r2.issues.length) pill(this.L.labels, mx, my, '⚠ ' + (r2.vlans.length ? r2.vlans.join(' · ') : '—'), 'wpill dimmable', l.id);
        else if (r2.known && !r2.tags.length) pill(this.L.labels, mx, my, '∅', 'bpill dimmable', l.id);
        else if (r2.kind === 'trunk' && g.len > 240) pill(this.L.labels, mx, my, r2.vlans.join(' · '), 'vpill dimmable', l.id);
        const d = Math.min(46, g.len * 0.32);
        [[l.a, 1, r2.ia], [l.b, -1, r2.ib]].forEach(([e, s, i]) => {
            const bad = this.endDown(e);
            const sp = i && i.switchport, text = sp ? e.text + (sp.mode === 'access' ? ' · ' + sp.vlan : sp.mode === 'none' ? ' · —' : ' · T') : e.text;
            pill(this.L.labels, (s > 0 ? g.ax : g.bx) + s * g.dx * d, (s > 0 ? g.ay : g.by) + s * g.dy * d, text, 'ppill l1 dimmable' + (bad ? ' bad' : '') + (this.factsFailed(e.nodeId) ? ' fstale' : ''), l.id);
        });
    }

    /** Layer L3: subnets as separate objects, the interfaces with addresses are tied to them. */
    drawL3() {
        const m = this.model, S = this.der.l3;
        S.subnets.forEach(s => s.members.forEach(mb => {
            const N = m.nodes.get(mb.nodeId);
            const G = el('g', {class: 'edge dimmable' + (mb.down ? ' down' : '') + (mb.dup ? ' dup' : ''), 'data-subnet': s.key}, this.L.links);
            el('line', {class: 'hit', x1: N.cx, y1: N.cy, x2: s.x, y2: s.y}, G);
            el('line', {class: 'wire', x1: N.cx, y1: N.cy, x2: s.x, y2: s.y}, G);
            const dx = s.x - N.cx, dy = s.y - N.cy, len = Math.hypot(dx, dy) || 1, d = Math.min(dy / len > 0.5 ? 66 : 50, len * 0.5);
            pill(this.L.labels, N.cx + dx / len * d, N.cy + dy / len * d, hostPart(mb.c), 'ppill l1 dimmable' + (mb.dup || mb.down ? ' bad' : '') + (this.factsFailed(mb.nodeId) ? ' fstale' : ''), null).setAttribute('data-subnet', s.key);
        }));
        S.subnets.forEach(s => {
            pill(this.L.labels, s.x, s.y, (s.issues.length ? '⚠ ' : '') + s.key, 'subnet dimmable' + (s.issues.length ? ' warn' : ''), null).setAttribute('data-subnet', s.key);
            this.grow(s.x - 60, s.y - 14, s.x + 60, s.y + 14);
        });
    }

    /** Layer OSPF: adjacency edges coloured by area; a missing adjacency is a red dashed line. */
    drawRT() {
        const m = this.model, R = this.der.rt;
        R.edges.forEach(e => {
            const A = m.nodes.get(e.a), B = m.nodes.get(e.b);
            const G = el('g', {class: 'adj dimmable' + (e.miss ? ' miss' : ''), 'data-adj': e.id}, this.L.links);
            el('line', {class: 'hit', x1: A.cx, y1: A.cy, x2: B.cx, y2: B.cy}, G);
            el('line', {class: 'wire', x1: A.cx, y1: A.cy, x2: B.cx, y2: B.cy, style: e.miss ? null : `stroke:${acolor(this.der, e.area)}`}, G);
            const text = e.miss ? '✕' : `area ${e.area}` + (e.state !== 'full' ? ' · ' + e.state : '');
            pill(this.L.labels, (A.cx + B.cx) / 2, (A.cy + B.cy) / 2, text, (e.miss ? 'bpill' : 'vpill') + ' dimmable', null).setAttribute('data-adj', e.id);
        });
    }

    subtitle(n) {
        const f = this.fx && this.fx.nodes.get(n.id);
        if (!f) return '';
        if (f.rid) return 'RID ' + f.rid;
        const i = (f.ifs || []).find(x => x.ipv4 && x.ipv4.length);
        if (i) return i.ipv4[0];
        if (f.vlans && f.vlans.length) return 'VLAN ' + f.vlans.map(v => v.id).join(', ');
        return '';
    }

    renderNodes() {
        const L = this.layer, D = this.der;
        this.model.nodes.forEach(n => {
            const st = this.nodeStatus(n);
            const ghost = !!D && ((L === 'l3' && !D.l3.nodes.has(n.id)) || (L === 'rt' && !D.rt.nodes.has(n.id)));
            const g = el('g', {class: 'node dimmable st-' + (st || 'none') + (ghost ? ' ghost' : '') + (this.factsFailed(n.id) ? ' fstale' : ''), transform: `translate(${n.cx} ${n.cy})`,
                'data-node': n.id, tabindex: '0', role: 'button', 'aria-label': ''}, this.L.nodes);
            n.g = g;
            g.setAttribute('aria-label', this.nodeLabel(n));
            el('circle', {r: 31, class: 'halo'}, g);
            el('use', {href: `#${this.prefix}-ico-${n.cat}`, x: -22, y: -22, width: 44, height: 44, class: 'ico'}, g);
            el('circle', {cx: 19, cy: -17, r: 5.5, class: 'sdot'}, g);
            el('text', {y: 25, class: 'nname'}, g).textContent = n.name;
            const f = this.fx && this.fx.nodes.get(n.id);
            const sub = this.subtitle(n);
            if (sub) el('text', {y: 25, dy: '1.9em', class: 'nsub l2'}, g).textContent = sub;
            if (f && f.adj && f.adj.length) {
                const full = f.adj.filter(x => x.state === 'full').length;
                el('text', {y: 25, dy: '3.6em', class: 'nadj l2' + (full < f.adj.length ? ' warn' : '')}, g).textContent = `OSPF ${full}/${f.adj.length} FULL`;
            }
            const bd = el('g', {class: 'cbadge', transform: 'translate(-22 -21)'}, g);
            el('rect', {x: -10, y: -7.5, width: 20, height: 15, rx: 3.5}, bd);
            el('text', {y: 3}, bd).textContent = '>_';
            this.paintBadge(n);
            const gs = this.statusKnown && this.guests && this.guests[n.id];
            if (gs) {
                const vm = gs.items.filter(x => x.type === 'qemu').length, ct = gs.items.filter(x => x.type === 'lxc').length;
                pill(this.L.nodes, n.cx, n.cy + 61, `${vm} VM · ${ct} CT`, 'gpill', null);
            } else if (L === 'rt' && f && f.rid && !ghost) pill(this.L.nodes, n.cx, n.cy + 60, 'RID ' + f.rid, 'rpill', null);
            this.grow(n.cx - 40, n.cy - 36, n.cx + 40, n.cy + (gs ? 72 : 52));
        });
    }

    /**
     * Console badge of a node: has a console, new output, the window is minimized.
     *
     * @param {string} nodeId node id
     * @param {object|null} flags {has, unread, min} or null to clear
     */
    setBadge(nodeId, flags) {
        if (flags) this.badges.set(nodeId, flags);
        else this.badges.delete(nodeId);
        const n = this.model && this.model.nodes.get(nodeId);
        if (n) this.paintBadge(n);
    }

    paintBadge(n) {
        if (!n.g) return;
        const f = this.badges.get(n.id) || {};
        n.g.classList.toggle('has-con', !!f.has);
        n.g.classList.toggle('unread', !!f.unread);
        n.g.classList.toggle('minimized', !!f.min);
    }

    /* ---------------- console previews on the scheme ---------------- */

    /**
     * Sets the preview of a console on the scheme: the last lines of its output.
     * It is drawn from the level "Addresses" on, and only while the console is not on the screen.
     *
     * @param {string} nodeId node id
     * @param {object|null} data {lines: string[]|function, unread: boolean} or null to remove
     */
    setCard(nodeId, data) {
        if (data) this.cards.set(nodeId, data);
        else this.cards.delete(nodeId);
        this.scheduleCard(nodeId);
    }

    scheduleCard(nodeId) {
        if (this.cardTimers.has(nodeId)) return;
        this.cardTimers.set(nodeId, setTimeout(() => {
            this.cardTimers.delete(nodeId);
            this.drawCard(nodeId);
        }, 120));
    }

    drawCard(nodeId) {
        this.L.cards.querySelectorAll('.card').forEach(c => { if (c.getAttribute('data-card') === nodeId) c.remove(); });
        const data = this.cards.get(nodeId), n = this.model && this.model.nodes.get(nodeId);
        if (!data || !n || this.lod < 2) return;
        const g = el('g', {class: 'card', 'data-card': nodeId}, this.L.cards);
        const tail = (typeof data.lines === 'function' ? data.lines() : data.lines).slice(-14);
        while (tail.length && !tail[0].trim()) tail.shift();
        const variants = [{cls: 'l2x', n: 5, fs: 6.4, w: 158}, {cls: 'l3', n: 12, fs: 4.3, w: 150}];
        for (const v of variants) {
            const sub = el('g', {class: v.cls, transform: `translate(${n.cx + 36} ${n.cy - 30})`}, g);
            const rows = tail.slice(-v.n), lh = v.fs * 1.32, max = Math.floor(v.w / (v.fs * 0.6)) - 1, hh = v.fs * 1.9;
            el('path', {d: `M-4 ${hh / 2} L0 ${hh / 2 - 3} L0 ${hh / 2 + 3} Z`, class: 'cpt'}, sub);
            el('rect', {x: 0, y: 0, width: v.w, height: hh + rows.length * lh + v.fs * 0.9, rx: 3.5, class: 'cbg'}, sub);
            el('text', {x: v.fs * 0.8, y: v.fs * 1.35, class: 'chd', 'font-size': v.fs * 0.86}, sub).textContent = `${n.name} · ${this.s('ui_console')}` + (data.unread ? ' · ' + this.s('ui_new') : '');
            rows.forEach((row, i) => {
                const t = el('text', {x: v.fs * 0.8, y: hh + (i + 0.85) * lh, class: 'cln', 'font-size': v.fs}, sub);
                t.textContent = row.length > max ? row.slice(0, max - 1) + '…' : row;
            });
        }
    }

    /* ---------------- view: zoom, position, levels of detail ---------------- */

    lodOf(k) {
        const s = 44 * k;
        return s < 34 ? 0 : s < 70 ? 1 : s < 120 ? 2 : 3;
    }

    applyView() {
        const v = this.view;
        this.vp.setAttribute('transform', `translate(${v.tx} ${v.ty}) scale(${v.k})`);
        this.dots.setAttribute('patternTransform', `translate(${v.tx} ${v.ty}) scale(${v.k})`);
        this.svg.style.setProperty('--inv', (1 / v.k).toFixed(4));
        const nl = this.lodOf(v.k);
        if (nl !== this.lod) {
            this.lod = nl;
            this.svg.dataset.lod = nl;
            const name = this.r('lodName'), bar = this.r('lodBar');
            if (name) name.textContent = this.s('ui_lod' + nl);
            if (bar) Array.from(bar.children).forEach((i, j) => i.classList.toggle('on', j <= nl));
            this.cards.forEach((_, id) => this.scheduleCard(id));
            this.call('lodChanged', nl);
        }
        this.call('viewChanged');
    }

    toScreen(x, y) {
        return {x: this.view.tx + this.view.k * x, y: this.view.ty + this.view.k * y};
    }

    fitTarget() {
        const r = this.wrap.getBoundingClientRect(), b = this.bounds, pad = this.mobile ? 14 : 36;
        const k = clamp(Math.min((r.width - 2 * pad) / b.w, (r.height - 2 * pad) / b.h), KMIN, 2.2);
        return {k, tx: r.width / 2 - k * (b.x + b.w / 2), ty: r.height / 2 - k * (b.y + b.h / 2)};
    }

    fit(animate = true) {
        this.animateTo(this.fitTarget(), animate ? 320 : 0);
    }

    stopAnim() {
        if (this.anim) {
            cancelAnimationFrame(this.anim);
            this.anim = null;
        }
    }

    animateTo(t, ms) {
        this.stopAnim();
        if (this.reduced || ms === 0) {
            Object.assign(this.view, t);
            this.applyView();
            return;
        }
        const s = {...this.view}, t0 = performance.now(), dur = ms || 320, v = this.view;
        const step = now => {
            const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 3);
            v.k = s.k + (t.k - s.k) * e;
            v.tx = s.tx + (t.tx - s.tx) * e;
            v.ty = s.ty + (t.ty - s.ty) * e;
            this.applyView();
            this.anim = p < 1 ? requestAnimationFrame(step) : null;
        };
        this.anim = requestAnimationFrame(step);
    }

    zoomAt(sx, sy, f) {
        const v = this.view, k = clamp(v.k * f, KMIN, KMAX), r = k / v.k;
        v.tx = sx - (sx - v.tx) * r;
        v.ty = sy - (sy - v.ty) * r;
        v.k = k;
    }

    zoomBy(f) {
        const r = this.wrap.getBoundingClientRect(), v = this.view, k = clamp(v.k * f, KMIN, KMAX), q = k / v.k;
        this.animateTo({k, tx: r.width / 2 - (r.width / 2 - v.tx) * q, ty: r.height / 2 - (r.height / 2 - v.ty) * q}, 260);
    }

    zoomToNode(n, k, anchorY) {
        const r = this.wrap.getBoundingClientRect();
        k = k || 2.3;
        this.animateTo({k, tx: r.width / 2 - k * n.cx, ty: (anchorY != null ? anchorY : r.height / 2) - k * n.cy}, 360);
    }

    zoomToLink(l) {
        const m = this.model, r = this.wrap.getBoundingClientRect(), A = m.nodes.get(l.a.nodeId), B = m.nodes.get(l.b.nodeId);
        const k = clamp(Math.min(r.width / (Math.abs(A.cx - B.cx) + 160), r.height / (Math.abs(A.cy - B.cy) + 160)), KMIN, 3);
        this.animateTo({k, tx: r.width / 2 - k * (A.cx + B.cx) / 2, ty: r.height / 2 - k * (A.cy + B.cy) / 2}, 360);
    }

    /**
     * On a phone, scrolls the scheme so that the point is not under the card at the bottom.
     *
     * @param {number} x scene x
     * @param {number} y scene y
     */
    revealAbove(x, y) {
        if (!this.mobile) return;
        const r = this.wrap.getBoundingClientRect(), p = this.toScreen(x, y), want = r.height * 0.24, v = this.view;
        if (Math.abs(p.y - want) > 30 || p.x < 20 || p.x > r.width - 20) this.animateTo({k: v.k, tx: v.tx + (r.width / 2 - p.x), ty: v.ty + (want - p.y)}, 300);
    }

    nodeNear(sx, sy, maxPx) {
        let best = null, bd = maxPx;
        this.model.nodes.forEach(n => {
            const p = this.toScreen(n.cx, n.cy), d = Math.hypot(p.x - sx, p.y - sy);
            if (d < bd) {
                bd = d;
                best = n;
            }
        });
        return best;
    }

    resized() {
        const r = this.wrap.getBoundingClientRect();
        if (this.needFit && this.model && r.width && r.height) {
            this.needFit = false;
            this.fit(false);
        }
        if (this.lastSize && r.width && r.height) {
            this.view.tx += (r.width - this.lastSize.w) / 2;
            this.view.ty += (r.height - this.lastSize.h) / 2;
            this.applyView();
        }
        if (r.width && r.height) this.lastSize = {w: r.width, h: r.height};
    }

    /* ---------------- selection ---------------- */

    /**
     * Selects an object of the scheme.
     *
     * @param {object|null} s {kind: node|link|subnet|vlan|adj, id} or null
     * @param {boolean} [quiet] do not show the hint chip
     */
    select(s, quiet = false) {
        if (s && LAYER_OF[s.kind] && !LAYER_OF[s.kind].includes(this.layer)) {
            this.layer = LAYER_OF[s.kind][0];
            this.syncLayerUI();
            this.sel = s;
            this.render();
            this.call('layerChanged', this.layer);
        }
        this.sel = s;
        this.applySel();
        if (quiet) {
            const chip = this.r('chip');
            if (chip) chip.hidden = true;
        } else {
            this.updateChip();
        }
        this.call('select', s);
    }

    applySel() {
        const s = this.sel, m = this.model, D = this.der, svg = this.svg;
        const dim = !!s && (this.layer !== 'l1' || ['subnet', 'vlan', 'adj'].includes(s.kind));
        svg.classList.toggle('has-sel', dim);
        svg.querySelectorAll('.on,.sel').forEach(e => e.classList.remove('on', 'sel'));
        if (!s) return;
        const mark = (attr, idv, cls) => svg.querySelectorAll(`[${attr}="${CSS.escape(idv)}"]`).forEach(e => e.classList.add(...cls));
        if (s.kind === 'node') {
            mark('data-node', s.id, ['on', 'sel']);
            if (!dim) return;
            if (this.layer === 'l3' && D) D.l3.subnets.forEach(x => { if (x.members.some(mb => mb.nodeId === s.id)) mark('data-subnet', x.key, ['on']); });
            else if (this.layer === 'rt' && D) D.rt.edges.forEach(e => { if (e.a === s.id || e.b === s.id) { mark('data-adj', e.id, ['on']); mark('data-node', e.a === s.id ? e.b : e.a, ['on']); } });
            else m.links.forEach(l => {
                if (l.a.nodeId === s.id || l.b.nodeId === s.id) {
                    mark('data-link', l.id, ['on']);
                    mark('data-node', l.a.nodeId === s.id ? l.b.nodeId : l.a.nodeId, ['on']);
                }
            });
        } else if (s.kind === 'subnet' && D) {
            const x = D.l3.subnets.find(v => v.key === s.id);
            if (!x) return;
            mark('data-subnet', x.key, ['on', 'sel']);
            x.members.forEach(mb => mark('data-node', mb.nodeId, ['on']));
        } else if (s.kind === 'vlan' && D) {
            const d = D.l2.domains.find(v => v.id === s.id);
            if (!d) return;
            d.links.forEach(idv => mark('data-link', idv, ['on', 'sel']));
            d.nodes.forEach(idv => mark('data-node', idv, ['on']));
        } else if (s.kind === 'adj' && D) {
            const e = D.rt.edges.find(v => v.id === s.id);
            if (!e) return;
            mark('data-adj', e.id, ['on', 'sel']);
            mark('data-node', e.a, ['on']);
            mark('data-node', e.b, ['on']);
        } else if (s.kind === 'link') {
            const l = m.links.find(x => x.id === s.id);
            if (!l) return;
            mark('data-link', l.id, ['on', 'sel']);
            mark('data-node', l.a.nodeId, ['on']);
            mark('data-node', l.b.nodeId, ['on']);
            const g = this.L.links.querySelector(`.link[data-link="${CSS.escape(l.id)}"]`);
            if (g) this.L.links.appendChild(g);
        }
    }

    /** Hint under the finger: what the selected node is and how to open its actions. */
    updateChip() {
        const chip = this.r('chip'), s = this.sel, n = s && s.kind === 'node' && this.model.nodes.get(s.id);
        clearTimeout(this.chipT);
        if (!chip) return;
        if (!n) {
            chip.hidden = true;
            return;
        }
        const st = this.nodeStatus(n);
        chip.innerHTML = `<b>${esc(n.name)}</b> <span>· ${esc(this.s('ui_cat_' + n.cat).toLowerCase())}${st ? ' · ' + esc(this.s('ui_st_' + st)) : ''} · ${esc(this.s(this.mobile ? 'ui_chip_touch' : 'ui_chip_mouse'))}</span>`;
        chip.hidden = false;
        this.chipT = setTimeout(() => { chip.hidden = true; }, 3800);
    }

    /**
     * A port name that was clicked in a console: shows the link of that port.
     *
     * @param {string} nodeId node id
     * @param {string} port port name as in the console
     */
    showPort(nodeId, port) {
        const want = portKey(port);
        if (!want || !this.model) return;
        const same = end => {
            const k = portKey(end.text);
            return k && k.num === want.num && (k.k === want.k || !k.k);
        };
        const l = this.model.links.find(x => (x.a.nodeId === nodeId && same(x.a)) || (x.b.nodeId === nodeId && same(x.b)));
        if (!l) {
            this.call('toast', fill(this.s('ui_port_unlinked'), port));
            return;
        }
        if (this.layer !== 'l1' && this.layer !== 'l2') this.setLayer('l1');
        this.select({kind: 'link', id: l.id});
        this.call('details', this.sel);
    }

    /* ---------------- controls ---------------- */

    bindControls() {
        const r = this.r;
        r('zoomIn').onclick = () => this.zoomBy(1.6);
        r('zoomOut').onclick = () => this.zoomBy(1 / 1.6);
        r('zoomFit').onclick = () => this.fit();
        const seg = r('layerSeg');
        if (seg) seg.addEventListener('click', e => {
            const b = e.target.closest('button[data-layer]');
            if (b && !b.disabled) this.setLayer(b.dataset.layer);
        });
        const fs = r('factsSel');
        if (fs) fs.addEventListener('change', e => this.setFactsKey(e.target.value));
    }

    setHint() {
        const h = this.r('hint');
        if (!h) return;
        if (store.get('hinted', false)) {
            h.hidden = true;
            return;
        }
        h.textContent = this.s(this.mobile ? 'ui_hint_touch' : 'ui_hint_mouse');
        h.hidden = false;
    }

    hideHint() {
        const h = this.r('hint');
        if (!h || h.hidden || this.hintT) return;
        this.hintT = setTimeout(() => {
            h.hidden = true;
            store.set('hinted', true);
        }, 6000);
    }

    markHinted() {
        const h = this.r('hint');
        if (h) h.hidden = true;
        store.set('hinted', true);
    }

    showDiveHint(text) {
        const h = this.r('dhint');
        if (!h) return;
        h.textContent = text || '';
        h.hidden = !text;
    }

    /* ---------------- gestures ---------------- */

    targetOf(t) {
        const n = t.closest && t.closest('.node');
        if (n) return this.model.nodes.get(n.dataset.node);
        const l = t.closest && t.closest('.link[data-link]');
        const link = l ? this.model.links.find(x => x.id === l.dataset.link) : null;
        return link ? {isLink: true, link, id: link.id} : null;
    }

    relPt(e) {
        const r = this.svg.getBoundingClientRect();
        return {x: e.clientX - r.left, y: e.clientY - r.top};
    }

    clearLp() {
        if (this.lpTimer) {
            clearTimeout(this.lpTimer);
            this.lpTimer = null;
        }
    }

    bindGestures() {
        const svg = this.svg;
        svg.addEventListener('contextmenu', e => e.preventDefault());
        svg.addEventListener('pointerdown', e => {
            if (!this.model || (this.wheel && this.wheel.tracking)) return;
            this.stopAnim();
            this.hideHint();
            const tgt = this.targetOf(e.target);
            if (e.pointerType === 'mouse' && e.button === 2) {
                if (tgt && this.wheel) this.wheel.open(tgt, this.relPt(e), e.pointerId, 'mouse');
                return;
            }
            if (e.pointerType === 'mouse' && e.button !== 0) return;
            try {
                svg.setPointerCapture(e.pointerId);
            } catch (err) {
                // The pointer is already gone.
            }
            this.ptrs.set(e.pointerId, this.relPt(e));
            if (this.ptrs.size === 1) {
                const p = this.relPt(e), t0 = performance.now();
                this.gest = {sx: p.x, sy: p.y, moved: false, pinched: false, target: e.target, samples: [{t: t0, x: p.x, y: p.y}]};
                if (tgt && e.pointerType !== 'mouse' && this.wheel) {
                    const pid = e.pointerId;
                    this.lpTimer = setTimeout(() => {
                        this.lpTimer = null;
                        if (!this.gest || this.gest.moved || this.ptrs.size !== 1) return;
                        const at = this.ptrs.get(pid) || p;
                        this.gest = null;
                        this.ptrs.clear();
                        try {
                            svg.releasePointerCapture(pid);
                        } catch (err) {
                            // Already released.
                        }
                        this.wheel.open(tgt, at, pid, 'touch');
                    }, 430);
                }
            } else {
                this.clearLp();
                if (this.gest) {
                    this.gest.moved = true;
                    this.gest.pinched = true;
                }
                this.pinch = null;
            }
        });
        svg.addEventListener('pointermove', e => {
            const prev = this.ptrs.get(e.pointerId);
            if (!prev) return;
            const p = this.relPt(e), v = this.view;
            this.ptrs.set(e.pointerId, p);
            if (this.ptrs.size === 1 && this.gest) {
                const g = this.gest;
                if (!g.moved && Math.hypot(p.x - g.sx, p.y - g.sy) > 8) {
                    g.moved = true;
                    this.clearLp();
                    svg.classList.add('dragging');
                }
                if (g.moved && !g.pinched) {
                    v.tx += p.x - prev.x;
                    v.ty += p.y - prev.y;
                    this.applyView();
                    g.samples.push({t: performance.now(), x: p.x, y: p.y});
                    if (g.samples.length > 6) g.samples.shift();
                }
            } else if (this.ptrs.size === 2) {
                const [a, b] = Array.from(this.ptrs.values());
                const mid = {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2}, dist = Math.hypot(a.x - b.x, a.y - b.y);
                if (this.pinch && this.pinch.dist > 0) {
                    this.zoomAt(mid.x, mid.y, dist / this.pinch.dist);
                    v.tx += mid.x - this.pinch.mid.x;
                    v.ty += mid.y - this.pinch.mid.y;
                    this.applyView();
                    this.updateDiveCandidate(mid);
                }
                this.pinch = {mid, dist};
            }
        });
        const end = e => {
            if (!this.ptrs.has(e.pointerId)) return;
            this.ptrs.delete(e.pointerId);
            this.clearLp();
            if (this.ptrs.size < 2 && this.pinch) {
                this.pinch = null;
                this.finishPinch();
            }
            if (this.ptrs.size === 0) {
                svg.classList.remove('dragging');
                if (this.gest) {
                    if (e.type === 'pointerup' && !this.gest.moved) this.onTap(e, this.gest);
                    else if (this.gest.moved && !this.gest.pinched) this.fling(this.gest.samples);
                }
                this.gest = null;
            }
        };
        svg.addEventListener('pointerup', end);
        svg.addEventListener('pointercancel', end);
        svg.addEventListener('wheel', e => {
            e.preventDefault();
            this.stopAnim();
            this.hideHint();
            const p = this.relPt(e), unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
            this.zoomAt(p.x, p.y, Math.exp(-e.deltaY * unit * (e.ctrlKey ? 0.01 : 0.0018)));
            this.applyView();
        }, {passive: false});
        // A node reached with the keyboard is selected; a click selects by itself (and a right click opens the wheel).
        svg.addEventListener('focusin', e => {
            const nodeEl = e.target.closest && e.target.closest('.node');
            if (!nodeEl || !nodeEl.matches(':focus-visible') || (this.sel && this.sel.id === nodeEl.dataset.node)) return;
            this.select({kind: 'node', id: nodeEl.dataset.node});
        });
    }

    fling(s) {
        if (this.reduced || s.length < 2) return;
        const a = s[0], b = s[s.length - 1], dt = b.t - a.t;
        if (dt <= 0 || performance.now() - b.t > 80) return;
        let vx = (b.x - a.x) / dt * 16, vy = (b.y - a.y) / dt * 16;
        if (Math.hypot(vx, vy) < 2) return;
        const step = () => {
            vx *= 0.92;
            vy *= 0.92;
            this.view.tx += vx;
            this.view.ty += vy;
            this.applyView();
            this.anim = Math.hypot(vx, vy) > 0.4 ? requestAnimationFrame(step) : null;
        };
        this.anim = requestAnimationFrame(step);
    }

    onTap(e, g) {
        if (this.wheel && this.wheel.node) {
            this.wheel.close();
            return;
        }
        const now = performance.now(), p = this.relPt(e);
        const dbl = this.lastTap && now - this.lastTap.t < 320 && Math.hypot(p.x - this.lastTap.x, p.y - this.lastTap.y) < 30;
        this.lastTap = dbl ? null : {t: now, x: p.x, y: p.y};
        const t = g.target, near = sel => t.closest && t.closest(sel);
        const badge = near('.cbadge'), nodeEl = near('.node'), cardEl = near('.card');
        const m = this.model;
        if (cardEl) {
            const n = m.nodes.get(cardEl.getAttribute('data-card'));
            if (n) this.call('console', n);
            return;
        }
        if (badge && nodeEl) {
            this.call('console', m.nodes.get(nodeEl.dataset.node));
            return;
        }
        if (nodeEl) {
            const n = m.nodes.get(nodeEl.dataset.node);
            if (dbl) {
                if (this.mobile) this.zoomToNode(n, Math.max(this.view.k * 1.8, 2.3));
                else this.call('console', n);
                return;
            }
            this.call('closeOverlays');
            this.select({kind: 'node', id: n.id});
            return;
        }
        const sub = near('[data-subnet]'), adj = near('[data-adj]'), linkEl = near('[data-link]');
        const obj = sub ? {kind: 'subnet', id: sub.getAttribute('data-subnet')} : adj ? {kind: 'adj', id: adj.getAttribute('data-adj')}
            : linkEl ? {kind: 'link', id: linkEl.getAttribute('data-link')} : null;
        if (obj) {
            if (dbl && obj.kind === 'link') {
                const l = m.links.find(x => x.id === obj.id);
                if (l) this.zoomToLink(l);
                return;
            }
            this.select(obj);
            this.call('details', obj);
            return;
        }
        this.select(null);
        this.call('closeOverlays');
        if (dbl) {
            const v = this.view, k = clamp(v.k * 2, KMIN, KMAX), q = k / v.k;
            this.animateTo({k, tx: p.x - (p.x - v.tx) * q, ty: p.y - (p.y - v.ty) * q}, 260);
        }
    }

    /**
     * Zooming deep into a node on a phone opens its console.
     *
     * @param {object} mid Pinch midpoint in screen coordinates.
     */
    updateDiveCandidate(mid) {
        if (!this.mobile) return;
        const n = 44 * this.view.k >= DIVE_PX ? this.nodeNear(mid.x, mid.y, 44 * this.view.k) : null;
        const cand = n && this.hooks.canConsole && this.hooks.canConsole(n) ? n : null;
        if ((cand && cand.id) !== (this.diveCand && this.diveCand.id)) {
            this.diveCand = cand;
            this.call('diveCandidate', cand);
        }
    }

    finishPinch() {
        if (!this.mobile) return;
        const cand = this.diveCand;
        this.diveCand = null;
        this.call('diveRelease', cand);
    }

    /* ---------------- keyboard ---------------- */

    bindKeys() {
        this.svg.addEventListener('keydown', e => {
            if (!this.model) return;
            const ae = document.activeElement;
            const nodeEl = ae && ae.closest && ae.closest('.node');
            const linkEl = !nodeEl && ae && ae.closest && ae.closest('.link[data-link]');
            const node = nodeEl ? this.model.nodes.get(nodeEl.dataset.node) : null;
            const link = linkEl ? this.model.links.find(x => x.id === linkEl.dataset.link) : null;
            const cur = node || (link && {isLink: true, link, id: link.id}) || (this.sel && this.sel.kind === 'node' ? this.model.nodes.get(this.sel.id) : null);
            if (e.key === 'Enter' && link) {
                e.preventDefault();
                this.select({kind: 'link', id: link.id});
                this.call('details', this.sel);
            } else if (e.key === 'Enter' && node) {
                e.preventDefault();
                this.call('nodeAction', node);
            } else if ((e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) && cur && this.wheel) {
                e.preventDefault();
                const at = cur.isLink ? this.toScreen((this.model.nodes.get(cur.link.a.nodeId).cx + this.model.nodes.get(cur.link.b.nodeId).cx) / 2, (this.model.nodes.get(cur.link.a.nodeId).cy + this.model.nodes.get(cur.link.b.nodeId).cy) / 2) : this.toScreen(cur.cx, cur.cy);
                this.wheel.open(cur, at, null, 'key');
            } else if (e.key === '+' || e.key === '=') this.zoomBy(1.6);
            else if (e.key === '-') this.zoomBy(1 / 1.6);
            else if (e.key === '0') this.fit();
            else if (e.key >= '1' && e.key <= '4' && !e.ctrlKey && !e.metaKey && !e.altKey && !(this.wheel && this.wheel.node)) this.setLayer(LAYERS[+e.key - 1]);
            else if (e.key === 'Escape') {
                if (this.wheel) this.wheel.close();
                this.call('closeOverlays');
                this.select(null);
            }
        });
    }
}
