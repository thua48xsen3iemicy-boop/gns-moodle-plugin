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
 * The lab page of a student: connects the session, the scheme, the action wheel, the details card and the consoles.
 *
 * Layout and behaviour come from prototypes/student-ui.html (levels of detail by zoom, an action wheel, consoles as
 * a panel, a grid or windows, diving into a console on a phone). The session is the source of truth: the scheme shows
 * the reference topology until the lab runs and the live topology of the session after that. While the lab runs and its
 * session reports facts, the page polls them (docs/live-state.md) and the scheme builds the layers L2, L3 and OSPF.
 *
 * Permissions are decided by the server; the flags here only choose what to draw.
 *
 * @module     mod_gns3lab/lab
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';
import {getStrings} from 'core/str';
import {STRING_KEYS as CONSOLE_KEYS} from 'mod_gns3lab/console';
import {STRING_KEYS as SNIPPET_KEYS} from 'mod_gns3lab/snippets';
import {Consoles} from 'mod_gns3lab/consoles';
import {Details} from 'mod_gns3lab/details';
import {fill} from 'mod_gns3lab/dom';
import {Session, STRING_KEYS as SESSION_KEYS} from 'mod_gns3lab/session';
import {Stage} from 'mod_gns3lab/stage';
import {STRING_KEYS as VNC_KEYS} from 'mod_gns3lab/vnc';
import {Wheel} from 'mod_gns3lab/wheel';

const CATEGORIES = ['router', 'switch', 'pc', 'server', 'cloud', 'firewall', 'hypervisor'];

/** Poll interval of the facts of a running lab while the page is visible, ms (docs/live-state.md §6). */
const FACTS_POLL = 30000;

/** After "Refresh now" the facts are read again this much later: the collection takes a few seconds, ms. */
const FACTS_AFTER_REFRESH = 5000;

/** Codes of a failed collection with an own text. */
const FACT_ERRORS = ['unreachable', 'timeout', 'stopped', 'unsupported', 'failed'];

/** Language strings of the interface; the texts of the layers and of the details tables are in the modules. */
const UI_KEYS = [
    'ui_lod0', 'ui_lod1', 'ui_lod2', 'ui_lod3', 'ui_st_started', 'ui_st_stopped', 'ui_st_suspended',
    ...CATEGORIES.map(c => 'ui_cat_' + c),
    'ui_chip_touch', 'ui_chip_mouse', 'ui_hint_touch', 'ui_hint_mouse', 'ui_link_aria', 'ui_filter_on', 'ui_port_unlinked',
    'ui_console', 'ui_new', 'ui_link', 'ui_status_nodata', 'ui_open_console', 'ui_console_of', 'ui_close',
    'ui_snippets', 'ui_snippets_for', 'ui_snippet_run', 'ui_snippet_type', 'ui_snip_filter', 'ui_snip_none', 'ui_snip_insert',
    'ui_snip_pin', 'ui_snip_unpin', 'ui_snip_hint_run', 'ui_snip_hint_type', 'ui_snip_keys', 'ui_snip_ios_prep',
    'ui_snip_nopins', 'ui_font', 'ui_font_smaller', 'ui_font_larger', 'ui_font_reset', 'ui_not_connected',
    'ui_term_fail', 'ui_close_console',
    'ui_minimize', 'ui_maximize', 'ui_new_output', 'ui_grid_hint', 'ui_dive_release',
    'ui_act_console', 'ui_act_console_open', 'ui_act_console_goto', 'ui_act_info', 'ui_act_info_full', 'ui_act_zoom',
    'ui_act_zoom_node', 'ui_act_close', 'ui_act_close_full', 'ui_act_free', 'ui_act_free_full',
    'ui_act_free_why', 'ui_act_link_info', 'ui_act_link_zoom', 'ui_why_node_off', 'ui_why_viewer', 'ui_live_bad',
    'consoleneedsrunning', 'consolenone',
    'ui_facts_at', 'ui_facts_ago', 'ui_facts_ago_now', 'ui_facts_stale', 'ui_facts_stale_mark', 'ui_facts_nodata',
    'ui_facts_refresh', 'ui_facts_refresh_sent', 'ui_facts_last', 'ui_facts_never', 'ui_facts_err_other',
    'ui_mac_table', 'ui_mac_col_mac', 'ui_mac_col_port', 'ui_mac_col_vlan', 'ui_mac_empty', 'ui_mac_truncated', 'ui_mac_note',
    ...FACT_ERRORS.map(code => 'ui_facts_err_' + code),
];

/**
 * The lab page.
 */
class LabPage {
    /**
     * Constructor.
     *
     * @param {HTMLElement} root the application element
     * @param {object} options
     * @param {string} options.prefix id prefix of the template
     * @param {number} options.cmid course module id
     * @param {number} options.userid whose lab, 0 for the current user
     * @param {object} options.strings language strings
     */
    constructor(root, {prefix, cmid, userid, strings}) {
        this.root = root;
        this.strings = strings;
        this.data = JSON.parse(root.dataset.json || '{}');
        this.liveKey = null;
        this.cmid = cmid;
        this.userid = userid;
        // Live facts: the session they belong to, the last document and its rev, the poll timer.
        this.factsSession = null;
        this.factsDoc = null;
        this.factsRev = 0;
        this.factsTimer = null;
        this.factsShown = '';
        const region = name => root.querySelector(`[data-region="${name}"]`);
        this.toastEl = region('toast');
        this.toastTimer = null;

        this.stage = new Stage(root, {prefix, strings});
        this.details = new Details(region('details'), {
            stage: this.stage,
            strings,
            host: {
                canConsole: n => this.canConsole(n),
                openConsole: n => this.openConsole(n),
                refreshFacts: () => this.refreshFacts(),
            },
        });
        this.consoles = new Consoles(root, {
            stage: this.stage,
            strings,
            cmid,
            userid,
            readonly: userid !== 0,
            nodeStatus: nodeId => (this.session
                ? this.session.freshNodeStatus(nodeId)
                : Promise.resolve({status: null, stale: false})),
            toast: (text, ms) => this.toast(text, ms),
        });
        this.wheel = new Wheel(region('radial'), {
            stage: this.stage,
            prefix,
            strings,
            host: {
                actionsFor: target => this.actionsFor(target),
                run: (id, target) => this.runAction(id, target),
                toast: (text, ms) => this.toast(text, ms),
            },
        });
        this.hookStage();
        this.bindChrome(region);
        // Facts are polled only while somebody looks at the page.
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                clearTimeout(this.factsTimer);
            } else if (this.factsSession) {
                this.pollFacts();
            }
        });

        // Users who may only look at the lab have no session bar.
        const controllable = !!region('start');
        this.session = controllable ? new Session(root, {
            cmid,
            userid,
            strings,
            onState: (state, before) => this.stateChanged(state, before),
        }) : null;
        this.preview();
        if (this.session) {
            this.session.render();
            this.session.refresh();
        }
    }

    /**
     * Connects the stage with the details card, the wheel and the consoles.
     */
    hookStage() {
        const stage = this.stage, consoles = this.consoles, details = this.details, wheel = this.wheel;
        stage.hooks = {
            details: sel => details.show(sel),
            console: n => this.openConsole(n),
            nodeAction: n => {
                if (this.canConsole(n)) {
                    this.openConsole(n);
                } else {
                    stage.select({kind: 'node', id: n.id}, true);
                    details.show(stage.sel);
                }
            },
            canConsole: n => this.canConsole(n),
            diveCandidate: n => consoles.diveCandidate(n),
            diveRelease: n => consoles.diveRelease(n),
            closeOverlays: () => details.hide(),
            viewChanged: () => {
                if (wheel.node && !wheel.tracking) {
                    wheel.position();
                }
                consoles.drawLinks();
            },
            layerChanged: () => details.refresh(),
            mobileChanged: mobile => {
                consoles.mobileChanged(mobile);
                details.hide();
            },
            toast: text => this.toast(text),
        };
    }

    /**
     * The help card, the overview button and the demo switch of the facts.
     *
     * @param {Function} region finds an element of the template
     */
    bindChrome(region) {
        const help = region('help');
        region('helpBtn').onclick = () => {
            help.hidden = false;
            region('helpClose').focus();
        };
        region('helpClose').onclick = () => {
            help.hidden = true;
        };
        help.addEventListener('click', e => {
            if (e.target === help) {
                help.hidden = true;
            }
        });
        document.addEventListener('keydown', e => {
            if (e.key === 'Escape' && !help.hidden) {
                help.hidden = true;
            }
        });
        region('overview').onclick = () => this.details.toggleSummary();
    }

    /**
     * Shows the reference topology of the activity (the lab is not running).
     *
     * @param {boolean} [keepView] keep zoom and position
     */
    preview(keepView = false) {
        const data = this.data;
        // Statuses exist only in the demo data: the reference lab does not run anywhere.
        const live = data.live && data.live.node_status;
        this.stage.load(data.topology, {
            statuses: live ? new Map(Object.entries(live)) : null,
            guests: (data.live && data.live.guests) || null,
            facts: data.facts || null,
            keepView,
        });
        this.details.hide();
    }

    /**
     * A new state from the server: the scheme and the consoles follow the session.
     *
     * @param {object} state result of get_state
     * @param {object|null} before the session before this state
     */
    stateChanged(state, before) {
        const s = state.session;
        const running = !!s && s.status === 'running';
        if (!running || (before && before.sessionid !== s.sessionid)) {
            this.consoles.closeAll();
        }
        if (this.factsSession && (!running || s.sessionid !== this.factsSession)) {
            this.stopFacts();
        }
        if (running && s.topology) {
            const statuses = new Map(s.nodes.map(n => [n.nodeid, n.status]));
            const key = s.sessionid + '\n' + s.topology;
            if (key !== this.liveKey) {
                this.loadLive(s, statuses, key);
            } else {
                this.stage.setStatuses(statuses);
                this.details.refresh();
            }
            this.consoles.updateNodes(statuses);
            this.followFacts(s);
        } else if (this.liveKey) {
            this.liveKey = null;
            this.preview(true);
        }
    }

    /**
     * Shows the live topology of the session.
     *
     * @param {object} s session
     * @param {Map} statuses node id → status
     * @param {string} key identity of this topology
     */
    loadLive(s, statuses, key) {
        let doc;
        try {
            doc = JSON.parse(s.topology);
        } catch (e) {
            this.toast(this.strings.ui_live_bad);
            return;
        }
        const types = new Map(s.nodes.map(n => [n.nodeid, n.consoletype]));
        // Node states come from the live data of the session, not from the saved project.
        ((doc.topology && doc.topology.nodes) || []).forEach(n => {
            delete n.status;
            if (types.get(n.node_id)) {
                n.console_type = types.get(n.node_id);
            }
        });
        // The project name of a session is technical (lab-…-s-…): the title stays the name of the lab.
        doc.name = this.stage.model ? this.stage.model.name : doc.name;
        this.liveKey = key;
        const meta = this.stage.factsMeta;
        this.stage.load(doc, {statuses, keepView: true});
        if (this.factsSession === s.sessionid) {
            // The same session with a new topology: the facts are still those of the session.
            this.stage.setFactsMeta(meta);
            this.stage.setFacts(this.factsDoc);
            this.factsShown = '';
        }
        this.details.hide();
    }

    /* ---------------- live facts ---------------- */

    /**
     * Starts polling the facts of a running session that reports them, or stops it when the session does not.
     *
     * @param {object} s running session
     */
    followFacts(s) {
        const on = Array.isArray(s.features) && s.features.includes('facts');
        if (!on) {
            if (this.factsSession) {
                this.stopFacts();
                this.loadLiveAgain();
            }
            return;
        }
        if (this.factsSession === s.sessionid) {
            return;
        }
        this.factsSession = s.sessionid;
        this.factsDoc = null;
        this.factsRev = 0;
        this.factsShown = '';
        // Live mode without data: the demo facts of the preview are not shown on a live lab.
        this.stage.setFacts(null);
        this.pollFacts();
    }

    /**
     * Stops polling the facts; the scheme keeps what it shows until it is loaded again.
     */
    stopFacts() {
        clearTimeout(this.factsTimer);
        this.factsSession = null;
        this.factsDoc = null;
        this.factsRev = 0;
    }

    /**
     * Draws the live topology again without facts (the session stopped reporting them).
     */
    loadLiveAgain() {
        const s = this.session && this.session.session;
        this.liveKey = null;
        if (s) {
            this.stateChanged(this.session.state, s);
        }
    }

    /**
     * Reads the facts from the server and schedules the next read while the page is visible.
     */
    async pollFacts() {
        clearTimeout(this.factsTimer);
        const sessionid = this.factsSession;
        if (!sessionid || document.hidden) {
            return;
        }
        let result = null;
        try {
            [result] = await Promise.all(Ajax.call([{
                methodname: 'mod_gns3lab_get_facts',
                args: {cmid: this.cmid, userid: this.userid, sincerev: this.factsRev},
            }]));
        } catch (error) {
            // The last facts stay on the scheme; the next read may work.
            result = null;
        }
        if (sessionid !== this.factsSession) {
            return;
        }
        if (result) {
            this.takeFacts(result);
        }
        clearTimeout(this.factsTimer);
        this.factsTimer = setTimeout(() => this.pollFacts(), FACTS_POLL);
    }

    /**
     * Takes an answer of get_facts: new facts redraw the layers, the age of the old ones only the details card.
     *
     * @param {object} result {available, changed, rev, generatedat, ttl, facts}
     */
    takeFacts(result) {
        let changed = false;
        if (result.available && result.changed) {
            let doc = null;
            try {
                doc = JSON.parse(result.facts);
            } catch (e) {
                doc = null;
            }
            if (doc && typeof doc === 'object') {
                this.factsDoc = doc;
                this.factsRev = result.rev;
                changed = true;
            }
        } else if (!result.available && (this.factsDoc || this.factsRev)) {
            this.factsDoc = null;
            this.factsRev = 0;
            changed = true;
        }
        const at = result.generatedat ? result.generatedat * 1000 : null;
        const stale = !!at && Date.now() - at > result.ttl * 1000;
        this.stage.setFactsMeta({at, stale});
        if (changed) {
            this.stage.setFacts(this.factsDoc);
        }
        // What the card says changes with the minute of the collection and with going out of date.
        const shown = [at ? Math.floor(at / 60000) : 0, stale].join();
        if (changed || shown !== this.factsShown) {
            this.factsShown = shown;
            this.details.refresh();
        }
    }

    /**
     * Asks the manager to collect the facts now, then reads them again a little later.
     */
    async refreshFacts() {
        if (!this.factsSession) {
            return;
        }
        try {
            await Promise.all(Ajax.call([{
                methodname: 'mod_gns3lab_refresh_facts',
                args: {cmid: this.cmid, userid: this.userid},
            }]));
            this.toast(this.strings.ui_facts_refresh_sent);
        } catch (error) {
            this.toast(error.message || String(error));
            this.details.refresh();
            return;
        }
        clearTimeout(this.factsTimer);
        this.factsTimer = setTimeout(() => this.pollFacts(), FACTS_AFTER_REFRESH);
    }

    /* ---------------- consoles: can the student open one now ---------------- */

    get live() {
        const state = this.session && this.session.state;
        const s = state && state.session;
        return state && s && s.status === 'running' ? {state, session: s} : null;
    }

    /**
     * Why the console of a node cannot be opened now.
     *
     * @param {object} n node of the model
     * @returns {string} empty if it can
     */
    consoleWhy(n) {
        const live = this.live;
        if (!live) {
            return this.session ? this.strings.consoleneedsrunning : this.strings.ui_why_viewer;
        }
        if (!live.state.canuse && !live.state.readonly) {
            return this.strings.ui_why_viewer;
        }
        if (n.consoleType === 'vnc' && live.state.readonly) {
            // The screen is the student's own: the teacher does not see it (and the server issues no ticket for it).
            return this.strings.consolevncobserver;
        }
        if (!n.consoleType || n.consoleType === 'none') {
            return this.strings.consolenone;
        }
        if (this.stage.nodeStatus(n) !== 'started') {
            return fill(this.strings.ui_why_node_off, n.name);
        }
        return '';
    }

    canConsole(n) {
        return this.consoleWhy(n) === '';
    }

    openConsole(n) {
        const why = this.consoleWhy(n);
        if (why) {
            this.toast(why);
            return;
        }
        this.consoles.open(n);
    }

    /* ---------------- the action wheel ---------------- */

    actionsFor(target) {
        const s = this.strings, free = id => ({
            id, label: s.ui_act_free, full: s.ui_act_free_full, icon: 'free', off: true, why: s.ui_act_free_why, free: true,
        });
        if (target.isLink) {
            const items = [
                {id: 'linfo', label: s.ui_act_info, full: s.ui_act_link_info, icon: 'info'},
                {id: 'lzoom', label: s.ui_act_zoom, full: s.ui_act_link_zoom, icon: 'zoom'},
                free('free1'),
            ];
            [target.link.a, target.link.b].forEach((e, i) => {
                const n = this.stage.model.nodes.get(e.nodeId);
                if (n && n.consoleType !== 'none') {
                    const why = this.consoleWhy(n);
                    items.push({
                        id: i ? 'conb' : 'cona', label: n.name, full: fill(s.ui_console_of, n.name), icon: 'term',
                        off: !!why, why,
                    });
                }
            });
            return items;
        }
        const n = target, open = this.consoles.has(n.id), why = n.consoleType === 'none' ? '' : this.consoleWhy(n);
        const items = [];
        if (n.consoleType !== 'none') {
            items.push({
                id: 'console', label: s.ui_act_console, full: open ? s.ui_act_console_goto : s.ui_act_console_open,
                icon: 'term', off: !!why, why,
            });
        }
        items.push({id: 'info', label: s.ui_act_info, full: s.ui_act_info_full, icon: 'info'});
        items.push({id: 'zoom', label: s.ui_act_zoom, full: s.ui_act_zoom_node, icon: 'zoom'});
        if (open) {
            items.push({id: 'close', label: s.ui_act_close, full: s.ui_act_close_full, icon: 'close'});
        }
        // Free places: node control (start, stop, restart) is not part of the plugin API yet.
        items.push(free('free1'), free('free2'));
        return items;
    }

    runAction(id, t) {
        const stage = this.stage, ends = t.isLink ? [t.link.a, t.link.b].map(e => stage.model.nodes.get(e.nodeId)) : [];
        ({
            console: () => this.openConsole(t),
            info: () => {
                stage.select({kind: 'node', id: t.id}, true);
                this.details.show(stage.sel);
            },
            zoom: () => stage.zoomToNode(t, Math.max(2.4, stage.view.k)),
            close: () => this.consoles.close(t.id),
            linfo: () => this.details.show(stage.sel && stage.sel.kind === 'link' ? stage.sel : {kind: 'link', id: t.id}),
            lzoom: () => stage.zoomToLink(t.link),
            cona: () => this.openConsole(ends[0]),
            conb: () => this.openConsole(ends[1]),
        })[id]();
    }

    /**
     * Short message over the scheme.
     *
     * @param {string} text message
     * @param {number} [ms] how long
     */
    toast(text, ms) {
        const t = this.toastEl;
        t.textContent = text;
        t.hidden = false;
        clearTimeout(this.toastTimer);
        this.toastTimer = setTimeout(() => {
            t.hidden = true;
        }, ms || 2600);
    }
}

/**
 * Starts the lab page.
 *
 * @param {string} prefix id prefix of the template ({{uniqid}})
 * @param {number} cmid course module id
 * @param {number} userid whose lab, 0 for the current user
 */
export const init = async(prefix, cmid, userid = 0) => {
    const root = document.getElementById(prefix + '-app');
    if (!root || root.dataset.labReady) {
        return;
    }
    root.dataset.labReady = '1';
    const keys = [...new Set([...UI_KEYS, ...SESSION_KEYS, ...CONSOLE_KEYS, ...VNC_KEYS, ...SNIPPET_KEYS])];
    const values = await getStrings(keys.map(key => ({key, component: 'mod_gns3lab'})));
    const strings = Object.fromEntries(keys.map((key, i) => [key, values[i]]));
    new LabPage(root, {prefix, cmid, userid, strings});
};
