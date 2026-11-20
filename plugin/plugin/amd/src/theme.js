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
 * The light / dark switch of the full-screen pages (the lab, the course page, the lab editor).
 *
 * The page follows the system theme until the user picks one: then the root gets data-theme="light" or "dark" and
 * styles.css switches its colour-scheme, which every light-dark() colour of the page follows. The choice is a per-browser
 * convenience shared by all pages of the plugin; without storage the page just follows the system. Terminals stay dark.
 *
 * @module     mod_gns3lab/theme
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {store} from 'mod_gns3lab/dom';

/** The modes in the order the button goes through them. */
export const MODES = ['auto', 'light', 'dark'];

/** Storage key of the choice. */
const KEY = 'theme';

/**
 * The stored mode, 'auto' when there is none or it is not known.
 *
 * @returns {string}
 */
export const stored = () => {
    const m = store.get(KEY, 'auto');
    return MODES.includes(m) ? m : 'auto';
};

/**
 * The mode after this one.
 *
 * @param {string} mode
 * @returns {string}
 */
export const next = mode => MODES[(MODES.indexOf(mode) + 1) % MODES.length];

/**
 * Puts a mode on a page root: no attribute for 'auto', so the styles follow the system.
 *
 * @param {HTMLElement} root
 * @param {string} mode
 */
export const apply = (root, mode) => {
    if (mode === 'auto') {
        root.removeAttribute('data-theme');
    } else {
        root.setAttribute('data-theme', mode);
    }
};

/**
 * Wires the switch of a page: templates/theme_toggle.mustache inside the root {prefix}-app.
 *
 * @param {string} prefix uniqid of the page template
 */
export const init = prefix => {
    const root = document.getElementById(prefix + '-app');
    const button = document.getElementById(prefix + '-theme');
    if (!root) {
        return;
    }
    const show = mode => {
        apply(root, mode);
        if (button) {
            button.dataset.mode = mode;
            const label = button.dataset['label' + mode[0].toUpperCase() + mode.slice(1)] || mode;
            button.title = label;
            button.setAttribute('aria-label', label);
        }
    };
    show(stored());
    if (button) {
        button.hidden = false;
        button.addEventListener('click', () => {
            const mode = next(button.dataset.mode || stored());
            store.set(KEY, mode);
            show(mode);
        });
    }
    // Another tab of the plugin changed the choice: follow it.
    window.addEventListener('storage', e => {
        if (e.key === 'gns3lab.' + KEY) {
            show(stored());
        }
    });
};
