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
 * Lab session control: start and stop buttons, status polling, and the link between the viewer and the consoles.
 *
 * Talks to the viewer through events on its root element:
 * - gns3lab:session (sent here) — the session changed, detail {session, readonly};
 * - gns3lab:openconsole (from the viewer) — open the console of a node, detail {nodeId, name, consoleType, nodeType, ports}.
 *
 * Permissions are decided by the server; the flags here only choose what to draw.
 *
 * @module     mod_gns3lab/lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';
import ModalEvents from 'core/modal_events';
import {saveCancel, saveCancelPromise} from 'core/notification';
import {getStrings} from 'core/str';
import {ConsolePanel} from 'mod_gns3lab/console';

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

const STRING_KEYS = [
    'status_none', 'status_queued', 'status_starting', 'status_running', 'status_stopping', 'status_stopped',
    'status_failed', 'timeleft', 'startlab', 'startagain', 'stoplab', 'consoleneedsrunning', 'consolevnclater',
    'consolenone', 'managernotconfigured', 'labnotready', 'failedretry', 'failedadmin', 'stopconfirmtitle',
    'stopconfirm', 'conflictlink', 'stopsave', 'stopsavecheck', 'savingcisco', 'savefailed',
    ...STAGES.map(stage => 'stage_' + stage),
];

/** Node types that run Cisco IOS: their configuration lives in memory until "write memory". */
const CISCO_TYPES = ['iou', 'dynamips'];

/** Saves the configuration from any IOS mode: clear the line, leave configuration, enable, write. */
const IOS_SAVE = '\x15\x1a\renable\rwrite memory\r';

/**
 * Controller of one lab on the page.
 */
class Lab {
    /**
     * Constructor.
     *
     * @param {HTMLElement} app viewer root
     * @param {number} cmid course module id
     * @param {number} userid whose lab, 0 for the current user
     * @param {object} strings language strings
     */
    constructor(app, cmid, userid, strings) {
        this.app = app;
        this.cmid = cmid;
        this.userid = userid;
        this.strings = strings;
        this.state = null;
        this.timer = null;
        this.busy = false;
        const region = name => app.querySelector(`[data-region="${name}"]`);
        this.statusEl = region('status');
        this.timeEl = region('timeleft');
        this.startBtn = region('start');
        this.stopBtn = region('stop');
        this.messageEl = region('message');
        this.consoles = new ConsolePanel({
            root: region('consoles'),
            app,
            cmid,
            userid,
            readonly: userid !== 0,
            nodeStatus: nodeId => this.freshNodeStatus(nodeId),
        });

        this.startBtn.addEventListener('click', () => this.act('mod_gns3lab_start_session'));
        this.stopBtn.addEventListener('click', () => this.stop());
        app.addEventListener('gns3lab:openconsole', event => this.openConsole(event.detail));
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
     *
     * When it does, the disks of the devices are kept, not their memory: a Cisco device keeps only the
     * configuration saved with "write memory". The student is offered to save it on every Cisco device first.
     */
    async stop() {
        if (!this.state.resume) {
            try {
                await saveCancelPromise(this.strings.stopconfirmtitle, this.strings.stopconfirm, this.strings.stoplab);
            } catch (cancelled) {
                return;
            }
        } else {
            const cisco = this.ciscoNodes();
            if (cisco.length) {
                const save = await this.askSave();
                if (save === null) {
                    return;
                }
                if (save && !(await this.saveCisco(cisco))) {
                    return;
                }
            }
        }
        await this.act('mod_gns3lab_stop_session');
    }

    /**
     * Running Cisco devices of the session.
     *
     * @returns {Array} nodes {nodeId, name, nodeType}
     */
    ciscoNodes() {
        const session = this.session;
        if (!session || session.status !== 'running' || !session.topology) {
            return [];
        }
        let doc;
        try {
            doc = JSON.parse(session.topology);
        } catch (error) {
            return [];
        }
        const started = new Set(session.nodes.filter(n => n.status === 'started').map(n => n.nodeid));
        return ((doc.topology && doc.topology.nodes) || [])
            .filter(n => CISCO_TYPES.includes(n.node_type) && started.has(n.node_id))
            .map(n => ({nodeId: n.node_id, name: n.name || n.node_id, nodeType: n.node_type}));
    }

    /**
     * Asks whether to stop and whether to save the Cisco configuration first.
     *
     * @returns {Promise<boolean|null>} save first, or null when the student cancelled
     */
    askSave() {
        return new Promise(resolve => {
            let answered = false;
            let modalRef = null;
            const body = `<p>${this.strings.stopsave}</p><div class="form-check">` +
                '<input class="form-check-input" type="checkbox" id="gns3lab-savecisco" data-region="savecisco" checked>' +
                `<label class="form-check-label" for="gns3lab-savecisco">${this.strings.stopsavecheck}</label></div>`;
            saveCancel(this.strings.stopconfirmtitle, body, this.strings.stoplab, () => {
                answered = true;
                const box = modalRef && modalRef.getRoot()[0].querySelector('[data-region="savecisco"]');
                resolve(!!box && box.checked);
            }, () => {
                answered = true;
                resolve(null);
            }).then(modal => {
                modalRef = modal;
                modal.getRoot().on(ModalEvents.hidden, () => {
                    if (!answered) {
                        resolve(null);
                    }
                });
                return modal;
            }).catch(() => resolve(null));
        });
    }

    /**
     * Saves the configuration on the Cisco devices through their consoles.
     *
     * @param {Array} nodes Cisco devices
     * @returns {Promise<boolean>} go on with stopping
     */
    async saveCisco(nodes) {
        this.busy = true;
        this.render();
        this.showMessage(this.strings.savingcisco);
        let results;
        try {
            results = await this.consoles.runAll(nodes, IOS_SAVE, /\[OK\]/);
        } finally {
            this.busy = false;
            this.render();
        }
        this.showMessage('');
        const failed = results.filter(r => !r.ok).map(r => r.name);
        if (!failed.length) {
            return true;
        }
        try {
            await saveCancelPromise(this.strings.stopconfirmtitle, this.strings.savefailed.replace('{$a}', failed.join(', ')),
                this.strings.stoplab);
            return true;
        } catch (cancelled) {
            return false;
        }
    }

    /**
     * Takes a new state: redraws, tells the viewer and the consoles, schedules polling.
     *
     * @param {object} state result of get_state (or a state with a fresh session)
     */
    apply(state) {
        const before = this.session;
        this.state = state;
        const session = this.session;
        const running = session && session.status === 'running';
        if (!running || (before && before.sessionid !== session.sessionid)) {
            this.consoles.closeAll();
        }
        if (running) {
            this.consoles.updateNodes(new Map(session.nodes.map(n => [n.nodeid, n.status])));
        }
        this.app.dispatchEvent(new CustomEvent('gns3lab:session', {detail: {session, readonly: state.readonly}}));
        this.render();
        clearTimeout(this.timer);
        if (session && TRANSIENT.includes(session.status)) {
            this.timer = setTimeout(() => this.refresh(), POLL_TRANSIENT);
        } else if (running) {
            this.timer = setTimeout(() => this.refresh(), POLL_RUNNING);
        }
    }

    /**
     * Draws the status bar.
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
        this.app.dataset.session = status;
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
     * Opens a console if the session allows it.
     *
     * @param {object} node detail of gns3lab:openconsole
     */
    openConsole(node) {
        const session = this.session;
        if (!session || session.status !== 'running') {
            this.showMessage(this.strings.consoleneedsrunning);
            return;
        }
        const live = session.nodes.find(n => n.nodeid === node.nodeId);
        const type = (live && live.consoletype) || node.consoleType;
        if (type === 'vnc') {
            this.showMessage(this.strings.consolevnclater);
            return;
        }
        if (type === 'none') {
            this.showMessage(this.strings.consolenone);
            return;
        }
        this.showMessage('');
        this.consoles.open({nodeId: node.nodeId, name: node.name, nodeType: node.nodeType, ports: node.ports || []});
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

/**
 * Starts the controller for a viewer on the page.
 *
 * @param {string} prefix id prefix of the template
 * @param {number} cmid course module id
 * @param {number} userid whose lab, 0 for the current user
 */
export const init = async(prefix, cmid, userid = 0) => {
    const app = document.getElementById(prefix + '-app');
    if (!app || app.dataset.labReady) {
        return;
    }
    app.dataset.labReady = '1';
    const values = await getStrings(STRING_KEYS.map(key => ({key, component: 'mod_gns3lab'})));
    const strings = Object.fromEntries(STRING_KEYS.map((key, i) => [key, values[i]]));
    const lab = new Lab(app, cmid, userid, strings);
    lab.render();
    lab.refresh();
};
