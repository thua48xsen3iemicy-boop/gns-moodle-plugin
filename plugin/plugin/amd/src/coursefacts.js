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
 * What the course page of a teacher says about a student's lab, computed from the facts (docs/teacher-dashboard.md §4.2).
 *
 * Pure functions without DOM access. The layers are derived by the same code as on the lab page: the model of the topology
 * (`model.buildModel`) and `facts.derive`; the findings are the `issues` of the layers L1, L2, L3 and routing with the
 * severity `bad`, `warn` or `info`. Nothing here repeats the rules of the layers.
 *
 * @module     mod_gns3lab/coursefacts
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {derive, v4} from 'mod_gns3lab/facts';

/** Layers in the order of the card: id of the layer in the derived object. */
export const LAYERS = ['l1', 'l2', 'l3', 'rt'];

const SEVERITY = {bad: 0, warn: 1, info: 2};

/**
 * Findings of the layers, the important ones first.
 *
 * @param {object} der derived layers
 * @returns {Object<string, {sev: string, text: string}[]>} by layer id
 */
const findings = der => {
    const out = {};
    for (const id of LAYERS) {
        const list = (der[id] && Array.isArray(der[id].issues)) ? der[id].issues : [];
        out[id] = list
            .map(x => ({sev: x.sev in SEVERITY ? x.sev : 'info', text: String(x.text)}))
            .sort((a, b) => SEVERITY[a.sev] - SEVERITY[b.sev]);
    }
    return out;
};

/**
 * Derives the layers; null when the facts say nothing about the devices.
 *
 * "Nothing" is a document without a node that reported its interfaces (not collected yet, collector failed): that is
 * unknown, not "all is well" (docs/live-state.md §1).
 *
 * @param {object} model model of the lab (model.buildModel)
 * @param {object|null} doc facts document gns3lab.facts/1 or null
 * @returns {{fx: object, der: object}|null}
 */
const layers = (model, doc) => {
    const result = model && doc ? derive(model, doc) : null;
    if (!result) {
        return null;
    }
    const reported = [...result.fx.nodes.values()].some(n => n.ifs !== null);
    return reported ? result : null;
};

/**
 * Summary of a cell of the table: the number of findings and the worst one.
 *
 * @param {object} model model of the lab
 * @param {object|null} doc facts document or null
 * @param {{stale: boolean}} [meta] stale: the facts are out of date (past their ttl)
 * @returns {{level: string, bad: number, warn: number, info: number}} level: `unknown` (no usable facts),
 *     `stale` (counts are the last known ones), `bad`, `warn` or `ok`
 */
export const summarize = (model, doc, meta = {stale: false}) => {
    const result = layers(model, doc);
    if (!result) {
        return {level: 'unknown', bad: 0, warn: 0, info: 0};
    }
    const all = LAYERS.flatMap(id => findings(result.der)[id]);
    const count = sev => all.filter(x => x.sev === sev).length;
    const summary = {bad: count('bad'), warn: count('warn'), info: count('info')};
    let level = 'ok';
    if (summary.bad) {
        level = 'bad';
    } else if (summary.warn) {
        level = 'warn';
    }
    return {...summary, level: meta.stale ? 'stale' : level};
};

/**
 * Data of the card of a student: findings by layer and the devices.
 *
 * @param {object} model model of the lab
 * @param {object|null} doc facts document or null
 * @param {Map<string, string>} [statuses] node id → status from the session (started, stopped, suspended)
 * @returns {{known: boolean, layers: {id: string, issues: object[]}[], devices: object[]}} without usable facts `known`
 *     is false, the findings are empty and the devices carry only the state; a device is
 *     `{id, name, state: on|off|unknown, up, total, addresses: {iface, address}[]}` (`up` and `total` are null when the
 *     interfaces are not known)
 */
export const card = (model, doc, statuses = new Map()) => {
    const result = layers(model, doc);
    const found = result ? findings(result.der) : {};
    const devices = [];
    for (const node of model ? model.nodes.values() : []) {
        const rec = result ? result.fx.nodes.get(node.id) : null;
        const status = statuses.get(node.id) || (rec && rec.status) || node.status || '';
        const ifs = rec && rec.ifs ? rec.ifs : null;
        const counted = (ifs || []).filter(i => i.kind !== 'loopback' && i.kind !== 'subif');
        const up = i => i.oper ? i.oper === 'up' : i.admin !== 'down';
        devices.push({
            id: node.id,
            name: node.name,
            state: status === 'started' ? 'on' : (status ? 'off' : 'unknown'),
            up: ifs ? counted.filter(up).length : null,
            total: ifs ? counted.length : null,
            addresses: (ifs || []).flatMap(i => v4(i).map(c => ({iface: i.name, address: `${c.ip}/${c.pre}`}))),
        });
    }
    return {
        known: !!result,
        layers: LAYERS.map(id => ({id, issues: found[id] || []})),
        devices,
    };
};

/**
 * Follows the rev of the facts of a session as the page sees it (the sign of a student who is stuck).
 *
 * The page only knows the changes it has seen: from the moment it was opened. Until the first change the time is the one
 * the page saw the rev for the first time.
 *
 * @param {{rev: number, since: number, changes: number}|null} prev what was known, null for the first look
 * @param {number} rev rev of the facts now
 * @param {number} now current time, ms
 * @returns {{rev: number, since: number, changes: number}} `since` is when the current rev was first seen, `changes` how
 *     many times it changed after the first look
 */
export const trackRev = (prev, rev, now) => {
    if (!prev) {
        return {rev, since: now, changes: 0};
    }
    if (prev.rev !== rev) {
        return {rev, since: now, changes: prev.changes + 1};
    }
    return prev;
};

/**
 * How long the facts have not changed, ready for the text of a cell.
 *
 * @param {{since: number, changes: number}} tracked result of trackRev
 * @param {number} now current time, ms
 * @returns {{kind: string, since: number, minutes: number}} `idle`: no change for `minutes` since the last change the
 *     page saw; `first`: no change seen at all since `since` (the page was opened then)
 */
export const idleSince = (tracked, now) => {
    const minutes = Math.max(0, Math.floor((now - tracked.since) / 60000));
    return {kind: tracked.changes > 0 ? 'idle' : 'first', since: tracked.since, minutes};
};
