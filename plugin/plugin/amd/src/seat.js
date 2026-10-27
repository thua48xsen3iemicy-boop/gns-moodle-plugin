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
 * The seat of the lab page: only one page drives a student's lab at a time (docs/seat.md).
 *
 * The server decides (classes/local/seat.php); this module only asks and shows the answer. The page claims the seat
 * when it opens and then keeps it alive with a beat every few seconds. A beat never takes the seat from another page,
 * so two pages of one login do not fight over it. When the page does not hold the seat, a wall covers the lab and says
 * why: the lab is open on another page of this browser (the student can take it over with a button), or the account
 * works in this lab in another login session (no button: that one is not taken over, it is waited for).
 *
 * The id of the page load is made once here and sent with every call that needs the seat (start and stop, console
 * tickets, the uncovered mask). It is not a secret: it only tells the tabs of one person apart.
 *
 * @module     mod_gns3lab/seat
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';

/** Language strings of the wall. */
export const STRING_KEYS = [
    'seat_take', 'seat_text_otherlogin', 'seat_text_othertab', 'seat_title_otherlogin', 'seat_title_othertab',
];

/** Codes of the errors the server gives when a call needs the seat and the page does not hold it. */
const SEAT_ERRORS = {seatotherlogin: 'otherlogin', seatothertab: 'othertab'};

/** The event on `document` that a refused call raises, so that the wall appears at once, not at the next beat. */
const REFUSED = 'gns3lab:seat-refused';

/** Seconds between beats until the server says otherwise. */
const DEFAULT_BEAT = 20;

/**
 * A random id for this page load: 32 hex digits.
 *
 * @returns {string}
 */
const makeId = () => {
    const bytes = new Uint8Array(16);
    if (window.crypto && window.crypto.getRandomValues) {
        window.crypto.getRandomValues(bytes);
    } else {
        bytes.forEach((_, i) => {
            bytes[i] = Math.floor(Math.random() * 256);
        });
    }
    return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
};

const PAGE_ID = makeId();

/**
 * The id of this page load, for the calls that need the seat.
 *
 * @returns {string}
 */
export const tabId = () => PAGE_ID;

/**
 * Tells the page that a call was refused because of the seat. Every place that calls the server for the lab does
 * this in its error handler; the page then shows the wall without waiting for the next beat.
 *
 * @param {Error} error error of core/ajax
 * @returns {boolean} true if it was a refusal of the seat
 */
export const reportRefusal = error => {
    const status = error && SEAT_ERRORS[error.errorcode];
    if (!status) {
        return false;
    }
    document.dispatchEvent(new CustomEvent(REFUSED, {detail: {status}}));
    return true;
};

export class Seat {
    /**
     * Constructor.
     *
     * @param {HTMLElement} root the application element
     * @param {object} options
     * @param {number} options.cmid course module id
     * @param {object} options.strings language strings
     * @param {Function} options.onChange called with (held) whenever the page gains or loses the seat
     */
    constructor(root, {cmid, strings, onChange}) {
        this.root = root;
        this.cmid = cmid;
        this.strings = strings;
        this.onChange = onChange;
        this.status = 'held';
        this.timer = null;
        this.stopped = false;
        const region = name => root.querySelector(`[data-region="${name}"]`);
        this.wall = region('seatwall');
        this.titleEl = region('seatTitle');
        this.textEl = region('seatText');
        this.takeBtn = region('seatTake');
        this.takeBtn.addEventListener('click', () => this.claim(true));
        document.addEventListener(REFUSED, event => this.set(event.detail.status));
        // A page that was in the background wakes up: ask at once, the seat may have changed hands meanwhile.
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && !this.stopped) {
                this.claim(false);
            }
        });
    }

    /**
     * Claims the seat for the page that has just opened, then keeps it alive.
     *
     * @returns {Promise}
     */
    start() {
        return this.claim(true);
    }

    /**
     * Asks the server for the seat.
     *
     * @param {boolean} takeover a page that was just opened or the student chose this page: take the seat from another
     *     page of the same login. A beat does not.
     * @returns {Promise}
     */
    async claim(takeover) {
        clearTimeout(this.timer);
        let beat = DEFAULT_BEAT;
        try {
            const [result] = await Promise.all(Ajax.call([{
                methodname: 'mod_gns3lab_claim_seat',
                args: {cmid: this.cmid, tabid: tabId(), takeover},
            }]));
            if (result.mode === 'off') {
                // The teacher switched the limit off: nothing is asked any more until the page is opened again.
                this.stopped = true;
                this.set('held');
                return;
            }
            beat = result.beat || DEFAULT_BEAT;
            this.set(result.status);
        } catch (error) {
            // The server did not answer: the page keeps what it knows. What needs the seat is checked by the server anyway.
            beat = DEFAULT_BEAT;
        }
        this.timer = setTimeout(() => this.claim(false), beat * 1000);
    }

    /**
     * Takes a new status and tells the page when the seat was gained or lost.
     *
     * @param {string} status held, othertab or otherlogin
     */
    set(status) {
        if (status === this.status) {
            return;
        }
        const before = this.status;
        this.status = status;
        this.drawWall();
        if ((before === 'held') !== (status === 'held')) {
            this.onChange(status === 'held');
        }
    }

    /**
     * Shows or hides the wall; while it is up nothing under it takes a click or a key.
     */
    drawWall() {
        const held = this.status === 'held';
        this.wall.hidden = held;
        for (const child of this.root.children) {
            if (child !== this.wall && !child.matches('[data-region="toast"]')) {
                child.toggleAttribute('inert', !held);
            }
        }
        if (held) {
            return;
        }
        this.titleEl.textContent = this.strings['seat_title_' + this.status];
        this.textEl.textContent = this.strings['seat_text_' + this.status];
        // Another tab of this browser can be taken over; another login cannot, it has to be waited for.
        this.takeBtn.hidden = this.status !== 'othertab';
        this.takeBtn.textContent = this.strings.seat_take;
        (this.takeBtn.hidden ? this.wall : this.takeBtn).focus();
    }
}
