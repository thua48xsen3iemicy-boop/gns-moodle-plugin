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
 * Lab session control: start and stop buttons, status line, polling of the state.
 *
 * Permissions are decided by the server; the flags here only choose what to draw.
 *
 * @module     mod_gns3lab/session
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';
import {saveCancelPromise} from 'core/notification';

/** Statuses that change by themselves: poll often. */
const TRANSIENT = ['queued', 'starting', 'stopping'];

/** Poll interval while the session changes, ms (SPEC §3). */
const POLL_TRANSIENT = 3000;

/** Poll interval of a running lab, for node states and the time left, ms. */
const POLL_RUNNING = 15000;

/** Manager steps with an own text (CONTRACTS §2.3); an unknown step shows the text of its status. */
const STAGES = [
    'wait_slot', 'ct_prepare', 'student_dir', 'ct_start', 'slot_ready', 'lab_submit', 'lab_wait', 'ready',
    'lab_stop', 'lab_stop_wait', 'ct_stop', 'ct_park',
];

/** Language strings of the session bar. */
export const STRING_KEYS = [
    'status_none', 'status_queued', 'status_starting', 'status_running', 'status_stopping', 'status_stopped',
    'status_failed', 'timeleft', 'startlab', 'startagain', 'stoplab', 'managernotconfigured', 'labnotready',
    'failedretry', 'failedadmin', 'stopconfirmtitle', 'stopconfirm', 'conflictlink',
    ...STAGES.map(stage => 'stage_' + stage),
];

export class Session {
    /**
     * Constructor.
     *
     * @param {HTMLElement} root the application element
     * @param {object} options
     * @param {number} options.cmid course module id
     * @param {number} options.userid whose lab, 0 for the current user
     * @param {object} options.strings language strings
     * @param {Function} options.onState called with (state, before) after every state from the server
     */
    constructor(root, {cmid, userid, strings, onState}) {
        this.root = root;
        this.cmid = cmid;
        this.userid = userid;
        this.strings = strings;
        this.onState = onState;
        this.state = null;
        this.timer = null;
        this.busy = false;
        const region = name => root.querySelector(`[data-region="${name}"]`);
        this.statusEl = region('status');
        this.timeEl = region('timeleft');
        this.startBtn = region('start');
        this.stopBtn = region('stop');
        this.messageEl = region('message');
        this.startBtn.addEventListener('click', () => this.act('mod_gns3lab_start_session'));
        this.stopBtn.addEventListener('click', () => this.stop());
        setInterval(() => this.renderTime(), 1000);
    }

    /**
     * Current session or null.
     *
     * @returns {object|null}
     */
    get session() {
        return (this.state && this.state.session) || null;
    }

    /**
     * Reads the state from the server and schedules the next poll.
     *
     * @returns {Promise<object|null>} state, null on error
     */
    async refresh() {
        clearTimeout(this.timer);
        try {
            const [state] = await Promise.all(Ajax.call([{
                methodname: 'mod_gns3lab_get_state',
                args: {cmid: this.cmid, userid: this.userid},
            }]));
            this.apply(state);
            return state;
        } catch (error) {
            this.showMessage(error.message || String(error));
            this.timer = setTimeout(() => this.refresh(), POLL_RUNNING);
            return null;
        }
    }

    /**
     * Starts or stops the lab.
     *
     * @param {string} methodname external function
     */
    async act(methodname) {
        if (this.busy) {
            return;
        }
        this.busy = true;
        this.render();
        try {
            const [session] = await Promise.all(Ajax.call([{methodname, args: {cmid: this.cmid}}]));
            this.showMessage('');
            this.apply({...this.state, session});
        } catch (error) {
            // A conflict names the activity with the other running lab and links to it.
            const link = error.errorcode === 'sessionconflictat' ? error.link : null;
            this.showMessage(error.message || String(error), link);
        } finally {
            this.busy = false;
            this.render();
        }
    }

    /**
     * Stops the lab, asking first when the manager does not keep the work.
     */
    async stop() {
        if (!this.state.resume) {
            try {
                await saveCancelPromise(this.strings.stopconfirmtitle, this.strings.stopconfirm, this.strings.stoplab);
            } catch (cancelled) {
                return;
            }
        }
        await this.act('mod_gns3lab_stop_session');
    }

    /**
     * Takes a new state: redraws, tells the interface, schedules polling.
     *
     * @param {object} state result of get_state (or a state with a fresh session)
     */
    apply(state) {
        const before = this.session;
        this.state = state;
        const session = this.session;
        this.root.dataset.session = session ? session.status : 'none';
        this.onState(state, before);
        this.render();
        clearTimeout(this.timer);
        if (session && TRANSIENT.includes(session.status)) {
            this.timer = setTimeout(() => this.refresh(), POLL_TRANSIENT);
        } else if (session && session.status === 'running') {
            this.timer = setTimeout(() => this.refresh(), POLL_RUNNING);
        }
    }

    /**
     * Draws the status line and the buttons.
     */
    render() {
        if (!this.state) {
            // Nothing is known before the first answer: draw nothing rather than a wrong status.
            this.startBtn.hidden = true;
            this.stopBtn.hidden = true;
            return;
        }
        const state = this.state;
        const session = this.session;
        const status = session ? session.status : 'none';
        let text = this.strings['status_' + status] || status;
        if (session) {
            text = this.sessionText(session) || text;
        } else if (!state.managerconfigured) {
            text = this.strings.managernotconfigured;
        } else if (!state.labready) {
            text = this.strings.labnotready;
        }
        this.statusEl.textContent = text;
        this.statusEl.dataset.status = status;
        // The manager's own text (in Russian) stays available as a detail.
        this.statusEl.title = (session && session.statusmessage) || '';

        const live = session && !['stopped', 'failed'].includes(status);
        const retry = status === 'failed' && session.errorretryable;
        this.startBtn.textContent = retry ? this.strings.startagain : this.strings.startlab;
        this.startBtn.hidden = !state.canuse || !!live;
        this.stopBtn.hidden = !state.canuse || !live || status === 'stopping';
        this.startBtn.disabled = this.busy || !state.labready;
        this.stopBtn.disabled = this.busy;
        this.renderTime();
    }

    /**
     * Text of a session in the site language: its step and progress, or why it failed.
     *
     * @param {object} session session
     * @returns {string} empty if the session says nothing more than its status
     */
    sessionText(session) {
        if (session.status === 'failed' && session.errorcode) {
            return session.errorretryable
                ? this.strings.failedretry
                : this.strings.failedadmin.replace('{$a}', session.errorcode);
        }
        if (!TRANSIENT.includes(session.status) || !STAGES.includes(session.stage)) {
            return '';
        }
        const text = this.strings['stage_' + session.stage];
        return session.progress >= 0 ? `${text} ${session.progress}%` : text;
    }

    /**
     * Time left before the manager stops the lab.
     */
    renderTime() {
        const session = this.session;
        if (!session || session.status !== 'running' || !session.expiresat) {
            this.timeEl.hidden = true;
            return;
        }
        const left = Math.max(0, session.expiresat - Math.floor(Date.now() / 1000));
        const h = Math.floor(left / 3600);
        const m = String(Math.floor(left / 60) % 60).padStart(2, '0');
        const s = String(left % 60).padStart(2, '0');
        this.timeEl.textContent = this.strings.timeleft.replace('{$a}', h ? `${h}:${m}:${s}` : `${m}:${s}`);
        this.timeEl.hidden = false;
    }

    /**
     * Shows or clears an error message.
     *
     * @param {string} text message, empty to clear
     * @param {string|null} link URL of a page that helps, shown after the message
     */
    showMessage(text, link = null) {
        this.messageEl.textContent = text;
        if (text && link) {
            const a = document.createElement('a');
            a.href = link;
            a.textContent = this.strings.conflictlink;
            this.messageEl.append(' ', a);
        }
        this.messageEl.hidden = !text;
    }

    /**
     * Node status from a fresh state, asked by a console that the gateway closed.
     *
     * @param {string} nodeId node id
     * @returns {Promise<{status: string|null, stale: boolean}>} stale: the manager has only an old snapshot
     */
    async freshNodeStatus(nodeId) {
        const state = await this.refresh();
        const session = state && state.session;
        if (!session || session.status !== 'running') {
            return {status: null, stale: false};
        }
        const node = session.nodes.find(n => n.nodeid === nodeId);
        return {status: node ? node.status : null, stale: session.nodesstale};
    }
}
