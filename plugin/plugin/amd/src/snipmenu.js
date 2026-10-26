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
 * Snippet menu and the hint over the snippet buttons.
 *
 * The menu opens from the "Snippets" button under a console and lists the commands of the device in groups,
 * with a search field. A row runs the command; the second button of a row only types it (to finish the line),
 * the star pins it to the bar under the terminal. Hovering (or arrowing to) a row shows in the footer exactly what
 * will be typed. The hint over a button of the bar says the same. All texts are put in with textContent.
 *
 * @module     mod_gns3lab/snipmenu
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {hel, fill} from 'mod_gns3lab/dom';
import {clamp} from 'mod_gns3lab/model';
import {snippetSet} from 'mod_gns3lab/snippets';

const ICON_TYPE = '<svg viewBox="0 0 16 16"><path d="M3 3.5h10M8 3.5v9M5.5 12.5h5"/></svg>';
const ICON_STAR = '<svg viewBox="0 0 16 16">'
    + '<path d="M8 1.9l1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2.1.7-4.3-3.1-3 4.3-.6z"/></svg>';
const ICON_X = '<svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8"/></svg>';

/** Gap between the button and the menu or the hint, px. */
const GAP = 6;

/** The hint over a button appears after this pause, ms: it must not flicker while the pointer passes by. */
const TIP_DELAY = 350;

/** The widest menu, px. */
const MENU_WIDTH = 440;

let counter = 0;

/**
 * Words of a text in lower case, to match a typed query against.
 *
 * @param {string} text text
 * @returns {string[]}
 */
const words = text => text.toLowerCase().split(/[^\p{L}\p{N}-]+/u).filter(Boolean);

/**
 * The snippet menu of the consoles and the hint over the pinned snippets; one for all consoles, one menu open at a time.
 */
export class SnipMenu {
    /**
     * Constructor.
     *
     * @param {object} owner the Consoles: root, s(), toast, isPinned(), togglePin()
     */
    constructor(owner) {
        this.o = owner;
        this.menu = null;
        this.con = null;
        this.anchor = null;
        this.tip = null;
        this.tipTimer = null;
        this.onDown = event => {
            if (this.menu && !this.menu.contains(event.target) && !this.anchor.contains(event.target)) {
                this.close(false);
            }
        };
        this.onFocus = event => {
            if (this.menu && !this.menu.contains(event.target) && !this.anchor.contains(event.target)) {
                this.close(false);
            }
        };
        this.onKey = event => {
            if (event.key === 'Escape' && this.menu) {
                event.stopPropagation();
                this.close(true);
            }
        };
        this.onResize = () => this.menu && this.place();
    }

    /**
     * Is the menu of this console open?
     *
     * @param {object} con console
     * @returns {boolean}
     */
    isOpenFor(con) {
        return !!this.menu && this.con === con;
    }

    /**
     * Opens the menu under the button, or closes it if it is open for this button.
     *
     * @param {object} con console
     * @param {HTMLElement} anchor the "Snippets" button
     */
    toggle(con, anchor) {
        if (this.menu && this.anchor === anchor) {
            this.close(true);
        } else {
            this.open(con, anchor);
        }
    }

    /**
     * Opens the menu.
     *
     * @param {object} con console: n (node), runSnippet(seq), flash(id), focus()
     * @param {HTMLElement} anchor the "Snippets" button
     */
    open(con, anchor) {
        this.close(false);
        this.hideTip();
        const o = this.o, set = snippetSet(con.n), uid = 'sm' + (++counter);
        this.con = con;
        this.anchor = anchor;
        this.set = set;
        this.rows = [];
        this.active = null;
        this.items = new Map(set.items.map(item => [item.id, item]));

        const menu = this.menu = hel('div', 'snipmenu', o.root);
        menu.setAttribute('role', 'dialog');
        menu.setAttribute('aria-label', fill(o.s('ui_snippets_for'), con.n.name));
        menu.style.zIndex = '2000';

        const head = hel('div', 'sm-head', menu);
        const search = this.search = hel('input', 'sm-search', head);
        search.type = 'search';
        search.placeholder = o.s('ui_snip_filter');
        search.setAttribute('aria-label', o.s('ui_snip_filter'));
        search.setAttribute('role', 'combobox');
        search.setAttribute('aria-expanded', 'true');
        search.setAttribute('aria-controls', uid + '-list');
        search.setAttribute('aria-autocomplete', 'list');
        search.autocomplete = 'off';
        search.spellcheck = false;
        const x = hel('button', 'sm-x', head);
        x.type = 'button';
        x.innerHTML = ICON_X;
        x.setAttribute('aria-label', o.s('ui_close'));
        x.title = o.s('ui_close');
        x.addEventListener('click', () => this.close(true));

        const list = this.list = hel('div', 'sm-list', menu);
        list.id = uid + '-list';
        list.setAttribute('role', 'listbox');
        set.groups.forEach(group => this.buildGroup(list, group, uid));
        this.none = hel('p', 'sm-none', list);
        this.none.textContent = o.s('ui_snip_none');
        this.none.hidden = true;

        const foot = hel('div', 'sm-foot', menu);
        this.prev = hel('div', 'sm-prev', foot);
        this.note = hel('div', 'sm-note', foot);
        hel('div', 'sm-keys', foot).textContent = o.s('ui_snip_keys');

        search.addEventListener('input', () => this.filter());
        menu.addEventListener('keydown', event => this.keydown(event));

        anchor.setAttribute('aria-expanded', 'true');
        anchor.classList.add('open');
        this.place();
        document.addEventListener('pointerdown', this.onDown, true);
        document.addEventListener('focusin', this.onFocus, true);
        document.addEventListener('keydown', this.onKey, true);
        window.addEventListener('resize', this.onResize);

        this.filter();
        if (!matchMedia('(pointer: coarse)').matches) {
            search.focus({preventScroll: true});
        }
    }

    /**
     * One group of the list.
     *
     * @param {HTMLElement} list the list
     * @param {object} group {id, items}
     * @param {string} uid prefix of element ids
     */
    buildGroup(list, group, uid) {
        const o = this.o;
        const section = hel('div', 'sm-group', list);
        section.dataset.g = group.id;
        section.setAttribute('role', 'group');
        const title = hel('div', 'sm-gh', section);
        title.id = `${uid}-g-${group.id}`;
        section.setAttribute('aria-labelledby', title.id);
        hel('span', 'sdot', title);
        const name = hel('span', 'sm-gn', title);
        name.textContent = o.s('snipgroup_' + group.id);

        group.items.forEach(item => {
            const row = hel('div', 'sm-item', section);
            row.id = `${uid}-i-${item.id}`;
            row.dataset.id = item.id;
            row.dataset.g = item.group;
            row.setAttribute('role', 'option');
            row.setAttribute('aria-selected', 'false');
            const desc = o.s('snip_' + item.id);
            row.words = words(`${item.cmd} ${desc} ${name.textContent}`);

            const main = hel('div', 'sm-main', row);
            const code = hel('code', 'sm-cmd', main);
            code.textContent = item.cmd.trim();
            if (item.insertOnly) {
                hel('span', 'sm-arg', code).textContent = '…';
            }
            hel('span', 'sm-desc', main).textContent = desc;

            const acts = hel('div', 'sm-acts', row);
            if (!item.insertOnly) {
                const type = hel('button', 'sm-act sm-type', acts);
                type.type = 'button';
                type.tabIndex = -1;
                type.innerHTML = ICON_TYPE;
                type.title = o.s('ui_snip_insert');
                type.setAttribute('aria-label', `${o.s('ui_snip_insert')}: ${item.cmd.trim()}`);
                type.addEventListener('click', () => this.act(item, true));
            }
            const pin = hel('button', 'sm-act sm-pin', acts);
            pin.type = 'button';
            pin.tabIndex = -1;
            pin.innerHTML = ICON_STAR;
            pin.addEventListener('click', () => this.pin(item, row));
            this.paintPin(item, row);

            // Pressing a button must not take the focus from the search field.
            row.addEventListener('mousedown', event => event.preventDefault());
            row.addEventListener('click', event => {
                if (!event.target.closest('.sm-acts')) {
                    this.act(item, false);
                }
            });
            row.addEventListener('pointermove', event => {
                if (event.pointerType === 'mouse') {
                    this.setActive(row, false);
                }
            });
            this.rows.push(row);
        });
    }

    /**
     * Keys of the menu: arrows choose, Enter runs, Shift+Enter only types, Alt+P pins.
     *
     * @param {KeyboardEvent} event key event
     */
    keydown(event) {
        const visible = this.rows.filter(row => !row.hidden);
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            if (!visible.length) {
                return;
            }
            const i = visible.indexOf(this.active), step = event.key === 'ArrowDown' ? 1 : -1;
            this.setActive(visible[i < 0 ? 0 : (i + step + visible.length) % visible.length], true);
        } else if (event.key === 'Enter' && this.active && event.target === this.search) {
            event.preventDefault();
            this.act(this.items.get(this.active.dataset.id), event.shiftKey);
        } else if (event.code === 'KeyP' && event.altKey && this.active) {
            event.preventDefault();
            this.pin(this.items.get(this.active.dataset.id), this.active);
        }
    }

    /**
     * Marks a row as the current one and shows what it will type.
     *
     * @param {HTMLElement} row row
     * @param {boolean} scroll bring the row into view
     */
    setActive(row, scroll) {
        if (this.active !== row) {
            if (this.active) {
                this.active.classList.remove('active');
                this.active.setAttribute('aria-selected', 'false');
            }
            this.active = row;
            row.classList.add('active');
            row.setAttribute('aria-selected', 'true');
            this.search.setAttribute('aria-activedescendant', row.id);
            this.preview(this.items.get(row.dataset.id));
        }
        if (scroll) {
            row.scrollIntoView({block: 'nearest'});
        }
    }

    /**
     * Footer: the line as it will appear in the terminal, and what happens when it is chosen.
     *
     * @param {object} item snippet
     */
    preview(item) {
        const o = this.o;
        this.prev.textContent = '';
        hel('span', 'sm-p', this.prev).textContent = this.con.n.name + this.set.prompt;
        hel('code', 'sm-t', this.prev).textContent = item.cmd;
        hel('span', item.insertOnly ? 'sm-caret' : 'sm-ret', this.prev).textContent = item.insertOnly ? '' : '↵';
        const lines = [o.s(item.insertOnly ? 'ui_snip_hint_type' : 'ui_snip_hint_run')];
        if (this.set.kind === 'ios') {
            lines.push(o.s('ui_snip_ios_prep'));
        }
        this.note.textContent = lines.join(' ');
    }

    /**
     * Shows the rows that match the query: every word of it must begin a word of the command or of its description.
     */
    filter() {
        const query = words(this.search.value);
        let first = null;
        this.rows.forEach(row => {
            row.hidden = !query.every(q => row.words.some(w => w.startsWith(q)));
            if (!row.hidden && !first) {
                first = row;
            }
        });
        this.list.querySelectorAll('.sm-group').forEach(section => {
            section.hidden = !section.querySelector('.sm-item:not([hidden])');
        });
        this.none.hidden = !!first;
        if (this.active && this.active.hidden) {
            this.active.classList.remove('active');
            this.active.setAttribute('aria-selected', 'false');
            this.active = null;
            this.search.removeAttribute('aria-activedescendant');
        }
        if (first && !this.active) {
            this.setActive(first, true);
        } else if (!first) {
            this.prev.textContent = '';
            this.note.textContent = '';
        }
    }

    /**
     * Runs a snippet or only types it, then closes the menu.
     *
     * @param {object} item snippet
     * @param {boolean} typeOnly type the command without Enter
     */
    act(item, typeOnly) {
        const con = this.con;
        this.close(false);
        if (con.runSnippet(typeOnly ? item.typed : item.seq)) {
            con.flash(item.id);
        }
    }

    /**
     * Pins a snippet to the bar or takes it off.
     *
     * @param {object} item snippet
     * @param {HTMLElement} row its row
     */
    pin(item, row) {
        this.o.togglePin(this.set, item.id);
        this.paintPin(item, row);
    }

    /**
     * The star of a row shows whether the snippet is on the bar.
     *
     * @param {object} item snippet
     * @param {HTMLElement} row its row
     */
    paintPin(item, row) {
        const pinned = this.o.isPinned(this.set, item.id), button = row.querySelector('.sm-pin');
        const label = this.o.s(pinned ? 'ui_snip_unpin' : 'ui_snip_pin');
        button.setAttribute('aria-pressed', String(pinned));
        button.setAttribute('aria-label', `${label}: ${item.cmd.trim()}`);
        button.title = label;
    }

    /**
     * Puts the menu above the button, or under it when there is more room there.
     */
    place() {
        const menu = this.menu, r = this.anchor.getBoundingClientRect(), vw = innerWidth, vh = innerHeight;
        const width = Math.min(MENU_WIDTH, vw - 16), above = r.top - 8, below = vh - r.bottom - 8;
        menu.style.width = width + 'px';
        menu.style.left = clamp(r.left, 8, vw - width - 8) + 'px';
        if (above >= Math.min(300, below)) {
            menu.style.top = 'auto';
            menu.style.bottom = (vh - r.top + GAP) + 'px';
            menu.style.maxHeight = Math.max(160, Math.min(520, above - GAP)) + 'px';
        } else {
            menu.style.bottom = 'auto';
            menu.style.top = (r.bottom + GAP) + 'px';
            menu.style.maxHeight = Math.max(160, Math.min(520, below - GAP)) + 'px';
        }
    }

    /**
     * Closes the menu.
     *
     * @param {boolean} restoreFocus give the focus back to the terminal
     */
    close(restoreFocus) {
        if (!this.menu) {
            return;
        }
        document.removeEventListener('pointerdown', this.onDown, true);
        document.removeEventListener('focusin', this.onFocus, true);
        document.removeEventListener('keydown', this.onKey, true);
        window.removeEventListener('resize', this.onResize);
        this.menu.remove();
        this.menu = null;
        this.anchor.setAttribute('aria-expanded', 'false');
        this.anchor.classList.remove('open');
        const con = this.con;
        this.con = null;
        this.anchor = null;
        if (restoreFocus && con) {
            con.focus();
        }
    }

    /**
     * Closes the menu if it belongs to this console.
     *
     * @param {object} con console
     */
    closeFor(con) {
        if (this.con === con) {
            this.close(false);
        }
        this.hideTip();
    }

    /**
     * Hint over a button of the bar: the command, what it does and whether it runs at once.
     *
     * @param {HTMLElement} button the button
     * @param {object} item snippet
     */
    attachTip(button, item) {
        button.addEventListener('pointerenter', event => {
            if (event.pointerType === 'mouse') {
                clearTimeout(this.tipTimer);
                this.tipTimer = setTimeout(() => this.showTip(button, item), TIP_DELAY);
            }
        });
        button.addEventListener('pointerleave', () => this.hideTip());
        button.addEventListener('focus', () => {
            if (button.matches(':focus-visible')) {
                this.showTip(button, item);
            }
        });
        button.addEventListener('blur', () => this.hideTip());
        button.addEventListener('click', () => this.hideTip());
    }

    /**
     * Shows the hint.
     *
     * @param {HTMLElement} button the button
     * @param {object} item snippet
     */
    showTip(button, item) {
        if (this.menu || !button.isConnected) {
            return;
        }
        const o = this.o;
        const tip = this.tip || (this.tip = hel('div', 'snip-tip', o.root));
        tip.setAttribute('aria-hidden', 'true');
        tip.textContent = '';
        tip.dataset.g = item.group;
        const code = hel('code', 'st-cmd', tip);
        code.textContent = item.cmd.trim();
        if (item.insertOnly) {
            hel('span', 'sm-arg', code).textContent = '…';
        }
        hel('span', 'st-desc', tip).textContent = o.s('snip_' + item.id);
        hel('span', 'st-hint', tip).textContent = o.s(item.insertOnly ? 'ui_snip_hint_type' : 'ui_snip_hint_run');
        tip.hidden = false;
        tip.style.zIndex = '2000';
        const r = button.getBoundingClientRect(), t = tip.getBoundingClientRect();
        tip.style.left = clamp(r.left + r.width / 2 - t.width / 2, 8, Math.max(8, innerWidth - t.width - 8)) + 'px';
        tip.style.top = (r.top - t.height - GAP > 8 ? r.top - t.height - GAP : r.bottom + GAP) + 'px';
    }

    /**
     * Hides the hint.
     */
    hideTip() {
        clearTimeout(this.tipTimer);
        if (this.tip) {
            this.tip.hidden = true;
        }
    }
}
