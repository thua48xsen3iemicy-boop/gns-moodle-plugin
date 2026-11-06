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
 * - details(sel)           the user asked for the details of an object (a click on a subnet or adjacency, Enter);
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
import {derive, endIf, endState} from 'mod_gns3lab/facts';
import {drawModel, layerModel} from 'mod_gns3lab/reveal';
import {CanvasView, PORT_DOT} from 'mod_gns3lab/canvasview';

const KMIN = 0.22, KMAX = 6;

/** A node icon this big on the screen (px) is "deep enough" to dive into its console on a phone. */
const DIVE_PX = 140;

/** On which layers an object can be seen; selecting it from a list switches to the first one. */
const LAYER_OF = {subnet: ['l3'], vlan: ['l2'], link: ['l1', 'l2'], switchvlan: ['l2'], segment: ['l2']};

const LAYERS = ['l1', 'l2', 'l3', 'l4'];

const OK_EL = new Set(['rect', 'ellipse', 'circle', 'line', 'text']);
const OK_AT = new Set(['x', 'y', 'width', 'height', 'rx', 'ry', 'cx', 'cy', 'r', 'x1', 'y1', 'x2', 'y2', 'fill', 'fill-opacity',
    'stroke', 'stroke-opacity', 'stroke-width', 'stroke-dasharray', 'font-size', 'font-weight', 'opacity']);

/**
 * Whitelist sanitizer of a .gns3 drawing: only these shapes and attributes, never innerHTML (the file is untrusted).
 *
 * @param {string} src SVG source of the drawing
 * @returns {{g: SVGElement, w: number, h: number}|null}
 */
export const sanitizeDrawing = src => {
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

/** Radius of the ring around a node: the port dots of its cables sit on it. */
const NODE_R = 31;

/**
 * Parallel links between one pair of nodes are spread by 16 px.
 * The side is taken from the pair, not from the link: a cable stored B→A would otherwise land on the one stored A→B.
 * The port dots sit on the node ring, and the wire runs between them: it does not cross the dot into the node.
 *
 * @param {object} m model
 * @returns {Map} link id → {ax, ay, bx, by, dx, dy, len, pa, pb, t}: the offset centre line, its direction and length,
 *     the two dots and how far along the line from its end they are
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
        const side = String(l.a.nodeId) < String(l.b.nodeId) ? 1 : -1;
        const off = (i - (list.length - 1) / 2) * 16 * side, nx = -dy * off, ny = dx * off;
        // Where the offset line meets the ring; a far outer cable of a wide bundle keeps its dot on the ring too.
        let t = Math.sqrt(Math.max(0, NODE_R * NODE_R - off * off)), lift = off;
        if (Math.abs(off) > NODE_R * .8) {
            const k = NODE_R / Math.hypot(NODE_R * .6, off);
            t = NODE_R * .6 * k;
            lift = off * k;
        }
        const qx = -dy * lift, qy = dx * lift;
        out.set(l.id, {ax: A.cx + nx, ay: A.cy + ny, bx: B.cx + nx, by: B.cy + ny, dx, dy, len, t,
            pa: {x: A.cx + qx + dx * t, y: A.cy + qy + dy * t}, pb: {x: B.cx + qx - dx * t, y: B.cy + qy - dy * t}});
    }));
    return out;
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
        this.L = {draw: r('lyrDraw'), links: r('lyrLinks'), labels: r('lyrLabels'), nodes: r('lyrNodes'), cards: r('lyrCards'), l2: r('lyrL2'), l3: r('lyrL3'), leads: r('lyrLabelLeads')};
        // The lab as it is built from the .gns3 (`base`), as it is drawn (`model`: no hidden node, only the uncovered cables)
        // and as the layers see it (`lmodel`: only the named nodes). They are one and the same without a fog or `seen`.
        this.base = null;
        this.model = null;
        this.lmodel = null;
        this.reveal = null;
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
        this.canvas = new CanvasView(this);
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
     * @param {object|Function} [options.view] what the modes seen and fog show of the lab (reveal.js applyReveal), or a
     *      function that returns it for the model that was just built; absent: everything
     */
    load(doc, {statuses = null, guests = null, facts = null, keepView = false, view = null} = {}) {
        if (!doc || typeof doc !== 'object' || !doc.topology) {
            throw new Error('Not a GNS3 topology');
        }
        this.base = buildModel(doc);
        // A node that is not uncovered shows another name; the real one is kept for the starting nodes of a fog.
        this.base.nodes.forEach(n => {
            n.realName = n.name;
        });
        this.setReveal(typeof view === 'function' ? view(this.base) : view, false);
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
        const res = doc ? derive(this.lmodel, this.factsLive ? this.withLiveStatuses(doc) : doc) : null;
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
        // A silhouette shows no state.
        const st = n.state === 'silhouette' ? null : this.nodeStatus(n);
        n.g.setAttribute('class', n.g.getAttribute('class').replace(/st-\w+/, 'st-' + (st || 'none')));
        n.g.setAttribute('aria-label', this.nodeLabel(n));
    }

    nodeLabel(n) {
        if (n.state === 'silhouette') return this.s('ui_fog_unknown');
        const st = this.nodeStatus(n);
        return [n.name, this.s('ui_cat_' + n.cat).toLowerCase(), st ? this.s('ui_st_' + st) : null].filter(Boolean).join(', ');
    }

    /* ---------------- what the modes seen and fog show ---------------- */

    /**
     * Applies what the modes seen and fog show of the lab to the model (reveal.js applyReveal).
     *
     * A silhouette keeps its place and its console; its name is a question mark. A hidden node and a cable that is not
     * uncovered are not in the model that is drawn. The layers are built from the named nodes only.
     *
     * @param {object|null} view the view, null: everything is shown
     * @param {boolean} [render] draw the scheme again
     */
    setReveal(view, render = true) {
        this.reveal = view || null;
        const base = this.base;
        if (!base) return;
        base.nodes.forEach(n => {
            n.state = view ? (view.states.get(n.id) || 'hidden') : 'open';
            n.name = n.state === 'silhouette' || n.state === 'hidden' ? '?' : n.realName;
        });
        this.model = drawModel(base, view);
        this.lmodel = layerModel(base, view);
        if (render) this.factsChanged();
    }

    /**
     * Applies the view and the facts together: one drawing for both.
     *
     * @param {object|null} view the view of reveal.js, null: everything is shown
     * @param {object|null} doc the facts as the view lets them through
     */
    setScene(view, doc) {
        this.setReveal(view, false);
        this.setFacts(doc);
    }

    /**
     * Whether the name of a port at a link end is shown: always, except in a fog where the interface is not uncovered.
     *
     * @param {{nodeId: string, adapter: number, port: number}} e end of a link
     * @returns {boolean}
     */
    endShown(e) {
        return !this.reveal || this.reveal.ends.has(e.nodeId + '|' + e.adapter + '/' + e.port);
    }

    /* ---------------- layers ---------------- */

    /**
     * Switches the layer.
     *
     * @param {string} layer l1, l2, l3 or the reserved l4
     */
    setLayer(layer) {
        if (!LAYERS.includes(layer) || layer === this.layer) return;
        if (layer === 'l2' || this.layer === 'l2') {
            this.sel = null;
            this.call('closeOverlays');
        }
        this.layer = layer;
        this.syncLayerUI();
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
        this.canvas.view();
    }

    /**
     * Derives the layers again after the facts changed and drops a selection that is gone.
     */
    factsChanged() {
        this.derive();
        this.syncLayerUI();
        const s = this.sel, D = this.der;
        if (s && ((s.kind === 'node' && !this.model.nodes.has(s.id)) || (s.kind === 'link' && !this.model.links.some(l => l.id === s.id)))) this.sel = null;
        else if (s && ['subnet', 'vlan', 'adj'].includes(s.kind) && (!D || (s.kind === 'subnet' && !D.l3.subnets.some(x => x.key === s.id)) || (s.kind === 'vlan' && !D.l2.domains.some(x => x.id === s.id)) || (s.kind === 'adj' && !D.rt.edges.some(x => x.id === s.id)))) this.sel = null;
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
        this.canvas.renderHud();
    }

    /* ---------------- rendering ---------------- */

    render() {
        const m = this.model;
        if (!m) return;
        for (const k in this.L) this.L[k].textContent = '';
        this.canvas.reset();
        if (this.sel && this.sel.kind === 'switchvlan' && (!this.canvas.l2 || !this.canvas.l2.virtuals.has(this.sel.id))) this.sel = null;
        if (this.sel && this.sel.kind === 'segment' && !this.canvas.l2?.segments.some(p => p.id === this.sel.id)) this.sel = null;
        const b = {x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity};
        this.grow = (x0, y0, x1, y1) => {
            b.x0 = Math.min(b.x0, x0);
            b.y0 = Math.min(b.y0, y0);
            b.x1 = Math.max(b.x1, x1);
            b.y1 = Math.max(b.y1, y1);
        };
        this.renderDrawings();
        const geo = linkGeometry(m), L = this.layer, D = this.der;
        // L2 draws its own segments; L3 draws the lines to the IP subnets over pale cables.
        if (L !== 'l2' && L !== 'l3') m.links.forEach(l => this.drawLink(l, geo.get(l.id)));
        // L3 keeps the cables as pale lines for orientation, without ports, labels or clicks.
        if (L === 'l3') m.links.forEach(l => {
            const g = geo.get(l.id);
            el('line', {x1: g.ax, y1: g.ay, x2: g.bx, y2: g.by, class: 'ghost-wire', 'aria-hidden': 'true'}, this.L.links);
        });
        if (L === 'l2' && D) this.canvas.renderL2();
        if (L === 'l3' && D) this.canvas.renderL3();
        this.renderNodes();
        this.canvas.prepareLabels();
        this.bounds = isFinite(b.x0) ? {x: b.x0, y: b.y0, w: b.x1 - b.x0, h: b.y1 - b.y0} : {x: -200, y: -150, w: 400, h: 300};
        this.applySel();
        this.view.k = this.view.k || 1;
        this.applyView();
        // Labels that fade in are skipped by the layout while transparent: lay them out again once they are shown.
        clearTimeout(this.relabelTimer);
        this.relabelTimer = setTimeout(() => this.canvas.scheduleLabels(), 450);
    }

    renderDrawings() {
        // The shapes and labels of the lab may give away what a fog hides.
        if (this.reveal && this.reveal.mode === 'fog') return;
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
        const unknown = !!(f1 && f1.unknown) && !l.suspend;
        const name = `${m.nodes.get(a.nodeId).name} ${a.text} — ${m.nodes.get(b.nodeId).name} ${b.text}`;
        const G = el('g', {class: 'link' + (l.suspend ? ' suspended' : '') + (down ? ' down' : '') + (unknown ? ' unknown' : ''), 'data-link': l.id, tabindex: '0', role: 'button',
            'aria-label': fill(this.s('ui_link_aria'), name)}, this.L.links);
        el('line', {x1: g.pa.x, y1: g.pa.y, x2: g.pb.x, y2: g.pb.y, class: 'hit'}, G);
        el('line', {x1: g.pa.x, y1: g.pa.y, x2: g.pb.x, y2: g.pb.y, class: 'wire'}, G);
        if (unknown) el('title', {}, G).textContent = this.s('ui_l1_unknown');
        this.canvas.duplex(l, g, G);
        const fl = fmtFilters(l.filters);
        if (fl.length) {
            const mx = (g.ax + g.bx) / 2, my = (g.ay + g.by) / 2, f = el('g', {class: 'l1'}, G);
            f.dataset.fixedX = mx;
            f.dataset.fixedY = my;
            el('circle', {cx: mx, cy: my, r: 8, class: 'filt'}, f);
            el('text', {x: mx, y: my + 2.8, class: 'filt-t'}, f).textContent = 'ms';
            el('title', {}, f).textContent = fill(this.s('ui_filter_on'), fl.map(x => x.k).join(', '));
        }
        const d = Math.min(58, g.len * 0.36);
        [[a, ia, g.pa, g.dx, g.dy], [b, ib, g.pb, -g.dx, -g.dy]].forEach(([e, ifc, dot, ux, uy]) => {
            const portState = endState(this.der, e);
            const bad = this.endDown(e);
            const old = (portState && portState.live ? portState.unknown : this.factsFailed(e.nodeId)) ? ' fstale' : '';
            el('circle', {cx: dot.x, cy: dot.y, r: PORT_DOT, class: 'pdot l1' + (bad ? ' down' : '') + old}, G);
            if (!this.endShown(e)) return;
            // The name sits on the wire, `d` from the node centre (the dot is `t` from it).
            const px = dot.x + ux * (d - g.t), py = dot.y + uy * (d - g.t);
            el('text', {x: px, y: py + 3, class: 'plabel l1'}, G).textContent = e.text;
            if (ifc && ifc.ipv4 && ifc.ipv4.length) el('text', {x: px, y: py + 3, dy: '1.35em', class: 'pip l2' + old}, G).textContent = ifc.ipv4[0];
        });
    }

    renderNodes() {
        const L = this.layer;
        this.model.nodes.forEach(n => {
            n.g = null;
            if (this.canvas.l2 && this.canvas.l2.switches.has(n.id)) return;
            // A silhouette is a place with a console: no name, no kind, no state, no facts.
            const sil = n.state === 'silhouette';
            const st = sil ? null : this.nodeStatus(n);
            const g = el('g', {class: 'node dimmable st-' + (st || 'none') + (sil ? ' silhouette' : '') +
                (L === 'l2' ? ' l2-endpoint' : '') + (this.factsFailed(n.id) ? ' collection-error' : ''), transform: `translate(${n.cx} ${n.cy})`,
                'data-node': n.id, tabindex: '0', role: 'button', 'aria-label': ''}, this.canvas.nodeLayer || this.L.nodes);
            n.g = g;
            g.setAttribute('aria-label', this.nodeLabel(n));
            el('circle', {r: NODE_R, class: 'halo'}, g);
            el('use', {href: `#${this.prefix}-ico-${sil ? 'unknown' : n.cat}`, x: -22, y: -22, width: 44, height: 44, class: 'ico'}, g);
            const label = el('g', {class: 'auto-label', 'data-priority': 0}, g);
            const title = el('text', {y: L === 'l2' ? 23 : 25, class: 'nname'}, label);
            el('tspan', {}, title).textContent = sil ? this.s('ui_fog_unknown') : n.name;
            const bd = el('g', {class: 'cbadge', transform: 'translate(-22 -21)'}, g);
            el('rect', {x: -10, y: -7.5, width: 20, height: 15, rx: 3.5}, bd);
            el('text', {y: 3}, bd).textContent = '>_';
            this.paintBadge(n);
            this.grow(n.cx - 40, n.cy - 36, n.cx + 40, n.cy + 52);
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
            this.call('lodChanged', nl);
        }
        this.canvas.view();
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

    /**
     * Glides to the zoom of a wheel notch around the pointer in about 150 ms; notches in a row add up.
     *
     * @param {{x: number, y: number}} p pointer in the coordinates of the scheme area
     * @param {number} f zoom factor of this notch
     */
    wheelGlide(p, f) {
        const v = this.view;
        this.wheelK = clamp((this.wheelK || v.k) * f, KMIN, KMAX);
        const step = () => {
            const k = v.k + (this.wheelK - v.k) * 0.32;
            const done = Math.abs(this.wheelK - k) / this.wheelK < 0.002;
            this.zoomAt(p.x, p.y, (done ? this.wheelK : k) / v.k);
            this.applyView();
            if (done) {
                this.wheelK = null;
                this.anim = null;
            } else {
                this.anim = requestAnimationFrame(step);
            }
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
        // The scheme holds on to its top left corner, so a change of the console mode only covers or uncovers its right
        // part and the dots and nodes stay put. A phone keeps the centre (a turn of the device).
        if (this.mobile && this.lastSize && r.width && r.height) {
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
        this.canvas.select();
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
        const st = n.state === 'silhouette' ? null : this.nodeStatus(n);
        const kind = n.state === 'silhouette' ? this.s('ui_fog_unknown') : this.s('ui_cat_' + n.cat);
        chip.innerHTML = `<b>${esc(n.name)}</b> <span>· ${esc(kind.toLowerCase())}${st ? ' · ' + esc(this.s('ui_st_' + st)) : ''} · ${esc(this.s(this.mobile ? 'ui_chip_touch' : 'ui_chip_mouse'))}</span>`;
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
            // In a fog the cable may exist and not be uncovered yet: "not connected" would be a lie.
            if (!this.reveal) this.call('toast', fill(this.s('ui_port_unlinked'), port));
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
        const virtual = t.closest && t.closest('[data-virtual]');
        if (virtual && this.canvas.l2) {
            const v = this.canvas.l2.virtuals.get(virtual.dataset.virtual);
            if (v) return v.orig;
        }
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
            const pending = this.anim && this.wheelK;
            this.stopAnim();
            if (!pending) this.wheelK = null;
            this.hideHint();
            const p = this.relPt(e), unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
            const f = Math.exp(-e.deltaY * unit * (e.ctrlKey ? 0.01 : 0.0018));
            // A notch of a mouse wheel glides to its zoom; a touchpad and a pinch (small steps, Ctrl) follow the fingers.
            if (this.reduced || e.ctrlKey || Math.abs(e.deltaY * unit) < 40) {
                this.wheelK = null;
                this.zoomAt(p.x, p.y, f);
                this.applyView();
                return;
            }
            this.wheelGlide(p, f);
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
        const segment = near('[data-segment]');
        if (segment) {
            this.select({kind: 'segment', id: segment.dataset.segment});
            this.call('details', this.sel);
            return;
        }
        const virtual = near('[data-virtual]');
        if (virtual) {
            const v = this.canvas.l2 && this.canvas.l2.virtuals.get(virtual.dataset.virtual);
            if (dbl && !this.mobile && v && this.call('canConsole', v.orig)) {
                this.call('closeOverlays');
                this.call('console', v.orig);
                return;
            }
            this.select({kind: 'switchvlan', id: virtual.dataset.virtual});
            this.call('details', this.sel);
            return;
        }
        const badge = near('.cbadge'), nodeEl = near('.node');
        const m = this.model;
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
            // A click only selects: the card opens from the action wheel ("Details").
            this.call('closeOverlays');
            this.select({kind: 'node', id: n.id});
            return;
        }
        const info = near('[data-subnet-info]');
        if (info) {
            // The "i" of a subnet pill opens its card; a tap elsewhere on the pill only selects the subnet.
            this.call('closeOverlays');
            this.select({kind: 'subnet', id: info.getAttribute('data-subnet-info')});
            this.call('details', this.sel);
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
            this.call('closeOverlays');
            this.select(obj);
            // Nodes and links have the action wheel for their card, a subnet its "i"; the other objects open it at once.
            if (obj.kind !== 'link' && obj.kind !== 'subnet') this.call('details', obj);
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
            const semantic = ae && ae.closest && ae.closest('[data-virtual],[data-subnet],[data-segment]');
            if ((e.key === 'Enter' || e.key === ' ') && semantic) {
                e.preventDefault();
                this.select(semantic.hasAttribute('data-virtual') ? {kind: 'switchvlan', id: semantic.dataset.virtual}
                    : semantic.hasAttribute('data-segment') ? {kind: 'segment', id: semantic.dataset.segment} : {kind: 'subnet', id: semantic.dataset.subnet});
                this.call('details', this.sel);
            } else if (e.key === 'Enter' && link) {
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
