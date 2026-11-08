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
 * The report of an auto-check run without DOM (docs/autochecks.md): findings grouped by root cause, the state of the
 * check button and the poll interval. Checked without a browser by devtools/check_checkreport.mjs.
 *
 * The lab manager points `caused_by` of a consequence at the root of its chain (CONTRACTS §2.9), so one level is
 * enough: a root and its consequences. A consequence whose root is not in the list is shown as a root of its own.
 *
 * @module     mod_gns3lab/checkreport
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/** Classes of a finding, from the most severe. */
export const CLASSES = ['critical', 'major', 'minor', 'note'];

/** Statuses of a run that is not over. */
export const PENDING = ['queued', 'running'];

/**
 * Whether a run is still going on.
 *
 * @param {object|null} run run (summary or full)
 * @returns {boolean}
 */
export const isPending = run => !!run && PENDING.includes(run.status);

/**
 * Findings grouped for the report: counted roots first (critical, major, minor), then the forgiven ones (notes) and
 * the rest; each root with its consequences, in the order of the manager.
 *
 * @param {Array} findings findings of the run (get_check_run)
 * @returns {Array<{finding: object, consequences: Array}>}
 */
export const groupFindings = findings => {
    const list = Array.isArray(findings) ? findings : [];
    const ids = new Set(list.map(f => f.id).filter(Boolean));
    const roots = [];
    const byId = new Map();
    list.forEach(f => {
        if (!f.causedby || !ids.has(f.causedby) || f.causedby === f.id) {
            const entry = {finding: f, consequences: []};
            roots.push(entry);
            if (f.id) {
                byId.set(f.id, entry);
            }
        }
    });
    list.forEach(f => {
        const root = f.causedby && f.causedby !== f.id ? byId.get(f.causedby) : null;
        if (root) {
            root.consequences.push(f);
        } else if (f.causedby && ids.has(f.causedby) && f.causedby !== f.id) {
            // The manager points at the root; a chain through another consequence still lands under a root.
            let id = f.causedby;
            const seen = new Set();
            while (id && !byId.has(id) && !seen.has(id)) {
                seen.add(id);
                id = (list.find(x => x.id === id) || {}).causedby;
            }
            if (byId.has(id)) {
                byId.get(id).consequences.push(f);
            } else {
                roots.push({finding: f, consequences: []});
            }
        }
    });
    const rank = e => (e.finding.counted ? 0 : 10) + Math.max(0, CLASSES.indexOf(e.finding.class));
    return roots
        .map((e, i) => ({e, i}))
        .sort((a, b) => rank(a.e) - rank(b.e) || a.i - b.i)
        .map(x => x.e);
};

/**
 * Whether a finding is forgiven: a note that would cost a mark by the profile without the overrides and the focus.
 *
 * @param {object} f finding
 * @returns {boolean}
 */
export const isForgiven = f => f.class === 'note';

/**
 * What the check button offers.
 *
 * @param {object|null} overview result of get_checks
 * @param {object|null} session session of get_state
 * @param {boolean} own the user looks at the own lab
 * @returns {{show: boolean, enabled: boolean, reason: string}} reason: a language string key, empty if enabled
 */
export const startState = (overview, session, own) => {
    if (!overview || !overview.available || !own) {
        return {show: false, enabled: false, reason: ''};
    }
    const running = !!session && session.status === 'running';
    const offers = running && Array.isArray(session.features) && session.features.includes('checks');
    if (isPending((overview.runs || [])[0])) {
        return {show: true, enabled: false, reason: 'chk_running'};
    }
    if (overview.attempts && overview.attempts.left === 0) {
        return {show: true, enabled: false, reason: 'chk_noattempts'};
    }
    if (!running) {
        return {show: true, enabled: false, reason: 'chk_needsrunning'};
    }
    if (!offers) {
        return {show: true, enabled: false, reason: 'chk_nofacts'};
    }
    if (!overview.canstart) {
        return {show: true, enabled: false, reason: 'chk_cannot'};
    }
    return {show: true, enabled: true, reason: ''};
};

/**
 * How long to wait before the next poll of a pending run: quickly at first, then less often.
 *
 * @param {number} polls polls of this run so far
 * @returns {number} ms
 */
export const pollDelay = polls => (polls < 10 ? 2000 : polls < 40 ? 4000 : 10000);
