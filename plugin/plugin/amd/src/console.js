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
 * Connection of a terminal to the console gateway (docs/SPEC.md §4) and what the console views share.
 *
 * The browser talks to the gateway directly with a one-time ticket from mod_gns3lab_get_console_ticket;
 * Moodle never sees the WebSocket. Every (re)connection takes a new ticket. The views (panel, grid, windows,
 * phone) are in mod_gns3lab/consoles.
 *
 * xterm.js is loaded on demand, when the first console opens.
 *
 * @module     mod_gns3lab/console
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';

/** Close code of the gateway: the console closed on the slot side. */
export const CLOSE_UPSTREAM = 4000;

/** Close code of the gateway: the slot refused the console (node not ready, slot unreachable, wrong token). */
export const CLOSE_UNAVAILABLE = 4001;

/** A connection that lived this long, ms, counts as working: the reconnection pauses start again from 1 s. */
export const STABLE_AFTER = 10000;

/** Before "Connected" is printed, ms: a socket the gateway closes at once never says it. */
const CONNECTED_NOTE_DELAY = 400;

/**
 * Pause between the lines of a long paste into Cisco IOS, ms: IOU drops characters when a configuration
 * comes in faster than it reads it.
 */
const PASTE_LINE_PAUSE = 30;

/** Input this long with line breaks is a paste and is paced on Cisco devices. */
const PASTE_MIN = 64;

/**
 * Largest input frame, in UTF-16 code units: at most 12 KB of UTF-8, well below the 64 KB the gateway takes.
 * A pasted configuration goes out in several frames.
 */
const MAX_FRAME = 4096;

/** Node types that run Cisco IOS: long pastes go to them line by line. */
export const CISCO_TYPES = ['iou', 'dynamips'];

/** Reconnection pauses, seconds; the last one repeats. */
export const BACKOFF = [1, 2, 4, 8, 16, 30];

/** Keys for phones and for what a desktop browser keeps for itself. */
export const KEYS = [
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
export const PORT_RE = /\b((?:GigabitEthernet|FastEthernet|Ethernet|Serial|Loopback|Gi|Fa|Et|Se|Lo|e)\d+(?:\/\d+){0,2})\b/g;

/** Terminal colours: the console stays dark in both themes. */
export const THEME = {
    background: '#0f1720', foreground: '#d6dee5', cursor: '#5aa8f2', cursorAccent: '#0f1720',
    selectionBackground: 'rgba(90,168,242,.32)', black: '#1b2631', red: '#ee6b62', green: '#3cc47a', yellow: '#e0b13a',
    blue: '#5aa8f2', magenta: '#c792ea', cyan: '#4fc1c9', white: '#d6dee5', brightBlack: '#7d8b97', brightRed: '#ff8a80',
    brightGreen: '#6ee09e', brightYellow: '#f2cc60', brightBlue: '#8cc4ff', brightMagenta: '#ddb3f6', brightCyan: '#7fdde3',
    brightWhite: '#ffffff',
};

/** Strings of the connection and of the key strip. */
export const STRING_KEYS = [
    'consoleconnecting', 'consoleconnected', 'consolereconnected', 'consolereconnecting', 'consolenodestopped',
    'consoleerror', 'consolecopied', 'consolecopyfailed', 'consolekeys',
    'keyctrl', 'keybreak', 'keyspace', 'consolereconnect', 'consolenodesunknown', 'consoleunavailable',
];

/**
 * Address of a console on the gateway: the same for text and graphical consoles.
 *
 * @param {object} ticket answer of mod_gns3lab_get_console_ticket
 * @param {string} nodeId node id in the live project
 * @returns {string} wss://…/console/<node_id>?ticket=…
 */
export const consoleUrl = (ticket, nodeId) =>
    `${ticket.gatewayurl}/console/${encodeURIComponent(nodeId)}?ticket=${encodeURIComponent(ticket.ticket)}`;

let xtermPromise = null;

/**
 * Loads xterm.js and the fit addon once.
 *
 * @returns {Promise<{Terminal: Function, FitAddon: Function}>}
 */
export const loadXterm = () => {
    if (!xtermPromise) {
        xtermPromise = Promise.all([import('mod_gns3lab/xterm'), import('mod_gns3lab/xterm-addon-fit')])
            .then(([xterm, fit]) => ({Terminal: xterm.Terminal, FitAddon: fit.FitAddon}));
    }
    return xtermPromise;
};

/**
 * Text in dim grey for messages from the page itself, not from the device.
 *
 * @param {string} text message
 * @returns {string}
 */
export const note = text => `\r\n\x1b[2m${text}\x1b[0m\r\n`;

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
     * @param {boolean} [options.paced] send long pastes line by line (Cisco IOS)
     */
    constructor({cmid, userid, nodeId, term, strings, nodeStatus, onState, transform = data => data, paced = false}) {
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
        this.paced = paced;
        this.queue = [];
        this.pump = null;
        this.openedAt = 0;
        this.gotData = false;
        this.noteTimer = null;
        this.pendingNote = null;
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
        const ws = new WebSocket(consoleUrl(ticket, this.nodeId));
        ws.binaryType = 'arraybuffer';
        ws.onopen = () => this.opened(ws);
        ws.onmessage = event => {
            if (ws !== this.ws) {
                return;
            }
            if (!this.gotData) {
                // The node answered: the connection works, the pauses start again from 1 s.
                this.gotData = true;
                this.attempt = 0;
                this.flushConnectedNote();
            }
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
        // The pauses are not reset here: the gateway accepts first and closes at once when the slot refuses.
        this.openedAt = Date.now();
        this.gotData = false;
        this.sendSize();
        // Nothing is sent on reconnection: the user presses Enter to get a prompt.
        this.pendingNote = this.everOpened ? this.strings.consolereconnected : this.strings.consoleconnected;
        this.noteTimer = setTimeout(() => this.flushConnectedNote(), CONNECTED_NOTE_DELAY);
        this.setState('open');
    }

    /**
     * Prints "Connected" once the connection is known to stay, before the first output of the node.
     */
    flushConnectedNote() {
        clearTimeout(this.noteTimer);
        if (this.pendingNote) {
            this.term.write(note(this.pendingNote));
            this.pendingNote = null;
            this.everOpened = true;
        }
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
        clearTimeout(this.noteTimer);
        this.pendingNote = null;
        this.stopPump();
        if (this.openedAt && Date.now() - this.openedAt >= STABLE_AFTER) {
            this.attempt = 0;
        }
        this.openedAt = 0;
        if (event.code === CLOSE_UNAVAILABLE) {
            this.term.write(note(this.strings.consoleunavailable));
        }
        if (event.code === CLOSE_UPSTREAM || event.code === CLOSE_UNAVAILABLE) {
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
     * On Cisco a long paste goes line by line with a short pause, and whatever is typed meanwhile waits its turn.
     *
     * @param {string} data what xterm.js or a key button produced
     */
    send(data) {
        if (this.readonly || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            return;
        }
        if (this.paced && (this.queue.length || (data.length >= PASTE_MIN && /[\r\n]/.test(data)))) {
            const lines = data.replace(/\r\n?|\n/g, '\r').split('\r');
            lines.forEach((line, i) => {
                const chunk = i < lines.length - 1 ? line + '\r' : line;
                if (chunk) {
                    this.queue.push(chunk);
                }
            });
            if (!this.pump) {
                this.drain();
            }
            return;
        }
        this.sendNow(data);
    }

    /**
     * Sends the next queued line and schedules the one after it.
     */
    drain() {
        const line = this.queue.shift();
        if (line === undefined || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
            this.stopPump();
            return;
        }
        this.sendNow(line);
        this.pump = setTimeout(() => this.drain(), PASTE_LINE_PAUSE);
    }

    /**
     * Drops the rest of a paced paste, for example when the connection closes.
     */
    stopPump() {
        clearTimeout(this.pump);
        this.pump = null;
        this.queue = [];
    }

    /**
     * Sends input in frames below the gateway limit.
     *
     * @param {string} data input
     */
    sendNow(data) {
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
        clearTimeout(this.noteTimer);
        this.stopPump();
        if (this.ws) {
            const ws = this.ws;
            this.ws = null;
            ws.close(1000);
        }
        this.disposables.forEach(d => d.dispose());
    }
}
