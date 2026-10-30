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
 * The course page of a teacher: students by labs, the live state of their sessions and the card of a student in a lab
 * (docs/teacher-dashboard.md).
 *
 * The table polls mod_gns3lab_get_course_overview every 15 s while the page is visible. The facts of a running session are
 * read by the calls the lab page uses (get_state once per session, get_facts with sincerev every minute, at most four
 * requests at a time); the figures are computed by coursefacts.js. The open card reads its own facts like the lab page.
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

/** Language strings of the page. */
const STRING_KEYS = [
    'cl_allgroups', 'cl_back', 'cl_bad', 'cl_card_back', 'cl_card_close', 'cl_card_devices', 'cl_card_findings',
    'cl_card_nofacts', 'cl_card_nofindings', 'cl_card_notstarted', 'cl_card_open', 'cl_counts', 'cl_dev_ifs', 'cl_dev_ip',
    'cl_dev_name', 'cl_dev_off', 'cl_dev_on', 'cl_dev_state', 'cl_dev_unknown', 'cl_elapsed', 'cl_factsok',
    'cl_factsunknown', 'cl_history', 'cl_history_soon', 'cl_idlemin', 'cl_idlesince', 'cl_labfailed', 'cl_layer_l1',
    'cl_layer_l2', 'cl_layer_l3', 'cl_layer_rt', 'cl_left', 'cl_loadfailed', 'cl_loading', 'cl_nostudents', 'cl_sortname',
    'cl_sortstate', 'cl_state_failed', 'cl_state_none', 'cl_state_queued', 'cl_state_running', 'cl_state_starting',
    'cl_state_stopped', 'cl_state_stopping', 'cl_student', 'cl_unobservable', 'cl_updated', 'cl_warn',
];

/** The table is read this often while the page is visible, ms. */
const OVERVIEW_POLL = 15000;
/** Facts of a running session in the table, ms. */
const FACTS_POLL = 60000;
/** Facts of the open card, ms; with live L1 they change faster (as on the lab page). */
const CARD_POLL = 30000;
const CARD_POLL_L1 = 3000;
/** Requests to get_state and get_facts at the same time. */
const MAX_REQUESTS = 4;

/** Statuses of a session whose lab is on the way up or down: shown as they are, no facts. */
const LIVE_STATUSES = ['queued', 'starting', 'running', 'stopping'];

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
 * The course page.
 */
class CoursePage {
    /**
     * Constructor.
     *
     * @param {HTMLElement} root the application element
     * @param {number} courseid course id
     * @param {object} strings language strings
     */
    constructor(root, courseid, strings) {
        this.root = root;
        this.courseid = courseid;
        this.strings = strings;
        this.labbase = root.dataset.labbase;
        const region = name => root.querySelector(`[data-region="${name}"]`);
        this.region = region;
        this.groupId = 0;
        this.search = '';
        // Sorting: by the name of the student, or by the state in a lab (cmid).
        this.sort = {by: 'name', dir: 1};
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
        try {
            const [result] = await Promise.all(Ajax.call([{
                methodname: 'mod_gns3lab_get_course_overview',
                args: {courseid: this.courseid, groupid: groupId},
            }]));
            if (groupId === this.groupId) {
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
        this.labs = result.labs;
        this.students = result.students;
        this.cells = new Map(result.cells.map(c => [this.key(c.cmid, c.userid), c]));
        this.region('updated').textContent = this.str('cl_updated', this.clock(Date.now()));
        this.followHash(true);
        this.draw();
        this.wantFacts();
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
     * Cells with a running session that offers facts: the figures of the table come from them.
     *
     * @returns {object[]} cells
     */
    factCells() {
        return [...this.cells.values()].filter(c => c.observable && c.session && c.session.status === 'running'
            && c.session.facts !== 'no');
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
        let rows = this.students.filter(s => !this.search || s.fullname.toLocaleLowerCase().includes(this.search));
        const by = this.sort.by;
        const dir = this.sort.dir;
        rows = [...rows].sort((a, b) => {
            if (by !== 'name') {
                const ra = rank(this.cells.get(this.key(by, a.userid)), this.factOf(by, a.userid));
                const rb = rank(this.cells.get(this.key(by, b.userid)), this.factOf(by, b.userid));
                if (ra !== rb) {
                    return (ra - rb) * dir;
                }
            }
            return a.fullname.localeCompare(b.fullname, undefined, {numeric: true}) * (by === 'name' ? dir : 1);
        });
        return rows;
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
            this.drawTable(table, rows);
        }
        const html = table.innerHTML;
        if (html !== this.tableHtml) {
            this.tableHtml = html;
            this.region('table').replaceChildren(...table.childNodes);
        }
    }

    /**
     * Fills the table: the header with a button per lab and a row per student.
     *
     * @param {HTMLTableElement} table the table
     * @param {object[]} rows students shown
     */
    drawTable(table, rows) {
        const head = hel('tr', '', hel('thead', '', table));
        const corner = hel('th', 'stud', head);
        corner.scope = 'col';
        this.sortButton(corner, this.str('cl_student'), 'name', this.str('cl_sortname'));
        for (const lab of this.labs) {
            const th = hel('th', 'lab', head);
            th.scope = 'col';
            this.sortButton(th, lab.name, lab.cmid, this.str('cl_sortstate'));
            if (lab.error) {
                const warn = hel('span', 'laberr', th);
                warn.textContent = '!';
                warn.title = this.str('cl_labfailed', lab.error);
            }
        }
        const body = hel('tbody', '', table);
        for (const student of rows) {
            const tr = hel('tr', '', body);
            const th = hel('th', 'stud', tr);
            th.scope = 'row';
            const img = hel('img', 'pic', th);
            img.src = student.pictureurl;
            img.alt = '';
            img.width = 24;
            img.height = 24;
            hel('span', 'name', th).textContent = student.fullname;
            for (const lab of this.labs) {
                const td = hel('td', '', tr);
                this.drawCell(td, lab, student, this.cells.get(this.key(lab.cmid, student.userid)));
            }
        }
    }

    /**
     * A header button that sorts the table.
     *
     * @param {HTMLElement} th header cell
     * @param {string} label text
     * @param {string|number} by sorting key
     * @param {string} title tooltip
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
    }

    /**
     * Draws the cell of a student in a lab.
     *
     * @param {HTMLElement} td table cell
     * @param {object} lab lab of the table
     * @param {object} student student of the table
     * @param {object|undefined} cell cell from the server
     */
    drawCell(td, lab, student, cell) {
        if (!cell || !cell.observable) {
            td.className = 'none';
            td.textContent = '—';
            td.title = this.str('cl_unobservable');
            return;
        }
        const session = cell.session;
        const status = session ? session.status : 'none';
        const button = hel('button', 'cellbtn', td);
        button.type = 'button';
        button.dataset.status = status;
        const fact = this.factOf(cell.cmid, cell.userid);
        const level = status === 'running' && fact && fact.summary ? fact.summary.level : '';
        if (level) {
            button.dataset.level = level;
        }
        if (this.open && this.open.cmid === cell.cmid && this.open.userid === cell.userid) {
            button.setAttribute('aria-current', 'true');
        }
        hel('span', 'state', button).textContent = this.stateText(status);
        const time = this.timeText(session);
        if (time) {
            hel('span', 'time', button).textContent = time;
        }
        if (status === 'running') {
            this.drawFacts(button, fact, session.facts === 'no');
        }
        button.setAttribute('aria-label', `${student.fullname}, ${lab.name}: ${button.textContent}`);
        button.addEventListener('click', () => this.show(cell.cmid, cell.userid, true));
    }

    /**
     * What the facts say in a cell: the findings, how long nothing changed, or that nothing is known.
     *
     * @param {HTMLElement} button cell button
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
     * Counters per lab above the table: running, errors, not started.
     *
     * @param {object[]} rows students shown
     */
    drawCounts(rows) {
        const list = this.region('counts');
        list.replaceChildren();
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
        this.root.classList.add('has-card');
        this.region('card').hidden = false;
        this.readCardFacts(cell);
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
        this.renderHistory(el);
        if (el.innerHTML !== this.cardHtml) {
            this.cardHtml = el.innerHTML;
            this.region('card').replaceChildren(...el.childNodes);
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
     * The place for the history of attempts and checks: the heading and a line now, a list when the checks exist.
     *
     * @param {HTMLElement} el card
     */
    renderHistory(el) {
        const section = hel('section', 'chistory', el);
        section.dataset.region = 'history';
        hel('h3', '', section).textContent = this.str('cl_history');
        const list = hel('ul', 'historylist', section);
        list.dataset.region = 'historylist';
        list.hidden = true;
        hel('p', 'cnote', section).textContent = this.str('cl_history_soon');
    }
}

/**
 * Starts the course page.
 *
 * @param {string} prefix id prefix of the template ({{uniqid}})
 * @param {number} courseid course id
 */
export const init = async(prefix, courseid) => {
    const root = document.getElementById(prefix + '-app');
    if (!root || root.dataset.courseReady) {
        return;
    }
    root.dataset.courseReady = '1';
    const values = await getStrings(STRING_KEYS.map(key => ({key, component: 'mod_gns3lab'})));
    const strings = Object.fromEntries(STRING_KEYS.map((key, i) => [key, values[i]]));
    new CoursePage(root, courseid, strings);
};
