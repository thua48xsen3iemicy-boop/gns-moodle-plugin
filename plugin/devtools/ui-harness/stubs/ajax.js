/**
 * Stand-in for core/ajax: plays the external functions of the plugin without Moodle.
 *
 * One lab session in memory: start → starting → running, stop → stopping → stopped. The live topology is the demo
 * lab. Tests reach into window.__stub to stop and start nodes.
 *
 * The seat (classes/local/seat.php, docs/seat.md): who drives the lab is `seat.holder` — `me` (this page), `othertab`
 * (another page of this login) or `otherlogin` (another login of the account); `?seat=othertab|otherlogin` opens the
 * page with the seat held by somebody else. A page that does not hold it is refused at start, stop, console tickets and
 * the mask, like the server refuses; claim_seat gives the seat to a page that was just opened or chose to use it, but
 * never takes it from another login. window.__stub.seat.give(<holder>) moves it, as another page or login would.
 *
 * Live facts (get_facts, refresh_facts) are the demo fixtures: a refresh switches between the correct and the broken
 * configuration and moves rev, like mock-manager does. window.__stub.facts changes them: empty() (rev 0, no data),
 * fail(nodeId, code) (the last collection of a node failed), stale(true) (the collection stopped long ago).
 *
 * The course page of a teacher (course.php, amd/src/course.js): mod_gns3lab_get_course_overview answers with five students
 * and two labs (`window.__stub.course`); get_state and get_facts called with the id of a student of the page (11 to 14)
 * play that student's lab: 11 has correct facts, 12 the broken ones, `?laberror` makes the second lab fail like a manager
 * that does not answer. window.__stub.course.set(cmid, userid, session|null) changes a cell, .bump(userid) moves the rev of facts.
 */
define([], function() {
    const cfg = window.GNS3LAB_STUB || {};
    const now = () => Math.floor(Date.now() / 1000);
    let session = cfg.observer ? {status: 'running', stage: 'ready', progress: 100, at: 0, start: Math.floor(Date.now() / 1000)} : null;
    let demo = null;
    const nodeStatus = {};
    const seat = {holder: ['othertab', 'otherlogin'].includes(cfg.seat) ? cfg.seat : 'me', tabid: ''};
    // The page that asks for the seat (no id: a call from a test) is the page that holds it.
    const needSeat = (args, name) => {
        window.__stub.seat.gated.push({name, tabid: (args && args.tabid) || ''});
        if (seat.holder !== 'me') {
            const e = new Error(seat.holder === 'otherlogin'
                ? 'This account is working in this lab in another login session.'
                : 'The lab is open on another page.');
            e.errorcode = 'seat' + seat.holder;
            throw e;
        }
        seat.tabid = (args && args.tabid) || seat.tabid;
    };
    const facts = {rev: cfg.factsempty ? 0 : 1, variant: 'ok', at: now(), stale: false, errors: {}, patches: []};

    // What the student has uncovered (mod_gns3lab_reveal): kept in the tab, so that a reload shows it again.
    const REVEAL_KEY = 'gns3lab-stub-reveal';
    const revealed = () => {
        try {
            return JSON.parse(sessionStorage.getItem(REVEAL_KEY) || '[]');
        } catch (e) {
            return [];
        }
    };
    const keepRevealed = items => {
        try {
            sessionStorage.setItem(REVEAL_KEY, JSON.stringify(items));
        } catch (e) {
            // A tab that does not keep it loses it on reload.
        }
    };

    const loadDemo = async() => {
        if (!demo) {
            demo = await (await fetch(cfg.lab ? `demo.${cfg.lab}.json` : 'demo.json')).json();
            Object.assign(nodeStatus, demo.live.node_status);
        }
        return demo;
    };

    const view = async() => {
        await loadDemo();
        if (!session) {
            return null;
        }
        const wait = cfg.fast ? 300 : 2500;
        if (session.status === 'starting' && Date.now() - session.at > wait) {
            session.status = 'running';
            session.stage = 'ready';
            session.progress = 100;
        }
        if (session.status === 'stopping' && Date.now() - session.at > wait) {
            session.status = 'stopped';
        }
        const running = session.status === 'running';
        const doc = JSON.parse(JSON.stringify(demo.topology));
        doc.name = 'lab-1-s-1';
        return {
            sessionid: 's-1',
            status: session.status,
            stage: session.stage,
            progress: session.progress,
            statusmessage: '',
            errorcode: '',
            errorretryable: false,
            startedat: running ? session.start : 0,
            expiresat: running ? session.start + 3 * 3600 : 0,
            topology: running ? JSON.stringify(doc) : '',
            nodes: running ? doc.topology.nodes.map(n => ({
                nodeid: n.node_id,
                status: nodeStatus[n.node_id] || 'stopped',
                consoletype: n.console_type || 'none',
            })) : [],
            nodesstale: false,
            features: running ? (cfg.nofacts ? ['consoles'] : ['consoles', 'facts', 'checks']) : [],
        };
    };

    /** The facts document of the live session, as the manager would send it (docs/live-state.md). */
    const factsDoc = () => {
        const generated = facts.stale ? now() - 600 : now();
        const doc = {schema: 'gns3lab.facts/1', rev: facts.rev, ttl: 90, nodes: {}};
        if (!facts.rev) {
            return doc;
        }
        doc.generated_at = new Date(generated * 1000).toISOString().replace(/\.\d+Z$/, 'Z');
        doc.nodes = JSON.parse(JSON.stringify(demo.facts[facts.variant].nodes));
        facts.patches.forEach(p => {
            const iface = ((doc.nodes[p.nodeId] || {}).interfaces || []).find(i => i.name === p.name);
            if (iface) {
                Object.assign(iface, p.fields);
            }
        });
        Object.entries(facts.errors).forEach(([id, err]) => {
            if (doc.nodes[id]) {
                doc.nodes[id].error = {code: err.code, message: 'stub'};
                doc.nodes[id].collected_at = new Date(err.at * 1000).toISOString().replace(/\.\d+Z$/, 'Z');
            }
        });
        return doc;
    };

    const state = async() => {
        const s = await view();
        const out = {canuse: !cfg.observer, readonly: !!cfg.observer, managerconfigured: true, labready: !cfg.norunning, resume: true};
        if (s) {
            out.session = s;
        }
        return out;
    };

    // The course page of a teacher: students 11 to 14 in two labs, the cells as get_course_overview sends them.
    const COURSE_USERS = {11: 'ok', 12: 'broken'};
    const course = {
        labs: [{cmid: 1, name: 'Lab 4 · OSPF multi-area', error: ''}, {cmid: 2, name: 'Lab 5 · Linux switch', error: cfg.laberror ? 'unreachable' : ''}],
        students: [
            {userid: 11, fullname: 'Alice Ivanova', groups: [5]}, {userid: 12, fullname: 'Boris Petrov', groups: [5]},
            {userid: 13, fullname: 'Chen Wei', groups: [6]}, {userid: 14, fullname: 'Dana Kim', groups: [6]},
            // A name that looks like markup: the page must show it as text.
            {userid: 15, fullname: 'Eve <b>x</b> & Co', groups: [6]},
        ],
        rev: {},
        cells: new Map(),
        calls: [],
    };
    const summary = (status, facts = 'unknown', left = 3 * 3600) => ({
        status,
        startedat: status === 'none' ? 0 : now() - 600,
        expiresat: status === 'running' ? now() + left : 0,
        facts,
    });
    const setCell = (cmid, userid, session, observable = true) => course.cells.set(cmid + ':' + userid, {cmid, userid, observable, session});
    setCell(1, 11, summary('running'));
    setCell(1, 12, summary('running'));
    setCell(1, 13, summary('failed'));
    setCell(1, 14, null);
    setCell(2, 11, summary('stopped'));
    setCell(2, 12, null);
    setCell(2, 13, summary('running', 'no'));
    setCell(2, 14, null, false);
    setCell(1, 15, null);
    setCell(2, 15, null);

    const courseOverview = async args => {
        course.calls.push(args);
        if (cfg.coursefail) {
            throw new Error('The course could not be read.');
        }
        const students = course.students.filter(s => !args.groupid || s.groups.includes(args.groupid));
        const cells = [];
        students.forEach(st => course.labs.forEach(lab => cells.push({...course.cells.get(lab.cmid + ':' + st.userid)})));
        return {
            labs: course.labs,
            students: students.map(st => ({userid: st.userid, fullname: st.fullname, pictureurl: 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'})),
            cells,
        };
    };

    /** The running lab of a student of the course page. */
    const courseView = async userid => {
        await loadDemo();
        const doc = JSON.parse(JSON.stringify(demo.topology));
        return {
            sessionid: 's-' + userid, status: 'running', stage: 'ready', progress: 100, statusmessage: '', errorcode: '',
            errorretryable: false, startedat: now() - 600, expiresat: now() + 3 * 3600, topology: JSON.stringify(doc),
            nodes: doc.topology.nodes.map(n => ({nodeid: n.node_id, status: 'started', consoletype: n.console_type || 'none'})),
            nodesstale: false, features: ['consoles', 'facts'],
        };
    };
    const courseFacts = async args => {
        await loadDemo();
        const variant = COURSE_USERS[args.userid];
        if (!variant) {
            return {available: false, changed: false, rev: 0, generatedat: 0, ttl: 0, facts: ''};
        }
        const rev = course.rev[args.userid] || 1;
        const doc = {
            schema: 'gns3lab.facts/1', rev, ttl: 90, generated_at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
            nodes: JSON.parse(JSON.stringify(demo.facts[variant].nodes)),
        };
        const changed = !args.sincerev || args.sincerev !== rev;
        return {available: true, changed, rev, generatedat: now(), ttl: 90, facts: changed ? JSON.stringify(doc) : ''};
    };

    // Auto-checks (classes/external/get_checks.php, start_check.php, get_check_run.php): runs in memory, a run takes
    // two seconds (?fast: half a second); the report depends on the facts variant like the mock manager. ?checksreport=counts
    // or =after hides the findings like the server does, ?checksmax=N limits the attempts (0: unlimited), ?checkteacher
    // runs them as a teacher, who is told what could not be checked.
    const checks = {runs: [], next: null};
    const checkFindings = () => facts.variant === 'broken' ? [
        {id: 'f1', code: 'vlan_wrong', group: 'l2', class: 'major', baseclass: 'major', counted: true, causedby: '',
            nodes: ['SW1'], ifaces: [{node: 'SW1', iface: 'Ethernet0/1'}], message: 'SW1: порт Ethernet0/1 в VLAN 1, а должен быть в VLAN 10'},
        {id: 'f2', code: 'segment_split', group: 'l2', class: 'critical', baseclass: 'critical', counted: false, causedby: 'f1',
            nodes: ['PC1', 'PC2'], ifaces: [], message: 'Сегмент LAN1: PC1 и PC2 в разных L2-доменах'},
        {id: 'f3', code: 'address_missing', group: 'addressing', class: 'major', baseclass: 'major', counted: true, causedby: '',
            nodes: ['PC1'], ifaces: [{node: 'PC1', iface: 'eth0'}], message: 'PC1: на eth0 нет адреса 192.168.10.2/24 <b>'},
        {id: 'f4', code: 'hostname_wrong', group: 'host', class: 'note', baseclass: 'minor', counted: false, causedby: '',
            nodes: ['PC1'], ifaces: [], message: 'PC1: hostname «localhost»'},
    ] : [
        {id: 'f1', code: 'vlan_extra', group: 'l2', class: 'minor', baseclass: 'minor', counted: true, causedby: '',
            nodes: ['SW1'], ifaces: [], message: 'SW1: создан VLAN 99, которого нет в задании'},
    ];
    const checkRun = r => {
        const age = Date.now() - r.at, span = cfg.fast ? 500 : 2000;
        if (r.status !== 'done' && r.status !== 'failed') {
            r.status = age < span / 4 ? 'queued' : age < span ? 'running' : (r.outcome === 'failed' ? 'failed' : 'done');
            if (r.status === 'done') {
                r.findings = r.outcome === 'graded' ? checkFindings() : [];
                const counts = {critical: 0, major: 0, minor: 0, note: 0};
                r.findings.filter(f => !f.causedby).forEach(f => { counts[f.class]++; });
                r.counts = counts;
                r.mark = r.outcome !== 'graded' ? -1 : counts.critical ? 2 : counts.major > 1 ? 3 : counts.major ? 4 : 5;
                r.finishedat = now();
            }
            if (r.status === 'failed') {
                r.errorcode = 'SLOT_UNAVAILABLE';
                r.finishedat = now();
            }
        }
        return r;
    };
    const checkSummary = r => ({runid: r.runid, status: r.status, outcome: r.status === 'done' ? r.outcome : '', mark: r.mark,
        counts: r.counts, errorcode: r.errorcode, requestedat: r.requestedat, finishedat: r.finishedat});
    const checksOverview = () => {
        checks.runs.forEach(checkRun);
        const used = checks.runs.filter(r => r.status === 'done' && r.outcome === 'graded').length;
        const left = cfg.checksmax ? Math.max(0, cfg.checksmax - used) : -1;
        return {enabled: true, available: true, canstart: !cfg.observer && left !== 0, report: cfg.checksreport,
            detailsfrom: cfg.observer || cfg.checksreport === 'full' ? 0 : cfg.checksreport === 'counts' ? -1 : now() + 86400,
            attempts: {max: cfg.checksmax || 0, used, left}, runs: checks.runs.slice().reverse().map(checkSummary)};
    };

    const handlers = {
        mod_gns3lab_authoring: async args => {
            const response = await fetch('/authoring-api', {method: 'POST',
                headers: {'Content-Type': 'application/json'}, body: JSON.stringify(args)});
            const result = await response.json();
            if (!response.ok) throw new Error(result.message || result.error);
            return result;
        },
        mod_gns3lab_get_checks: async() => checksOverview(),
        mod_gns3lab_start_check: async args => {
            needSeat(args, 'start_check');
            const s = await view();
            if (!s || s.status !== 'running') {
                throw new Error('The lab is not running.');
            }
            const o = checksOverview();
            if (o.attempts.left === 0) {
                throw new Error('You have no check attempts left.');
            }
            if (!checks.runs.some(r => r.status === 'queued' || r.status === 'running')) {
                checks.runs.push({runid: 'c-' + Math.random().toString(16).slice(2, 18).padEnd(16, '0'), status: 'queued',
                    outcome: checks.next || 'graded', mark: -1, counts: {critical: 0, major: 0, minor: 0, note: 0}, errorcode: '',
                    requestedat: now(), finishedat: 0, at: Date.now(), findings: []});
                checks.next = null;
            }
            return checksOverview();
        },
        mod_gns3lab_get_check_run: async args => {
            const r = checks.runs.find(x => x.runid === args.runid);
            if (!r) {
                throw new Error('This check is not known in this activity.');
            }
            checkRun(r);
            const o = checksOverview();
            const detailed = o.detailsfrom === 0 && r.status === 'done';
            return {...checkSummary(r), detailed, detailsfrom: o.detailsfrom, gone: false,
                findings: detailed ? r.findings : [],
                // Like the server: the rules skipped for want of data, to a teacher only (?checkteacher, ?observer).
                rules: detailed && (cfg.checkteacher || cfg.observer) ? [
                    {code: 'address_not_saved', group: 'addressing', status: 'skipped', reason: 'no_collector'},
                    {code: 'config_not_applied', group: 'addressing', status: 'skipped', reason: 'no_collector'},
                    {code: 'hostname_wrong', group: 'host', status: 'skipped', reason: 'collect_failed'},
                ] : []};
        },
        mod_gns3lab_get_course_overview: courseOverview,
        mod_gns3lab_get_state: async args => {
            if (cfg.course && args.userid) {
                return COURSE_USERS[args.userid]
                    ? {canuse: false, readonly: true, managerconfigured: true, labready: true, resume: true, session: await courseView(args.userid)}
                    : {canuse: false, readonly: true, managerconfigured: true, labready: true, resume: true};
            }
            return state();
        },
        mod_gns3lab_claim_seat: async args => {
            window.__stub.seat.claims.push({tabid: args.tabid, takeover: !!args.takeover});
            if (seat.holder === 'othertab' && args.takeover) {
                seat.holder = 'me';
            }
            if (seat.holder === 'me') {
                seat.tabid = args.tabid;
            }
            return {status: seat.holder === 'me' ? 'held' : seat.holder, mode: 'tab', idle: seat.holder === 'me' ? 0 : 12, beat: 20};
        },
        mod_gns3lab_start_session: async args => {
            needSeat(args, 'start_session');
            await loadDemo();
            if (cfg.conflict) {
                const e = new Error('You already have a lab running in "Lab 2". Stop it first.');
                e.errorcode = 'sessionconflictat';
                e.link = '#other';
                throw e;
            }
            session = {status: 'starting', stage: 'ct_start', progress: 40, at: Date.now(), start: now()};
            return view();
        },
        mod_gns3lab_stop_session: async args => {
            needSeat(args, 'stop_session');
            session.status = 'stopping';
            session.at = Date.now();
            return view();
        },
        mod_gns3lab_get_facts: async args => {
            if (cfg.course && args.userid) {
                return courseFacts(args);
            }
            const s = await view();
            if (!s || !s.features.includes('facts')) {
                return {available: false, changed: false, rev: 0, generatedat: 0, ttl: 0, facts: ''};
            }
            const doc = factsDoc();
            const changed = !args.sincerev || args.sincerev !== doc.rev;
            return {
                available: true,
                changed,
                rev: doc.rev,
                generatedat: doc.generated_at ? Math.floor(Date.parse(doc.generated_at) / 1000) : 0,
                ttl: doc.ttl,
                facts: changed ? JSON.stringify(doc) : '',
            };
        },
        mod_gns3lab_refresh_facts: async() => {
            const s = await view();
            if (!s || s.status !== 'running') {
                throw new Error('The lab is not running.');
            }
            facts.rev += 1;
            facts.variant = facts.variant === 'ok' ? 'broken' : 'ok';
            facts.stale = false;
            return {requested: true};
        },
        // classes/external/get_reveal.php: the mask of the user (or, for a teacher, of the student).
        mod_gns3lab_get_reveal: async() => ({items: revealed()}),
        // classes/external/reveal.php and classes/local/reveal.php: only items of the live topology, only added.
        mod_gns3lab_reveal: async args => {
            needSeat(args, 'reveal');
            await loadDemo();
            const s = await view();
            if (!s || s.status !== 'running') {
                const e = new Error('The lab is not running.');
                e.errorcode = 'sessionnotrunning';
                throw e;
            }
            if (args.items.length > 500) {
                throw new Error('Invalid parameter value detected: too many items');
            }
            const topo = demo.topology.topology;
            const nodes = new Set(topo.nodes.map(n => n.node_id)), links = new Set(topo.links.map(l => l.link_id));
            const have = revealed(), key = i => [i.nodeid, i.kind, i.item].join('\n');
            const known = new Set(have.map(key));
            let added = 0;
            args.items.forEach(raw => {
                const i = {nodeid: raw.nodeid || '', kind: raw.kind, item: raw.item || ''};
                const ok = i.kind === 'link' ? !i.nodeid && links.has(i.item)
                    : nodes.has(i.nodeid) && (['node', 'known'].includes(i.kind) ? i.item === ''
                        : i.kind === 'group' ? ['l1', 'l2', 'l3', 'routes'].includes(i.item)
                            : i.kind === 'iface' && /^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$/.test(i.item) && i.item.length <= 100);
                if (!ok) {
                    throw new Error('Invalid parameter value detected: bad reveal item ' + JSON.stringify(raw));
                }
                if (!known.has(key(i))) {
                    known.add(key(i));
                    have.push(i);
                    added++;
                }
            });
            keepRevealed(have);
            return {added};
        },
        mod_gns3lab_get_console_ticket: async args => {
            if (!cfg.observer) {
                needSeat(args, 'get_console_ticket');
            }
            await loadDemo();
            const node = demo.topology.topology.nodes.find(n => n.node_id === args.nodeid);
            // classes/local/reveal.php console_allowed(): in a fog with starting nodes a node that is not found has no ticket.
            if (!cfg.observer && cfg.mode === 'fog' && cfg.start.length && node && cfg.start.includes(node.name)
                === false && !revealed().some(i => i.nodeid === args.nodeid && ['node', 'known'].includes(i.kind))) {
                const e = new Error('This device has not been found yet.');
                e.errorcode = 'nodenotfound';
                throw e;
            }
            if (node && node.console_type === 'vnc' && cfg.observer) {
                // classes/external/get_console_ticket.php: the teacher does not see the screen of a student.
                const e = new Error('The teacher does not see the graphical console of a student.');
                e.errorcode = 'consolevncobserver';
                throw e;
            }
            if ((nodeStatus[args.nodeid] || 'stopped') !== 'started') {
                const e = new Error('The device is stopped.');
                e.errorcode = 'managererror_node_not_running';
                throw e;
            }
            window.__stub.tickets = (window.__stub.tickets || 0) + 1;
            return {ticket: 't' + Math.random().toString(36).slice(2), gatewayurl: `ws://${location.host}/gw`, expiresin: 30, readonly: !!cfg.observer};
        },
    };

    // The lab editor: a pretend lab manager. validate refuses what the manager would refuse in these simple cases and
    // warns about every gateway, like manager 0.10; save and publish remember the last definition they got.
    const editor = {saved: null, published: null, validated: 0};
    const SUBNET = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/;
    const problemsOf = def => {
        const problems = [], warnings = [];
        (def.segments || []).forEach((g, i) => {
            const m = SUBNET.exec((g.subnet || {}).fixed || '');
            if (!m || +m[5] < 8 || +m[5] > 30) {
                problems.push({path: `segments[${i}].subnet.fixed`, code: 'bad_subnet', message: ''});
            }
            const seen = new Set();
            g.members.forEach((x, j) => {
                if (Number.isInteger(x.host) && seen.has(x.host)) {
                    problems.push({path: `segments[${i}].members[${j}]`, code: 'host_in_use', message: `host ${x.host}`});
                }
                seen.add(x.host);
            });
            if (!g.members.length) {
                problems.push({path: `segments[${i}].members`, code: 'required', message: 'non-empty list'});
            }
            // As the lab manager: the notes only for what it does not set up, and it sets up Cisco IOL (image ids cisco-iou-*).
            const iol = name => /^cisco-iou-/.test(((def.nodes || []).find(n => n.name === name) || {}).image || '');
            if (g.gateway && !iol(g.gateway.node)) {
                warnings.push({path: `segments[${i}].gateway`, code: 'gateway_not_configured', message: ''});
            }
            const switches = (def.nodes || []).filter(n => /switch/.test(n.image) || n.image === 'cisco-iou-l2');
            if (g.vlan && switches.some(n => !iol(n.name))) {
                warnings.push({path: `segments[${i}].vlan`, code: 'switch_not_configured', message: ''});
            }
        });
        return {problems, warnings};
    };
    Object.assign(handlers, {
        mod_gns3lab_validate_lab: async args => {
            editor.validated++;
            const {problems, warnings} = problemsOf(JSON.parse(args.definition));
            return {valid: !problems.length, memorymb: problems.length ? 0 : 3072, cores: problems.length ? 0 : 2, problems,
                warnings: problems.length ? [] : warnings};
        },
        mod_gns3lab_save_lab_draft: async args => {
            editor.saved = JSON.parse(args.definition);
            return {version: 1, status: 'draft'};
        },
        mod_gns3lab_publish_lab: async args => {
            const def = JSON.parse(args.definition);
            const {problems} = problemsOf(def);
            editor.saved = def;
            if (!problems.length) {
                editor.published = def;
            }
            return {published: !problems.length, version: 1, problems};
        },
    });

    window.__stub = {
        editor,
        course: {
            set: setCell,
            // The facts of a student changed: the next read sees a new rev.
            bump: userid => {
                course.rev[userid] = (course.rev[userid] || 1) + 1;
            },
            calls: course.calls,
        },
        // How many times each external function was called: a test tells a page that read the facts from one that did not.
        calls: {},
        setNode: (id, status) => {
            nodeStatus[id] = status;
        },
        nodeStatus,
        facts: {
            empty: () => {
                facts.rev = 0;
            },
            fill: () => {
                facts.rev = Math.max(1, facts.rev + 1);
            },
            // The deciding facts of an interface change (a student raised it): the next poll shows it.
            patch: (nodeId, name, fields) => {
                facts.patches.push({nodeId, name, fields});
                facts.rev += 1;
            },
            fail: (id, code = 'unreachable') => {
                facts.errors[id] = {code, at: now() - 240};
                facts.rev += 1;
            },
            stale: on => {
                facts.stale = !!on;
            },
            state: facts,
        },
        get session() {
            return session;
        },
        // Auto-checks: the runs, and the outcome of the next run ('incomplete' or 'failed').
        checks: {
            get runs() {
                return checks.runs;
            },
            next: outcome => {
                checks.next = outcome;
            },
        },
        // Calls an external function the way the page does, for a test.
        call: (name, args) => handlers[name](args),
        reveal: {
            items: revealed,
            clear: () => keepRevealed([]),
        },
        seat: {
            claims: [],
            // The calls that need the seat, with the page id each carried.
            gated: [],
            get holder() {
                return seat.holder;
            },
            get tabid() {
                return seat.tabid;
            },
            // Another page or login takes the seat (or gives it back with 'me').
            give: holder => {
                seat.holder = holder;
            },
        },
    };

    return {
        call: requests => requests.map(r => {
            window.__stub.calls[r.methodname] = (window.__stub.calls[r.methodname] || 0) + 1;
            if (!handlers[r.methodname]) {
                return Promise.reject(new Error('Unknown external function ' + r.methodname));
            }
            return handlers[r.methodname](r.args);
        }),
    };
});
