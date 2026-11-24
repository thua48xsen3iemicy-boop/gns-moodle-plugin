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
 * Where the student works: the consoles they have open and the one they type into, for a teacher who looks at their lab
 * (manager CONTRACTS §6.3, docs/teacher-dashboard.md §15).
 *
 * The gateway knows it exactly: it relays the student's consoles. With a ticket of mod_gns3lab_get_activity_ticket the
 * page opens `<gateway>/console/activity` and gets text JSON frames: a snapshot first, then `open`, `close` and `input`
 * with a node id. The input itself never comes, only that there was some. No DOM here: the page paints the nodes from
 * state() whenever onChange names them.
 *
 * Reconnection as for a console (SPEC §4): any close, a pause 1 → 2 → 4 … 30 s, reset by the first frame.
 *
 * @module     mod_gns3lab/consoleactivity
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/** A node blinks this long after an input frame, ms: the gateway sends one at most every 0.5 s while the student types. */
export const TYPING_MS = 1500;

const RETRY_MIN = 1000;
const RETRY_MAX = 30000;

export class ConsoleActivity {
    /**
     * @param {object} options
     * @param {number} options.cmid course module id
     * @param {number} options.userid the student whose lab is shown
     * @param {Function} options.api Ajax.call or a transport with the same signature
     * @param {Function} options.onChange called with an array of node ids whose state() changed
     * @param {Function} [options.WebSocketImpl] WebSocket constructor (tests)
     * @param {number} [options.typingMs] how long a node blinks after an input frame (tests)
     */
    constructor({cmid, userid, api, onChange, WebSocketImpl = null, typingMs = TYPING_MS}) {
        this.cmid = cmid;
        this.userid = userid;
        this.api = api;
        this.onChange = onChange;
        this.WebSocketImpl = WebSocketImpl;
        this.typingMs = typingMs;
        this.key = null;
        this.ws = null;
        this.timer = null;
        this.delay = RETRY_MIN;
        this.open = new Set();
        this.inputAt = new Map();
        this.typingTimers = new Map();
        this.last = null;
    }

    /**
     * Follows the consoles of a session; the same key again changes nothing.
     *
     * @param {string} key identity of the running session
     */
    start(key) {
        if (this.key === key) {
            return;
        }
        this.stop();
        this.key = key;
        this.connect();
    }

    /**
     * Stops following: the lab stopped or the page leaves. The nodes lose their marks.
     */
    stop() {
        this.key = null;
        clearTimeout(this.timer);
        this.delay = RETRY_MIN;
        const ws = this.ws;
        this.ws = null;
        if (ws) {
            try {
                ws.close();
            } catch (e) {
                // Already closing.
            }
        }
        this.reset();
    }

    /**
     * What the student does with the console of a node.
     *
     * @param {string} nodeId node id
     * @return {{open: boolean, typing: boolean, last: boolean}} open: the student has the console open; typing: input
     *     came a moment ago; last: of the open consoles, this one got the student's last input
     */
    state(nodeId) {
        const open = this.open.has(nodeId);
        const at = this.inputAt.get(nodeId);
        return {
            open,
            typing: open && at !== undefined && Date.now() - at < this.typingMs,
            last: open && this.last === nodeId,
        };
    }

    async connect() {
        const key = this.key;
        if (key === null) {
            return;
        }
        let ticket;
        try {
            [ticket] = await Promise.all(this.api([{
                methodname: 'mod_gns3lab_get_activity_ticket',
                args: {cmid: this.cmid, userid: this.userid},
            }]));
        } catch (error) {
            if (this.key === key) {
                this.retry();
            }
            return;
        }
        if (this.key !== key) {
            return;
        }
        let ws;
        try {
            const Socket = this.WebSocketImpl || WebSocket;
            ws = new Socket(ticket.gatewayurl + '/console/activity?ticket=' + encodeURIComponent(ticket.ticket));
        } catch (error) {
            this.retry();
            return;
        }
        this.ws = ws;
        ws.onmessage = event => {
            if (this.ws !== ws || typeof event.data !== 'string') {
                return;
            }
            let frame;
            try {
                frame = JSON.parse(event.data);
            } catch (error) {
                return;
            }
            this.delay = RETRY_MIN;
            this.take(frame);
        };
        ws.onclose = () => {
            if (this.ws !== ws) {
                return;
            }
            this.ws = null;
            // Not knowing is not "nothing is open": the marks go until the next snapshot.
            this.reset();
            this.retry();
        };
    }

    retry() {
        if (this.key === null) {
            return;
        }
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this.connect(), this.delay);
        this.delay = Math.min(this.delay * 2, RETRY_MAX);
    }

    /**
     * Applies a frame of the gateway; unknown frames are skipped.
     *
     * @param {object} frame
     */
    take(frame) {
        if (!frame || typeof frame !== 'object') {
            return;
        }
        const node = typeof frame.node === 'string' ? frame.node : null;
        if (frame.type === 'snapshot') {
            const changed = new Set([...this.open, ...this.inputAt.keys()]);
            this.clearTimers();
            this.open = new Set((Array.isArray(frame.open) ? frame.open : []).filter(id => typeof id === 'string'));
            this.inputAt = new Map();
            const now = Date.now();
            const input = frame.input && typeof frame.input === 'object' ? frame.input : {};
            Object.keys(input).forEach(id => {
                const ago = Number(input[id]);
                if (Number.isFinite(ago) && ago >= 0) {
                    this.inputAt.set(id, now - ago * 1000);
                    this.typingEnds(id);
                }
            });
            this.pickLast();
            this.open.forEach(id => changed.add(id));
            this.emit([...changed]);
        } else if (frame.type === 'open' && node) {
            this.open.add(node);
            this.emit(this.pickLast(node));
        } else if (frame.type === 'close' && node) {
            this.open.delete(node);
            this.emit(this.pickLast(node));
        } else if (frame.type === 'input' && node) {
            const before = this.last;
            this.inputAt.set(node, Date.now());
            this.last = node;
            this.typingEnds(node);
            this.emit(before && before !== node ? [node, before] : [node]);
        }
    }

    /**
     * Chooses the open console with the latest input.
     *
     * @param {string} [also] a node to name among the changed ones
     * @return {string[]} the nodes whose marks may have changed
     */
    pickLast(also) {
        const before = this.last;
        let best = null;
        this.open.forEach(id => {
            const at = this.inputAt.get(id);
            if (at !== undefined && (best === null || at > this.inputAt.get(best))) {
                best = id;
            }
        });
        this.last = best;
        return [...new Set([also, before, best].filter(Boolean))];
    }

    typingEnds(nodeId) {
        clearTimeout(this.typingTimers.get(nodeId));
        const left = this.inputAt.get(nodeId) + this.typingMs - Date.now();
        if (left > 0) {
            this.typingTimers.set(nodeId, setTimeout(() => {
                this.typingTimers.delete(nodeId);
                this.emit([nodeId]);
            }, left));
        }
    }

    clearTimers() {
        this.typingTimers.forEach(t => clearTimeout(t));
        this.typingTimers.clear();
    }

    reset() {
        const changed = [...new Set([...this.open, ...this.inputAt.keys()])];
        this.clearTimers();
        this.open = new Set();
        this.inputAt = new Map();
        this.last = null;
        this.emit(changed);
    }

    emit(nodeIds) {
        if (nodeIds.length) {
            this.onChange(nodeIds);
        }
    }
}
