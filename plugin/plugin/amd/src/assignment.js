// This file is part of Moodle - http://moodle.org/
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
// along with Moodle.  If not, see <http://www.gnu.org/licenses/>.

/**
 * The assignment of the activity as a floating window over the scheme.
 *
 * The window lives in the layer of the console windows and shares their order: the one clicked last is on top.
 * Clicks on the scheme leave it alone; only its own buttons, the toolbar button and Escape inside it close it.
 * It can be dragged by its title, resized by its corner and folded to the title. Where it was, how large, whether it
 * was open or folded and how far it was scrolled is remembered per activity in the browser. On a phone it is a sheet
 * above the zoom controls that folds to its title.
 *
 * @module     mod_gns3lab/assignment
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {store} from 'mod_gns3lab/dom';

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

/** The part of the title that stays on the scheme whatever the window is dragged to, px. */
const GRIP = 80;
/** Height of the title bar, px. */
const HEAD = 40;

export class Assignment {
    /**
     * @param {object} o options
     * @param {HTMLElement} o.win the window (data-region="assignments")
     * @param {HTMLElement} o.toggle the toolbar button
     * @param {HTMLElement} o.layer the layer the window is positioned in
     * @param {number} o.cmid course module id: the key of what is remembered
     * @param {Function} o.mobile returns true on a phone sized page
     * @param {Function} o.raise returns the z-index that puts a window above the others
     */
    constructor({win, toggle, layer, cmid, mobile, raise}) {
        this.win = win;
        this.toggleBtn = toggle;
        this.layer = layer;
        this.mobile = mobile;
        this.raise = raise;
        this.key = 'assignment.' + cmid;
        this.head = win.querySelector('[data-region="assignmentsHead"]');
        this.body = win.querySelector('[data-region="assignmentsBody"]');
        this.foldBtn = win.querySelector('[data-region="assignmentsFold"]');
        this.closeBtn = win.querySelector('[data-region="assignmentsClose"]');
        const saved = store.get(this.key, null);
        this.st = saved && typeof saved === 'object' ? saved : {};
        this.st.open = !!this.st.open;
        this.st.folded = !!this.st.folded;

        toggle.addEventListener('click', () => this.toggle());
        this.foldBtn.addEventListener('click', () => this.fold(!this.st.folded));
        this.closeBtn.addEventListener('click', () => { this.close(); toggle.focus(); });
        win.addEventListener('keydown', e => {
            if (e.key === 'Escape') {
                e.stopPropagation();
                this.close();
                toggle.focus();
            }
        });
        win.addEventListener('pointerdown', () => this.front());
        // A double click on the title folds and unfolds, as the title of a window of the desktop.
        this.head.addEventListener('dblclick', e => {
            if (!e.target.closest('button') && !this.mobile()) this.fold(!this.st.folded);
        });
        // A phone sheet does not move: a tap on its title folds and unfolds it.
        this.head.addEventListener('click', e => {
            if (!e.target.closest('button') && this.mobile()) this.fold(!this.st.folded);
        });
        this.drag();
        let scrollTimer = null;
        this.body.addEventListener('scroll', () => {
            clearTimeout(scrollTimer);
            scrollTimer = setTimeout(() => { this.st.scroll = Math.round(this.body.scrollTop); this.save(); }, 250);
        }, {passive: true});
        if (window.ResizeObserver) {
            // The corner of the window resizes it: keep the size it was given.
            new ResizeObserver(() => {
                if (this.win.hidden || this.st.folded || this.mobile() || !this.win.offsetWidth) return;
                const w = this.win.offsetWidth, h = this.win.offsetHeight;
                // Only a size the student gave: not the one place() fitted into a smaller scheme.
                if (this.placed && w === this.placed.w && h === this.placed.h) return;
                this.st.w = w; this.st.h = h; this.placed = {w, h};
                this.save();
            }).observe(win);
            // The scheme got smaller: the title stays reachable.
            new ResizeObserver(() => { if (!this.win.hidden) this.place(); }).observe(layer);
        }
        this.render();
    }

    /** The toolbar button: opens a closed window, unfolds a folded one, closes an open one. */
    toggle() {
        if (!this.st.open) {
            this.open();
        } else if (this.st.folded) {
            this.fold(false);
            this.front();
        } else {
            this.close();
        }
    }

    open() {
        this.st.open = true;
        this.st.folded = false;
        this.render();
        this.front();
        this.foldBtn.focus();
        this.save();
    }

    close() {
        if (!this.st.open) return;
        this.st.open = false;
        // A closed window does not keep talking.
        this.body.querySelectorAll('video,audio').forEach(m => { try { m.pause(); } catch (e) { /* not a media yet */ } });
        this.render();
        this.save();
    }

    /**
     * Folds the window to its title or unfolds it.
     *
     * @param {boolean} folded fold
     */
    fold(folded) {
        this.st.folded = folded;
        this.render();
        this.save();
    }

    /** On a phone the object card takes the same place: an open assignment folds to its title. */
    yieldPlace() {
        if (this.mobile() && this.st.open && !this.st.folded) this.fold(true);
    }

    front() {
        this.win.style.zIndex = this.raise();
    }

    render() {
        const {open, folded} = this.st;
        this.win.hidden = !open;
        this.win.classList.toggle('folded', folded);
        this.toggleBtn.setAttribute('aria-expanded', String(open));
        this.foldBtn.setAttribute('aria-expanded', String(!folded));
        const label = this.foldBtn.dataset[folded ? 'expand' : 'collapse'];
        this.foldBtn.setAttribute('aria-label', label);
        this.foldBtn.title = label;
        if (!open) return;
        this.place();
        if (!folded && this.st.scroll) {
            // After the body is shown again: a hidden box does not scroll.
            requestAnimationFrame(() => { this.body.scrollTop = this.st.scroll; });
        }
    }

    /** Puts the window where it was, inside the scheme; a phone sheet is placed by the styles. */
    place() {
        const s = this.win.style;
        if (this.mobile()) {
            s.left = s.top = s.width = s.height = '';
            return;
        }
        const W = this.layer.clientWidth, H = this.layer.clientHeight;
        if (!W || !H) return;
        if (!this.st.w || !this.st.h) {
            this.st.w = Math.min(440, Math.max(260, W - 28));
            this.st.h = Math.min(520, Math.max(160, H - 28));
            this.st.x = W - this.st.w - 14;
            this.st.y = 14;
        }
        const w = Math.min(this.st.w, Math.max(260, W - 16));
        const h = Math.min(this.st.h, Math.max(160, H - 16));
        this.st.x = clamp(this.st.x, GRIP - w, W - GRIP);
        this.st.y = clamp(this.st.y, 0, Math.max(0, H - HEAD));
        s.left = this.st.x + 'px';
        s.top = this.st.y + 'px';
        // Folded, it is as narrow as its title.
        s.width = this.st.folded ? '' : w + 'px';
        s.height = this.st.folded ? '' : h + 'px';
        this.placed = {w, h};
    }

    /** Dragging by the title; not on a phone, where the window is a sheet. */
    drag() {
        let d = null;
        this.head.addEventListener('pointerdown', e => {
            if (e.button !== 0 || e.target.closest('button') || this.mobile()) return;
            d = {x: e.clientX, y: e.clientY, ox: this.st.x, oy: this.st.y};
            this.head.setPointerCapture(e.pointerId);
            this.head.classList.add('dragging');
        });
        this.head.addEventListener('pointermove', e => {
            if (!d) return;
            this.st.x = d.ox + e.clientX - d.x;
            this.st.y = d.oy + e.clientY - d.y;
            this.place();
        });
        const end = () => {
            if (!d) return;
            d = null;
            this.head.classList.remove('dragging');
            this.save();
        };
        this.head.addEventListener('pointerup', end);
        this.head.addEventListener('pointercancel', end);
    }

    save() {
        store.set(this.key, this.st);
    }
}
