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
 * The back arrow of the full-screen pages (lab, editor) works like the back button of the browser.
 *
 * A link with `data-gns3lab-back` goes back in the history when the page was opened from another page of this site in
 * the same tab (the admin page of busy slots, the course page, the activity, the library). Otherwise (a new tab, a
 * bookmark, another site) it follows its href: the activity or the library, as before.
 *
 * @module     mod_gns3lab/backlink
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/**
 * Whether going back in the history leaves for the previous page of this site.
 *
 * @returns {boolean}
 */
export const canGoBack = () => {
    if (window.history.length < 2 || !document.referrer) {
        return false;
    }
    try {
        const from = new URL(document.referrer);
        const here = new URL(window.location.href);
        // A reload of the same page brings no way back: the href is the better guess.
        return from.origin === here.origin && from.pathname + from.search !== here.pathname + here.search;
    } catch (e) {
        return false;
    }
};

let bound = false;

/**
 * Turns every back link of the page into a history back. Safe to call more than once.
 */
export const init = () => {
    if (bound) {
        return;
    }
    bound = true;
    document.addEventListener('click', (e) => {
        const link = e.target.closest ? e.target.closest('a[data-gns3lab-back]') : null;
        // A modified click (new tab, new window) keeps the plain link.
        if (!link || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || !canGoBack()) {
            return;
        }
        e.preventDefault();
        window.history.back();
    });
};
