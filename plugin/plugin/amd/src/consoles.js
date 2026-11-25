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
 * Consoles of the student interface: xterm.js terminals (and noVNC screens for nodes with a graphical console)
 * connected to the gateway, shown as a panel beside the scheme, as a grid with it, as windows over it, and on a phone
 * as a sheet the student dives into.
 *
 * Ported from prototypes/student-ui.html; the simulator of the prototype is replaced by the gateway connection
 * (mod_gns3lab/console). A console is tied to its node: the badge on the node, a preview of the output on the scheme
 * from the level "Addresses", and in the windows arrangement a line from the window to the node with the traffic.
 *
 * @module     mod_gns3lab/consoles
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

// Straight port of the prototype; these style rules stay relaxed.
/* eslint-disable curly, max-len, jsdoc/require-jsdoc, no-eq-null */

import {CISCO_TYPES, ConsoleConnection, KEYS, PORT_RE, THEME, loadXterm, note} from 'mod_gns3lab/console';
import {ConsoleWatch} from 'mod_gns3lab/consolewatch';
import {SnipMenu} from 'mod_gns3lab/snipmenu';
import {kindOf, notePrompt, snippetSet} from 'mod_gns3lab/snippets';
import {VncConnection, fullScreenSize, loadNovnc} from 'mod_gns3lab/vnc';
import {svgEl as el, hel, store, fill} from 'mod_gns3lab/dom';
import {clamp, esc} from 'mod_gns3lab/model';

const ICON_X = '<svg viewBox="0 0 16 16"><path d="M4 4l8 8M12 4l-8 8"/></svg>';
const ICON_MAX = '<svg viewBox="0 0 16 16"><path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4"/></svg>';
const ICON_MIN = '<svg viewBox="0 0 16 16"><path d="M3 12h10"/></svg>';
const ICON_FOLD = '<svg viewBox="0 0 16 16"><path d="M4 6l4 4 4-4"/></svg>';
const ICON_LIST = '<svg viewBox="0 0 16 16"><path d="M3 4h10M3 8h10M3 12h6"/></svg>';
const ICON_FULL = '<svg viewBox="0 0 16 16"><path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4"/></svg>';
const ICON_UNFULL = '<svg viewBox="0 0 16 16"><path d="M6 2v4H2M14 6h-4V2M10 14v-4h4M2 10h4v4"/></svg>';

/**
 * Font sizes of the terminals, px. Each console has its own choice and it stays in the browser, per activity and node:
 * a student makes one console larger without touching the others.
 */
export const FONT_STEPS = [9, 10, 11, 12, 13, 14, 16, 18, 20, 24, 28, 32];

/** Font size before the student chooses one, px. On a desktop most students went two steps up from 9, so it starts at 11. */
const FONT_DESKTOP = 11;
const FONT_MOBILE = 13;

/** Most remembered console font choices; the oldest are dropped. */
const FONT_KEEP = 200;
/** A tab of the panel starts to move after the pointer went this far sideways: a shorter move is still a click. */
const TAB_DRAG = 6;
/** Dragging a tab this close to an edge of the tab bar scrolls the bar. */
const TAB_EDGE = 28;

/** Ctrl + wheel turns the size by one step after this much scrolling (a wheel notch is 100, a trackpad pinch is many small ones). */
const FONT_WHEEL_STEP = 50;

/**
 * With pasting turned off for a node, input that arrives from the terminal in one piece longer than this many characters is
 * taken for a paste and dropped. A key gives one character, a word picked on a phone keyboard a few more.
 */
const TYPED_MAX = 16;

/** The marker a terminal in bracketed paste mode puts before pasted text. */
const PASTE_MARK = '\x1b[200~';

/**
 * Closed terminals kept connected, at most: the oldest one is disconnected for good when another is closed.
 * Reopening a kept terminal shows the screen as it was (nano, a pager, half-typed input) instead of a blank one.
 */
const PARK_MAX = 12;

/** The kinds of connection state that mean the console works now (a green dot). */
const STATE_DOT = {open: 'open', connecting: 'wait', waiting: 'wait', stopped: 'off', failed: 'bad', idle: 'wait', closed: 'off'};

/**
 * One console: the terminal, its connection, the bar under it (snippets and font size).
 *
 * A node with a graphical console (console_type "vnc") gets a noVNC screen instead of the terminal, and instead of the
 * bar a tab at the right edge of the screen with Ctrl+Alt+Del and full screen. The views do not tell them apart: both
 * have a host, a connection in `conn` and the same methods.
 */
/**
 * Whether a key press moves between the open consoles: Shift+Left or Shift+Right, with no other modifier.
 *
 * @param {KeyboardEvent} e key event
 * @returns {boolean}
 */
const cycleKey = e => e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight');

class Con {
    /**
     * Constructor.
     *
     * @param {Consoles} owner the views
     * @param {object} node node of the model
     */
    constructor(owner, node) {
        this.owner = owner;
        this.n = node;
        this.unread = false;
        this.min = false;
        // Closed by the student but still connected: out of every view, back as it was when opened again.
        this.parked = false;
        this.term = null;
        this.fit = null;
        this.conn = null;
        this.state = 'idle';
        this.vnc = node.consoleType === 'vnc';
        // The lab keeps the clipboard out of this console (a node of a library lab, lab_definition::NOPASTE).
        this.nopaste = !!node.nopaste;
        this.font = owner.loadFont(node.id);
        this.win = null;
        this.wpos = null;
        this.flowIn = 0;
        this.flowOut = 0;
        this.host = hel('div', 'termhost' + (this.vnc ? ' vnchost' : ''));
        this.viewport = hel('div', 'termview' + (this.vnc ? ' vncview' : ''), this.host);
        this.wheelSum = 0;
        // A screen in full screen: the size of the monitor it asks the node for.
        this.full = null;
        if (this.vnc) {
            this.buildScreenTab();
        } else {
            this.buildBar();
            this.host.addEventListener('wheel', event => this.wheel(event), {capture: true, passive: false});
        }
        this.ro = new ResizeObserver(() => {
            this.refit();
            this.fadeSnips();
        });
        this.ro.observe(this.host);
    }

    /** The bar under the terminal: the snippets menu, the pinned snippets, the font size, the fold button. */
    buildBar() {
        const o = this.owner, bar = hel('div', 'tbar', this.host);
        this.bar = bar;
        bar.setAttribute('role', 'toolbar');
        bar.setAttribute('aria-label', fill(o.s('ui_snippets_for'), this.n.name));
        this.quick = hel('div', 'snips');
        this.quick.setAttribute('role', 'group');
        this.quick.setAttribute('aria-label', o.s('ui_snippets'));
        if (!o.readonly) {
            // An observer cannot type, so he gets no snippets, only the font size.
            const b = hel('button', 'snipbtn', bar);
            b.type = 'button';
            b.innerHTML = ICON_LIST + '<span></span><i aria-hidden="true"></i>';
            b.querySelector('span').textContent = o.s('ui_snippets');
            b.setAttribute('aria-haspopup', 'dialog');
            b.setAttribute('aria-expanded', 'false');
            b.addEventListener('pointerdown', e => e.preventDefault());
            b.addEventListener('click', () => o.snip.toggle(this, b));
            this.menuBtn = b;
        }
        bar.appendChild(this.quick);
        this.quick.addEventListener('scroll', () => this.fadeSnips(), {passive: true});
        // The wheel over the strip scrolls it sideways: the scrollbar is hidden.
        this.quick.addEventListener('wheel', event => {
            const strip = this.quick;
            if (!event.deltaY || event.deltaX || strip.scrollWidth <= strip.clientWidth) return;
            strip.scrollLeft += event.deltaY;
            event.preventDefault();
        }, {passive: false});
        this.renderQuick();
        this.buildFont(bar);
        this.applyFont();
        this.buildFold(bar);
    }

    /**
     * The button at the end of the bar folds it to a thin strip and back: the terminal gets the room.
     *
     * @param {HTMLElement} bar Toolbar under the terminal.
     */
    buildFold(bar) {
        const o = this.owner, b = hel('button', 'tbar-fold', bar);
        b.type = 'button';
        b.innerHTML = ICON_FOLD;
        b.addEventListener('pointerdown', e => e.preventDefault());
        b.addEventListener('click', () => {
            o.foldBars(!o.barsFolded);
            this.focus();
        });
        this.foldBtn = b;
        this.applyFold();
    }

    /** Shows the bar folded or open, as the owner says. */
    applyFold() {
        if (!this.bar) return;
        const o = this.owner, folded = o.barsFolded, label = o.s(folded ? 'ui_bar_show' : 'ui_bar_hide');
        this.bar.classList.toggle('folded', folded);
        this.foldBtn.setAttribute('aria-expanded', String(!folded));
        this.foldBtn.setAttribute('aria-label', label);
        this.foldBtn.title = label;
        if (folded) {
            o.snip.closeFor(this);
            o.snip.hideTip();
        }
        this.refit();
        requestAnimationFrame(() => this.fadeSnips());
    }

    /** The snippets pinned to the bar. */
    renderQuick() {
        if (this.vnc) return;
        const o = this.owner, strip = this.quick;
        o.snip.hideTip();
        strip.textContent = '';
        if (o.readonly) {
            return;
        }
        const items = o.pinnedFor(this.n);
        if (!items.length) {
            hel('span', 'nopins', strip).textContent = o.s('ui_snip_nopins');
        }
        items.forEach(item => {
            const b = hel('button', 'snip', strip);
            b.type = 'button';
            b.dataset.id = item.id;
            b.dataset.g = item.group;
            hel('span', 'sdot', b);
            hel('span', 'sl', b).textContent = item.short + (item.insertOnly ? '…' : '');
            b.setAttribute('aria-label', fill(o.s(item.insertOnly ? 'ui_snippet_type' : 'ui_snippet_run'), item.cmd.trim()));
            b.addEventListener('pointerdown', e => e.preventDefault());
            b.addEventListener('click', () => {
                if (this.runSnippet(item.seq)) {
                    this.flash(item.id);
                }
            });
            o.snip.attachTip(b, item);
        });
        requestAnimationFrame(() => this.fadeSnips());
    }

    /** A fade at the edge of the strip says that more pinned snippets wait beyond it. */
    fadeSnips() {
        const strip = this.quick;
        if (!strip) return;
        strip.classList.toggle('more', strip.scrollLeft + strip.clientWidth < strip.scrollWidth - 2);
        strip.classList.toggle('less', strip.scrollLeft > 2);
    }

    /**
     * Sends a snippet to the device and makes this console the current one.
     *
     * @param {string} seq bytes
     * @returns {boolean} false: the console is not connected
     */
    runSnippet(seq) {
        const o = this.owner;
        if (!this.send(seq)) {
            o.toast(fill(o.s('ui_not_connected'), this.n.name));
            return false;
        }
        o.active = this.n.id;
        this.unread = false;
        o.paintBadges(this);
        o.markFocus();
        this.focus();
        return true;
    }

    /**
     * A short flash on the pinned button of a snippet that was just sent.
     *
     * @param {string} id snippet id
     */
    flash(id) {
        const b = Array.from(this.quick.querySelectorAll('.snip')).find(x => x.dataset.id === id);
        if (!b) return;
        b.classList.remove('sent');
        void b.offsetWidth;
        b.classList.add('sent');
        setTimeout(() => b.classList.remove('sent'), 700);
    }

    /**
     * Smaller, size, larger: the size belongs to this console only.
     *
     * @param {HTMLElement} bar Toolbar containing the font controls.
     */
    buildFont(bar) {
        const o = this.owner, g = hel('div', 'tfont', bar);
        g.setAttribute('role', 'group');
        g.setAttribute('aria-label', o.s('ui_font'));
        const button = (cls, text, label, action) => {
            const b = hel('button', cls, g);
            b.type = 'button';
            b.textContent = text;
            b.setAttribute('aria-label', label);
            b.title = label;
            b.addEventListener('pointerdown', e => e.preventDefault());
            b.addEventListener('click', () => {
                if (b.getAttribute('aria-disabled') !== 'true') action();
                this.focus();
            });
            return b;
        };
        this.fontDec = button('tf-btn tf-dec', 'A−', o.s('ui_font_smaller'), () => this.stepFont(-1));
        this.fontVal = button('tf-btn tf-val', '', '', () => this.resetFont());
        this.fontVal.setAttribute('aria-live', 'polite');
        this.fontInc = button('tf-btn tf-inc', 'A+', o.s('ui_font_larger'), () => this.stepFont(1));
    }

    /** Font size of this console, px: the student's choice or the default for the screen. */
    get fontSize() {
        return this.font || this.owner.defaultFont();
    }

    /**
     * Sets the font size of this console and remembers it.
     *
     * @param {number} size px; 0 goes back to the default, which follows the screen
     */
    setFont(size) {
        if (size === this.font) return;
        this.font = size;
        this.owner.saveFont(this.n.id, size);
        this.applyFont();
    }

    /**
     * One step larger (1) or smaller (-1).
     *
     * @param {number} dir Direction of the font size change.
     */
    stepFont(dir) {
        const i = FONT_STEPS.indexOf(this.fontSize);
        this.setFont(FONT_STEPS[clamp(i + dir, 0, FONT_STEPS.length - 1)]);
    }

    /** Back to the default size, which follows the screen: phone or desktop. */
    resetFont() {
        this.setFont(0);
    }

    /** The font size of this console: in the terminal and in the controls. */
    applyFont() {
        if (this.vnc) return;
        const o = this.owner, size = this.fontSize;
        this.fontVal.textContent = String(size);
        const label = fill(o.s('ui_font_reset'), size);
        this.fontVal.title = label;
        this.fontVal.setAttribute('aria-label', label);
        this.fontDec.setAttribute('aria-disabled', String(size <= FONT_STEPS[0]));
        this.fontInc.setAttribute('aria-disabled', String(size >= FONT_STEPS[FONT_STEPS.length - 1]));
        this.fontVal.classList.toggle('custom', size !== o.defaultFont());
        if (this.term && this.term.options.fontSize !== size) {
            this.term.options.fontSize = size;
            this.refit();
        }
    }

    /**
     * Ctrl + wheel over the terminal or its bar changes the font instead of zooming the page.
     *
     * @param {WheelEvent} event Wheel event over the terminal or toolbar.
     */
    wheel(event) {
        if (!(event.ctrlKey || event.metaKey)) return;
        event.preventDefault();
        event.stopPropagation();
        const delta = event.deltaMode === 0 ? event.deltaY : event.deltaY * 33;
        if (this.wheelSum * delta < 0) this.wheelSum = 0;
        this.wheelSum += delta;
        if (Math.abs(this.wheelSum) >= FONT_WHEEL_STEP) {
            this.stepFont(this.wheelSum < 0 ? 1 : -1);
            this.wheelSum = 0;
        }
    }

    /**
     * Sends input to the device.
     *
     * @param {string} seq bytes
     * @returns {boolean} false: the console is not connected
     */
    send(seq) {
        if (this.vnc || this.owner.readonly || !this.conn || this.conn.state !== 'open') return false;
        this.conn.send(seq);
        return true;
    }

    /**
     * The tab at the right edge of a screen (the top one is where desktops keep their panel): it opens a small menu with Ctrl+Alt+Del and full screen. Nothing else lies
     * over the picture, and the screen takes the whole tile.
     */
    buildScreenTab() {
        const o = this.owner, tools = hel('div', 'vnctools', this.host);
        const menu = hel('div', 'vncmenu', tools);
        menu.setAttribute('role', 'toolbar');
        menu.setAttribute('aria-label', fill(o.s('consolevnclabel'), this.n.name));
        menu.hidden = true;
        const tab = hel('button', 'vnctab', tools);
        tab.type = 'button';
        tab.innerHTML = ICON_FOLD;
        tab.title = o.s('consolevnctools');
        tab.setAttribute('aria-label', o.s('consolevnctools'));
        tab.setAttribute('aria-expanded', 'false');
        this.tools = {box: tools, menu, tab};
        const button = (html, text, label, action) => {
            const b = hel('button', 'vncbtn', menu);
            b.type = 'button';
            b.innerHTML = html;
            hel('span', '', b).textContent = text;
            b.title = label;
            // The accessible name keeps the visible text in it; otherwise the tooltip is only a hint.
            if (label.includes(text)) b.setAttribute('aria-label', label);
            b.addEventListener('click', () => {
                this.showTools(false);
                action();
            });
            return b;
        };
        // The screen keeps the keyboard focus while the tab or a button is pressed.
        tools.addEventListener('pointerdown', e => e.preventDefault());
        tab.addEventListener('click', () => this.showTools(menu.hidden));
        // An observer only looks (noVNC viewOnly): no keys to send, and full screen does not change the size of the screen.
        if (!o.readonly) {
            button('', 'Ctrl+Alt+Del', o.s('consolevncctrlaltdel'), () => {
                if (this.conn && this.conn.ctrlAltDel()) this.focus();
                else o.toast(fill(o.s('ui_not_connected'), this.n.name));
            });
        }
        if (document.fullscreenEnabled) {
            this.fullBtn = button(ICON_FULL, o.s('consolevncfullscreen'),
                o.s(o.readonly ? 'consolevncfullscreen' : 'consolevncfullscreenhint'), () => this.toggleFull());
        }
        menu.addEventListener('keydown', e => {
            if (e.key !== 'Escape') return;
            e.preventDefault();
            e.stopPropagation();
            this.showTools(false);
            this.focus();
        });
        // A press anywhere else closes the menu.
        this.onOutside = e => {
            if (!menu.hidden && !tools.contains(e.target)) this.showTools(false);
        };
        this.onFullChange = () => this.fullChanged();
        document.addEventListener('pointerdown', this.onOutside, true);
        document.addEventListener('fullscreenchange', this.onFullChange);
    }

    /**
     * Opens or closes the menu of the tab.
     *
     * @param {boolean} open open it
     */
    showTools(open) {
        const t = this.tools;
        t.menu.hidden = !open;
        t.tab.setAttribute('aria-expanded', String(open));
        t.box.classList.toggle('open', open);
    }

    /** Puts the screen on the whole monitor, or takes it back into the page. */
    toggleFull() {
        if (document.fullscreenElement === this.host) {
            document.exitFullscreen().catch(() => undefined);
            return;
        }
        this.host.requestFullscreen({navigationUI: 'hide'}).then(() => this.focus(), () => {
            this.owner.toast(this.owner.s('consolevncfullscreenfailed'));
        });
    }

    /**
     * Full screen began or ended (the button, Esc, F11, the console left the page). In full screen the node is asked
     * for the size of the monitor in device pixels, at most 2K, so that the picture is shown pixel for pixel; when full
     * screen ends, for the size it had before. A guest that does not follow such a request keeps its size, and noVNC
     * scales the picture as before.
     */
    fullChanged() {
        const full = document.fullscreenElement === this.host;
        if (full === !!this.full) return;
        const o = this.owner;
        this.full = full ? (fullScreenSize(window.screen.width, window.screen.height, window.devicePixelRatio) || {}) : null;
        this.host.classList.toggle('vncfull', full);
        if (this.fullBtn) {
            this.fullBtn.innerHTML = full ? ICON_UNFULL : ICON_FULL;
            hel('span', '', this.fullBtn).textContent = o.s(full ? 'consolevncfullscreenexit' : 'consolevncfullscreen');
            this.fullBtn.title = o.s(full ? 'consolevncfullscreenexit' : 'consolevncfullscreenhint');
            this.fullBtn.setAttribute('aria-label', this.fullBtn.title);
        }
        // Keys that the system or the browser would take (Esc, Win, Alt+Tab) go to the node while the screen is full
        // screen; holding Esc leaves it. Only Chromium has the Keyboard Lock API, elsewhere the keys stay as they are.
        const keyboard = navigator.keyboard;
        if (full && keyboard && keyboard.lock) keyboard.lock().catch(() => undefined);
        else if (!full && keyboard && keyboard.unlock) keyboard.unlock();
        if (!this.conn) return;
        if (full && this.full.w) this.conn.fitTo(this.full);
        else if (!full) this.conn.restoreSize();
    }

    /**
     * Where the size of the screen of the node before full screen is kept until it is given back: in the browser, per
     * activity and node, so that it survives a reload of the page in full screen.
     *
     * @returns {object} get(), set(size), clear()
     */
    screenSizes() {
        const key = this.owner.cmid + '/' + this.n.id;
        const all = () => {
            const value = store.get('vncsizes', {});
            return value && typeof value === 'object' ? value : {};
        };
        return {
            get: () => {
                const size = all()[key];
                return size && size.w > 0 && size.h > 0 ? {w: size.w, h: size.h} : null;
            },
            set: size => store.set('vncsizes', {...all(), [key]: {w: size.w, h: size.h}}),
            clear: () => {
                const sizes = all();
                delete sizes[key];
                store.set('vncsizes', sizes);
            },
        };
    }

    /** The screen of a node with a graphical console, connected to the gateway. */
    ensureScreen() {
        if (this.conn) return;
        const o = this.owner;
        this.screen = hel('div', 'vncscreen', this.viewport);
        this.screen.setAttribute('role', 'group');
        this.screen.setAttribute('aria-label', fill(o.s('consolevnclabel'), this.n.name));
        this.vnote = hel('div', 'vncnote', this.viewport);
        this.vnote.setAttribute('role', 'status');
        this.vnote.hidden = true;
        // The canvas takes the focus on a click; the screen is the current console from then on.
        this.screen.addEventListener('focusin', () => {
            if (o.active !== this.n.id) {
                o.active = this.n.id;
                o.markFocus();
            }
        });
        // Typed input and clicks light the line to the node in the windows arrangement.
        this.screen.addEventListener('keydown', () => this.activity('in'), true);
        this.screen.addEventListener('pointerdown', () => this.activity('in'), true);
        this.conn = new VncConnection({
            api: o.api, cmid: o.cmid, userid: o.userid, nodeId: this.n.id, RFB: o.RFB, screen: this.screen, strings: o.strings,
            sizes: this.screenSizes(),
            nodeStatus: () => o.nodeStatus(this.n.id),
            onState: state => this.setState(state),
            onNote: text => {
                this.vnote.textContent = text;
                this.vnote.hidden = !text;
            },
        });
        if (this.full && this.full.w) this.conn.fitTo(this.full);
        this.conn.connect();
    }

    ensureTerm() {
        if (this.term) return;
        const o = this.owner, {Terminal, FitAddon} = o.xterm;
        this.term = new Terminal({
            fontFamily: getComputedStyle(o.root).getPropertyValue('--mono').trim() || 'monospace',
            fontSize: this.fontSize, lineHeight: 1.15, cursorBlink: !o.readonly,
            scrollback: o.stage.mobile ? 1000 : 3000, theme: THEME, disableStdin: o.readonly,
        });
        this.fit = new FitAddon();
        this.term.loadAddon(this.fit);
        this.term.open(this.viewport);
        this.addKeyHandler();
        this.blockPaste();
        this.addPortLinks();
        this.term.onWriteParsed(() => this.output());
        this.term.onData(() => this.activity('in'));
        // What the student runs here also reaches the scheme as "seen in the console" (docs/live-state.md §8).
        this.watch = new ConsoleWatch(this.term, {
            hint: kindOf(this.n),
            onCommand: command => o.seen({nodeId: this.n.id, ...command}),
        });
        if (this.term.textarea) {
            this.term.textarea.addEventListener('focus', () => {
                if (o.active !== this.n.id) {
                    o.active = this.n.id;
                    o.markFocus();
                }
            });
        }
        this.conn = new ConsoleConnection({
            api: o.api, cmid: o.cmid, userid: o.userid, nodeId: this.n.id, term: this.term, strings: o.strings,
            nodeStatus: () => o.nodeStatus(this.n.id),
            transform: data => (this.pasted(data) ? '' : o.applyCtrl(data)),
            paced: CISCO_TYPES.includes(this.n.type),
            onState: state => this.setState(state),
        });
        this.term.write(note(o.s('consoleconnecting')));
        this.conn.connect();
    }

    /**
     * With pasting turned off for the node, keeps the clipboard and dragged text away from the terminal.
     *
     * xterm.js takes a paste (Ctrl+V, Shift+Insert, the menu of the browser, the middle button) from the paste event
     * of its text area, so the event stops here, on the way down. This holds a student back and no more: the page is
     * in their hands.
     */
    blockPaste() {
        const o = this.owner;
        if (!this.nopaste) return;
        const refuse = event => {
            event.preventDefault();
            event.stopPropagation();
            o.toast(o.s('consolenopaste'));
        };
        this.viewport.addEventListener('paste', refuse, true);
        this.viewport.addEventListener('drop', refuse, true);
        this.viewport.addEventListener('dragover', event => event.preventDefault(), true);
    }

    /**
     * Whether input of the terminal is a paste that the lab does not allow: the second line after blockPaste().
     *
     * Typing arrives a character at a time. What the terminal itself reports (arrows, function keys, the mouse)
     * is longer but starts with Esc. Snippets and the key strip do not come this way.
     *
     * @param {string} data what xterm.js produced
     * @returns {boolean}
     */
    pasted(data) {
        const o = this.owner;
        if (!this.nopaste) return false;
        const paste = data.includes(PASTE_MARK) || (data[0] !== '\x1b' && [...data].length > TYPED_MAX);
        if (paste) o.toast(o.s('consolenopaste'));
        return paste;
    }

    /** Ctrl+C with a selection copies; Ctrl+Shift+6 sends the Cisco break sequence. */
    addKeyHandler() {
        const o = this.owner, term = this.term;
        term.attachCustomKeyEventHandler(event => {
            if (event.type !== 'keydown') return true;
            if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'c' && term.hasSelection()) {
                if (navigator.clipboard) {
                    navigator.clipboard.writeText(term.getSelection()).then(() => o.toast(o.s('consolecopied')), () => o.toast(o.s('consolecopyfailed')));
                }
                return false;
            }
            if (event.ctrlKey && event.shiftKey && (event.code === 'Digit6' || event.key === '^')) {
                this.send('\x1e');
                return false;
            }
            // Shift+Left / Shift+Right move to the previous / next open console, as in Konsole; the device never sees them.
            if (cycleKey(event)) {
                event.preventDefault();
                o.cycle(event.key === 'ArrowLeft' ? -1 : 1);
                return false;
            }
            return true;
        });
    }

    /** Interface names in the output become links that show the port on the scheme. */
    addPortLinks() {
        const term = this.term, o = this.owner;
        term.registerLinkProvider({
            provideLinks: (y, callback) => {
                const line = term.buffer.active.getLine(y - 1);
                if (!line) {
                    callback(undefined);
                    return;
                }
                const links = [];
                for (const match of line.translateToString(true).matchAll(PORT_RE)) {
                    const port = match[1];
                    links.push({
                        text: port,
                        range: {start: {x: match.index + 1, y}, end: {x: match.index + port.length, y}},
                        decorations: {underline: true, pointerCursor: true},
                        activate: () => o.stage.showPort(this.n.id, port),
                    });
                }
                callback(links.length ? links : undefined);
            },
        });
    }

    setState(state) {
        this.state = state;
        // The first picture of a screen is all the output there is (docs/lab-constructor/README.md §2.2).
        if (state === 'open' && this.vnc) this.owner.onScreen(this.n.id);
        this.owner.paintState(this);
        this.host.querySelector('.creconnect')?.remove();
        if (state === 'failed' || state === 'stopped') {
            const b = hel('button', 'btn creconnect', this.viewport.parentNode);
            b.type = 'button';
            b.textContent = this.owner.s('consolereconnect');
            b.addEventListener('click', () => {
                b.remove();
                this.conn.attempt = 0;
                this.conn.connect();
            });
        }
    }

    output() {
        const o = this.owner;
        if (this.parked) return;
        this.activity('out');
        this.checkPrompt();
        if (!o.isVisible(this)) {
            if (!this.unread) {
                this.unread = true;
                o.paintBadges(this);
                o.renderTabsAndChips();
            }
        }
    }

    /**
     * A MikroTik node the manager did not name shows itself by the prompt of RouterOS: its snippets follow.
     */
    checkPrompt() {
        if (this.vnc || !this.term || this.n.type !== 'qemu' || kindOf(this.n) === 'routeros') return;
        const buf = this.term.buffer.active, line = buf.getLine(buf.baseY + buf.cursorY);
        if (line && notePrompt(this.n, line.translateToString(true))) {
            if (this.watch) this.watch.hint = 'routeros';
            this.owner.cons.forEach(c => c.n.id === this.n.id && c.renderQuick());
        }
    }

    /**
     * Typed input and output of the device in the windows arrangement: dots run along the line to the node.
     *
     * @param {string} dir Activity direction, in or out.
     */
    activity(dir) {
        const o = this.owner;
        if (o.stage.mobile || o.mode !== 'windows' || !this.win || this.min) return;
        const now = performance.now();
        if (dir === 'in') {
            this.flowIn = now + 1400;
            if (now - (this.lastPing || 0) > 150) {
                this.lastPing = now;
                o.pingNode(this.n, 'in');
            }
        } else {
            this.flowOut = now + 1100;
            if (now - (this.lastPong || 0) > 400) {
                this.lastPong = now;
                o.pingNode(this.n, 'out');
            }
        }
        o.drawLinks();
        clearTimeout(this.flowT);
        this.flowT = setTimeout(() => o.drawLinks(), 1500);
    }

    attach(container) {
        if (this.host.parentNode !== container) container.appendChild(this.host);
        if (this.vnc) this.ensureScreen();
        else this.ensureTerm();
        this.refit();
    }

    refit() {
        if (!this.term || !this.host.isConnected) return;
        cancelAnimationFrame(this.raf);
        this.raf = requestAnimationFrame(() => {
            if (this.viewport.offsetWidth > 40 && this.viewport.offsetHeight > 30) {
                try {
                    this.fit.fit();
                } catch (e) {
                    // The area is hidden; the next resize fits it.
                }
            }
        });
    }

    focus() {
        this.unread = false;
        this.owner.paintBadges(this);
        if (this.vnc) {
            if (this.conn) this.conn.focus();
            return;
        }
        if (this.term && !(this.owner.stage.mobile && matchMedia('(pointer: coarse)').matches)) this.term.focus();
    }

    dispose() {
        this.owner.snip.closeFor(this);
        this.ro.disconnect();
        if (this.onOutside) document.removeEventListener('pointerdown', this.onOutside, true);
        if (this.onFullChange) document.removeEventListener('fullscreenchange', this.onFullChange);
        if (this.full) {
            // The node gets its own size back now; the listener of full screen is gone already.
            if (this.conn) this.conn.restoreSize();
            if (document.fullscreenElement === this.host) document.exitFullscreen().catch(() => undefined);
        }
        clearTimeout(this.flowT);
        if (this.conn) this.conn.close();
        if (this.watch) this.watch.dispose();
        if (this.term) this.term.dispose();
        this.host.remove();
        if (this.win) this.win.remove();
    }
}

export class Consoles {
    /**
     * Constructor.
     *
     * @param {HTMLElement} root the application element
     * @param {object} options
     * @param {object} options.stage the stage
     * @param {object} options.strings language strings
     * @param {number} options.cmid course module id
     * @param {number} options.userid whose lab, 0 for the current user
     * @param {boolean} options.readonly observer: input is disabled
     * @param {Function} options.nodeStatus async nodeId => {status, stale} from a fresh session state
     * @param {Function} options.toast shows a short message
     * @param {Function} [options.onSeen] called with {nodeId, kind, command, lines, partial} for a command seen in a console
     * @param {Function} [options.onScreen] called with the node id when the screen of a node answers (a fog opens the node)
     * @param {Function} [options.api] the transport of web service calls, passed to the consoles
     */
    constructor(root, {stage, strings, cmid, userid, readonly, nodeStatus, toast, onSeen = () => {}, onScreen = () => {}, api}) {
        this.api = api;
        this.root = root;
        this.stage = stage;
        this.strings = strings;
        this.cmid = cmid;
        this.userid = userid;
        this.readonly = readonly;
        this.nodeStatus = nodeStatus;
        this.toast = toast;
        this.onSeen = onSeen;
        this.onScreen = onScreen;
        this.xterm = null;
        this.RFB = null;
        this.cons = new Map();
        // Terminals the student closed: hidden, still connected (PARK_MAX).
        this.parked = new Map();
        this.active = null;
        this.fonts = store.get('termfonts', {});
        if (!this.fonts || typeof this.fonts !== 'object' || Array.isArray(this.fonts)) this.fonts = {};
        this.pins = store.get('snippins', {});
        this.barsFolded = store.get('tbarfolded', false) === true;
        // The console panel pushed beyond the right edge, a tongue left on the edge brings it back.
        this.dockOff = store.get('dockoff', false) === true;
        this.tabDrag = false;
        this.tabDropped = false;
        this.snip = new SnipMenu(this);
        // Windows by default: students like them best; a mode the student chose is kept in the browser.
        this.mode = store.get('mode', 'windows');
        if (!['panel', 'grid', 'windows'].includes(this.mode)) this.mode = 'windows';
        this.diving = false;
        this.preDive = null;
        this.zTop = 10;
        this.ctrl = false;
        this.linked = null;
        this.flows = new Map();
        const r = name => root.querySelector(`[data-region="${name}"]`);
        this.r = r;
        this.box = r('stagebox');
        this.dock = r('dock');
        this.tabs = r('tabs');
        this.dockBody = r('dockBody');
        this.tongue = r('dockTongue');
        this.grid = r('grid');
        this.wins = r('wins');
        this.ov = r('ov');
        this.dive = r('dive');
        this.diveBody = r('diveBody');
        this.diveChips = r('diveChips');
        this.keys = r('keys');
        this.buildKeys();
        this.bind();
        this.layout();
    }

    s(key) {
        return this.strings[key] || key;
    }

    /**
     * A command was run in a console of a node and its output is on the screen.
     *
     * @param {object} event {nodeId, kind, command, lines, partial}
     */
    seen(event) {
        this.onSeen(event);
    }

    get size() {
        return this.cons.size;
    }

    has(nodeId) {
        return this.cons.has(nodeId);
    }

    /* ---------------- font size of each console and pinned snippets, kept in the browser ---------------- */

    defaultFont() {
        return this.stage.mobile ? FONT_MOBILE : FONT_DESKTOP;
    }

    /**
     * Key of the font choice of a console: per activity and node.
     *
     * @param {string} nodeId Node of the console.
     * @returns {string}
     */
    fontKey(nodeId) {
        return this.cmid + ':' + nodeId;
    }

    /**
     * The font size the student chose for the console of a node.
     *
     * @param {string} nodeId Node of the console.
     * @returns {number} px, 0 when nothing was chosen
     */
    loadFont(nodeId) {
        const size = Number(this.fonts[this.fontKey(nodeId)]);
        return FONT_STEPS.includes(size) ? size : 0;
    }

    /**
     * Remembers the font size of the console of a node; the newest choice goes last, the oldest are dropped.
     *
     * @param {string} nodeId Node of the console.
     * @param {number} size px, 0 forgets the choice
     */
    saveFont(nodeId, size) {
        const key = this.fontKey(nodeId);
        delete this.fonts[key];
        if (size) this.fonts[key] = size;
        const keys = Object.keys(this.fonts);
        keys.slice(0, Math.max(0, keys.length - FONT_KEEP)).forEach(k => delete this.fonts[k]);
        store.set('termfonts', this.fonts);
    }

    /**
     * Folds the bars under all terminals to a thin strip, or opens them; the choice stays in the browser.
     *
     * @param {boolean} folded Whether the bars are folded.
     */
    foldBars(folded) {
        this.barsFolded = folded;
        store.set('tbarfolded', folded);
        this.cons.forEach(c => c.applyFold());
    }

    /**
     * Ids of the snippets on the bar for this set of snippets: the choice of the student or the defaults.
     *
     * @param {object} set Snippet set with its pin key and defaults.
     */
    pinIds(set) {
        const ids = this.pins[set.pinKey];
        return Array.isArray(ids) ? ids : set.defaults;
    }

    isPinned(set, id) {
        return this.pinIds(set).includes(id);
    }

    /**
     * The snippets of a node that are pinned to the bar, in the order of the menu.
     *
     * @param {object} node Node of the topology model.
     */
    pinnedFor(node) {
        const set = snippetSet(node), ids = this.pinIds(set);
        return set.items.filter(item => ids.includes(item.id));
    }

    togglePin(set, id) {
        const ids = this.pinIds(set);
        this.pins = {...this.pins, [set.pinKey]: ids.includes(id) ? ids.filter(x => x !== id) : [...ids, id]};
        store.set('snippins', this.pins);
        this.cons.forEach(c => {
            if (snippetSet(c.n).pinKey === set.pinKey) c.renderQuick();
        });
    }

    /* ---------------- open and close ---------------- */

    /**
     * Opens the console of a node or brings it to the front.
     *
     * @param {object} node node of the model
     */
    async open(node) {
        const screen = node.consoleType === 'vnc';
        try {
            if (screen && !this.RFB) this.RFB = await loadNovnc();
            if (!screen && !this.xterm) this.xterm = await loadXterm();
        } catch (e) {
            this.toast(this.s(screen ? 'ui_screen_fail' : 'ui_term_fail'));
            return;
        }
        let c = this.cons.get(node.id) || this.unpark(node);
        if (!c) {
            c = new Con(this, node);
            c.titled = node.name;
            this.cons.set(node.id, c);
        }
        this.active = node.id;
        c.unread = false;
        this.paintBadges(c);
        this.stage.call('closeOverlays');
        this.stage.select(null);
        if (this.stage.mobile) {
            this.enterDive(c);
            return;
        }
        // A console opened from the scheme comes with its panel: hidden, it would open out of sight.
        if (this.mode === 'panel' && this.dockOff) {
            this.setDockOff(false, false);
            c.focus();
            return;
        }
        if (this.mode === 'windows' && c.min) {
            this.layout();
            this.restoreWin(c);
            return;
        }
        this.layout();
        c.focus();
    }

    /**
     * Closes a console in the views. A terminal stays connected in the background, so that opening it again shows
     * the screen of the device as it was: a new connection to a node gets nothing until the device prints, and a
     * full-screen program (nano, less) would leave a black terminal. A screen (noVNC) redraws itself on connection
     * and is disconnected.
     *
     * @param {string} nodeId Node of the console.
     */
    close(nodeId) {
        const c = this.cons.get(nodeId);
        if (!c) return;
        if (c.vnc || !c.conn || c.state === 'failed') c.dispose();
        else this.park(c);
        this.cons.delete(nodeId);
        this.stage.setBadge(nodeId, null);
        if (this.active === nodeId) this.active = this.cons.size ? Array.from(this.cons.keys()).pop() : null;
        if (this.diving) {
            if (this.active) this.enterDive(this.cons.get(this.active));
            else this.exitDive(true);
            return;
        }
        this.layout();
    }

    /**
     * Takes a closed terminal out of the views and keeps its connection; the oldest kept one is dropped past PARK_MAX.
     *
     * @param {Con} c the console
     */
    park(c) {
        this.snip.closeFor(c);
        c.parked = true;
        c.unread = false;
        c.min = false;
        c.host.remove();
        if (c.win) c.win.remove();
        c.win = null;
        this.parked.delete(c.n.id);
        this.parked.set(c.n.id, c);
        while (this.parked.size > PARK_MAX) {
            const [id, old] = this.parked.entries().next().value;
            this.parked.delete(id);
            old.dispose();
        }
    }

    /**
     * Brings back a terminal closed earlier, still connected.
     *
     * @param {object} node node of the model
     * @returns {Con|null} the console, or null when none is kept for the node
     */
    unpark(node) {
        const c = this.parked.get(node.id);
        if (!c) return null;
        this.parked.delete(node.id);
        c.parked = false;
        c.n = node;
        c.titled = node.name;
        this.cons.set(node.id, c);
        // The bar and the font may have changed while the terminal was hidden.
        c.renderQuick();
        c.applyFont();
        c.applyFold();
        return c;
    }

    /**
     * Closes every console and its connection: the session stopped or the page lost the seat.
     */
    closeAll() {
        [...this.cons.keys()].forEach(id => this.close(id));
        this.parked.forEach(c => c.dispose());
        this.parked.clear();
    }

    /**
     * The session reported fresh node states: a console of a node that runs again reconnects.
     *
     * @param {Map} statuses node id → status
     */
    updateNodes(statuses) {
        [this.cons, this.parked].forEach(map => map.forEach((c, id) => {
            if (c.conn) c.conn.nodeChanged(statuses.get(id) || null);
        }));
    }

    /**
     * The scheme uncovered a device whose console is open: tabs, tiles and chips show its name now, not the question mark.
     */
    refreshNames() {
        const base = this.stage.base;
        let changed = false;
        this.cons.forEach((c, id) => {
            // The scheme is loaded again now and then: the node of a console is a new object then.
            const fresh = base && base.nodes.get(id);
            if (fresh) c.n = fresh;
            if (c.titled !== c.n.name) {
                c.titled = c.n.name;
                changed = true;
            }
        });
        if (!changed) return;
        if (this.stage.mobile) this.renderTabsAndChips();
        else this.layout();
    }

    /* ---------------- state of a console on the screen ---------------- */

    isVisible(c) {
        if (this.stage.mobile) return this.diving && this.active === c.n.id;
        if (this.mode === 'grid') return true;
        if (this.mode === 'windows') return !c.min;
        return !this.dockOff && this.active === c.n.id;
    }

    paintBadges(c) {
        if (c.parked) return;
        this.stage.setBadge(c.n.id, {has: true, unread: c.unread, min: c.min});
    }

    paintState(c) {
        const cls = 'dotst ' + (STATE_DOT[c.state] || 'wait');
        this.root.querySelectorAll(`[data-con="${CSS.escape(c.n.id)}"] .dotst`).forEach(d => { d.className = cls; });
    }

    markFocus() {
        this.root.querySelectorAll('.tile,.win').forEach(t => t.classList.toggle('focus', t.dataset.id === this.active));
        const c = this.cons.get(this.active);
        if (c && c.unread) {
            c.unread = false;
            this.paintBadges(c);
        }
    }

    /* ---------------- the arrangements ---------------- */

    layout() {
        const stage = this.stage, mobile = stage.mobile;
        this.snip.close(false);
        this.root.classList.toggle('mob', mobile);
        this.box.dataset.mode = mobile ? 'mobile' : this.mode;
        this.box.classList.toggle('has-cons', this.cons.size > 0);
        this.box.classList.toggle('dock-off', !mobile && this.mode === 'panel' && this.dockOff && this.cons.size > 0);
        this.root.querySelectorAll('[data-region="modes"] button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.mode === this.mode)));
        this.grid.textContent = '';
        this.box.style.gridTemplateColumns = '';
        this.box.style.gridTemplateRows = '';
        if (mobile) {
            this.wins.querySelectorAll(':scope > .win').forEach(w => w.remove());
            this.cons.forEach(c => { c.win = null; });
            if (this.diving && this.cons.get(this.active)) this.cons.get(this.active).attach(this.diveBody);
        } else if (this.mode === 'panel') {
            this.renderPanel();
        } else if (this.mode === 'grid') {
            this.renderGrid();
        }
        if (!mobile && this.mode === 'windows') {
            this.renderWindows();
        } else if (!mobile) {
            this.wins.querySelectorAll(':scope > .win').forEach(w => w.remove());
            this.cons.forEach(c => { c.win = null; });
        }
        requestAnimationFrame(() => {
            this.cons.forEach(c => c.refit());
            this.drawLinks();
        });
    }

    headHtml(c, buttons) {
        const kind = c.n.state === 'silhouette' ? this.s('ui_fog_unknown') : this.s('ui_cat_' + c.n.cat);
        return `<span class="dotst ${STATE_DOT[c.state] || 'wait'}"></span><span class="nm">${esc(c.n.name)}<small>${esc(kind.toLowerCase())}</small></span>`
            + `${c.unread ? `<span class="unr" title="${esc(this.s('ui_new_output'))}"></span>` : ''}${buttons}`;
    }

    renderPanel() {
        const tabs = this.tabs, body = this.dockBody;
        // A tab being dragged stays under the pointer: the bar is drawn again when it is dropped.
        if (this.tabDrag) return;
        this.paintTongue();
        const scrolled = tabs.scrollLeft;
        tabs.textContent = '';
        if (!this.cons.size) {
            body.textContent = '';
            return;
        }
        if (!this.cons.has(this.active)) this.active = Array.from(this.cons.keys())[0];
        this.cons.forEach(c => {
            const t = hel('button', 'tab', tabs);
            t.type = 'button';
            t.dataset.con = c.n.id;
            t.setAttribute('role', 'tab');
            t.setAttribute('aria-selected', String(c.n.id === this.active));
            t.innerHTML = `<span class="dotst ${STATE_DOT[c.state] || 'wait'}"></span>${esc(c.n.name)}${c.unread ? '<span class="unr"></span>' : ''}<span class="x" data-x title="${esc(this.s('ui_close_console'))}">${ICON_X}</span>`;
            t.onclick = ev => {
                if (ev.target.closest('[data-x]')) {
                    this.close(c.n.id);
                    return;
                }
                this.active = c.n.id;
                c.unread = false;
                this.paintBadges(c);
                this.renderPanel();
                c.focus();
            };
        });
        tabs.scrollLeft = scrolled;
        // A console that became active (opened, chosen by Shift+arrows) shows its tab, the bar keeps its place otherwise.
        if (this.tabShown !== this.active) {
            this.tabShown = this.active;
            tabs.querySelector('[aria-selected="true"]')?.scrollIntoView({block: 'nearest', inline: 'nearest'});
        }
        const act = this.cons.get(this.active);
        Array.from(body.children).forEach(ch => {
            if (ch !== act.host) ch.remove();
        });
        act.attach(body);
    }

    /**
     * The tongue of the hidden panel: how many consoles are open there and whether one of them printed something new.
     */
    paintTongue() {
        this.r('dockCount').textContent = this.cons.size ? String(this.cons.size) : '';
        this.r('dockUnread').hidden = !Array.from(this.cons.values()).some(c => c.unread);
    }

    /**
     * Pushes the console panel beyond the right edge of the scheme or brings it back. The terminals stay connected
     * and keep their size; the scheme gets the whole width while the panel is away.
     *
     * @param {boolean} off true hides the panel
     * @param {boolean} [focus] give the keyboard to the tongue (hidden) or to the active console (back)
     */
    setDockOff(off, focus = true) {
        if (this.dockOff === off) return;
        this.dockOff = off;
        store.set('dockoff', off);
        if (off) {
            this.unmax(false);
            this.snip.close(false);
        } else {
            const dock = this.dock;
            dock.classList.remove('dock-in');
            // Reading the layout restarts the animation when the panel comes back twice in a row.
            dock.getBoundingClientRect();
            dock.classList.add('dock-in');
            dock.addEventListener('animationend', () => dock.classList.remove('dock-in'), {once: true});
            const c = this.cons.get(this.active);
            if (c) {
                c.unread = false;
                this.paintBadges(c);
            }
        }
        this.layout();
        if (!focus) return;
        if (off) this.tongue.focus({preventScroll: true});
        else this.cons.get(this.active)?.focus();
    }

    /**
     * Puts the open consoles in a new order: tabs, tiles, windows and Shift+arrows follow it.
     *
     * @param {string[]} ids node ids in the new order; consoles left out keep their place after them
     */
    reorder(ids) {
        const next = new Map();
        ids.forEach(id => {
            if (this.cons.has(id)) next.set(id, this.cons.get(id));
        });
        this.cons.forEach((c, id) => {
            if (!next.has(id)) next.set(id, c);
        });
        this.cons = next;
    }

    /**
     * The tab bar of the panel: a tab is dragged to another place, the wheel scrolls the bar sideways.
     */
    bindTabs() {
        const tabs = this.tabs;
        let d = null;
        tabs.addEventListener('wheel', e => {
            if (e.ctrlKey || tabs.scrollWidth <= tabs.clientWidth) return;
            // A sideways swipe of a trackpad scrolls the bar by itself.
            if (!e.deltaY || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
            tabs.scrollLeft += e.deltaMode === 1 ? e.deltaY * 40 : e.deltaY;
            e.preventDefault();
        }, {passive: false});
        tabs.addEventListener('pointerdown', e => {
            this.tabDropped = false;
            if (e.button !== 0 || this.mode !== 'panel' || e.target.closest('[data-x]')) return;
            const t = e.target.closest('.tab');
            if (t) d = {id: t.dataset.con, x: e.clientX, pid: e.pointerId, t: null};
        });
        tabs.addEventListener('pointermove', e => {
            if (!d || e.pointerId !== d.pid) return;
            if (!d.t) {
                if (Math.abs(e.clientX - d.x) < TAB_DRAG) return;
                d.t = tabs.querySelector(`.tab[data-con="${CSS.escape(d.id)}"]`);
                if (!d.t) {
                    d = null;
                    return;
                }
                // The bar holds the pointer: the tab itself moves in the DOM, which would drop a capture on it.
                tabs.setPointerCapture(e.pointerId);
                this.tabDrag = true;
                d.t.classList.add('dragging');
                tabs.classList.add('dragging');
            }
            const box = tabs.getBoundingClientRect();
            if (e.clientX < box.left + TAB_EDGE) tabs.scrollLeft -= TAB_EDGE / 2;
            else if (e.clientX > box.right - TAB_EDGE) tabs.scrollLeft += TAB_EDGE / 2;
            const before = Array.from(tabs.children).find(x => {
                if (x === d.t) return false;
                const r = x.getBoundingClientRect();
                return e.clientX < r.left + r.width / 2;
            });
            if (before && d.t.nextElementSibling !== before) tabs.insertBefore(d.t, before);
            else if (!before && tabs.lastElementChild !== d.t) tabs.appendChild(d.t);
        });
        const end = e => {
            if (!d || e.pointerId !== d.pid) return;
            const moved = d.t;
            d = null;
            if (!moved) return;
            this.tabDrag = false;
            this.tabDropped = true;
            tabs.classList.remove('dragging');
            this.reorder(Array.from(tabs.children).map(x => x.dataset.con));
            this.renderPanel();
            this.drawLinks();
        };
        tabs.addEventListener('pointerup', end);
        tabs.addEventListener('pointercancel', end);
        // The click that ends a drag does not choose the tab under the pointer.
        tabs.addEventListener('click', e => {
            if (this.tabDropped) {
                this.tabDropped = false;
                e.stopPropagation();
                e.preventDefault();
            }
        }, true);
    }

    renderGrid() {
        const n = this.cons.size + 1, cols = n <= 2 ? 2 : n <= 4 ? 2 : n <= 6 ? 3 : n <= 9 ? 3 : 4;
        if (this.cons.size === 0) {
            this.box.style.gridTemplateColumns = '1fr';
            return;
        }
        this.box.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
        this.box.style.gridTemplateRows = `repeat(${Math.ceil(n / cols)}, minmax(0, 1fr))`;
        // One row of tiles: the scheme tile can reach the bottom edge as in the other modes (styles.css).
        this.box.dataset.rows = Math.ceil(n / cols);
        this.cons.forEach(c => {
            const t = hel('div', 'tile', this.grid);
            t.dataset.id = c.n.id;
            t.dataset.con = c.n.id;
            const hd = hel('div', 'thead', t);
            hd.innerHTML = this.headHtml(c, `<button type="button" data-max title="${esc(this.s('ui_maximize'))}">${ICON_MAX}</button><button type="button" data-x title="${esc(this.s('ui_close_console'))}">${ICON_X}</button>`);
            const b = hel('div', 'tbody', t);
            hd.title = this.s('ui_max_title');
            this.focusOnTitle(c, hd);
            hd.addEventListener('dblclick', e => {
                if (!e.target.closest('button')) this.toggleMax(t);
            });
            hd.querySelector('[data-max]').onclick = () => this.toggleMax(t);
            hd.querySelector('[data-x]').onclick = () => this.close(c.n.id);
            t.addEventListener('pointerdown', () => {
                this.active = c.n.id;
                this.markFocus();
            });
            c.attach(b);
        });
        this.markFocus();
    }

    renderWindows() {
        const layer = this.wins, r = this.stage.wrap.getBoundingClientRect();
        let i = 0;
        this.cons.forEach(c => {
            if (!c.win) {
                const w = hel('div', 'win', layer);
                w.dataset.id = c.n.id;
                w.dataset.con = c.n.id;
                c.win = w;
                if (!c.wpos) {
                    const W = Math.min(620, r.width * 0.5), H = Math.min(300, r.height * 0.44);
                    c.wpos = {x: Math.max(8, r.width - W - 16 - i * 26), y: clamp(16 + i * 30, 8, Math.max(8, r.height - H - 60)), w: W, h: H};
                }
                Object.assign(w.style, {left: c.wpos.x + 'px', top: c.wpos.y + 'px', width: c.wpos.w + 'px', height: c.wpos.h + 'px', zIndex: ++this.zTop});
                const hd = hel('div', 'thead', w);
                const b = hel('div', 'tbody', w);
                w.addEventListener('pointerdown', () => {
                    w.style.zIndex = ++this.zTop;
                    this.active = c.n.id;
                    this.markFocus();
                });
                this.dragWin(c, hd);
                hd.title = this.s('ui_max_title');
                this.focusOnTitle(c, hd);
                hd.addEventListener('dblclick', e => {
                    if (!e.target.closest('button')) this.toggleMax(w);
                });
                new ResizeObserver(() => {
                    // A window over the whole scheme keeps the size it returns to.
                    if (c.win === w && w.offsetWidth && !w.classList.contains('max')) {
                        c.wpos.w = w.offsetWidth;
                        c.wpos.h = w.offsetHeight;
                        this.drawLinks();
                    }
                }).observe(w);
                w.addEventListener('pointerenter', () => this.showLink(c));
                w.addEventListener('pointerleave', () => this.showLink(null));
                c.attach(b);
            }
            c.win.querySelector('.thead').innerHTML = this.headHtml(c, `<button type="button" data-min title="${esc(this.s('ui_minimize'))}">${ICON_MIN}</button><button type="button" data-x title="${esc(this.s('ui_close_console'))}">${ICON_X}</button>`);
            c.win.querySelector('[data-min]').onclick = ev => {
                ev.stopPropagation();
                this.minimizeWin(c);
            };
            c.win.querySelector('[data-x]').onclick = ev => {
                ev.stopPropagation();
                this.close(c.n.id);
            };
            c.win.hidden = c.min;
            i++;
        });
        layer.querySelectorAll('.win').forEach(w => {
            if (!this.cons.has(w.dataset.id)) w.remove();
        });
        this.markFocus();
    }

    /**
     * A click on the title of a tile or a window gives that console the keyboard: the title itself takes no input, so
     * without this the press only took the focus away from the terminal. After a drag of a window as well.
     *
     * @param {object} c console
     * @param {HTMLElement} hd its title bar
     */
    focusOnTitle(c, hd) {
        hd.addEventListener('click', e => {
            if (e.target.closest('button')) return;
            this.active = c.n.id;
            this.markFocus();
            c.focus();
        });
    }

    dragWin(c, hd) {
        let s = null;
        hd.addEventListener('pointerdown', e => {
            if (e.target.closest('button') || (c.win && c.win.classList.contains('max'))) return;
            s = {x: e.clientX, y: e.clientY, ox: c.wpos.x, oy: c.wpos.y};
            hd.setPointerCapture(e.pointerId);
            this.showLink(c);
        });
        hd.addEventListener('pointermove', e => {
            if (!s) return;
            const r = this.stage.wrap.getBoundingClientRect();
            c.wpos.x = clamp(s.ox + e.clientX - s.x, -c.wpos.w + 80, r.width - 80);
            c.wpos.y = clamp(s.oy + e.clientY - s.y, 0, r.height - 36);
            c.win.style.left = c.wpos.x + 'px';
            c.win.style.top = c.wpos.y + 'px';
            this.showLink(c);
        });
        const end = () => {
            if (s) {
                s = null;
                this.showLink(null);
            }
        };
        hd.addEventListener('pointerup', end);
        hd.addEventListener('pointercancel', end);
    }

    showLink(c) {
        this.linked = c;
        this.drawLinks();
    }

    /**
     * Where the line meets the window: always a top corner, the one nearer the node. A window right of the node is
     * joined at its top left corner, a window left of it at its top right one, so the end stays put while it moves.
     *
     * @param {object} c console
     * @param {{x: number, y: number}} p node on screen
     * @returns {{x: number, y: number}}
     */
    winAnchor(c, p) {
        const {x, y, w} = c.wpos;
        return {x: p.x <= x + w / 2 ? x : x + w, y};
    }

    /** A dashed line from a console window to its node; typed input runs to the node, the answer runs back. */
    drawLinks() {
        const ov = this.ov, now = performance.now(), want = new Set();
        this.cons.forEach(c => {
            if (this.stage.mobile || this.mode !== 'windows' || !c.win || c.min || c.win.hidden) return;
            const fin = c.flowIn > now, fout = c.flowOut > now;
            if (this.linked !== c && !fin && !fout) return;
            want.add(c);
            let f = this.flows.get(c);
            if (!f) {
                const g = el('g', {class: 'flow'}, ov);
                f = {g, base: el('line', {class: 'fl-base'}, g), fin: el('line', {class: 'fl-in'}, g), fout: el('line', {class: 'fl-out'}, g), dot: el('circle', {r: 4, class: 'fl-node'}, g)};
                this.flows.set(c, f);
            }
            const p = this.stage.toScreen(c.n.cx, c.n.cy), w = this.winAnchor(c, p);
            for (const ln of [f.base, f.fin, f.fout]) {
                ln.setAttribute('x1', w.x);
                ln.setAttribute('y1', w.y);
                ln.setAttribute('x2', p.x);
                ln.setAttribute('y2', p.y);
            }
            f.dot.setAttribute('cx', p.x);
            f.dot.setAttribute('cy', p.y);
            f.fin.classList.toggle('on', fin);
            f.fout.classList.toggle('on', fout);
            f.g.classList.toggle('hover', this.linked === c);
        });
        this.flows.forEach((f, c) => {
            if (!want.has(c)) {
                f.g.remove();
                this.flows.delete(c);
            }
        });
    }

    pingNode(n, dir) {
        const p = this.stage.toScreen(n.cx, n.cy), c = el('circle', {cx: p.x, cy: p.y, r: 24, class: 'ping ' + dir}, this.ov);
        setTimeout(() => c.remove(), 650);
    }

    minimizeWin(c) {
        const w = c.win;
        if (!w) return;
        const p = this.stage.toScreen(c.n.cx, c.n.cy), cx = c.wpos.x + c.wpos.w / 2, cy = c.wpos.y + c.wpos.h / 2;
        w.classList.add('anim');
        w.style.transform = `translate(${p.x - cx}px, ${p.y - cy}px) scale(.05)`;
        w.style.opacity = '0';
        this.showLink(null);
        setTimeout(() => {
            w.hidden = true;
            w.classList.remove('anim');
            c.min = true;
            this.paintBadges(c);
            this.drawLinks();
        }, this.stage.reduced ? 0 : 270);
    }

    restoreWin(c) {
        const w = c.win;
        if (!w) return;
        c.min = false;
        this.paintBadges(c);
        const p = this.stage.toScreen(c.n.cx, c.n.cy), cx = c.wpos.x + c.wpos.w / 2, cy = c.wpos.y + c.wpos.h / 2;
        w.style.transform = `translate(${p.x - cx}px, ${p.y - cy}px) scale(.05)`;
        w.style.opacity = '0';
        w.hidden = false;
        void w.offsetWidth;
        w.classList.add('anim');
        w.style.transform = '';
        w.style.opacity = '';
        w.style.zIndex = ++this.zTop;
        setTimeout(() => {
            w.classList.remove('anim');
            c.refit();
            c.focus();
        }, this.stage.reduced ? 0 : 270);
        this.active = c.n.id;
        this.markFocus();
    }

    /* ---------------- phone: diving into a console ---------------- */

    enterDive(c) {
        if (!this.diving) this.preDive = {...this.stage.view};
        this.diving = true;
        this.active = c.n.id;
        c.unread = false;
        this.paintBadges(c);
        this.stage.markHinted();
        const chip = this.stage.r('chip');
        if (chip) chip.hidden = true;
        const d = this.dive, was = !d.hidden;
        d.hidden = false;
        if (!was) {
            d.classList.remove('in');
            void d.offsetWidth;
            d.classList.add('in');
        }
        if (this.stage.wheel) this.stage.wheel.close();
        // The key strip sends text to a terminal; a screen has no use for it.
        this.keys.hidden = c.vnc;
        this.fitDive();
        c.attach(this.diveBody);
        Array.from(this.diveBody.children).forEach(ch => {
            if (ch !== c.host) ch.remove();
        });
        this.renderTabsAndChips();
        const top = this.stage.wrap.getBoundingClientRect().height - d.offsetHeight;
        this.stage.zoomToNode(c.n, 1.7, Math.max(60, top / 2 + 10));
        setTimeout(() => c.refit(), 280);
    }

    exitDive(restore) {
        this.diving = false;
        this.dive.hidden = true;
        this.stage.showDiveHint('');
        if (restore && this.preDive) this.stage.animateTo(this.preDive, 320);
        this.preDive = null;
        this.renderTabsAndChips();
    }

    /** The sheet follows the visible part of the screen: the system keyboard covers the lower part of it. */
    fitDive() {
        const d = this.dive;
        if (d.hidden) return;
        const vv = window.visualViewport, H = vv ? vv.height : innerHeight, off = vv ? vv.offsetTop : 0;
        const kb = Math.max(0, innerHeight - H - off);
        d.style.bottom = kb + 'px';
        d.style.height = Math.round(H * (kb > 80 ? 0.9 : 0.68)) + 'px';
        const c = this.cons.get(this.active);
        if (c) c.refit();
    }

    /**
     * Pinch over a node that has a console: show what happens when the fingers are lifted.
     *
     * @param {object|null} n Candidate node, or null when no node is selected.
     */
    diveCandidate(n) {
        if (n && !(this.diving && this.active === n.id)) this.stage.showDiveHint(fill(this.s('ui_dive_release'), n.name));
        else this.stage.showDiveHint('');
    }

    diveRelease(n) {
        this.stage.showDiveHint('');
        if (n && !(this.diving && this.active === n.id)) {
            this.open(n);
            return;
        }
        if (this.diving && this.stage.view.k < 1.1) this.exitDive(true);
    }

    /**
     * Chips of the open consoles in the phone sheet, state marks in the tabs of the other arrangements.
     */
    renderTabsAndChips() {
        if (this.stage.mobile) {
            const box = this.diveChips;
            box.textContent = '';
            this.cons.forEach(c => {
                const b = hel('button', 'dchip', box);
                b.type = 'button';
                b.dataset.con = c.n.id;
                b.setAttribute('aria-pressed', String(c.n.id === this.active));
                b.innerHTML = `<span class="dotst ${STATE_DOT[c.state] || 'wait'}"></span>${esc(c.n.name)}${c.unread ? '<span class="unr"></span>' : ''}`;
                b.onclick = () => this.enterDive(c);
            });
            return;
        }
        // The other arrangements: tabs, tiles and windows are drawn again with their marks.
        if (this.mode === 'panel') this.renderPanel();
        else if (this.mode === 'grid') {
            this.root.querySelectorAll('.tile').forEach(t => {
                const c = this.cons.get(t.dataset.id);
                if (!c) return;
                const u = t.querySelector('.unr');
                if (c.unread && !u) t.querySelector('.nm').insertAdjacentHTML('afterend', '<span class="unr"></span>');
                if (!c.unread && u) u.remove();
            });
        } else if (this.mode === 'windows') {
            this.cons.forEach(c => {
                if (!c.win) return;
                const u = c.win.querySelector('.unr');
                if (c.unread && !u) c.win.querySelector('.nm').insertAdjacentHTML('afterend', '<span class="unr"></span>');
                if (!c.unread && u) u.remove();
            });
        }
    }

    /**
     * The page became phone sized or the other way round.
     *
     * @param {boolean} mobile Whether the page now uses the phone layout.
     */
    mobileChanged(mobile) {
        if (!mobile && this.diving) this.exitDive(false);
        this.cons.forEach(c => c.applyFont());
        this.layout();
        this.renderTabsAndChips();
    }

    /* ---------------- key strip of the phone sheet ---------------- */

    buildKeys() {
        const strip = this.keys;
        strip.setAttribute('aria-label', this.s('consolekeys'));
        KEYS.forEach(key => {
            const b = hel('button', 'key', strip);
            b.type = 'button';
            b.textContent = key.label;
            b.disabled = this.readonly;
            const label = key.ctrl ? this.s('keyctrl') : key.key ? this.s(key.key) : '';
            if (label) {
                b.title = label;
                b.setAttribute('aria-label', label);
            }
            if (key.ctrl) {
                b.setAttribute('aria-pressed', 'false');
                this.ctrlButton = b;
            }
            b.addEventListener('pointerdown', e => e.preventDefault());
            b.addEventListener('click', () => {
                if (key.ctrl) {
                    this.setCtrl(!this.ctrl);
                    return;
                }
                const c = this.cons.get(this.active);
                if (c) c.send(key.seq);
            });
        });
    }

    setCtrl(on) {
        this.ctrl = on;
        if (this.ctrlButton) this.ctrlButton.setAttribute('aria-pressed', String(on));
    }

    /**
     * The next typed letter becomes a control character while Ctrl of the strip is pressed.
     *
     * @param {string} data Typed terminal input.
     */
    applyCtrl(data) {
        if (!this.ctrl) return data;
        this.setCtrl(false);
        if (data.length === 1 && /[a-z@[\\\]^_]/i.test(data)) return String.fromCharCode(data.toUpperCase().charCodeAt(0) % 32);
        return data;
    }

    /* ---------------- controls ---------------- */

    bind() {
        this.tabs.title = this.s('ui_tabs_title');
        this.bindTabs();
        this.r('dockHide').onclick = () => this.setDockOff(true);
        this.tongue.onclick = () => this.setDockOff(false);
        this.tabs.addEventListener('dblclick', e => {
            if (this.mode === 'panel' && !e.target.closest('[data-x]')) this.toggleMax(this.r('dock'));
        });
        this.root.querySelectorAll('[data-region="modes"] button').forEach(b => {
            b.onclick = () => {
                this.unmax(false);
                this.mode = b.dataset.mode;
                store.set('mode', this.mode);
                // Choosing the panel shows it, also when it was pushed beyond the edge before.
                if (this.mode === 'panel' && this.dockOff) {
                    this.dockOff = false;
                    store.set('dockoff', false);
                }
                if (this.mode === 'windows') {
                    this.cons.forEach(c => {
                        c.min = false;
                        this.paintBadges(c);
                    });
                }
                if (this.mode === 'grid' && !this.cons.size) this.toast(this.s('ui_grid_hint'));
                this.layout();
            };
        });
        this.splitter();
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape' && !this.stage.mobile) this.unmax();
        });
        // Shift+Left / Shift+Right also work from the buttons of a console; a terminal handles them itself (addKeyHandler).
        this.root.addEventListener('keydown', e => {
            if (!cycleKey(e) || e.target.closest('.xterm') || !e.target.closest('.tile,.win,.dock,.dive')) return;
            e.preventDefault();
            this.cycle(e.key === 'ArrowLeft' ? -1 : 1);
        });
        const vv = window.visualViewport;
        if (vv) {
            vv.addEventListener('resize', () => this.fitDive());
            vv.addEventListener('scroll', () => this.fitDive());
        }
        this.r('diveBack').onclick = () => this.exitDive(true);
        this.r('diveClose').onclick = () => {
            const c = this.cons.get(this.active);
            if (c) this.close(c.n.id);
        };
        // Pinching the sheet apart or together leaves it, like pinching the scheme out of a node.
        let d0 = 0;
        const dist = t => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
        this.dive.addEventListener('touchstart', e => {
            d0 = e.touches.length === 2 ? dist(e.touches) : 0;
        }, {passive: true});
        this.dive.addEventListener('touchmove', e => {
            if (d0 && e.touches.length === 2 && dist(e.touches) < d0 * 0.6) {
                d0 = 0;
                this.exitDive(true);
            }
        }, {passive: true});
    }

    /**
     * Moves to the previous or the next open console, round, in the order they were opened, in every arrangement:
     * the panel shows its tab, the grid and the windows give it the keyboard (a minimized window comes back, a window
     * goes on top), a console spread over the scheme hands the spread over to it.
     *
     * @param {number} dir -1 previous, 1 next
     */
    cycle(dir) {
        const list = Array.from(this.cons.values());
        if (list.length < 2) return;
        const at = Math.max(0, list.findIndex(c => c.n.id === this.active));
        const c = list[(at + dir + list.length) % list.length];
        this.active = c.n.id;
        c.unread = false;
        this.paintBadges(c);
        if (this.stage.mobile) {
            if (this.diving) this.enterDive(c);
            return;
        }
        const spread = this.root.querySelector('.tile.max,.win.max');
        if (this.mode === 'panel') {
            this.renderPanel();
        } else if (this.mode === 'windows' && c.min) {
            if (spread) spread.classList.remove('max');
            this.restoreWin(c);
            if (spread) c.win.classList.add('max');
            this.afterMax(c);
            return;
        } else if (this.mode === 'windows' && c.win) {
            c.win.style.zIndex = ++this.zTop;
        }
        if (spread) {
            const el = this.mode === 'windows' ? c.win : this.root.querySelector(`.tile[data-id="${CSS.escape(c.n.id)}"]`);
            spread.classList.remove('max');
            if (el) el.classList.add('max');
        }
        this.afterMax(c);
    }

    /**
     * A double click on the title of a console (the tab bar in the panel mode) spreads it over the whole scheme; another
     * one, Escape or a change of mode puts it back.
     *
     * @param {HTMLElement} el the tile, the window or the dock
     */
    toggleMax(el) {
        if (this.stage.mobile) return;
        const on = !el.classList.contains('max');
        // The console whose title was double clicked keeps (or gets) the keyboard both ways.
        const c = this.cons.get(el.dataset.con || this.active);
        this.unmax(false);
        el.classList.toggle('max', on);
        this.box.classList.toggle('con-max', on);
        this.afterMax(c);
    }

    /**
     * Puts a console spread over the scheme back where it was.
     *
     * @param {boolean} [refocus] give the keyboard back to the active console (Escape); a change of mode does not
     */
    unmax(refocus = true) {
        const maxed = this.root.querySelectorAll('.tile.max,.win.max,.dock.max');
        if (!maxed.length) return;
        maxed.forEach(el => el.classList.remove('max'));
        this.box.classList.remove('con-max');
        this.afterMax(refocus ? this.cons.get(this.active) : null);
    }

    /**
     * Refits the terminals to their new size and gives the keyboard to a console.
     *
     * @param {object|null} c console to focus, or null
     */
    afterMax(c) {
        if (c) {
            this.active = c.n.id;
            this.markFocus();
        }
        requestAnimationFrame(() => {
            this.cons.forEach(x => x.refit());
            this.drawLinks();
            if (c) c.focus();
        });
    }

    splitter() {
        const sp = this.r('splitter'), box = this.box;
        let s = null;
        sp.addEventListener('pointerdown', e => {
            s = {x: e.clientX, w: this.dock.offsetWidth};
            sp.setPointerCapture(e.pointerId);
            sp.classList.add('drag');
        });
        sp.addEventListener('pointermove', e => {
            if (!s) return;
            const w = clamp(s.w - (e.clientX - s.x), 320, box.offsetWidth * 0.72);
            box.style.setProperty('--dockw', w + 'px');
        });
        const end = () => {
            if (!s) return;
            s = null;
            sp.classList.remove('drag');
            store.set('dockw', box.style.getPropertyValue('--dockw'));
            this.cons.forEach(c => c.refit());
        };
        sp.addEventListener('pointerup', end);
        sp.addEventListener('pointercancel', end);
        const saved = store.get('dockw', '');
        if (saved && /^\d+(\.\d+)?px$/.test(saved)) box.style.setProperty('--dockw', saved);
    }
}
