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
 * Device consoles: xterm.js terminals in tabs, connected to the console gateway (docs/SPEC.md §4).
 *
 * The browser talks to the gateway directly with a one-time ticket from mod_gns3lab_get_console_ticket;
 * Moodle never sees the WebSocket. Every (re)connection takes a new ticket.
 *
 * xterm.js is loaded on demand, when the first console opens.
 *
 * @module     mod_gns3lab/console
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';
import {getStrings} from 'core/str';
import {CommandBar} from 'mod_gns3lab/keyboard';
import {PLACEHOLDERS, SETS} from 'mod_gns3lab/commands';

/** Close code of the gateway: the console closed or is unavailable on the slot side. */
const CLOSE_UPSTREAM = 4000;

/**
 * Largest input frame, in UTF-16 code units: at most 12 KB of UTF-8, well below the 64 KB the gateway takes.
 * A pasted configuration goes out in several frames.
 */
const MAX_FRAME = 4096;

/** Reconnection pauses, seconds; the last one repeats. */
const BACKOFF = [1, 2, 4, 8, 16, 30];

/** Keys for phones and for what a desktop browser keeps for itself. */
const KEYS = [
    {label: 'Tab', seq: '\t'},
    {label: '?', seq: '?'},
    {label: 'Ctrl', ctrl: true},
    {label: 'Esc', seq: '\x1b'},
    {label: '↑', seq: '\x1b[A'},
    {label: '↓', seq: '\x1b[B'},
    {label: '←', seq: '\x1b[D'},
    {label: '→', seq: '\x1b[C'},
    {label: '^Z', seq: '\x1a'},
    {label: '^C', seq: '\x03'},
    {label: '^⇧6', seq: '\x1e', key: 'keybreak'},
    {label: '|', seq: '|'},
    {label: '␣', seq: ' ', key: 'keyspace'},
];

/** Interface names in the output that become links to the port on the topology. */
const PORT_RE = /\b((?:GigabitEthernet|FastEthernet|Ethernet|Serial|Loopback|Gi|Fa|Et|Se|Lo|e)\d+(?:\/\d+){0,2})\b/g;

/** Terminal colours: the console stays dark in both themes. */
const THEME = {
    background: '#0f1720', foreground: '#d6dee5', cursor: '#5aa8f2', cursorAccent: '#0f1720',
    selectionBackground: 'rgba(90,168,242,.32)', black: '#1b2631', red: '#ee6b62', green: '#3cc47a', yellow: '#e0b13a',
    blue: '#5aa8f2', magenta: '#c792ea', cyan: '#4fc1c9', white: '#d6dee5', brightBlack: '#7d8b97', brightRed: '#ff8a80',
    brightGreen: '#6ee09e', brightYellow: '#f2cc60', brightBlue: '#8cc4ff', brightMagenta: '#ddb3f6', brightCyan: '#7fdde3',
    brightWhite: '#ffffff',
};

const STRING_KEYS = [
    'consoleconnecting', 'consoleconnected', 'consolereconnected', 'consolereconnecting', 'consolenodestopped',
    'consoleerror', 'consolereadonly', 'consoleclose', 'consolecopied', 'consolecopyfailed', 'consolekeys',
    'consoletabs', 'keyctrl', 'keybreak', 'keyspace', 'consolereconnect', 'consolenodesunknown',
    'kb_commands', 'kb_keyboard', 'kb_send', 'kb_placeholder', 'kb_fill', 'kb_needif', 'kb_needrouter', 'kb_ports',
    ...PLACEHOLDERS.map(p => 'ph_' + p),
    ...[...new Set(SETS.flatMap(set => set.groups.map(g => 'kbg_' + g.key)))],
];

let xtermPromise = null;

/**
 * Loads xterm.js and the fit addon once.
 *
 * @returns {Promise<{Terminal: Function, FitAddon: Function}>}
 */
const loadXterm = () => {
    if (!xtermPromise) {
        xtermPromise = Promise.all([import('mod_gns3lab/xterm'), import('mod_gns3lab/xterm-addon-fit')])
            .then(([xterm, fit]) => ({Terminal: xterm.Terminal, FitAddon: fit.FitAddon}));
    }
    return xtermPromise;
};

/**
 * Whether the page is phone sized.
 *
 * @returns {boolean}
 */
const isMobile = () => window.matchMedia('(max-width: 760px)').matches;

/**
 * Text in dim grey for messages from the page itself, not from the device.
 *
 * @param {string} text message
 * @returns {string}
 */
const note = text => `\r\n\x1b[2m${text}\x1b[0m\r\n`;

/**
 * One WebSocket connection of one terminal to the gateway, with reconnection.
 *
 * States: idle, connecting, open, waiting (pause before reconnecting), stopped (the node is stopped),
 * failed (no ticket, nothing to retry), closed (the tab is closed).
 */
export class ConsoleConnection {
    /**
     * Constructor.
     *
     * @param {object} options
     * @param {number} options.cmid course module id
     * @param {number} options.userid whose lab, 0 for the current user
     * @param {string} options.nodeId node id in the live project
     * @param {object} options.term xterm.js Terminal
     * @param {object} options.strings language strings
     * @param {Function} options.nodeStatus async () => {status, stale} from a fresh session state
     * @param {Function} options.onState called with the new state name
     * @param {Function} [options.transform] changes typed input before it is sent
     */
    constructor({cmid, userid, nodeId, term, strings, nodeStatus, onState, transform = data => data}) {
        this.cmid = cmid;
        this.userid = userid;
        this.nodeId = nodeId;
        this.term = term;
        this.strings = strings;
        this.nodeStatus = nodeStatus;
        this.onState = onState;
        this.state = 'idle';
        this.readonly = false;
        this.ws = null;
        this.attempt = 0;
        this.everOpened = false;
        this.timer = null;
        this.encoder = new TextEncoder();
        this.disposables = [
            term.onData(data => this.send(transform(data))),
            term.onResize(() => this.sendSize()),
        ];
    }

    /**
     * Changes the state and tells the panel.
     *
     * @param {string} state new state
     */
    setState(state) {
        this.state = state;
        this.onState(state);
    }

    /**
     * Gets a fresh ticket and opens the WebSocket.
     */
    async connect() {
        if (this.state === 'closed' || this.state === 'connecting' || this.state === 'open') {
            return;
        }
        clearTimeout(this.timer);
        this.setState('connecting');
        let ticket;
        try {
            [ticket] = await Promise.all(Ajax.call([{
                methodname: 'mod_gns3lab_get_console_ticket',
                args: {cmid: this.cmid, nodeid: this.nodeId, userid: this.userid},
            }]));
        } catch (error) {
            if (this.state === 'closed') {
                return;
            }
            if (error.errorcode === 'managererror_node_not_running') {
                // Waits for the node: the panel reconnects when the session reports it started.
                this.term.write(note(this.strings.consolenodestopped));
                this.setState('stopped');
                return;
            }
            this.term.write(note(this.strings.consoleerror + ' ' + (error.message || '')));
            this.setState('failed');
            return;
        }
        if (this.state === 'closed') {
            return;
        }
        this.readonly = ticket.readonly;
        this.term.options.disableStdin = ticket.readonly;
        const url = `${ticket.gatewayurl}/console/${encodeURIComponent(this.nodeId)}?ticket=${encodeURIComponent(ticket.ticket)}`;
        const ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';
        ws.onopen = () => this.opened(ws);
        ws.onmessage = event => {
            this.term.write(typeof event.data === 'string' ? event.data : new Uint8Array(event.data));
        };
        ws.onclose = event => this.closed(ws, event);
        this.ws = ws;
    }

    /**
     * The gateway accepted the ticket and connected the console.
     *
     * @param {WebSocket} ws the socket
     */
    opened(ws) {
        if (ws !== this.ws) {
            return;
        }
        this.attempt = 0;
        this.sendSize();
        // Nothing is sent on reconnection: the user presses Enter to get a prompt.
        this.term.write(note(this.everOpened ? this.strings.consolereconnected : this.strings.consoleconnected));
        this.everOpened = true;
        this.setState('open');
    }

    /**
     * The socket closed: decide whether the node is gone or the connection should be retried.
     *
     * @param {WebSocket} ws the socket
     * @param {CloseEvent} event close event
     */
    async closed(ws, event) {
        if (ws !== this.ws || this.state === 'closed') {
            return;
        }
        this.ws = null;
        if (event.code === CLOSE_UPSTREAM) {
            let node = {status: null, stale: false};
            try {
                node = await this.nodeStatus();
            } catch (error) {
                node = {status: null, stale: false};
            }
            if (this.state === 'closed') {
                return;
            }
            if (node.stale) {
                // The manager has only an old snapshot: "started" proves nothing, keep trying with the usual pauses.
                this.term.write(note(this.strings.consolenodesunknown));
            } else if (node.status !== 'started') {
                this.term.write(note(this.strings.consolenodestopped));
                this.setState('stopped');
                return;
            }
        }
        this.retry();
    }

    /**
     * Waits 1 → 2 → 4 … 30 s and reconnects.
     */
    retry() {
        const delay = BACKOFF[Math.min(this.attempt, BACKOFF.length - 1)];
        this.attempt++;
        this.term.write(note(this.strings.consolereconnecting.replace('{$a}', delay)));
        this.setState('waiting');
        this.timer = setTimeout(() => this.connect(), delay * 1000);
    }

    /**
     * Sends keyboard input.
     *
     * @param {string} data what xterm.js or a key button produced
     */
    send(data) {
        if (this.readonly || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return;
        }
        for (let start = 0; start < data.length;) {
            let end = Math.min(start + MAX_FRAME, data.length);
            const last = data.charCodeAt(end - 1);
            if (end < data.length && last >= 0xd800 && last <= 0xdbff) {
                end--; // Keep a surrogate pair in one frame.
            }
            this.ws.send(data.slice(start, end));
            start = end;
        }
    }

    /**
     * Sends the window size as a binary JSON frame.
     *
     * The gateway drops it until Lab Slot passes it to the node (telnet NAWS), but the client always sends it.
     */
    sendSize() {
        if (!this.readonly && this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(this.encoder.encode(JSON.stringify({cols: this.term.cols, rows: this.term.rows})));
        }
    }

    /**
     * The node status changed in the session: reconnect if it was stopped and runs again.
     *
     * @param {string|null} status node status
     */
    nodeChanged(status) {
        if (status === 'started' && this.state === 'stopped') {
            this.attempt = 0;
            this.connect();
        }
    }

    /**
     * Closes the connection for good.
     */
    close() {
        this.setState('closed');
        clearTimeout(this.timer);
        if (this.ws) {
            const ws = this.ws;
            this.ws = null;
            ws.close(1000);
        }
        this.disposables.forEach(d => d.dispose());
    }
}

/**
 * Console tabs under the topology: one terminal per opened node.
 */
export class ConsolePanel {
    /**
     * Constructor.
     *
     * @param {object} options
     * @param {HTMLElement} options.root the console section of the template
     * @param {HTMLElement} options.app the viewer root, events about ports go there
     * @param {number} options.cmid course module id
     * @param {number} options.userid whose lab, 0 for the current user
     * @param {boolean} options.readonly observer: input disabled
     * @param {Function} options.nodeStatus async nodeId => {status, stale} from a fresh session state
     */
    constructor({root, app, cmid, userid, readonly, nodeStatus}) {
        this.root = root;
        this.app = app;
        this.cmid = cmid;
        this.userid = userid;
        this.readonly = readonly;
        this.nodeStatus = nodeStatus;
        this.tabs = new Map();
        this.active = null;
        this.ctrl = false;
        this.strings = null;
        this.tablist = root.querySelector('[data-region="tabs"]');
        this.host = root.querySelector('[data-region="termhost"]');
        this.keys = root.querySelector('[data-region="keys"]');
        this.obsbar = root.querySelector('[data-region="obsbar"]');
        this.toastEl = root.querySelector('[data-region="toast"]');
        this.bar = null;
        this.resizer = new ResizeObserver(() => this.fitActive());
        this.resizer.observe(this.host);
        root.addEventListener('focusin', () => this.updateLayout());
        root.addEventListener('focusout', () => setTimeout(() => this.updateLayout(), 0));
        this.watchViewport();
    }

    /**
     * Keeps the full screen lab inside the visible part of the screen when the system keyboard is open.
     *
     * Phones shrink only the visual viewport: a fixed element of 100dvh would go on under the keyboard and
     * hide the input line. The app takes the height and the offset of the visual viewport instead.
     */
    watchViewport() {
        const vv = window.visualViewport;
        if (!vv || !this.app.classList.contains('is-fullscreen')) {
            return;
        }
        const fit = () => {
            const shrunk = vv.height < window.innerHeight - 1 || vv.offsetTop > 0;
            this.app.style.height = shrunk ? `${vv.height}px` : '';
            this.app.style.top = shrunk ? `${vv.offsetTop}px` : '';
            this.app.style.bottom = shrunk ? 'auto' : '';
            this.fitActive();
        };
        vv.addEventListener('resize', fit);
        vv.addEventListener('scroll', fit);
    }

    /**
     * On phones the consoles take the whole screen while the student types or the command panel is open.
     */
    updateLayout() {
        const active = document.activeElement;
        const typing = !!active && this.root.contains(active) && /^(INPUT|TEXTAREA)$/.test(active.tagName);
        const on = isMobile() && !this.root.hidden && (typing || (!!this.bar && this.bar.isPadOpen()));
        if (this.app.classList.contains('kb-open') !== on) {
            this.app.classList.toggle('kb-open', on);
            requestAnimationFrame(() => this.fitActive());
        }
    }

    /**
     * Loads the strings and builds the key strips once.
     */
    async prepare() {
        if (this.strings) {
            return;
        }
        const values = await getStrings(STRING_KEYS.map(key => ({key, component: 'mod_gns3lab'})));
        this.strings = Object.fromEntries(STRING_KEYS.map((key, i) => [key, values[i]]));
        this.tablist.setAttribute('aria-label', this.strings.consoletabs);
        this.keys.setAttribute('aria-label', this.strings.consolekeys);
        this.buildStrips();
        this.bar = new CommandBar({
            root: this.root,
            strings: this.strings,
            send: seq => this.sendTo(this.active, seq),
            focusTerminal: () => {
                const tab = this.tabs.get(this.active);
                if (tab) {
                    tab.term.focus();
                }
            },
            toast: message => this.toast(message),
            onLayout: () => {
                this.updateLayout();
                requestAnimationFrame(() => this.fitActive());
            },
        });
        this.setReadonly(this.readonly);
    }

    /**
     * Runs a command on several devices through their consoles and waits for its answer.
     *
     * The consoles are opened (they show what happens); each one gets the sequence once it is connected.
     *
     * @param {Array} nodes {nodeId, name, nodeType}
     * @param {string} seq bytes to send
     * @param {RegExp} done output that means the command finished
     * @param {number} [timeout] ms per device
     * @returns {Promise<Array>} {name, ok} per device
     */
    async runAll(nodes, seq, done, timeout = 20000) {
        for (const node of nodes) {
            await this.open(node);
        }
        return Promise.all(nodes.map(async node => {
            const tab = this.tabs.get(node.nodeId);
            const ok = !!tab && !this.readonly && await this.whenOpen(tab, timeout) && await this.runOn(tab, seq, done, timeout);
            return {name: node.name, ok};
        }));
    }

    /**
     * Waits until a console is connected.
     *
     * @param {object} tab console tab
     * @param {number} timeout ms
     * @returns {Promise<boolean>}
     */
    whenOpen(tab, timeout) {
        const until = Date.now() + timeout;
        return new Promise(resolve => {
            const check = () => {
                if (tab.conn.state === 'open') {
                    resolve(true);
                } else if (Date.now() > until || ['failed', 'stopped', 'closed'].includes(tab.conn.state)) {
                    resolve(false);
                } else {
                    setTimeout(check, 100);
                }
            };
            check();
        });
    }

    /**
     * Sends a sequence to a connected console and waits for the expected output.
     *
     * @param {object} tab console tab
     * @param {string} seq bytes to send
     * @param {RegExp} done expected output
     * @param {number} timeout ms
     * @returns {Promise<boolean>}
     */
    runOn(tab, seq, done, timeout) {
        const buffer = () => tab.term.buffer.active;
        const from = Math.max(0, buffer().baseY + buffer().cursorY);
        return new Promise(resolve => {
            let listener = null;
            const finish = ok => {
                clearTimeout(timer);
                if (listener) {
                    listener.dispose();
                }
                resolve(ok);
            };
            const timer = setTimeout(() => finish(false), timeout);
            listener = tab.term.onWriteParsed(() => {
                const b = buffer();
                const lines = [];
                for (let y = from; y < b.length; y++) {
                    const line = b.getLine(y);
                    lines.push(line ? line.translateToString(true) : '');
                }
                if (done.test(lines.join('\n'))) {
                    finish(true);
                }
            });
            tab.conn.send(seq);
        });
    }

    /**
     * Opens the console of a node or brings its tab to the front.
     *
     * @param {object} node
     * @param {string} node.nodeId node id
     * @param {string} node.name node name for the tab
     * @param {string} [node.nodeType] GNS3 node type, chooses the commands of the keyboard
     * @param {Array} [node.ports] ports of the node for the keyboard: {name, peer}
     */
    async open({nodeId, name, nodeType = '', ports = []}) {
        await this.prepare();
        if (!this.tabs.has(nodeId)) {
            const {Terminal, FitAddon} = await loadXterm();
            if (this.tabs.has(nodeId)) {
                this.show(nodeId);
                return;
            }
            this.tabs.set(nodeId, this.createTab(nodeId, name, nodeType, Terminal, FitAddon));
        }
        this.tabs.get(nodeId).ports = ports;
        this.root.hidden = false;
        this.app.classList.add('has-consoles');
        this.show(nodeId);
        const tab = this.tabs.get(nodeId);
        if (tab.conn.state === 'idle') {
            tab.conn.connect();
        }
    }

    /**
     * Creates the tab button, the terminal and its connection.
     *
     * @param {string} nodeId node id
     * @param {string} name node name
     * @param {string} nodeType GNS3 node type
     * @param {Function} Terminal xterm.js Terminal class
     * @param {Function} FitAddon fit addon class
     * @returns {object} tab
     */
    createTab(nodeId, name, nodeType, Terminal, FitAddon) {
        const button = document.createElement('div');
        button.className = 'ctab';
        button.setAttribute('role', 'presentation');
        const select = document.createElement('button');
        select.type = 'button';
        select.className = 'ctab-name';
        select.setAttribute('role', 'tab');
        const dot = document.createElement('i');
        dot.className = 'cdot';
        select.append(dot, document.createTextNode(name));
        select.addEventListener('click', () => this.show(nodeId));
        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'ctab-x';
        close.textContent = '×';
        close.setAttribute('aria-label', `${this.strings.consoleclose} ${name}`);
        close.addEventListener('click', () => this.closeTab(nodeId));
        button.append(select, close);
        this.tablist.appendChild(button);

        const el = document.createElement('div');
        el.className = 'cterm';
        el.setAttribute('role', 'tabpanel');
        el.hidden = true;
        this.host.appendChild(el);

        const term = new Terminal({
            fontFamily: getComputedStyle(this.root).getPropertyValue('--mono').trim() || 'monospace',
            fontSize: isMobile() ? 13 : 14,
            lineHeight: 1.15,
            cursorBlink: !this.readonly,
            scrollback: 3000,
            theme: THEME,
            disableStdin: this.readonly,
        });
        const fit = new FitAddon();
        term.loadAddon(fit);
        term.open(el);
        this.addKeyHandler(term, nodeId);
        this.addPortLinks(term, nodeId);

        const tab = {nodeId, name, nodeType, button, select, el, term, fit, conn: null, ports: [], history: [], historyPos: 0};
        // The keyboard follows the prompt of the device (Cisco mode).
        term.onWriteParsed(() => {
            if (this.active === nodeId && this.bar) {
                this.bar.updatePrompt();
            }
        });
        tab.conn = new ConsoleConnection({
            cmid: this.cmid,
            userid: this.userid,
            nodeId,
            term,
            strings: this.strings,
            nodeStatus: () => this.nodeStatus(nodeId),
            transform: data => this.applyCtrl(data),
            onState: state => {
                button.dataset.state = state;
                if (state === 'failed' || state === 'stopped') {
                    this.offerReconnect(tab);
                } else {
                    tab.el.querySelector('.creconnect')?.remove();
                }
            },
        });
        term.write(note(this.strings.consoleconnecting));
        return tab;
    }

    /**
     * Shows a "reconnect" action in the terminal area of a tab that gave up.
     *
     * @param {object} tab tab
     */
    offerReconnect(tab) {
        if (tab.el.querySelector('.creconnect')) {
            return;
        }
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'gbtn creconnect';
        button.textContent = this.strings.consolereconnect;
        button.addEventListener('click', () => {
            button.remove();
            tab.conn.attempt = 0;
            tab.conn.connect();
        });
        tab.el.appendChild(button);
    }

    /**
     * Ctrl+C with a selection copies; Ctrl+Shift+6 sends the Cisco break sequence.
     *
     * @param {object} term terminal
     * @param {string} nodeId node id
     */
    addKeyHandler(term, nodeId) {
        term.attachCustomKeyEventHandler(event => {
            if (event.type !== 'keydown') {
                return true;
            }
            if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'c' && term.hasSelection()) {
                const text = term.getSelection();
                if (navigator.clipboard) {
                    navigator.clipboard.writeText(text).then(
                        () => this.toast(this.strings.consolecopied),
                        () => this.toast(this.strings.consolecopyfailed));
                }
                return false;
            }
            if (event.ctrlKey && event.shiftKey && (event.code === 'Digit6' || event.key === '^')) {
                this.sendTo(nodeId, '\x1e');
                return false;
            }
            return true;
        });
    }

    /**
     * Interface names in the output become links that show the port on the topology.
     *
     * @param {object} term terminal
     * @param {string} nodeId node id
     */
    addPortLinks(term, nodeId) {
        term.registerLinkProvider({
            provideLinks: (y, callback) => {
                const line = term.buffer.active.getLine(y - 1);
                if (!line) {
                    callback(undefined);
                    return;
                }
                const text = line.translateToString(true);
                const links = [];
                for (const match of text.matchAll(PORT_RE)) {
                    const port = match[1];
                    links.push({
                        text: port,
                        range: {start: {x: match.index + 1, y}, end: {x: match.index + port.length, y}},
                        decorations: {underline: true, pointerCursor: true},
                        activate: () => this.app.dispatchEvent(new CustomEvent('gns3lab:showport', {detail: {nodeId, port}})),
                    });
                }
                callback(links.length ? links : undefined);
            },
        });
    }

    /**
     * Brings a tab to the front.
     *
     * @param {string} nodeId node id
     */
    show(nodeId) {
        this.active = nodeId;
        this.bar.setTab(this.tabs.get(nodeId) || null);
        this.tabs.forEach((tab, id) => {
            const on = id === nodeId;
            tab.el.hidden = !on;
            tab.select.setAttribute('aria-selected', String(on));
        });
        const tab = this.tabs.get(nodeId);
        requestAnimationFrame(() => {
            this.fitActive();
            if (tab && !isMobile()) {
                tab.term.focus();
            }
        });
    }

    /**
     * Closes one tab and its connection.
     *
     * @param {string} nodeId node id
     */
    closeTab(nodeId) {
        const tab = this.tabs.get(nodeId);
        if (!tab) {
            return;
        }
        tab.conn.close();
        tab.term.dispose();
        tab.el.remove();
        tab.button.remove();
        this.tabs.delete(nodeId);
        if (this.active === nodeId) {
            const next = this.tabs.keys().next();
            if (next.done) {
                this.active = null;
                this.bar.setTab(null);
                this.root.hidden = true;
                this.app.classList.remove('has-consoles');
                this.updateLayout();
            } else {
                this.show(next.value);
            }
        }
    }

    /**
     * Closes every tab, for example when the session ends.
     */
    closeAll() {
        [...this.tabs.keys()].forEach(id => this.closeTab(id));
    }

    /**
     * Passes fresh node states to the connections.
     *
     * @param {Map<string, string>} statuses node id → status
     */
    updateNodes(statuses) {
        this.tabs.forEach((tab, id) => tab.conn.nodeChanged(statuses.get(id) || null));
    }

    /**
     * Switches observer mode: the gateway drops input anyway, the page also disables it.
     *
     * @param {boolean} readonly observer
     */
    setReadonly(readonly) {
        this.readonly = readonly;
        this.obsbar.hidden = !readonly;
        if (this.bar) {
            this.bar.setReadonly(readonly);
        }
        this.root.querySelectorAll('.k').forEach(b => {
            b.disabled = readonly;
        });
        this.tabs.forEach(tab => {
            tab.term.options.disableStdin = readonly;
            tab.term.options.cursorBlink = !readonly;
        });
    }

    /**
     * Fits the visible terminal to its area.
     */
    fitActive() {
        const tab = this.active && this.tabs.get(this.active);
        if (tab && !tab.el.hidden) {
            try {
                tab.fit.fit();
            } catch (error) {
                // The area has no size yet (hidden); the next resize fits it.
            }
        }
    }

    /**
     * Sends a sequence to the console of a node.
     *
     * @param {string} nodeId node id
     * @param {string} seq bytes to send
     */
    sendTo(nodeId, seq) {
        const tab = this.tabs.get(nodeId);
        if (tab && !this.readonly) {
            tab.conn.send(seq);
        }
    }

    /**
     * Builds the key strip.
     */
    buildStrips() {
        const bind = (button, action) => {
            let hadFocus = null;
            // Keep the system keyboard open: do not take the focus from the terminal or the input line.
            button.addEventListener('pointerdown', event => {
                const active = document.activeElement;
                hadFocus = active && this.root.contains(active) && /^(INPUT|TEXTAREA)$/.test(active.tagName) ? active : null;
                event.preventDefault();
            });
            button.addEventListener('click', () => {
                action();
                const tab = this.tabs.get(this.active);
                if (hadFocus) {
                    hadFocus.focus();
                } else if (tab && !isMobile()) {
                    tab.term.focus();
                }
            });
        };
        KEYS.forEach(key => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'k';
            button.textContent = key.label;
            const label = key.ctrl ? this.strings.keyctrl : key.key ? this.strings[key.key] : '';
            if (label) {
                button.title = label;
                button.setAttribute('aria-label', label);
            }
            if (key.ctrl) {
                button.setAttribute('aria-pressed', 'false');
                bind(button, () => this.setCtrl(!this.ctrl));
            } else {
                bind(button, () => this.sendTo(this.active, key.seq));
            }
            this.keys.appendChild(button);
        });
        // Ctrl from the strip applies to the next typed letter.
        this.ctrlButton = this.keys.querySelector('[aria-pressed]');
    }

    /**
     * Sets the sticky Ctrl of the key strip.
     *
     * @param {boolean} on pressed
     */
    setCtrl(on) {
        this.ctrl = on;
        this.ctrlButton.setAttribute('aria-pressed', String(on));
    }

    /**
     * Turns the next typed letter into a control character while the strip Ctrl is pressed.
     *
     * @param {string} data typed input
     * @returns {string} what to send
     */
    applyCtrl(data) {
        if (!this.ctrl) {
            return data;
        }
        this.setCtrl(false);
        if (data.length === 1 && /[a-z@[\\\]^_]/i.test(data)) {
            return String.fromCharCode(data.toUpperCase().charCodeAt(0) % 32);
        }
        return data;
    }

    /**
     * Short message over the terminal.
     *
     * @param {string} message text
     */
    toast(message) {
        this.toastEl.textContent = message;
        this.toastEl.hidden = false;
        clearTimeout(this.toastTimer);
        this.toastTimer = setTimeout(() => {
            this.toastEl.hidden = true;
        }, 1400);
    }
}
