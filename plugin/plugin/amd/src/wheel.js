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
 * Action wheel: a radial menu of a node or a link.
 *
 * A touch: hold the node, then without lifting the finger move to an action and lift. Lifted at once, the wheel stays
 * open and an action is chosen by a tap. A mouse: the right button works the same way. Keyboard: Shift+F10, then 1-9.
 * Ported from prototypes/student-ui.html.
 *
 * The wheel only draws and tracks. Which actions a target has and what they do is decided by the host:
 * - host.actionsFor(target) → [{id, label, full, icon, off, why, danger}];
 * - host.run(id, target);
 * - host.toast(text).
 *
 * @module     mod_gns3lab/wheel
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

// Straight port of the prototype; these style rules stay relaxed.
/* eslint-disable curly, max-len, jsdoc/require-jsdoc, no-eq-null */

import {svgEl as el, hel} from 'mod_gns3lab/dom';
import {esc} from 'mod_gns3lab/model';

const R0 = 34, R1 = 108;

export class Wheel {
    /**
     * Constructor.
     *
     * @param {HTMLElement} box element of the wheel inside the stage
     * @param {object} options
     * @param {object} options.stage the stage
     * @param {string} options.prefix id prefix of the template
     * @param {object} options.host actions of the wheel
     * @param {object} options.strings language strings
     */
    constructor(box, {stage, prefix, host, strings}) {
        this.el = box;
        this.stage = stage;
        this.prefix = prefix;
        this.host = host;
        this.strings = strings;
        this.node = null;
        this.tracking = false;
        this.pid = null;
        this.kind = null;
        this.items = [];
        this.hi = -1;
        this.moved = false;
        this.cap = null;
        stage.wheel = this;
        this.bind();
    }

    /**
     * Opens the wheel of a node or a link.
     *
     * @param {object} target a node of the model, or {isLink: true, link, id}
     * @param {{x: number, y: number}} at point on the scheme in screen pixels of the scheme element
     * @param {number|null} pid pointer id
     * @param {string} kind touch, mouse or key
     */
    open(target, at, pid, kind) {
        this.close();
        const stage = this.stage;
        stage.select(target.isLink ? {kind: 'link', id: target.id} : null);
        stage.call('closeOverlays');
        try {
            if (navigator.vibrate) navigator.vibrate(12);
        } catch (e) {
            // Not supported.
        }
        this.node = target;
        this.items = this.host.actionsFor(target);
        this.pid = pid;
        this.kind = kind;
        this.hi = -1;
        this.moved = false;
        this.tracking = kind === 'touch' || kind === 'mouse';
        const v = stage.view;
        this.pos = target.isLink ? {x: (at.x - v.tx) / v.k, y: (at.y - v.ty) / v.k} : {x: target.cx, y: target.cy};
        const box = this.el;
        box.textContent = '';
        box.hidden = false;
        box.classList.toggle('pinned', !this.tracking);
        box.classList.remove('in');
        void box.offsetWidth;
        box.classList.add('in');
        const s = el('svg', {viewBox: '0 0 236 236'}, box);
        el('circle', {cx: 118, cy: 118, r: R1 + 0.5, class: 'ring'}, s);
        const N = this.items.length, step = 2 * Math.PI / N, gap = 0.035;
        this.items.forEach((it, i) => {
            const mid = -Math.PI / 2 + i * step, a0 = mid - step / 2 + gap, a1 = mid + step / 2 - gap;
            const g = el('g', {class: 'wedge' + (it.off ? ' off' : '') + (it.danger ? ' danger' : '') + (it.free ? ' free' : ''), 'data-i': i}, s);
            el('path', {d: this.wedgePath(a0, a1)}, g);
            const rm = (R0 + R1) / 2 + 2, ix = 118 + rm * Math.cos(mid), iy = 118 + rm * Math.sin(mid);
            el('use', {href: `#${this.prefix}-act-${it.icon}`, x: ix - 11, y: iy - 18, width: 22, height: 22, class: 'wi'}, g);
            el('text', {x: ix, y: iy + 15}, g).textContent = it.label;
            g.addEventListener('click', ev => {
                ev.stopPropagation();
                this.run(i);
            });
            g.addEventListener('pointerenter', () => {
                if (!this.tracking) this.setHi(i);
            });
            g.addEventListener('pointerleave', () => {
                if (!this.tracking) this.setHi(-1);
            });
        });
        el('circle', {cx: 118, cy: 118, r: R0 - 5, class: 'hub'}, s);
        el('text', {x: 118, y: 119, class: 'hubt'}, s).textContent = target.isLink ? this.strings.ui_link : target.name;
        this.cap = hel('div', 'rcap', box);
        this.cap.hidden = true;
        this.position();
        if (!this.tracking && kind === 'key') {
            setTimeout(() => {
                const w = box.querySelector('.wedge:not(.off)');
                if (w) w.setAttribute('tabindex', '0');
            }, 0);
        }
    }

    wedgePath(a0, a1) {
        const p = (r, a) => `${(118 + r * Math.cos(a)).toFixed(2)} ${(118 + r * Math.sin(a)).toFixed(2)}`;
        const big = a1 - a0 > Math.PI ? 1 : 0;
        return `M${p(R1, a0)} A${R1} ${R1} 0 ${big} 1 ${p(R1, a1)} L${p(R0, a1)} A${R0} ${R0} 0 ${big} 0 ${p(R0, a0)} Z`;
    }

    setHi(i) {
        this.hi = i;
        this.el.querySelectorAll('.wedge').forEach((w, j) => w.classList.toggle('hi', j === i));
        const cap = this.cap;
        if (!cap) return;
        if (i < 0) {
            cap.hidden = true;
            return;
        }
        const it = this.items[i];
        cap.innerHTML = esc(it.full) + (it.off && it.why ? `<small>${esc(it.why)}</small>` : '');
        cap.hidden = false;
    }

    /** Puts the wheel on its node (the node can move with the scheme) and keeps it inside the scheme area. */
    position() {
        if (!this.node) return;
        const r = this.stage.wrap.getBoundingClientRect();
        const p = this.stage.toScreen(this.pos.x, this.pos.y);
        const m = 118 + 8;
        const x = Math.min(Math.max(p.x, m), Math.max(m, r.width - m)), y = Math.min(Math.max(p.y, m + 34), Math.max(m + 34, r.height - m));
        this.cx = x;
        this.cy = y;
        this.el.style.left = x + 'px';
        this.el.style.top = y + 'px';
    }

    close() {
        this.node = null;
        this.tracking = false;
        this.el.hidden = true;
        this.el.textContent = '';
        this.cap = null;
    }

    run(i) {
        const it = this.items[i], t = this.node;
        if (!it || !t) return;
        if (it.off) {
            if (it.why) this.host.toast(it.why);
            return;
        }
        this.close();
        this.host.run(it.id, t);
    }

    bind() {
        window.addEventListener('pointermove', e => {
            if (!this.tracking || (this.pid != null && e.pointerId !== this.pid)) return;
            const r = this.stage.wrap.getBoundingClientRect(), x = e.clientX - r.left - this.cx, y = e.clientY - r.top - this.cy, d = Math.hypot(x, y);
            if (d > 14) this.moved = true;
            if (d < R0 - 4) {
                this.setHi(-1);
                return;
            }
            const N = this.items.length, step = 2 * Math.PI / N;
            let a = Math.atan2(y, x) + Math.PI / 2 + step / 2;
            a = ((a % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
            this.setHi(Math.floor(a / step) % N);
        });
        const end = e => {
            if (!this.tracking || (this.pid != null && e.pointerId !== this.pid)) return;
            e.preventDefault();
            this.tracking = false;
            if (this.hi >= 0 && this.moved) {
                this.run(this.hi);
                return;
            }
            this.setHi(-1);
            this.el.classList.add('pinned');
            this.position();
        };
        window.addEventListener('pointerup', end, true);
        window.addEventListener('pointercancel', e => {
            if (this.tracking && e.pointerId === this.pid) {
                this.tracking = false;
                this.el.classList.add('pinned');
            }
        }, true);
        window.addEventListener('contextmenu', e => {
            if (this.node) e.preventDefault();
        });
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape' && this.node) {
                this.close();
            } else if (this.node && !this.tracking && /^[1-9]$/.test(e.key)) {
                const i = +e.key - 1;
                if (i < this.items.length) this.run(i);
            }
        });
    }
}
