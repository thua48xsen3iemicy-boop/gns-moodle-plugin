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
 * The course page of a teacher (docs/teacher-dashboard.md): the summary of the grades by students and labs, one lab with the
 * live state of the sessions of its students, and the card of a student in a lab with the history of the checks.
 *
 * The table polls mod_gns3lab_get_course_overview every 15 s while the page is visible: the summary asks for all labs, the
 * view of a lab for that lab only. The grades are the final grades of the gradebook, read by the server. In the view of a
 * lab the facts of a running session are read by the calls the lab page uses (get_state once per session, get_facts with
 * sincerev every minute, at most four requests at a time); the figures are computed by coursefacts.js. The open card reads
 * its own facts like the lab page and the runs of the checks from get_checks.
 * Everything from the server is put into the page as text, never as HTML.
 *
 * @module     mod_gns3lab/course
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';
import {getStrings} from 'core/str';
import {hel} from 'mod_gns3lab/dom';
import {buildModel} from 'mod_gns3lab/model';
import {card, idleSince, summarize, trackRev} from 'mod_gns3lab/coursefacts';
import {CLASSES, groupFindings, isForgiven} from 'mod_gns3lab/checkreport';

/** Language strings of the page. */
const STRING_KEYS = [
    'cl_allgroups', 'cl_attempts', 'cl_avg', 'cl_back', 'cl_bad', 'cl_card_back', 'cl_card_close', 'cl_card_devices',
    'cl_card_findings', 'cl_card_grade', 'cl_card_nofacts', 'cl_card_nofindings', 'cl_card_nograde', 'cl_card_notstarted',
    'cl_card_open', 'cl_card_outof', 'cl_card_passmark', 'cl_checking', 'cl_col_checks', 'cl_col_facts', 'cl_col_grade',
    'cl_col_state', 'cl_counts', 'cl_counts_graded', 'cl_counts_passed', 'cl_dev_ifs', 'cl_dev_ip', 'cl_dev_name',
    'cl_dev_off', 'cl_dev_on', 'cl_dev_state', 'cl_dev_unknown', 'cl_elapsed', 'cl_factsok', 'cl_factsunknown',
    'cl_graded_n', 'cl_history', 'cl_history_failed', 'cl_history_none', 'cl_history_off', 'cl_idlemin', 'cl_idlesince',
    'cl_labfailed', 'cl_lastcheck', 'cl_layer_l1', 'cl_layer_l2', 'cl_layer_l3', 'cl_layer_rt', 'cl_left', 'cl_live',
    'cl_loadfailed', 'cl_loading', 'cl_nochecks', 'cl_nograde', 'cl_nogradeitem', 'cl_nostudents', 'cl_openlab',
    'cl_overridden', 'cl_passed_n', 'cl_sortgrade', 'cl_sortname', 'cl_sortstate', 'cl_sorttotal', 'cl_started',
    'cl_card_checkmark', 'cl_col_attempts', 'cl_col_avg', 'cl_sortattempts', 'cl_sortavg', 'cl_sortchecks', 'cl_sortfacts',
    'cl_sortlab', 'cl_tries', 'cl_tries_title', 'cl_avg_title', 'cl_attempts_title',
    'cl_forecast', 'cl_forecast_title', 'cl_forecast_noscripts', 'cl_forecast_incomplete', 'cl_card_forecast',
    'cl_card_forecast_none', 'cl_card_forecast_at',
    'cl_state_failed', 'cl_state_none', 'cl_state_queued', 'cl_state_running', 'cl_state_starting', 'cl_state_stopped',
    'cl_state_stopping', 'cl_student', 'cl_total', 'cl_total_title', 'cl_unobservable', 'cl_updated', 'cl_warn',
    'chk_hist_failed', 'chk_hist_incomplete', 'chk_hist_mark', 'chk_hist_pending', 'chk_n_critical', 'chk_n_major',
    'chk_n_minor', 'chk_n_note',
    'cl_run_failed', 'cl_run_toggle', 'chk_consequence', 'chk_forgiven', 'chk_nofindings', 'chk_details_counts',
    'chk_details_gone', 'chk_gaps', 'chk_class_critical', 'chk_class_major', 'chk_class_minor', 'chk_class_note',
    'chk_gap_no_collector', 'chk_gap_collect_failed', 'chk_gap_unsupported_os', 'chk_gap_port_unknown',
];
/** Why a rule of a run could not be checked (get_check_run rules[].reason), as the panel of the student lists them. */
const DATA_GAPS = ['no_collector', 'collect_failed', 'unsupported_os', 'port_unknown'];
/** Statuses of a check run that is not over yet. */
const RUN_PENDING = ['queued', 'running'];
/** Statuses of a session that has a lab up or on its way. */
const LIVE = ['queued', 'starting', 'running'];

/** The table is read this often while the page is visible, ms. */
const OVERVIEW_POLL = 15000;
/** Facts of a running session in the table, ms. */
const FACTS_POLL = 60000;
/** Facts of the open card, ms; with live L1 they change faster (as on the lab page). */
const CARD_POLL = 30000;
const CARD_POLL_L1 = 3000;
/** Requests to get_state and get_facts at the same time. */
const MAX_REQUESTS = 4;

/**
 * Order of the cells when the table is sorted by a lab: what needs a look comes first.
 *
 * @param {object|null} cell cell of the table
 * @param {object|null} fact figures of the cell
 * @returns {number}
 */
const rank = (cell, fact) => {
    if (!cell || !cell.observable) {
        return 9;
    }
    if (!cell.session) {
        return 8;
    }
    switch (cell.session.status) {
        case 'failed':
            return 0;
        case 'running': {
            const level = fact && fact.summary ? fact.summary.level : 'unknown';
            return level === 'bad' ? 1 : (level === 'warn' ? 2 : 3);
        }
        case 'queued':
        case 'starting':
            return 4;
        case 'stopping':
            return 5;
        default:
            return 7;
    }
};

/**
 * The result of a student in a lab, as the page shows it.
 *
 * The mark of the last graded auto-check (5, 4, 3, 2) is the result, whatever the grade type of the activity: a lab whose
 * grade type is "None" has marks but no gradebook grade. A grade changed by hand in the gradebook wins over the mark;
 * without a mark the gradebook grade is shown as it is.
 *
 * @param {object|null|undefined} cell cell of the table
 * @returns {object|null} {text, value (a number or null), ratio (0 to 1), passed ('yes', 'no' or ''), overridden,
 *     attempts (graded auto-checks), at (when it was given)}
 */
const resultOf = cell => {
    if (!cell || !cell.observable) {
        return null;
    }
    const grade = cell.grade;
    const checks = cell.checks;
    const attempts = checks ? checks.attempts : 0;
    const marked = !!checks && typeof checks.mark === 'number' && checks.mark >= 0;
    if (grade && (grade.overridden || !marked)) {
        const value = parseFloat(String(grade.text).replace(',', '.'));
        return {
            text: grade.text, value: Number.isFinite(value) ? value : null, ratio: grade.ratio, passed: grade.passed,
            overridden: grade.overridden, attempts, at: grade.gradedat || 0,
        };
    }
    if (!marked) {
        return null;
    }
    const top = checks.topmark;
    let pass = grade && grade.passed ? grade.passed : '';
    if (!pass && top > checks.failmark) {
        pass = checks.mark > checks.failmark ? 'yes' : 'no';
    }
    return {
        text: String(checks.mark), value: checks.mark, ratio: top > 0 ? Math.max(0, Math.min(1, checks.mark / top)) : 0,
        passed: pass, overridden: false, attempts, at: checks.markedat || 0,
    };
};

/**
 * The sorting key of a result: the best grade first; of equal grades, the one got with fewer auto-checks, then earlier.
 *
 * @param {object|null} result result of a cell (resultOf)
 * @returns {number[]|null} null without a result
 */
const resultKey = result => (result ? [-result.ratio, result.attempts, result.at] : null);

/**
 * Whether a cell counts as passed: it has a result, not below the grade to pass (the gradebook's, or above the fail mark of
 * the auto-check).
 *
 * @param {object|null} cell cell of the table
 * @returns {boolean}
 */
const passed = cell => {
    const result = resultOf(cell);
    return !!result && result.passed !== 'no';
};

/**
 * Compares two sorting keys element by element.
 *
 * @param {number[]} a key
 * @param {number[]} b key
 * @returns {number}
 */
const compareKeys = (a, b) => {
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
        const d = (a[i] ?? 0) - (b[i] ?? 0);
        if (d) {
            return d;
        }
    }
    return 0;
};

/** Order of the levels of the facts in the column "Scheme": what needs a look first. */
const FACT_ORDER = {bad: 0, warn: 1, stale: 2, ok: 3, unknown: 4};

/**
 * The course page.
 */
class CoursePage {
    /**
     * Constructor.
     *
     * @param {HTMLElement} root the application element
     * @param {number} courseid course id
     * @param {object} strings language strings
     * @param {number} labid the lab to show first, 0 for the summary
     */
    constructor(root, courseid, strings, labid) {
        this.root = root;
        this.courseid = courseid;
        this.strings = strings;
        this.labbase = root.dataset.labbase;
        const region = name => root.querySelector(`[data-region="${name}"]`);
        this.region = region;
        this.groupId = 0;
        this.search = '';
        // What the page shows: 0 for the summary of all labs, else the course module id of one lab.
        this.view = labid || 0;
        // Sorting: by the name of the student, the total, the state or the grade.
        this.sort = this.defaultSort();
        this.showgrades = false;
        this.labs = [];
        this.students = [];
        this.cells = new Map();
        // What the facts of a session said, by cell key: the model, the document, the figures, the time of the next read.
        this.facts = new Map();
        this.queue = [];
        this.running = 0;
        this.pollTimer = null;
        this.cardTimer = null;
        this.open = null;
        this.pushed = false;
        // The runs of the checks of the open card: {key, sign, data, error}.
        this.history = null;
        // The run of the history shown in full ({key, runid}) and the reports read (get_check_run), by cell and run.
        this.runOpen = null;
        this.runs = new Map();
        this.drawPending = false;
        this.loaded = false;
        // The markup on the screen, so that a redraw with the same content leaves the elements (and the focus) alone.
        this.tableHtml = '';
        this.cardHtml = '';

        const group = region('group');
        if (group) {
            group.addEventListener('change', () => {
                this.groupId = parseInt(group.value, 10) || 0;
                this.facts.clear();
                this.poll();
            });
        }
        region('search').addEventListener('input', event => {
            this.search = event.target.value.trim().toLocaleLowerCase();
            this.draw();
        });
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                clearTimeout(this.pollTimer);
                clearTimeout(this.cardTimer);
            } else {
                this.poll();
                this.pollCard();
            }
        });
        window.addEventListener('hashchange', () => this.followHash());
        window.addEventListener('popstate', () => {
            const view = parseInt(new URLSearchParams(location.search).get('lab'), 10) || 0;
            if (view !== this.view) {
                this.setView(view, false);
            }
        });
        for (const link of root.querySelectorAll('[data-region="views"] [data-view]')) {
            link.addEventListener('click', event => {
                if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
                    return; // A new tab is the browser's business.
                }
                event.preventDefault();
                this.setView(parseInt(link.dataset.view, 10) || 0, true);
            });
        }
        this.markView();
        this.poll();
    }

    /**
     * Sorting a view starts with: the names in the summary, what needs a look first in a lab.
     *
     * @returns {object} {by, dir}
     */
    defaultSort() {
        return {by: this.view ? 'state' : 'name', dir: 1};
    }

    /**
     * The address of a view.
     *
     * @param {number} view 0 for the summary, else a course module id
     * @returns {string}
     */
    viewUrl(view) {
        const params = new URLSearchParams(location.search);
        if (view) {
            params.set('lab', String(view));
        } else {
            params.delete('lab');
        }
        return `${location.pathname}?${params.toString()}`;
    }

    /**
     * Marks the current view in the list of views and keeps it in sight.
     */
    markView() {
        for (const link of this.root.querySelectorAll('[data-region="views"] [data-view]')) {
            const current = (parseInt(link.dataset.view, 10) || 0) === this.view;
            if (current) {
                link.setAttribute('aria-current', 'page');
                if (link.scrollIntoView && link.parentElement.scrollWidth > link.parentElement.clientWidth) {
                    link.scrollIntoView({block: 'nearest', inline: 'nearest'});
                }
            } else {
                link.removeAttribute('aria-current');
            }
        }
        this.root.dataset.view = this.view ? 'lab' : 'summary';
    }

    /**
     * Switches between the summary and a lab.
     *
     * @param {number} view 0 for the summary, else a course module id
     * @param {boolean} push write the address (a click), not follow it (back and forward of the browser)
     */
    setView(view, push) {
        if (view === this.view) {
            return;
        }
        if (this.open) {
            // The card belongs to the view that is left; its entry in the history is left behind as well.
            this.pushed = false;
            this.hide();
        }
        if (push) {
            history.pushState(null, '', this.viewUrl(view));
        } else if (location.hash) {
            history.replaceState(null, '', location.pathname + location.search);
        }
        this.view = view;
        this.sort = this.defaultSort();
        this.labs = [];
        this.students = [];
        this.cells = new Map();
        this.facts.clear();
        this.queue = [];
        this.loaded = false;
        this.tableHtml = '';
        this.markView();
        this.region('empty').textContent = this.str('cl_loading');
        this.draw();
        this.poll();
    }

    /**
     * A string of the page with the placeholder filled.
     *
     * @param {string} key string key
     * @param {string|number|object} [a] value of {$a}, or an object whose fields fill {$a->field}
     * @returns {string}
     */
    str(key, a) {
        let text = this.strings[key] || key;
        if (a !== null && typeof a === 'object') {
            for (const field of Object.keys(a)) {
                text = text.replace(`{$a->${field}}`, () => String(a[field]));
            }
            return text;
        }
        return a === undefined ? text : text.replace('{$a}', () => String(a));
    }

    /**
     * Key of a cell.
     *
     * @param {number} cmid course module id
     * @param {number} userid user id
     * @returns {string}
     */
    key(cmid, userid) {
        return `${cmid}:${userid}`;
    }

    /**
     * Reads the table from the server and schedules the next read while the page is visible.
     */
    async poll() {
        clearTimeout(this.pollTimer);
        if (document.hidden) {
            return;
        }
        const groupId = this.groupId;
        const view = this.view;
        try {
            const [result] = await Promise.all(Ajax.call([{
                methodname: 'mod_gns3lab_get_course_overview',
                args: {courseid: this.courseid, groupid: groupId, cmid: view},
            }]));
            if (groupId === this.groupId && view === this.view) {
                this.take(result);
            }
        } catch (error) {
            this.notice(this.str('cl_loadfailed', error && error.message ? error.message : String(error)));
            if (!this.loaded) {
                this.region('empty').textContent = this.str('cl_nostudents');
            }
        }
        clearTimeout(this.pollTimer);
        this.pollTimer = setTimeout(() => this.poll(), OVERVIEW_POLL);
    }

    /**
     * Takes an answer of get_course_overview.
     *
     * @param {object} result {labs, students, cells}
     */
    take(result) {
        this.loaded = true;
        this.notice('');
        this.showgrades = result.showgrades;
        this.labs = result.labs;
        this.students = result.students;
        this.cells = new Map(result.cells.map(c => [this.key(c.cmid, c.userid), c]));
        this.region('updated').textContent = this.str('cl_updated', this.clock(Date.now()));
        this.followHash(true);
        this.draw();
        this.wantFacts();
        if (this.open) {
            this.wantHistory(this.cells.get(this.key(this.open.cmid, this.open.userid)));
        }
    }

    /**
     * Shows or clears a notice above the table.
     *
     * @param {string} text text, empty to clear
     */
    notice(text) {
        const el = this.region('notice');
        el.textContent = text;
        el.hidden = text === '';
    }

    /**
     * Time of day for the screen.
     *
     * @param {number} ms time, ms
     * @returns {string} HH:MM
     */
    clock(ms) {
        return new Date(ms).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
    }

    /**
     * Date and time of day for the screen: the time alone today.
     *
     * @param {number} ms time, ms
     * @returns {string}
     */
    when(ms) {
        const date = new Date(ms);
        if (date.toDateString() === new Date().toDateString()) {
            return this.clock(ms);
        }
        return date.toLocaleString([], {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'});
    }

    /**
     * Hours and minutes for a time span.
     *
     * @param {number} seconds seconds
     * @returns {string} H:MM
     */
    span(seconds) {
        const minutes = Math.floor(Math.max(0, seconds) / 60);
        return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}`;
    }

    /**
     * Cells with a running session that offers facts in the lab shown, and the open card: their figures are read.
     *
     * @returns {object[]} cells
     */
    factCells() {
        const open = this.open;
        return [...this.cells.values()].filter(c => c.observable && c.session && c.session.status === 'running'
            && c.session.facts !== 'no'
            && (this.view === c.cmid || (open && open.cmid === c.cmid && open.userid === c.userid)));
    }

    /**
     * Schedules the reading of facts for the cells that are due. Called after every answer of the overview.
     */
    wantFacts() {
        const now = Date.now();
        const live = new Set();
        for (const cell of this.factCells()) {
            const key = this.key(cell.cmid, cell.userid);
            live.add(key);
            const fact = this.facts.get(key);
            const identity = `${cell.session.status}|${cell.session.startedat}`;
            if (fact && fact.identity !== identity) {
                // Another session or another status: the topology is read again.
                this.facts.delete(key);
            }
            const known = this.facts.get(key);
            if (!known || (known.nextAt <= now && !known.busy && !known.nofacts)) {
                this.enqueue(cell);
            }
        }
        // Sessions that are no longer running drop their figures.
        for (const key of [...this.facts.keys()]) {
            if (!live.has(key)) {
                this.facts.delete(key);
            }
        }
    }

    /**
     * Puts a cell into the queue of fact reads, at most once.
     *
     * @param {object} cell cell of the table
     */
    enqueue(cell) {
        const key = this.key(cell.cmid, cell.userid);
        const fact = this.facts.get(key) || {
            identity: `${cell.session.status}|${cell.session.startedat}`,
            model: null, statuses: new Map(), doc: null, rev: 0, meta: {stale: false}, summary: null,
            track: null, nextAt: 0, busy: false, nofacts: false,
        };
        if (fact.busy) {
            return;
        }
        fact.busy = true;
        this.facts.set(key, fact);
        this.queue.push(() => this.readFacts(cell, fact));
        this.pump();
    }

    /**
     * Starts queued reads while fewer than MAX_REQUESTS are running.
     */
    pump() {
        while (this.running < MAX_REQUESTS && this.queue.length) {
            const job = this.queue.shift();
            this.running++;
            job().finally(() => {
                this.running--;
                this.pump();
            });
        }
    }

    /**
     * Reads the topology (once per session) and the facts of a cell and works the figures out.
     *
     * @param {object} cell cell of the table
     * @param {object} fact what is known about the session
     * @returns {Promise}
     */
    async readFacts(cell, fact) {
        try {
            if (!fact.model) {
                const [state] = await Promise.all(Ajax.call([{
                    methodname: 'mod_gns3lab_get_state',
                    args: {cmid: cell.cmid, userid: cell.userid},
                }]));
                const session = state.session;
                if (!session || session.status !== 'running' || !session.topology) {
                    fact.nextAt = Date.now() + FACTS_POLL;
                    return;
                }
                fact.model = buildModel(JSON.parse(session.topology));
                fact.statuses = new Map(session.nodes.map(n => [n.nodeid, n.status]));
                fact.features = session.features;
                if (!session.features.includes('facts')) {
                    fact.nofacts = true;
                    return;
                }
            }
            const [result] = await Promise.all(Ajax.call([{
                methodname: 'mod_gns3lab_get_facts',
                args: {cmid: cell.cmid, userid: cell.userid, sincerev: fact.rev},
            }]));
            this.takeFacts(fact, result);
        } catch (error) {
            // The last figures stay; the next round may work.
            fact.nextAt = Date.now() + FACTS_POLL;
        } finally {
            fact.busy = false;
            fact.nextAt = Math.max(fact.nextAt, Date.now() + FACTS_POLL - 1000);
            this.schedule();
            this.cardChanged(cell);
        }
    }

    /**
     * Takes an answer of get_facts into what is known about the session.
     *
     * @param {object} fact what is known
     * @param {object} result {available, changed, rev, generatedat, ttl, facts}
     */
    takeFacts(fact, result) {
        if (!result.available) {
            fact.nofacts = true;
            fact.doc = null;
            fact.summary = summarize(fact.model, null);
            return;
        }
        if (result.changed) {
            try {
                fact.doc = JSON.parse(result.facts);
            } catch (e) {
                fact.doc = null;
            }
        }
        // The rev is what the page follows to tell a student who is stuck.
        fact.track = trackRev(fact.track, result.rev, Date.now());
        fact.rev = result.rev;
        const at = result.generatedat ? result.generatedat * 1000 : 0;
        fact.meta = {stale: !!at && Date.now() - at > result.ttl * 1000};
        fact.live = !!fact.doc && Object.values(fact.doc.nodes || {}).some(n => n.l1_ports);
        fact.summary = summarize(fact.model, fact.doc, fact.meta);
    }

    /**
     * Redraws soon, once for a burst of changes.
     */
    schedule() {
        if (this.drawPending) {
            return;
        }
        this.drawPending = true;
        requestAnimationFrame(() => {
            this.drawPending = false;
            this.draw();
        });
    }

    /**
     * Figures of the facts of a cell.
     *
     * @param {number} cmid course module id
     * @param {number} userid user id
     * @returns {object|undefined}
     */
    factOf(cmid, userid) {
        return this.facts.get(this.key(cmid, userid));
    }

    /**
     * Rows of the table: the students that match the search, in the order asked.
     *
     * @returns {object[]}
     */
    rows() {
        const rows = this.students.filter(s => !this.search || s.fullname.toLocaleLowerCase().includes(this.search));
        const by = this.sort.by;
        const dir = this.sort.dir;
        const byName = (a, b) => a.fullname.localeCompare(b.fullname, undefined, {numeric: true});
        if (by === 'name') {
            return [...rows].sort((a, b) => byName(a, b) * dir);
        }
        // Students without a value for the column stay at the bottom whichever way it is sorted.
        const keyed = rows.map(student => ({student, key: this.sortKey(by, student)}));
        keyed.sort((a, b) => {
            if (!a.key || !b.key) {
                return (a.key ? 0 : 1) - (b.key ? 0 : 1) || byName(a.student, b.student);
            }
            return compareKeys(a.key, b.key) * dir || byName(a.student, b.student);
        });
        return keyed.map(k => k.student);
    }

    /**
     * The sorting key of a student for a column: smaller first in the order the column is sorted at the first click.
     *
     * @param {string} by column: state, facts, checks, grade, lab:<cmid>, total, avg, attempts
     * @param {object} student student of the table
     * @returns {number[]|null} null when the student has nothing in the column
     */
    sortKey(by, student) {
        if (by.startsWith('lab:')) {
            return resultKey(resultOf(this.cells.get(this.key(parseInt(by.slice(4), 10), student.userid))));
        }
        const cmid = this.view;
        const cell = this.cells.get(this.key(cmid, student.userid));
        switch (by) {
            case 'state':
                return cell && cell.observable ? [rank(cell, this.factOf(cmid, student.userid))] : null;
            case 'facts': {
                const fact = this.factOf(cmid, student.userid);
                const summary = cell && cell.session && cell.session.status === 'running' && fact ? fact.summary : null;
                return summary ? [FACT_ORDER[summary.level] ?? 5, -summary.bad, -summary.warn] : null;
            }
            case 'checks': {
                const checks = cell && cell.observable ? cell.checks : null;
                // A run in progress first, then the fewest graded attempts, then the latest check.
                return checks ? [RUN_PENDING.includes(checks.laststatus) ? 0 : 1, checks.attempts, -checks.lastat] : null;
            }
            case 'grade':
                return resultKey(resultOf(cell));
            case 'total': {
                const total = this.total(student);
                return total.total ? [-total.passed, -total.ratio, total.attempts] : null;
            }
            case 'avg': {
                const total = this.total(student);
                return total.avg === null ? null : [-total.avg, total.attempts];
            }
            case 'attempts': {
                const total = this.total(student);
                return total.attempts ? [total.attempts, -total.passed] : null;
            }
            default:
                return null;
        }
    }

    /**
     * Draws the counters and the table.
     */
    draw() {
        const rows = this.rows();
        this.drawCounts(rows);
        const empty = this.region('empty');
        empty.hidden = this.loaded && this.students.length > 0 && rows.length > 0;
        if (this.loaded && !empty.hidden) {
            empty.textContent = this.str('cl_nostudents');
        }
        const table = document.createElement('table');
        if (this.labs.length && rows.length) {
            if (this.view) {
                this.drawLab(table, rows, this.labs[0]);
            } else {
                this.drawSummary(table, rows);
            }
        }
        const html = table.className + table.innerHTML;
        if (html !== this.tableHtml) {
            this.tableHtml = html;
            this.region('table').className = table.className || 'grid';
            this.region('table').replaceChildren(...table.childNodes);
        }
    }

    /**
     * The cell of a student: the picture and the name; in a lab a button that opens the card.
     *
     * @param {HTMLElement} tr row
     * @param {object} student student of the table
     * @param {object|null} cell the cell the name opens, in the view of a lab
     */
    drawName(tr, student, cell) {
        const th = hel('th', 'stud', tr);
        th.scope = 'row';
        const holder = cell && cell.observable ? hel('button', 'namebtn', th) : th;
        if (holder !== th) {
            holder.type = 'button';
            holder.addEventListener('click', () => this.show(cell.cmid, cell.userid, true));
        }
        const img = hel('img', 'pic', holder);
        img.src = student.pictureurl;
        img.alt = '';
        img.width = 18;
        img.height = 18;
        hel('span', 'name', holder).textContent = student.fullname;
    }

    /**
     * Whether the page shows results of a lab: grades are visible, and the lab has a grade item or auto-checks.
     *
     * @param {object} lab lab of the table
     * @returns {boolean}
     */
    graded(lab) {
        return this.showgrades && (lab.gradetype !== 'none' || !!lab.checks);
    }

    /**
     * The results of a student over the labs with results the user may observe the student in.
     *
     * @param {object} student student of the table
     * @returns {object} {passed, total, ratio (average), avg (average value shown, null without one), attempts (all graded
     *     auto-checks of the labs with a result)}
     */
    total(student) {
        const out = {passed: 0, total: 0, ratio: 0, avg: null, attempts: 0};
        let ratios = 0;
        let values = 0;
        let sum = 0;
        for (const lab of this.labs) {
            const cell = this.cells.get(this.key(lab.cmid, student.userid));
            if (!this.graded(lab) || !cell || !cell.observable) {
                continue;
            }
            out.total++;
            const result = resultOf(cell);
            if (!result) {
                continue;
            }
            if (result.passed !== 'no') {
                out.passed++;
            }
            out.ratio += result.ratio;
            ratios++;
            out.attempts += result.attempts;
            if (result.value !== null) {
                sum += result.value;
                values++;
            }
        }
        out.ratio = ratios ? out.ratio / ratios : 0;
        out.avg = values ? sum / values : null;
        return out;
    }

    /**
     * How many of the students shown passed a lab, how many have a result, out of how many can be observed in it, and the
     * average of the results shown.
     *
     * @param {object} lab lab of the table
     * @param {object[]} rows students shown
     * @returns {object} {passed, graded, total, average (formatted, empty without numbers)}
     */
    gradeCounts(lab, rows) {
        const cells = rows.map(s => this.cells.get(this.key(lab.cmid, s.userid))).filter(c => c && c.observable);
        const results = cells.map(resultOf).filter(r => r);
        const values = results.map(r => r.value).filter(v => v !== null);
        return {
            passed: cells.filter(passed).length,
            graded: results.length,
            total: cells.length,
            average: values.length ? this.number(values.reduce((a, b) => a + b, 0) / values.length) : '',
        };
    }

    /**
     * A number as the language of the page writes it, with at most two decimals.
     *
     * @param {number} value number
     * @returns {string}
     */
    number(value) {
        try {
            return new Intl.NumberFormat(document.documentElement.lang || undefined, {maximumFractionDigits: 2}).format(value);
        } catch (e) {
            return String(Math.round(value * 100) / 100);
        }
    }

    /**
     * The summary: a column per lab with the grades of the gradebook, the total per student and per lab.
     *
     * @param {HTMLTableElement} table the table
     * @param {object[]} rows students shown
     */
    drawSummary(table, rows) {
        table.className = 'grid summary';
        const graded = this.labs.some(lab => this.graded(lab));
        const checked = this.labs.some(lab => lab.checks);
        const head = hel('tr', '', hel('thead', '', table));
        const corner = hel('th', 'stud', head);
        corner.scope = 'col';
        this.sortButton(corner, this.str('cl_student'), 'name', this.str('cl_sortname'));
        for (const lab of this.labs) {
            const th = hel('th', 'lab', head);
            th.scope = 'col';
            const box = hel('span', 'labhead', th);
            // The header leads to the lab, where its students are looked at closely; the arrow sorts by it.
            const link = hel('a', 'labbtn', box);
            link.href = this.viewUrl(lab.cmid);
            link.textContent = lab.name;
            link.title = this.str('cl_openlab');
            link.addEventListener('click', event => {
                if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
                    return;
                }
                event.preventDefault();
                this.setView(lab.cmid, true);
            });
            if (lab.error) {
                const warn = hel('span', 'laberr', box);
                warn.textContent = '!';
                warn.title = this.str('cl_labfailed', lab.error);
            }
            const sort = this.sortButton(box, '', 'lab:' + lab.cmid, this.str('cl_sortlab'));
            sort.classList.add('sortonly');
            sort.setAttribute('aria-label', this.str('cl_sortlab') + ': ' + lab.name);
        }
        const extra = (key, label, title) => {
            const th = hel('th', 'total', head);
            th.scope = 'col';
            this.sortButton(th, label, key, title);
        };
        if (graded) {
            extra('total', this.str('cl_total'), this.str('cl_sorttotal'));
            extra('avg', this.str('cl_col_avg'), this.str('cl_sortavg'));
        }
        if (graded && checked) {
            extra('attempts', this.str('cl_col_attempts'), this.str('cl_sortattempts'));
        }
        const body = hel('tbody', '', table);
        for (const student of rows) {
            const tr = hel('tr', '', body);
            this.drawName(tr, student, null);
            for (const lab of this.labs) {
                const td = hel('td', '', tr);
                this.drawSummaryCell(td, lab, student, this.cells.get(this.key(lab.cmid, student.userid)));
            }
            if (graded) {
                const total = this.total(student);
                const td = hel('td', 'total', tr);
                td.textContent = total.total ? `${total.passed} / ${total.total}` : '—';
                td.title = this.str('cl_total_title');
                const avg = hel('td', 'total avg', tr);
                avg.textContent = total.avg === null ? '—' : this.number(total.avg);
                avg.title = this.str('cl_avg_title');
                if (checked) {
                    const tries = hel('td', 'total tries', tr);
                    tries.textContent = total.attempts ? String(total.attempts) : '—';
                    tries.title = this.str('cl_attempts_title');
                }
            }
        }
        // Per lab: the average and how many passed of the students shown.
        if (this.showgrades) {
            const foot = hel('tr', '', hel('tfoot', '', table));
            hel('th', 'stud', foot).scope = 'row';
            for (const lab of this.labs) {
                const td = hel('td', 'labsum', foot);
                if (!this.graded(lab)) {
                    td.classList.add('muted');
                    td.textContent = this.str('cl_nogradeitem');
                    continue;
                }
                const count = this.gradeCounts(lab, rows);
                if (count.average) {
                    hel('span', 'avg', td).textContent = this.str('cl_avg', count.average);
                }
                hel('span', 'cnt', td).textContent = lab.gradepass || lab.checks
                    ? this.str('cl_passed_n', count) : this.str('cl_graded_n', count);
            }
            if (graded) {
                hel('td', 'total', foot).colSpan = checked ? 3 : 2;
            }
        }
    }

    /**
     * The cell of a student in a lab of the summary: the grade of the gradebook, or how far the student got.
     *
     * @param {HTMLElement} td table cell
     * @param {object} lab lab of the table
     * @param {object} student student of the table
     * @param {object|undefined} cell cell from the server
     */
    drawSummaryCell(td, lab, student, cell) {
        if (!cell || !cell.observable) {
            td.className = 'none';
            td.textContent = '—';
            td.title = this.str('cl_unobservable');
            return;
        }
        const button = hel('button', 'sumbtn', td);
        button.type = 'button';
        const session = cell.session;
        const live = !!session && LIVE.includes(session.status);
        if (this.open && this.open.cmid === cell.cmid && this.open.userid === cell.userid) {
            button.setAttribute('aria-current', 'true');
        }
        const result = this.graded(lab) ? resultOf(cell) : null;
        if (result) {
            button.dataset.kind = 'graded';
            if (result.passed) {
                button.dataset.pass = result.passed;
            }
            this.gradeText(button, result, true);
        } else {
            const pending = cell.checks && RUN_PENDING.includes(cell.checks.laststatus);
            let kind = 'none';
            if (pending) {
                kind = 'checking';
            } else if (session && session.status === 'running') {
                kind = 'running';
            } else if (session && session.status === 'failed') {
                kind = 'failed';
            } else if (session || cell.checks) {
                kind = 'started';
            }
            button.dataset.kind = kind;
            const forecast = this.graded(lab) ? cell.forecast : null;
            if (!forecast || kind !== 'started') {
                const text = {
                    none: this.str('cl_state_none'),
                    checking: this.str('cl_checking'),
                    running: this.str('cl_state_running'),
                    failed: this.str('cl_state_failed'),
                    started: this.graded(lab) ? this.str('cl_nograde') : this.str('cl_started'),
                }[kind];
                hel('span', 'state', button).textContent = text;
            }
            if (forecast) {
                this.forecastText(button, forecast);
            }
        }
        if (live && (result || session.status !== 'running')) {
            // A grade and a lab that is up again: both are worth knowing.
            const dot = hel('span', 'live', button);
            dot.textContent = '●';
            dot.title = this.str('cl_live');
        }
        button.setAttribute('aria-label', `${student.fullname}, ${lab.name}: ${button.textContent}`);
        button.addEventListener('click', () => this.show(cell.cmid, cell.userid, true));
    }

    /**
     * The line of the card with the forecast from the facts, or that there is none yet.
     *
     * @param {HTMLElement} el the card being built
     * @param {object|null} forecast forecast of the cell
     */
    renderCardForecast(el, forecast) {
        const line = hel('p', 'cgrade cforecast', el);
        line.title = this.str('cl_forecast_title');
        hel('span', 'lbl', line).textContent = this.str('cl_card_forecast') + ': ';
        if (!forecast) {
            hel('span', 'sub', line).textContent = this.str('cl_card_forecast_none');
            return;
        }
        hel('span', 'grade', line).textContent = String(forecast.mark);
        if (forecast.topmark) {
            hel('span', 'sub', line).textContent = ' ' + this.str('cl_card_outof', forecast.topmark);
        }
        const notes = [];
        if (forecast.scriptsignored) {
            notes.push(this.str('cl_forecast_noscripts'));
        }
        if (forecast.incomplete) {
            notes.push(this.str('cl_forecast_incomplete'));
        }
        if (forecast.assessedat) {
            notes.push(this.str('cl_card_forecast_at', this.when(forecast.assessedat * 1000)));
        }
        for (const note of notes) {
            hel('span', 'sub', line).textContent = ' · ' + note;
        }
    }

    /**
     * The forecast of the manager from the facts (docs/teacher-dashboard.md §14): never a grade, so it looks like none.
     *
     * @param {HTMLElement} el where it goes
     * @param {object} forecast forecast of a cell
     */
    forecastText(el, forecast) {
        const span = hel('span', 'forecast', el);
        span.textContent = this.str('cl_forecast', forecast.mark);
        span.title = this.forecastTitle(forecast);
    }

    /**
     * What a forecast is, for a tooltip.
     *
     * @param {object} forecast forecast of a cell
     * @returns {string}
     */
    forecastTitle(forecast) {
        const parts = [this.str('cl_forecast_title')];
        if (forecast.scriptsignored) {
            parts.push(this.str('cl_forecast_noscripts'));
        }
        if (forecast.incomplete) {
            parts.push(this.str('cl_forecast_incomplete'));
        }
        return parts.join('; ');
    }

    /**
     * A result: the grade, the mark of a grade changed by hand, and how many auto-checks it took.
     *
     * @param {HTMLElement} el where it goes
     * @param {object} result result of a cell (resultOf)
     * @param {boolean} tries show the graded auto-checks
     */
    gradeText(el, result, tries) {
        hel('span', 'grade', el).textContent = result.text;
        if (result.overridden) {
            const mark = hel('span', 'over', el);
            mark.textContent = '✎';
            mark.title = this.str('cl_overridden');
            mark.setAttribute('aria-label', this.str('cl_overridden'));
        }
        if (tries && result.attempts) {
            const count = hel('span', 'tries', el);
            count.textContent = this.str('cl_tries', result.attempts);
            count.title = this.str('cl_tries_title', result.attempts);
        }
    }

    /**
     * One lab: a row per student with the session, the live state of the scheme, the checks and the grade.
     *
     * @param {HTMLTableElement} table the table
     * @param {object[]} rows students shown
     * @param {object} lab the lab
     */
    drawLab(table, rows, lab) {
        table.className = 'grid lab';
        const grades = this.graded(lab);
        const head = hel('tr', '', hel('thead', '', table));
        const corner = hel('th', 'stud', head);
        corner.scope = 'col';
        this.sortButton(corner, this.str('cl_student'), 'name', this.str('cl_sortname'));
        const th = (cls, label, by, title) => {
            const cell = hel('th', cls, head);
            cell.scope = 'col';
            if (by) {
                this.sortButton(cell, label, by, title);
            } else {
                hel('span', 'colname', cell).textContent = label;
            }
            return cell;
        };
        const state = th('col-state', this.str('cl_col_state'), 'state', this.str('cl_sortstate'));
        if (lab.error) {
            const warn = hel('span', 'laberr', state);
            warn.textContent = '!';
            warn.title = this.str('cl_labfailed', lab.error);
        }
        th('col-facts', this.str('cl_col_facts'), 'facts', this.str('cl_sortfacts'));
        th('col-checks', this.str('cl_col_checks'), 'checks', this.str('cl_sortchecks'));
        if (grades) {
            th('col-grade', this.str('cl_col_grade'), 'grade', this.str('cl_sortgrade'));
        }
        const body = hel('tbody', '', table);
        for (const student of rows) {
            const cell = this.cells.get(this.key(lab.cmid, student.userid));
            const tr = hel('tr', 'labrow', body);
            this.drawName(tr, student, cell);
            if (!cell || !cell.observable) {
                const td = hel('td', 'none', tr);
                td.colSpan = grades ? 4 : 3;
                td.textContent = '—';
                td.title = this.str('cl_unobservable');
                continue;
            }
            if (this.open && this.open.cmid === cell.cmid && this.open.userid === cell.userid) {
                tr.setAttribute('aria-current', 'true');
            }
            // A click anywhere in the row opens the card; the button of the name is the way for the keyboard.
            tr.addEventListener('click', event => {
                if (!event.target.closest('button, a')) {
                    this.show(cell.cmid, cell.userid, true);
                }
            });
            const session = cell.session;
            const status = session ? session.status : 'none';
            const fact = this.factOf(cell.cmid, cell.userid);
            const td = hel('td', 'state', tr);
            td.dataset.status = status;
            const level = status === 'running' && fact && fact.summary ? fact.summary.level : '';
            if (level) {
                td.dataset.level = level;
            }
            hel('span', 'state', td).textContent = this.stateText(status);
            const time = this.timeText(session);
            if (time) {
                hel('span', 'time', td).textContent = time;
            }
            const facts = hel('td', 'facts', tr);
            if (status === 'running') {
                this.drawFacts(facts, fact, session.facts === 'no');
            }
            if (!facts.childNodes.length) {
                facts.textContent = '—';
                facts.classList.add('muted');
            }
            this.drawChecks(hel('td', 'checks', tr), cell.checks);
            if (grades) {
                const grade = hel('td', 'gradecell', tr);
                const result = resultOf(cell);
                if (result) {
                    if (result.passed) {
                        grade.dataset.pass = result.passed;
                    }
                    this.gradeText(grade, result, false);
                } else if (cell.forecast) {
                    this.forecastText(grade, cell.forecast);
                } else {
                    grade.textContent = '—';
                    grade.classList.add('muted');
                }
            }
        }
    }

    /**
     * The checks of a student in a lab: graded attempts and the last run.
     *
     * @param {HTMLElement} td table cell
     * @param {object|null} checks summary of the runs
     */
    drawChecks(td, checks) {
        if (!checks) {
            td.textContent = this.str('cl_nochecks');
            td.classList.add('muted');
            return;
        }
        if (RUN_PENDING.includes(checks.laststatus)) {
            hel('span', 'checking', td).textContent = this.str('cl_checking');
        } else {
            hel('span', '', td).textContent = this.str('cl_attempts', checks.attempts);
        }
        if (checks.lastat) {
            hel('span', 'time', td).textContent = this.str('cl_lastcheck', this.when(checks.lastat * 1000));
        }
    }

    /**
     * A header button that sorts the table.
     *
     * @param {HTMLElement} th header cell
     * @param {string} label text
     * @param {string|number} by sorting key
     * @param {string} title tooltip
     * @returns {HTMLButtonElement}
     */
    sortButton(th, label, by, title) {
        const button = hel('button', 'sortbtn', th);
        button.type = 'button';
        button.textContent = label;
        button.title = title;
        const active = this.sort.by === by;
        button.setAttribute('aria-pressed', active ? 'true' : 'false');
        if (active) {
            button.dataset.dir = this.sort.dir > 0 ? 'asc' : 'desc';
        }
        button.addEventListener('click', () => {
            this.sort = {by, dir: active ? -this.sort.dir : 1};
            this.draw();
        });
        return button;
    }

    /**
     * What the facts say in a cell: the findings, how long nothing changed, or that nothing is known.
     *
     * @param {HTMLElement} button where the figures go
     * @param {object|undefined} fact figures of the cell
     * @param {boolean} nofacts the list of the server says the session reports no facts
     */
    drawFacts(button, fact, nofacts) {
        const summary = fact ? fact.summary : null;
        const sign = () => {
            // Unknown is not "all is well": a grey sign with the reason.
            const el = hel('span', 'sign unknown', button);
            el.textContent = '◌';
            el.title = this.str('cl_factsunknown');
        };
        if (nofacts || (fact && fact.nofacts && !fact.doc)) {
            sign();
            return;
        }
        if (!summary) {
            return; // Still reading.
        }
        if (summary.level === 'unknown') {
            sign();
            return;
        }
        if (summary.level === 'stale') {
            sign();
        }
        const line = hel('span', 'finds', button);
        const badge = (cls, text, title) => {
            const el = hel('span', 'n ' + cls, line);
            el.textContent = text;
            el.title = title;
        };
        if (summary.bad) {
            badge('bad', String(summary.bad), this.str('cl_bad', summary.bad));
        }
        if (summary.warn) {
            badge('warn', String(summary.warn), this.str('cl_warn', summary.warn));
        }
        if (summary.level === 'ok') {
            badge('ok', '✓', this.str('cl_factsok'));
        }
        if (fact.track) {
            hel('span', 'idle', button).textContent = this.idleText(fact.track);
        }
    }

    /**
     * "No changes for N min" / "No changes since HH:MM".
     *
     * @param {object} track result of trackRev
     * @returns {string}
     */
    idleText(track) {
        const idle = idleSince(track, Date.now());
        return idle.kind === 'first'
            ? this.str('cl_idlesince', this.clock(idle.since))
            : this.str('cl_idlemin', idle.minutes);
    }

    /**
     * The name of a state.
     *
     * @param {string} status none, queued, starting, running, stopping, stopped or failed
     * @returns {string}
     */
    stateText(status) {
        return this.str('cl_state_' + status);
    }

    /**
     * How long a running lab still has, or how long it has been running.
     *
     * @param {object|null} session session of a cell
     * @returns {string} empty if the session is not running or says nothing
     */
    timeText(session) {
        if (!session || session.status !== 'running') {
            return '';
        }
        const now = Math.floor(Date.now() / 1000);
        if (session.expiresat) {
            return this.str('cl_left', this.span(session.expiresat - now));
        }
        return session.startedat ? this.str('cl_elapsed', this.span(now - session.startedat)) : '';
    }

    /**
     * Counters of the lab shown above the table: running, errors, not started, and the grades.
     *
     * @param {object[]} rows students shown
     */
    drawCounts(rows) {
        const list = this.region('counts');
        list.replaceChildren();
        if (!this.view) {
            return; // The summary counts in its last row.
        }
        for (const lab of this.labs) {
            const count = {running: 0, failed: 0, none: 0};
            for (const student of rows) {
                const cell = this.cells.get(this.key(lab.cmid, student.userid));
                if (!cell || !cell.observable) {
                    continue;
                }
                const status = cell.session ? cell.session.status : 'none';
                if (status === 'running') {
                    count.running++;
                } else if (status === 'failed') {
                    count.failed++;
                } else if (status === 'none') {
                    count.none++;
                }
            }
            const li = hel('li', lab.error ? 'has-error' : '', list);
            hel('b', '', li).textContent = lab.name;
            hel('span', '', li).textContent = lab.error ? this.str('cl_labfailed', lab.error) : this.str('cl_counts', count);
            if (this.graded(lab)) {
                const grades = this.gradeCounts(lab, rows);
                const line = hel('li', 'grades', list);
                hel('span', '', line).textContent = lab.gradepass || lab.checks
                    ? this.str('cl_counts_passed', grades) : this.str('cl_counts_graded', grades);
                if (grades.average) {
                    hel('span', '', line).textContent = this.str('cl_avg', grades.average);
                }
            }
        }
    }

    /**
     * Opens the card of a student in a lab and puts it into the address.
     *
     * @param {number} cmid course module id
     * @param {number} userid user id
     * @param {boolean} push write the address (a click), not follow it
     */
    show(cmid, userid, push) {
        const cell = this.cells.get(this.key(cmid, userid));
        if (!cell || !cell.observable) {
            return;
        }
        if (push) {
            const hash = `#cm=${cmid}&user=${userid}`;
            if (this.open) {
                // Another student while a card is open: the way back still leads to the table.
                history.replaceState(null, '', hash);
            } else if (location.hash !== hash) {
                this.pushed = true;
                location.hash = hash;
                return;
            }
        }
        this.open = {cmid, userid};
        this.cardHtml = '';
        this.history = null;
        this.root.classList.add('has-card');
        this.region('card').hidden = false;
        this.readCardFacts(cell);
        this.wantHistory(cell);
        this.renderCard();
        this.pollCard();
        this.draw();
    }

    /**
     * Closes the card: back in the history if the page opened it, else the address is cleared.
     */
    close() {
        if (this.pushed) {
            this.pushed = false;
            history.back();
            return;
        }
        history.replaceState(null, '', location.pathname + location.search);
        this.hide();
    }

    /**
     * Hides the card.
     */
    hide() {
        this.open = null;
        this.history = null;
        clearTimeout(this.cardTimer);
        this.root.classList.remove('has-card');
        this.region('card').hidden = true;
        this.draw();
    }

    /**
     * Follows the address: #cm=<cmid>&user=<userid> opens a card, an empty address closes it.
     *
     * @param {boolean} [quiet] the table was just loaded: open the card from the address only if it is not open yet
     */
    followHash(quiet = false) {
        const params = new URLSearchParams(location.hash.replace(/^#/, ''));
        const cmid = parseInt(params.get('cm'), 10);
        const userid = parseInt(params.get('user'), 10);
        const same = this.open && this.open.cmid === cmid && this.open.userid === userid;
        if (cmid > 0 && userid > 0) {
            if (!same || !quiet) {
                this.show(cmid, userid, false);
            } else {
                this.renderCard();
            }
        } else if (this.open) {
            this.hide();
        }
    }

    /**
     * The facts of the open card: read at once, then like the lab page (30 s, 3 s with live L1).
     *
     * @param {object} cell cell of the card
     */
    readCardFacts(cell) {
        if (!cell.session || cell.session.status !== 'running' || cell.session.facts === 'no') {
            return;
        }
        const key = this.key(cell.cmid, cell.userid);
        const fact = this.facts.get(key);
        if (!fact || (!fact.busy && !fact.nofacts)) {
            this.enqueue(cell);
        }
    }

    /**
     * Reads the facts of the open card again after a while.
     */
    pollCard() {
        clearTimeout(this.cardTimer);
        if (!this.open || document.hidden) {
            return;
        }
        const cell = this.cells.get(this.key(this.open.cmid, this.open.userid));
        const fact = cell ? this.factOf(cell.cmid, cell.userid) : null;
        this.cardTimer = setTimeout(() => {
            const current = this.open && this.cells.get(this.key(this.open.cmid, this.open.userid));
            if (current) {
                this.readCardFacts(current);
            }
            this.pollCard();
        }, fact && fact.live ? CARD_POLL_L1 : CARD_POLL);
    }

    /**
     * Facts of a cell changed: redraw the card if it shows that cell.
     *
     * @param {object} cell cell of the table
     */
    cardChanged(cell) {
        if (this.open && this.open.cmid === cell.cmid && this.open.userid === cell.userid) {
            this.renderCard();
        }
    }

    /**
     * Draws the card of the open student and lab.
     */
    renderCard() {
        if (!this.open) {
            return;
        }
        const {cmid, userid} = this.open;
        const cell = this.cells.get(this.key(cmid, userid));
        const lab = this.labs.find(l => l.cmid === cmid);
        const student = this.students.find(s => s.userid === userid);
        if (!cell || !cell.observable || !lab || !student) {
            return;
        }
        // Built aside: unchanged content leaves the card (and the focus in it) alone.
        const el = document.createElement('div');
        const session = cell.session;
        const status = session ? session.status : 'none';
        const head = hel('header', 'chead', el);
        const back = hel('button', 'cback', head);
        back.type = 'button';
        back.textContent = '←';
        back.title = this.str('cl_card_back');
        back.setAttribute('aria-label', this.str('cl_card_back'));
        back.addEventListener('click', () => this.close());
        const title = hel('div', 'ctitle', head);
        hel('h2', '', title).textContent = student.fullname;
        hel('p', 'sub', title).textContent = lab.name;
        const closer = hel('button', 'cclose', head);
        closer.type = 'button';
        closer.textContent = '×';
        closer.title = this.str('cl_card_close');
        closer.setAttribute('aria-label', this.str('cl_card_close'));
        closer.addEventListener('click', () => this.close());

        const state = hel('p', 'cstate', el);
        state.dataset.status = status;
        hel('b', '', state).textContent = this.stateText(status);
        const time = this.timeText(session);
        if (time) {
            hel('span', '', state).textContent = ' · ' + time;
        }
        if (session && session.startedat) {
            hel('span', '', state).textContent = ' · ' + this.clock(session.startedat * 1000);
        }
        const marked = this.showgrades && cell.checks && cell.checks.mark >= 0;
        if (marked) {
            const line = hel('p', 'cgrade', el);
            line.dataset.source = 'checks';
            hel('span', 'lbl', line).textContent = this.str('cl_card_checkmark') + ': ';
            const result = resultOf({...cell, grade: null});
            if (result.passed) {
                line.dataset.pass = result.passed;
            }
            this.gradeText(line, result, false);
            hel('span', 'sub', line).textContent = ' · ' + this.str('cl_tries_title', cell.checks.attempts);
        }
        if (this.showgrades && lab.checks && session) {
            this.renderCardForecast(el, cell.forecast);
        }
        if (this.showgrades && lab.gradetype !== 'none') {
            const grade = hel('p', 'cgrade', el);
            hel('span', 'lbl', grade).textContent = this.str('cl_card_grade') + ': ';
            if (cell.grade) {
                if (cell.grade.passed) {
                    grade.dataset.pass = cell.grade.passed;
                }
                this.gradeText(grade, cell.grade, false);
                if (lab.grademax) {
                    hel('span', 'sub', grade).textContent = ' ' + this.str('cl_card_outof', lab.grademax);
                }
                if (cell.grade.overridden) {
                    hel('span', 'sub', grade).textContent = ' · ' + this.str('cl_overridden');
                }
            } else {
                hel('span', 'sub', grade).textContent = this.str('cl_card_nograde');
            }
            if (lab.gradepass) {
                hel('span', 'sub', grade).textContent = ' · ' + this.str('cl_card_passmark', lab.gradepass);
            }
        }
        // The lab of the student, read only.
        const open = hel('a', 'btn', el);
        open.href = `${this.labbase}?id=${cmid}&userid=${userid}&from=course`;
        open.textContent = this.str('cl_card_open');
        open.hidden = !session;

        if (!session) {
            hel('p', 'cnote', el).textContent = this.str('cl_card_notstarted');
        } else if (status === 'running') {
            this.renderCardFacts(el, cell);
        }
        this.renderHistory(el, cell);
        if (el.innerHTML !== this.cardHtml) {
            this.cardHtml = el.innerHTML;
            const box = this.region('card');
            // A run opened from the keyboard keeps the focus on its line.
            const focused = box.contains(document.activeElement) && document.activeElement.dataset
                ? document.activeElement.dataset.runid : '';
            box.replaceChildren(...el.childNodes);
            const again = focused ? [...box.querySelectorAll('.runbtn')].find(b => b.dataset.runid === focused) : null;
            if (again) {
                again.focus();
            }
        }
    }

    /**
     * Findings and devices of a running session.
     *
     * @param {HTMLElement} el card
     * @param {object} cell cell of the card
     */
    renderCardFacts(el, cell) {
        const fact = this.factOf(cell.cmid, cell.userid);
        if (cell.session.facts === 'no' || (fact && fact.nofacts)) {
            hel('p', 'cnote', el).textContent = this.str('cl_card_nofacts');
            return;
        }
        if (!fact || !fact.model) {
            hel('p', 'cnote', el).textContent = this.str('cl_loading');
            return;
        }
        const data = card(fact.model, fact.doc, fact.statuses);
        if (!data.known || (fact.summary && fact.summary.level === 'stale')) {
            hel('p', 'cnote unknown', el).textContent = this.str('cl_factsunknown');
        }
        if (fact.track) {
            hel('p', 'cidle', el).textContent = this.idleText(fact.track);
        }
        const finds = hel('section', 'cfinds', el);
        hel('h3', '', finds).textContent = this.str('cl_card_findings');
        let any = false;
        if (data.known) {
            for (const layer of data.layers) {
                if (!layer.issues.length) {
                    continue;
                }
                any = true;
                hel('h4', '', finds).textContent = this.str('cl_layer_' + layer.id);
                const ul = hel('ul', 'issues', finds);
                for (const issue of layer.issues) {
                    const li = hel('li', issue.sev, ul);
                    li.textContent = issue.text;
                }
            }
            if (!any) {
                hel('p', 'cnote', finds).textContent = this.str('cl_card_nofindings');
            }
        }
        this.renderDevices(el, data);
    }

    /**
     * The table of devices of the card.
     *
     * @param {HTMLElement} el card
     * @param {object} data result of coursefacts.card
     */
    renderDevices(el, data) {
        const section = hel('section', 'cdevs', el);
        hel('h3', '', section).textContent = this.str('cl_card_devices');
        const wrap = hel('div', 'devbox', section);
        const table = hel('table', 'devs', wrap);
        const tr = hel('tr', '', hel('thead', '', table));
        for (const key of ['cl_dev_name', 'cl_dev_state', 'cl_dev_ifs', 'cl_dev_ip']) {
            hel('th', '', tr).textContent = this.str(key);
        }
        const body = hel('tbody', '', table);
        for (const device of data.devices) {
            const row = hel('tr', '', body);
            hel('th', '', row).textContent = device.name;
            const state = hel('td', 'dev-' + device.state, row);
            state.textContent = this.str('cl_dev_' + (device.state === 'on' ? 'on' : (device.state === 'off' ? 'off' : 'unknown')));
            hel('td', '', row).textContent = device.total === null ? '—' : `${device.up} / ${device.total}`;
            const addresses = hel('td', 'mono', row);
            addresses.textContent = device.addresses.length
                ? device.addresses.map(a => `${a.iface} ${a.address}`).join('\n') : '—';
        }
    }

    /**
     * Reads the runs of the checks of the open card: when it opens and when the table shows a new run.
     *
     * @param {object|undefined} cell cell of the card
     */
    async wantHistory(cell) {
        if (!cell || !cell.observable) {
            return;
        }
        const key = this.key(cell.cmid, cell.userid);
        const sign = cell.checks ? `${cell.checks.laststatus}|${cell.checks.lastat}|${cell.checks.attempts}` : '';
        if (this.history && this.history.key === key && this.history.sign === sign) {
            return;
        }
        const entry = {key, sign, data: this.history && this.history.key === key ? this.history.data : null, error: ''};
        this.history = entry;
        try {
            const [data] = await Promise.all(Ajax.call([{
                methodname: 'mod_gns3lab_get_checks',
                args: {cmid: cell.cmid, userid: cell.userid},
            }]));
            entry.data = data;
            entry.error = '';
        } catch (error) {
            entry.error = error && error.message ? error.message : String(error);
            // The next answer of the table tries again.
            entry.sign = '';
        }
        if (this.history === entry) {
            this.renderCard();
        }
    }

    /**
     * The history of the checks of the card: a line per run, the newest first.
     *
     * @param {HTMLElement} el card
     * @param {object} cell cell of the card
     */
    renderHistory(el, cell) {
        const section = hel('section', 'chistory', el);
        section.dataset.region = 'history';
        hel('h3', '', section).textContent = this.str('cl_history');
        const entry = this.history && this.history.key === this.key(cell.cmid, cell.userid) ? this.history : null;
        const note = text => {
            hel('p', 'cnote', section).textContent = text;
        };
        if (!entry || (!entry.data && !entry.error)) {
            note(this.str('cl_loading'));
            return;
        }
        if (!entry.data) {
            note(this.str('cl_history_failed', entry.error));
            return;
        }
        if (!entry.data.enabled && !entry.data.runs.length) {
            note(this.str('cl_history_off'));
            return;
        }
        if (!entry.data.runs.length) {
            note(this.str('cl_history_none'));
            return;
        }
        const list = hel('ul', 'historylist', section);
        list.dataset.region = 'historylist';
        for (const run of entry.data.runs) {
            const li = hel('li', '', list);
            let result;
            if (RUN_PENDING.includes(run.status)) {
                result = this.str('chk_hist_pending');
                li.dataset.result = 'pending';
            } else if (run.status === 'failed') {
                result = this.str('chk_hist_failed');
                li.dataset.result = 'failed';
            } else if (run.outcome === 'graded' && run.mark >= 0) {
                result = this.str('chk_hist_mark', run.mark);
                li.dataset.result = 'graded';
            } else {
                result = this.str('chk_hist_incomplete');
                li.dataset.result = 'incomplete';
            }
            // A finished run opens its report: the findings by class, where the student stumbled, or that nothing was found.
            const done = run.status === 'done' && !!run.runid;
            const open = done && this.runOpen && this.runOpen.key === this.key(cell.cmid, cell.userid)
                && this.runOpen.runid === run.runid;
            let line = li;
            if (done) {
                line = hel('button', 'runbtn', li);
                line.type = 'button';
                line.dataset.runid = run.runid;
                line.title = this.str('cl_run_toggle');
                line.setAttribute('aria-expanded', open ? 'true' : 'false');
                line.addEventListener('click', () => this.toggleRun(cell, run.runid));
            }
            hel('span', 'when', line).textContent = this.when((run.finishedat || run.requestedat) * 1000);
            hel('b', 'what', line).textContent = result;
            if (run.status === 'done') {
                const counts = CLASSES.filter(k => run.counts[k]).map(k => this.str('chk_n_' + k, run.counts[k]));
                if (counts.length) {
                    hel('span', 'counts', line).textContent = counts.join(', ');
                }
            }
            if (open) {
                li.dataset.open = 'true';
                this.renderRun(hel('div', 'rundetails', li), cell, run.runid);
            }
        }
    }

    /**
     * Opens or closes the report of a run in the history of the card; the first opening reads it.
     *
     * @param {object} cell cell of the card
     * @param {string} runid run
     */
    async toggleRun(cell, runid) {
        const key = this.key(cell.cmid, cell.userid);
        if (this.runOpen && this.runOpen.key === key && this.runOpen.runid === runid) {
            this.runOpen = null;
            this.renderCard();
            return;
        }
        this.runOpen = {key, runid};
        const id = `${key}:${runid}`;
        const known = this.runs.get(id);
        if (known && (known.data || known.loading)) {
            this.renderCard();
            this.showRun(runid);
            return;
        }
        // A finished run does not change: it is read once.
        const entry = {loading: true, data: null, error: ''};
        this.runs.set(id, entry);
        this.renderCard();
        try {
            const [data] = await Promise.all(Ajax.call([{
                methodname: 'mod_gns3lab_get_check_run',
                args: {cmid: cell.cmid, runid, userid: cell.userid},
            }]));
            entry.data = data;
        } catch (error) {
            entry.error = error && error.message ? error.message : String(error);
        }
        entry.loading = false;
        this.renderCard();
        this.showRun(runid);
        if (!entry.data && this.runs.get(id) === entry) {
            // The next opening tries again.
            this.runs.delete(id);
        }
    }

    /**
     * Scrolls the card so that the opened report of a run is in sight.
     *
     * @param {string} runid run
     */
    showRun(runid) {
        const button = [...this.region('card').querySelectorAll('.runbtn')].find(b => b.dataset.runid === runid);
        const details = button ? button.parentElement.querySelector('.rundetails') : null;
        if (details && details.scrollIntoView) {
            details.scrollIntoView({block: 'nearest'});
        }
    }

    /**
     * The report of a run: the findings that count first (critical, major, minor), each root with its consequences,
     * then the forgiven ones; or that nothing was found, or why the findings are not there.
     *
     * @param {HTMLElement} box where it goes
     * @param {object} cell cell of the card
     * @param {string} runid run
     */
    renderRun(box, cell, runid) {
        const entry = this.runs.get(`${this.key(cell.cmid, cell.userid)}:${runid}`);
        const note = (text, cls = 'cnote') => {
            hel('p', cls, box).textContent = text;
        };
        if (!entry || entry.loading) {
            note(this.str('cl_loading'));
            return;
        }
        if (!entry.data) {
            note(this.str('cl_run_failed', entry.error));
            return;
        }
        const run = entry.data;
        if (!run.detailed) {
            note(run.gone ? this.str('chk_details_gone') : this.str('chk_details_counts'));
            return;
        }
        const groups = groupFindings(run.findings);
        if (!groups.length) {
            note(this.str('chk_nofindings'), 'cnote runok');
        } else {
            const list = hel('ol', 'findings', box);
            for (const {finding, consequences} of groups) {
                const item = hel('li', 'finding sev-' + finding.class + (finding.counted ? ' counted' : ''), list);
                this.findingLine(item, finding);
                if (consequences.length) {
                    const sub = hel('ul', 'consequences', item);
                    for (const c of consequences) {
                        const li = hel('li', 'finding consequence sev-' + c.class, sub);
                        hel('span', 'tag', li).textContent = this.str('chk_consequence');
                        this.findingLine(li, c);
                    }
                }
            }
        }
        const gaps = DATA_GAPS.map(reason => [reason, (run.rules || []).filter(r => r.reason === reason).length])
            .filter(([, n]) => n > 0);
        if (gaps.length) {
            note(this.str('chk_gaps'));
            const ul = hel('ul', 'gaps', box);
            for (const [reason, n] of gaps) {
                hel('li', '', ul).textContent = this.str('chk_gap_' + reason, n);
            }
        }
    }

    /**
     * One finding of a report: its class and what is wrong; a forgiven one says so.
     *
     * @param {HTMLElement} parent where it goes
     * @param {object} f finding
     */
    findingLine(parent, f) {
        hel('span', 'sev', parent).textContent = this.str('chk_class_' + f.class);
        hel('span', 'msg', parent).textContent = f.message || f.code;
        if (isForgiven(f)) {
            hel('small', 'forgiven', parent).textContent = this.str('chk_forgiven');
        }
    }
}

/**
 * Starts the course page.
 *
 * @param {string} prefix id prefix of the template ({{uniqid}})
 * @param {number} courseid course id
 * @param {number} [labid] the lab to show first, 0 for the summary
 */
export const init = async(prefix, courseid, labid = 0) => {
    const root = document.getElementById(prefix + '-app');
    if (!root || root.dataset.courseReady) {
        return;
    }
    root.dataset.courseReady = '1';
    const values = await getStrings(STRING_KEYS.map(key => ({key, component: 'mod_gns3lab'})));
    const strings = Object.fromEntries(STRING_KEYS.map((key, i) => [key, values[i]]));
    new CoursePage(root, courseid, strings, labid);
};
