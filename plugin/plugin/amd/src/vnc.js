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
 * Connection of a graphical console (a node with console_type "vnc") to the console gateway.
 *
 * The same address, ticket and close codes as for a text console (mod_gns3lab/console, docs/SPEC.md §4), but the
 * frames are the raw RFB stream in both directions and noVNC draws it on a canvas. The page creates the WebSocket
 * itself and hands it to noVNC as its channel, so that the close code of the gateway (4000, 4001) stays visible.
 *
 * Only the owner of the lab gets a screen: the screen has no view-only mode, the teacher does not see it, and the
 * manager does not issue a ticket for it. There is no password: the server must offer the security type None.
 *
 * noVNC is loaded on demand, when the first graphical console opens.
 *
 * Full screen (docs/SPEC.md §5.4): the page may ask the node for a screen size of its own (SetDesktopSize, the
 * ExtendedDesktopSize extension of RFB). QEMU passes it to the display adapter as a new preferred mode, and the guest
 * switches to it if it follows such changes. When full screen ends, the node is asked for the size it had before.
 *
 * @module     mod_gns3lab/vnc
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';
import {reportRefusal, tabId} from 'mod_gns3lab/seat';
import {BACKOFF, CLOSE_UNAVAILABLE, CLOSE_UPSTREAM, STABLE_AFTER, consoleUrl} from 'mod_gns3lab/console';

/**
 * Strings of the graphical console: the messages over the screen reuse those of the text console
 * (mod_gns3lab/console STRING_KEYS).
 */
export const STRING_KEYS = [
    'consolevncobserver', 'consolevnccreds', 'consolevnclabel', 'consolevncctrlaltdel', 'consolevnctools',
    'consolevncfullscreen', 'consolevncfullscreenexit', 'consolevncfullscreenhint', 'consolevncfullscreenfailed',
    'ui_screen_fail',
];

/** The largest screen a node is asked for in full screen (2K): a bigger monitor gets it scaled up. */
export const MAX_SCREEN = {w: 2560, h: 1440};

/** How long after connecting the page waits for the server to say whether it can change the screen size. */
const RESIZE_WAIT = 5000;

/**
 * The size of the screen to ask a node for in full screen: the monitor in device pixels (so that a picture of that
 * size is shown pixel for pixel, also with the scaling of the operating system), at most MAX_SCREEN, same proportions.
 *
 * @param {number} width width of the monitor in CSS pixels
 * @param {number} height height of the monitor in CSS pixels
 * @param {number} ratio device pixels per CSS pixel
 * @returns {{w: number, h: number}|null} null for a size that makes no sense
 */
export const fullScreenSize = (width, height, ratio) => {
    let w = Math.round(width * (ratio || 1)), h = Math.round(height * (ratio || 1));
    if (!(w > 0 && h > 0)) {
        return null;
    }
    const k = Math.min(1, MAX_SCREEN.w / w, MAX_SCREEN.h / h);
    // Even sizes: some guests round odd modes and would leave a stripe. The epsilon keeps 3440 * (2560 / 3440) at 2560.
    w = Math.floor(w * k / 2 + 1e-9) * 2;
    h = Math.floor(h * k / 2 + 1e-9) * 2;
    return w >= 640 && h >= 400 ? {w, h} : null;
};

let novncPromise = null;

/**
 * Loads noVNC once.
 *
 * @returns {Promise<Function>} the RFB class
 */
export const loadNovnc = () => {
    if (!novncPromise) {
        novncPromise = import('mod_gns3lab/novnc').then(module => module.RFB);
        // A failed load is tried again the next time a screen opens.
        novncPromise.catch(() => {
            novncPromise = null;
        });
    }
    return novncPromise;
};

/**
 * One connection of one screen to the gateway, with reconnection.
 *
 * States are those of ConsoleConnection: idle, connecting, open, waiting (pause before reconnecting), stopped (the
 * node is stopped), failed (no ticket, or the server wants a password: nothing to retry), closed (the tab is closed).
 * Every (re)connection takes a new ticket and a new RFB object: a disconnected RFB object cannot be used again.
 */
export class VncConnection {
    /**
     * Constructor.
     *
     * @param {object} options
     * @param {number} options.cmid course module id
     * @param {number} options.userid whose lab, 0 for the current user
     * @param {string} options.nodeId node id in the live project
     * @param {Function} options.RFB the RFB class of noVNC
     * @param {HTMLElement} options.screen element that noVNC fills with its canvas
     * @param {object} options.strings language strings
     * @param {Function} options.nodeStatus async () => {status, stale} from a fresh session state
     * @param {Function} options.onState called with the new state name
     * @param {Function} options.onNote called with a message to show over the screen, an empty string clears it
     * @param {Function} [options.api] the transport of web service calls, Ajax.call by default
     * @param {object} [options.sizes] where the size of the screen before full screen is kept: get(), set(size), clear()
     */
    constructor({cmid, userid, nodeId, RFB, screen, strings, nodeStatus, onState, onNote, api = requests => Ajax.call(requests),
            sizes = null}) {
        this.api = api;
        this.cmid = cmid;
        this.userid = userid;
        this.nodeId = nodeId;
        this.RFB = RFB;
        this.screen = screen;
        this.strings = strings;
        this.nodeStatus = nodeStatus;
        this.onState = onState;
        this.onNote = onNote;
        this.state = 'idle';
        this.attempt = 0;
        this.timer = null;
        this.link = null;
        this.connectedAt = 0;
        this.fatal = '';
        // The size the node is asked for while the screen is full screen, null: the node keeps its own.
        this.wanted = null;
        this.sizes = sizes || {get: () => null, set: () => undefined, clear: () => undefined};
        this.sizeTimer = null;
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
        this.fatal = '';
        this.setState('connecting');
        this.onNote(this.strings.consoleconnecting);
        let ticket;
        try {
            [ticket] = await Promise.all(this.api([{
                methodname: 'mod_gns3lab_get_console_ticket',
                args: {cmid: this.cmid, nodeid: this.nodeId, userid: this.userid, tabid: tabId()},
            }]));
        } catch (error) {
            reportRefusal(error);
            if (this.state === 'closed') {
                return;
            }
            if (error.errorcode === 'managererror_node_not_running') {
                // Waits for the node: the panel reconnects when the session reports it started.
                this.onNote(this.strings.consolenodestopped);
                this.setState('stopped');
                return;
            }
            this.onNote(this.strings.consoleerror + ' ' + (error.message || ''));
            this.setState('failed');
            return;
        }
        if (this.state === 'closed') {
            return;
        }
        if (ticket.readonly) {
            // The screen has no view-only mode, so a read-only ticket is a mistake of the server, not something to use.
            this.onNote(this.strings.consolevncobserver);
            this.setState('failed');
            return;
        }
        this.open(ticket);
    }

    /**
     * Opens the socket and gives it to noVNC.
     *
     * @param {object} ticket answer of mod_gns3lab_get_console_ticket
     */
    open(ticket) {
        let ws;
        try {
            ws = new WebSocket(consoleUrl(ticket, this.nodeId));
        } catch (error) {
            // A ws:// gateway on an https page or a malformed URL: the browser refuses at once, no close event follows.
            this.onNote(this.strings.consoleerror + ' ' + (error.message || ''));
            this.setState('failed');
            return;
        }
        ws.binaryType = 'arraybuffer';
        let rfb;
        try {
            // No credentials: the server must not ask for any (security type None).
            rfb = new this.RFB(this.screen, ws);
        } catch (error) {
            ws.close();
            this.onNote(this.strings.consoleerror + ' ' + (error.message || ''));
            this.setState('failed');
            return;
        }
        rfb.scaleViewport = true;
        rfb.resizeSession = false;
        rfb.viewOnly = false;
        rfb.clipViewport = false;
        rfb.background = 'transparent';
        const link = {ws, rfb, done: false, off: []};
        const on = (type, handler) => {
            rfb.addEventListener(type, handler);
            link.off.push(() => rfb.removeEventListener(type, handler));
        };
        on('connect', () => this.opened(link));
        on('disconnect', () => {
            link.done = true;
        });
        on('credentialsrequired', () => this.refuse(link, this.strings.consolevnccreds));
        on('serververification', () => this.refuse(link, this.strings.consolevnccreds));
        on('securityfailure', event => {
            this.refuse(link, this.strings.consoleerror + ' ' + ((event.detail && event.detail.reason) || ''));
        });
        // noVNC owns the on* properties of the socket; a listener is how the close code reaches this class.
        link.onclose = event => this.closed(link, event);
        ws.addEventListener('close', link.onclose);
        this.link = link;
    }

    /**
     * The RFB handshake is done: the node answered, the first picture is on its way.
     *
     * @param {object} link the current connection
     */
    opened(link) {
        if (link !== this.link) {
            return;
        }
        this.connectedAt = Date.now();
        this.onNote('');
        this.setState('open');
        // In full screen the node gets the wanted size again; otherwise a size left from a full screen that ended
        // without a connection (or with a reload of the page) is given back.
        if (this.wanted || this.sizes.get()) {
            this.applySize();
        }
    }

    /**
     * Whether the server said it can change the size of the screen. noVNC 1.7.0 keeps it in a private field set by the
     * first ExtendedDesktopSize rectangle; it has no public API for a size other than that of its element.
     *
     * @param {object} rfb RFB object
     * @returns {boolean}
     */
    static resizable(rfb) {
        return !!(rfb && rfb._supportsSetDesktopSize && rfb._sock);
    }

    /**
     * The size of the screen of the node now.
     *
     * @returns {{w: number, h: number}|null} null: not connected yet
     */
    remoteSize() {
        const rfb = this.state === 'open' && this.link ? this.link.rfb : null;
        return rfb && rfb._fbWidth > 0 && rfb._fbHeight > 0 ? {w: rfb._fbWidth, h: rfb._fbHeight} : null;
    }

    /**
     * Sends SetDesktopSize: one screen of this size, with the id and flags the server gave for its screen.
     *
     * @param {{w: number, h: number}} size size
     * @returns {boolean} false: not connected, or the server cannot change the size
     */
    sendSize(size) {
        const rfb = this.state === 'open' && this.link ? this.link.rfb : null;
        if (!VncConnection.resizable(rfb) || !this.RFB.messages || !this.RFB.messages.setDesktopSize) {
            return false;
        }
        if (rfb._fbWidth !== size.w || rfb._fbHeight !== size.h) {
            this.RFB.messages.setDesktopSize(rfb._sock, size.w, size.h, rfb._screenID, rfb._screenFlags);
        }
        return true;
    }

    /**
     * Asks the node for the wanted size, or, out of full screen, for the size it had before full screen. Right after
     * connecting it waits a little for the server to say it can. The size before full screen is remembered once, in
     * the browser: it survives a reconnection and a reload of the page.
     */
    applySize() {
        clearTimeout(this.sizeTimer);
        const started = Date.now();
        const attempt = () => {
            if (this.state !== 'open' || !this.link) {
                return;
            }
            if (!VncConnection.resizable(this.link.rfb)) {
                if (Date.now() - started < RESIZE_WAIT) {
                    this.sizeTimer = setTimeout(attempt, 100);
                }
                return;
            }
            const before = this.sizes.get();
            if (this.wanted) {
                const now = this.remoteSize();
                if (now && !before && (now.w !== this.wanted.w || now.h !== this.wanted.h)) {
                    this.sizes.set(now);
                }
                this.sendSize(this.wanted);
            } else if (before && this.sendSize(before)) {
                this.sizes.clear();
            }
        };
        attempt();
    }

    /**
     * Full screen began: the node is asked for this size, also after a reconnection.
     *
     * @param {{w: number, h: number}} size size from fullScreenSize()
     */
    fitTo(size) {
        this.wanted = size;
        this.applySize();
    }

    /**
     * Full screen ended: the node is asked for the size it had before, once (or when it is connected again).
     */
    restoreSize() {
        this.wanted = null;
        this.applySize();
    }

    /**
     * The server asks for what the lab does not have (a password) or refuses: nothing to retry.
     *
     * @param {object} link the current connection
     * @param {string} text what to tell the student
     */
    refuse(link, text) {
        if (link !== this.link) {
            return;
        }
        this.fatal = text;
        link.rfb.disconnect();
    }

    /**
     * Takes noVNC off the socket and the page.
     *
     * @param {object} link connection
     * @param {boolean} [stopSocket] close the socket too (the tab is closed)
     */
    release(link, stopSocket = false) {
        link.ws.removeEventListener('close', link.onclose);
        link.off.forEach(off => off());
        link.off = [];
        if (stopSocket && link.ws.readyState <= WebSocket.OPEN) {
            link.ws.close(1000);
        }
        if (!link.done) {
            // Removes the canvas and the handlers of noVNC from the page; the socket closing completes it.
            link.rfb.disconnect();
        }
    }

    /**
     * The socket closed: decide whether the node is gone or the connection should be retried.
     *
     * @param {object} link the connection
     * @param {CloseEvent} event close event
     */
    async closed(link, event) {
        if (link !== this.link || this.state === 'closed') {
            return;
        }
        this.link = null;
        this.release(link);
        if (this.connectedAt && Date.now() - this.connectedAt >= STABLE_AFTER) {
            this.attempt = 0;
        }
        this.connectedAt = 0;
        if (this.fatal) {
            this.onNote(this.fatal);
            this.setState('failed');
            return;
        }
        let lead = '';
        if (event.code === CLOSE_UNAVAILABLE) {
            lead = this.strings.consoleunavailable + ' ';
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
                lead += this.strings.consolenodesunknown + ' ';
            } else if (node.status !== 'started') {
                this.onNote(this.strings.consolenodestopped);
                this.setState('stopped');
                return;
            }
        }
        this.retry(lead);
    }

    /**
     * Waits 1 → 2 → 4 … 30 s and reconnects.
     *
     * @param {string} lead what to say before the pause
     */
    retry(lead) {
        const delay = BACKOFF[Math.min(this.attempt, BACKOFF.length - 1)];
        this.attempt++;
        this.onNote(lead + this.strings.consolereconnecting.replace('{$a}', delay));
        this.setState('waiting');
        this.timer = setTimeout(() => this.connect(), delay * 1000);
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
     * Sends Ctrl+Alt+Del to the node.
     *
     * @returns {boolean} false: the screen is not connected
     */
    ctrlAltDel() {
        if (this.state !== 'open' || !this.link) {
            return false;
        }
        this.link.rfb.sendCtrlAltDel();
        return true;
    }

    /**
     * Moves the keyboard focus to the screen.
     */
    focus() {
        if (this.link) {
            this.link.rfb.focus({preventScroll: true});
        }
    }

    /**
     * Closes the connection for good.
     */
    close() {
        this.setState('closed');
        clearTimeout(this.timer);
        clearTimeout(this.sizeTimer);
        const link = this.link;
        this.link = null;
        if (link) {
            this.release(link, true);
        }
    }
}
