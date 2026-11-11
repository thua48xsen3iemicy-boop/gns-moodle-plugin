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
 * The auto-check panel of the lab page (docs/autochecks.md): the "Check" action, the progress of a run, the report
 * and the earlier runs.
 *
 * The lab manager grades; the server of the plugin decides what the student may see (it leaves the findings out when
 * the activity shows only the counts). The texts of the findings come from the manager in Russian and are shown as
 * given; everything else is a language string.
 *
 * @module     mod_gns3lab/checks
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {CLASSES, groupFindings, isForgiven, isPending, pollDelay, startState} from 'mod_gns3lab/checkreport';
import {hel} from 'mod_gns3lab/dom';
import {reportRefusal, tabId} from 'mod_gns3lab/seat';

/** Why a rule was skipped for want of data: the server sends these rules to a teacher only. */
const DATA_GAPS = ['no_collector', 'collect_failed', 'unsupported_os', 'port_unknown'];

/** Language strings of the panel. */
export const STRING_KEYS = [
    'chk_title', 'chk_open', 'chk_start', 'chk_again', 'chk_attempts', 'chk_attempts_unlimited', 'chk_running',
    'chk_noattempts', 'chk_needsrunning', 'chk_nofacts', 'chk_cannot', 'chk_progress_queued', 'chk_progress_running',
    'chk_mark', 'chk_incomplete', 'chk_failed', 'chk_nofindings', 'chk_consequence', 'chk_toroot', 'chk_forgiven',
    'chk_details_counts', 'chk_details_after', 'chk_details_gone', 'chk_history', 'chk_hist_mark', 'chk_hist_incomplete',
    'chk_hist_failed', 'chk_hist_pending', 'chk_gaps', 'chk_started', 'chk_done', 'chk_empty', 'chk_observer',
    'chk_disabled', 'chk_filter', 'chk_filter_all',
    ...CLASSES.map(c => 'chk_class_' + c),
    ...CLASSES.map(c => 'chk_n_' + c),
    ...DATA_GAPS.map(r => 'chk_gap_' + r),
];

/**
 * A group of findings belongs to a class when its root or one of its consequences does.
 *
 * @param {object} group a group of groupFindings()
 * @param {string} c class
 * @returns {boolean}
 */
const inClass = (group, c) => group.finding.class === c || group.consequences.some(f => f.class === c);

export class Checks {
    /**
     * Constructor.
     *
     * @param {HTMLElement} root the application element
     * @param {object} options
     * @param {number} options.cmid course module id
     * @param {number} options.userid whose lab, 0 for the current user
     * @param {Function} options.api the transport of web service calls
     * @param {object} options.strings language strings
     * @param {Function} options.toast short message over the scheme
     * @param {Function} [options.onOpen] called before the panel opens, to close the other panels
     */
    constructor(root, {cmid, userid, api, strings, toast, onOpen}) {
        this.cmid = cmid;
        this.userid = userid;
        this.api = api;
        this.strings = strings;
        this.toast = toast;
        this.onOpen = onOpen || (() => null);
        const region = name => root.querySelector(`[data-region="${name}"]`);
        this.openBtn = region('checksOpen');
        this.markEl = region('checksMark');
        this.panel = region('checksPanel');
        this.lead = region('checksLead');
        this.body = region('checksBody');
        this.closeBtn = region('checksClose');
        this.overview = null;
        this.session = null;
        this.run = null;
        this.runId = null;
        this.filter = null;
        this.polls = 0;
        this.timer = null;
        this.busy = false;
        this.error = '';
        if (!this.openBtn || !this.panel) {
            return;
        }
        this.openBtn.addEventListener('click', () => (this.panel.hidden ? this.open() : this.close()));
        this.closeBtn.addEventListener('click', () => {
            this.close();
            this.openBtn.focus();
        });
        this.panel.addEventListener('keydown', e => {
            if (e.key === 'Escape') {
                e.stopPropagation();
                this.close();
                this.openBtn.focus();
            }
        });
    }

    /**
     * Reads the settings, the attempts and the runs; follows a run that is going on.
     */
    async load() {
        try {
            const [overview] = await Promise.all(this.api([{
                methodname: 'mod_gns3lab_get_checks',
                args: {cmid: this.cmid, userid: this.userid},
            }]));
            this.takeOverview(overview);
        } catch (error) {
            this.error = error.message || String(error);
            this.render();
        }
    }

    /**
     * A new session state from the page: the check button follows it.
     *
     * @param {object|null} session session of get_state
     */
    setSession(session) {
        this.session = session;
        this.render();
    }

    /**
     * Opens the panel.
     */
    open() {
        this.onOpen();
        this.panel.hidden = false;
        this.openBtn.setAttribute('aria-expanded', 'true');
        this.closeBtn.focus();
    }

    /**
     * Closes the panel.
     */
    close() {
        if (!this.panel || this.panel.hidden) {
            return;
        }
        this.panel.hidden = true;
        this.openBtn.setAttribute('aria-expanded', 'false');
    }

    /**
     * Takes the answer of get_checks or start_check.
     *
     * @param {object} overview the answer
     */
    takeOverview(overview) {
        this.overview = overview;
        this.error = '';
        const runs = overview.runs || [];
        const latest = runs[0] || null;
        const followLatest = latest && (isPending(latest) || !this.runId || this.runId === latest.runid
            || !runs.some(r => r.runid === this.runId));
        if (followLatest) {
            this.show(latest.runid);
        } else {
            this.render();
        }
    }

    /**
     * Starts a check of the own running lab.
     */
    async start() {
        if (this.busy) {
            return;
        }
        this.busy = true;
        this.error = '';
        this.render();
        try {
            const [overview] = await Promise.all(this.api([{
                methodname: 'mod_gns3lab_start_check',
                args: {cmid: this.cmid, tabid: tabId()},
            }]));
            this.toast(this.strings.chk_started);
            this.run = null;
            this.runId = null;
            this.takeOverview(overview);
        } catch (error) {
            // The lab is driven from another page or login: the page puts up the wall (docs/seat.md).
            reportRefusal(error);
            this.error = error.message || String(error);
        } finally {
            this.busy = false;
            this.render();
        }
    }

    /**
     * Shows a run: reads it and, while it goes on, polls it.
     *
     * @param {string} runid run id
     */
    async show(runid) {
        clearTimeout(this.timer);
        if (this.runId !== runid) {
            this.runId = runid;
            this.filter = null;
            this.run = null;
            this.polls = 0;
        }
        this.render();
        let run = null;
        try {
            [run] = await Promise.all(this.api([{
                methodname: 'mod_gns3lab_get_check_run',
                args: {cmid: this.cmid, runid, userid: this.userid},
            }]));
        } catch (error) {
            if (this.runId === runid) {
                this.error = error.message || String(error);
                // Only a run that goes on is asked again; the report of an ended one waits for the next click.
                const summary = ((this.overview && this.overview.runs) || []).find(r => r.runid === runid);
                if (isPending(this.run) || isPending(summary)) {
                    this.timer = setTimeout(() => this.show(runid), pollDelay(++this.polls));
                }
                this.render();
            }
            return;
        }
        if (this.runId !== runid) {
            return;
        }
        const was = this.run;
        this.run = run;
        this.error = '';
        if (isPending(run)) {
            this.timer = setTimeout(() => this.show(runid), pollDelay(++this.polls));
            this.render();
        } else if (isPending(was) || this.polls > 0) {
            // The run has just ended: the attempts and the list changed.
            this.polls = 0;
            this.toast(this.strings.chk_done);
            this.load();
        } else {
            this.render();
        }
    }

    /**
     * Draws the button and the panel.
     */
    render() {
        if (!this.openBtn) {
            return;
        }
        const o = this.overview;
        this.openBtn.hidden = !o || !o.enabled;
        if (!o || !o.enabled) {
            this.close();
            return;
        }
        const latest = (o.runs || []).find(r => r.status === 'done' && r.outcome === 'graded');
        this.markEl.textContent = latest ? String(latest.mark) : '';
        this.markEl.hidden = !latest;
        const own = this.userid === 0;
        const a = o.attempts || {max: 0, used: 0, left: -1};
        this.lead.textContent = !own ? this.strings.chk_observer : a.max === 0 ? this.strings.chk_attempts_unlimited
            : fill(this.strings.chk_attempts, {left: a.left, max: a.max});
        // The body is drawn again: the focus goes back to the same control, or to the panel, not to the page.
        const focused = this.body.contains(document.activeElement) ? document.activeElement.dataset.key || '' : null;
        this.body.replaceChildren();
        if (!o.available) {
            hel('p', 'chk-note', this.body).textContent = this.strings.chk_disabled;
        }
        this.renderStart(o, own);
        if (this.error) {
            const p = hel('p', 'chk-error', this.body);
            p.setAttribute('role', 'alert');
            p.textContent = this.error;
        }
        this.renderRun();
        this.renderHistory(o);
        if (focused !== null && !this.panel.hidden) {
            const same = focused && this.body.querySelector(`[data-key="${focused}"]`);
            (same && !same.disabled ? same : this.closeBtn).focus();
        }
    }

    /**
     * The check button and why it is off.
     *
     * @param {object} o result of get_checks
     * @param {boolean} own the user looks at the own lab
     */
    renderStart(o, own) {
        const state = startState(o, this.session, own);
        if (!state.show) {
            return;
        }
        const box = hel('div', 'chk-start', this.body);
        const button = hel('button', 'btn primary', box);
        button.type = 'button';
        button.dataset.key = 'start';
        button.textContent = (o.runs || []).length ? this.strings.chk_again : this.strings.chk_start;
        button.disabled = !state.enabled || this.busy;
        button.addEventListener('click', () => this.start());
        if (state.reason) {
            hel('small', '', box).textContent = this.strings[state.reason];
        }
    }

    /**
     * The shown run: progress, or mark, counts and findings.
     */
    renderRun() {
        const run = this.run;
        if (!run) {
            if (!(this.overview.runs || []).length) {
                hel('p', 'chk-note', this.body).textContent = this.strings.chk_empty;
            }
            return;
        }
        const box = hel('section', 'chk-run', this.body);
        box.setAttribute('aria-live', 'polite');
        hel('p', 'chk-when', box).textContent = when(run.requestedat);
        if (isPending(run)) {
            const p = hel('p', 'chk-progress', box);
            hel('span', 'chk-spin', p).setAttribute('aria-hidden', 'true');
            p.append(this.strings['chk_progress_' + run.status]);
            return;
        }
        if (run.status === 'failed') {
            hel('p', 'chk-error', box).textContent = fill(this.strings.chk_failed, run.errorcode || '—');
            return;
        }
        if (run.outcome !== 'graded') {
            hel('p', 'chk-warn', box).textContent = this.strings.chk_incomplete;
        } else {
            const head = hel('div', 'chk-mark', box);
            hel('span', 'chk-mark-label', head).textContent = this.strings.chk_mark;
            hel('b', 'chk-mark-value', head).textContent = String(run.mark);
        }
        // With the findings at hand the counts are filters: a click shows the findings of one class, "all" shows them all.
        const groups = run.detailed ? groupFindings(run.findings) : null;
        const counts = hel('div', 'chk-counts', box);
        if (groups) {
            counts.setAttribute('role', 'group');
            counts.setAttribute('aria-label', this.strings.chk_filter);
            if (!CLASSES.some(c => this.filter === c && groups.some(g => inClass(g, c)))) {
                this.filter = null;
            }
            this.filterChip(counts, null, this.strings.chk_filter_all, groups.length > 0);
        }
        CLASSES.forEach(c => {
            const text = fill(this.strings['chk_n_' + c], run.counts[c]);
            if (groups) {
                this.filterChip(counts, c, text, groups.some(g => inClass(g, c)));
            } else {
                hel('span', 'chk-chip sev-' + c, counts).textContent = text;
            }
        });
        this.renderDetails(box, run, groups);
    }

    /**
     * A count that filters the findings by class.
     *
     * @param {HTMLElement} parent container
     * @param {string|null} c class, null for all findings
     * @param {string} text label
     * @param {boolean} enabled there are findings to show
     */
    filterChip(parent, c, text, enabled) {
        const chip = hel('button', 'chk-chip sev-' + (c || 'all'), parent);
        chip.type = 'button';
        chip.dataset.key = 'filter-' + (c || 'all');
        chip.textContent = text;
        chip.disabled = !enabled;
        chip.setAttribute('aria-pressed', String(this.filter === c));
        chip.addEventListener('click', () => {
            this.filter = c === null || this.filter === c ? null : c;
            this.render();
        });
    }

    /**
     * The findings of a run, or why they are not shown.
     *
     * @param {HTMLElement} box container
     * @param {object} run result of get_check_run
     * @param {Array|null} groups the findings grouped by root cause, null when they are not shown
     */
    renderDetails(box, run, groups) {
        if (!run.detailed) {
            const text = run.gone ? this.strings.chk_details_gone : run.detailsfrom > 0
                ? fill(this.strings.chk_details_after, when(run.detailsfrom)) : this.strings.chk_details_counts;
            hel('p', 'chk-note', box).textContent = text;
            return;
        }
        if (!groups.length) {
            hel('p', 'chk-ok', box).textContent = this.strings.chk_nofindings;
        }
        const list = hel('ol', 'chk-findings', box);
        const anchor = id => `gns3lab-chk-${this.runId}-${id}`;
        groups.filter(g => !this.filter || inClass(g, this.filter)).forEach(({finding, consequences}) => {
            const item = hel('li', 'chk-finding sev-' + finding.class + (finding.counted ? ' counted' : ''), list);
            if (finding.id) {
                item.id = anchor(finding.id);
                item.tabIndex = -1;
            }
            this.findingLine(item, finding);
            if (!consequences.length) {
                return;
            }
            const sub = hel('ul', 'chk-consequences', item);
            consequences.forEach(c => {
                const li = hel('li', 'chk-finding consequence', sub);
                const tag = hel('span', 'chk-tag', li);
                tag.textContent = this.strings.chk_consequence;
                this.findingLine(li, c);
                const back = hel('a', 'chk-toroot', li);
                back.href = '#' + anchor(finding.id);
                back.textContent = this.strings.chk_toroot;
                back.addEventListener('click', e => {
                    e.preventDefault();
                    item.focus();
                    item.scrollIntoView({block: 'nearest'});
                });
            });
        });
        const gaps = DATA_GAPS.map(reason => [reason, (run.rules || []).filter(r => r.reason === reason).length])
            .filter(([, n]) => n > 0);
        if (gaps.length) {
            hel('p', 'chk-note', box).textContent = this.strings.chk_gaps;
            const list = hel('ul', 'chk-note', box);
            gaps.forEach(([reason, n]) => {
                hel('li', '', list).textContent = fill(this.strings['chk_gap_' + reason], n);
            });
        }
    }

    /**
     * One finding: class, message, a forgiven note.
     *
     * @param {HTMLElement} parent container
     * @param {object} f finding
     */
    findingLine(parent, f) {
        const line = hel('div', 'chk-line', parent);
        hel('span', 'chk-class sev-' + f.class, line).textContent = this.strings['chk_class_' + f.class] || f.class;
        hel('span', 'chk-msg', line).textContent = f.message || f.code;
        if (isForgiven(f)) {
            hel('small', 'chk-forgiven', parent).textContent = this.strings.chk_forgiven;
        }
    }

    /**
     * Earlier runs: a click shows one.
     *
     * @param {object} o result of get_checks
     */
    renderHistory(o) {
        const runs = o.runs || [];
        if (runs.length < 2 && (!runs.length || runs[0].runid === this.runId)) {
            return;
        }
        hel('h3', 'chk-h', this.body).textContent = this.strings.chk_history;
        const list = hel('ul', 'chk-history', this.body);
        runs.forEach(r => {
            const b = hel('button', 'issue-row' + (r.runid === this.runId ? ' current' : ''), hel('li', '', list));
            b.type = 'button';
            b.dataset.key = 'run-' + r.runid;
            b.setAttribute('aria-current', String(r.runid === this.runId));
            const label = isPending(r) ? this.strings.chk_hist_pending : r.status === 'failed' ? this.strings.chk_hist_failed
                : r.outcome === 'graded' ? fill(this.strings.chk_hist_mark, r.mark) : this.strings.chk_hist_incomplete;
            b.textContent = label;
            hel('small', '', b).textContent = when(r.requestedat);
            b.addEventListener('click', () => this.show(r.runid));
        });
    }
}

/**
 * Puts values into a language string: {$a} or {$a->name}.
 *
 * @param {string} text language string
 * @param {*} a value or object of values
 * @returns {string}
 */
const fill = (text, a) => (a !== null && typeof a === 'object'
    ? String(text).replace(/\{\$a->(\w+)\}/g, (m, k) => (k in a ? String(a[k]) : ''))
    : String(text).split('{$a}').join(String(a)));

/**
 * A time in the language of the page.
 *
 * @param {number} ts Unix time
 * @returns {string}
 */
const when = ts => (ts > 0 ? new Date(ts * 1000).toLocaleString(document.documentElement.lang || undefined,
    {dateStyle: 'medium', timeStyle: 'short'}) : '');
