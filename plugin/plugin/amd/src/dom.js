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
 * Small DOM helpers of the student interface.
 *
 * @module     mod_gns3lab/dom
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

const SVG_NS = 'http://www.w3.org/2000/svg';

/**
 * Creates an SVG element.
 *
 * @param {string} tag tag name
 * @param {object} [attrs] attributes, null and undefined values are skipped
 * @param {Element} [parent] parent to append to
 * @returns {SVGElement}
 */
export const svgEl = (tag, attrs, parent) => {
    const e = document.createElementNS(SVG_NS, tag);
    for (const k in attrs || {}) {
        if (attrs[k] !== null && attrs[k] !== undefined) {
            e.setAttribute(k, attrs[k]);
        }
    }
    if (parent) {
        parent.appendChild(e);
    }
    return e;
};

/**
 * Creates an HTML element.
 *
 * @param {string} tag tag name
 * @param {string} [cls] class names
 * @param {Element} [parent] parent to append to
 * @returns {HTMLElement}
 */
export const hel = (tag, cls, parent) => {
    const e = document.createElement(tag);
    if (cls) {
        e.className = cls;
    }
    if (parent) {
        parent.appendChild(e);
    }
    return e;
};

/**
 * Per-browser conveniences (the console arrangement, the width of the console panel, the hint already seen).
 * Storage can be blocked: everything works without it.
 */
export const store = {
    /**
     * Reads a value.
     *
     * @param {string} k key
     * @param {*} d default
     * @returns {*}
     */
    get(k, d) {
        try {
            const v = localStorage.getItem('gns3lab.' + k);
            return v === null ? d : JSON.parse(v);
        } catch (e) {
            return d;
        }
    },
    /**
     * Writes a value.
     *
     * @param {string} k key
     * @param {*} v value
     */
    set(k, v) {
        try {
            localStorage.setItem('gns3lab.' + k, JSON.stringify(v));
        } catch (e) {
            // Storage is blocked: the value is just not remembered.
        }
    },
};

/**
 * Replaces {$a} in a language string.
 *
 * @param {string} text string
 * @param {*} a value
 * @returns {string}
 */
export const fill = (text, a) => String(text).replace('{$a}', a);
