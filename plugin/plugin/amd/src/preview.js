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
 * A small read-only scheme of the reference lab on the activity page, so that a lab is recognised before it is opened.
 *
 * Nodes with their icons and names, cables and the drawings of the lab (through the same whitelist sanitizer as the lab
 * page). No facts, no states, no clicks: the full interface is on the lab page.
 *
 * @module     mod_gns3lab/preview
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {buildModel} from 'mod_gns3lab/model';
import {svgEl as el} from 'mod_gns3lab/dom';
import {sanitizeDrawing} from 'mod_gns3lab/stage';

/** Room around the nodes and drawings, in scene units. */
const PAD = 40;

/**
 * Draws the scheme.
 *
 * @param {string} prefix id prefix of the template ({{uniqid}})
 */
export const init = prefix => {
    const root = document.getElementById(prefix + '-preview');
    if (!root) {
        return;
    }
    let data;
    try {
        data = JSON.parse(root.dataset.json || '{}');
    } catch (e) {
        root.hidden = true;
        return;
    }
    const model = buildModel(data.topology || {});
    const svg = root.querySelector('svg'), scene = root.querySelector('[data-region="scene"]');
    if (!model.nodes.size) {
        root.hidden = true;
        return;
    }
    const box = {x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity};
    const grow = (x0, y0, x1, y1) => {
        box.x0 = Math.min(box.x0, x0);
        box.y0 = Math.min(box.y0, y0);
        box.x1 = Math.max(box.x1, x1);
        box.y1 = Math.max(box.y1, y1);
    };
    const drawings = el('g', {class: 'pv-drawings'}, scene);
    [...model.drawings].sort((a, b) => (a.z || 0) - (b.z || 0)).forEach(d => {
        const r = sanitizeDrawing(d.svg);
        if (!r) {
            return;
        }
        const x = +d.x || 0, y = +d.y || 0;
        r.g.setAttribute('transform', `translate(${x} ${y})` + (d.rotation ? ` rotate(${+d.rotation})` : ''));
        drawings.appendChild(r.g);
        if (r.w && r.h) {
            grow(x, y, x + r.w, y + r.h);
        }
    });
    const links = el('g', {class: 'pv-links'}, scene);
    model.links.forEach(l => {
        const a = model.nodes.get(l.a.nodeId), b = model.nodes.get(l.b.nodeId);
        el('line', {x1: a.cx, y1: a.cy, x2: b.cx, y2: b.cy, class: 'pv-wire'}, links);
    });
    const nodes = el('g', {class: 'pv-nodes'}, scene);
    const marks = [];
    model.nodes.forEach(n => {
        const g = el('g', {class: 'pv-node', transform: `translate(${n.cx} ${n.cy})`}, nodes);
        const icon = el('use', {href: `#${prefix}-ico-${n.cat}`}, g);
        const name = el('text', {class: 'pv-name'}, g);
        name.textContent = n.name;
        marks.push({icon, name});
        grow(n.cx - 44, n.cy - 30, n.cx + 44, n.cy + 46);
    });
    const w = box.x1 - box.x0 + 2 * PAD, h = box.y1 - box.y0 + 2 * PAD;
    svg.setAttribute('viewBox', `${box.x0 - PAD} ${box.y0 - PAD} ${w} ${h}`);
    // A large lab is drawn small: names keep at least 12 px on the screen and icons 22 px.
    const fit = () => {
        const scale = Math.min(svg.clientWidth / w, svg.clientHeight / h) || 1;
        const size = Math.max(44, 22 / scale), font = Math.max(13, 12 / scale);
        marks.forEach(({icon, name}) => {
            Object.entries({x: -size / 2, y: -size / 2, width: size, height: size}).forEach(([k, v]) => icon.setAttribute(k, v));
            name.setAttribute('y', size / 2 + 4);
            name.style.fontSize = font + 'px';
        });
    };
    fit();
    if (window.ResizeObserver) {
        new ResizeObserver(fit).observe(svg);
    }
    const source = root.querySelector('[data-region="source"]');
    if (source) {
        source.textContent = data.source || '';
    }
    root.hidden = false;
};
