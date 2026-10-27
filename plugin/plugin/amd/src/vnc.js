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
    'consolevncobserver', 'consolevnccreds', 'consolevnclabel', 'consolevncctrlaltdel', 'consolevncpaste',
    'consolevncpasted', 'consolevncpasteempty', 'consolevncpastefailed', 'consolevncpastehint', 'consolevncpastelong',
    'ui_screen_fail',
];

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
     */
    constructor({cmid, userid, nodeId, RFB, screen, strings, nodeStatus, onState, onNote}) {
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
            [ticket] = await Promise.all(Ajax.call([{
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
        const ws = new WebSocket(consoleUrl(ticket, this.nodeId));
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
     * Puts text into the clipboard of the node. The node has to paste it itself (Ctrl+V), and only a guest with a
     * clipboard agent receives it at all.
     *
     * @param {string} text text
     * @returns {boolean} false: the screen is not connected
     */
    paste(text) {
        if (this.state !== 'open' || !this.link) {
            return false;
        }
        this.link.rfb.clipboardPasteFrom(text);
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
        const link = this.link;
        this.link = null;
        if (link) {
            this.release(link, true);
        }
    }
}
