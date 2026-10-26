/**
 * Stand-in for core/ajax: plays the external functions of the plugin without Moodle.
 *
 * One lab session in memory: start → starting → running, stop → stopping → stopped. The live topology is the demo
 * lab. Tests reach into window.__stub to stop and start nodes.
 *
 * Live facts (get_facts, refresh_facts) are the demo fixtures: a refresh switches between the correct and the broken
 * configuration and moves rev, like mock-manager does. window.__stub.facts changes them: empty() (rev 0, no data),
 * fail(nodeId, code) (the last collection of a node failed), stale(true) (the collection stopped long ago).
 */
define([], function() {
    const cfg = window.GNS3LAB_STUB || {};
    const now = () => Math.floor(Date.now() / 1000);
    let session = cfg.observer ? {status: 'running', stage: 'ready', progress: 100, at: 0, start: Math.floor(Date.now() / 1000)} : null;
    let demo = null;
    const nodeStatus = {};
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
            features: running ? (cfg.nofacts ? ['consoles'] : ['consoles', 'facts']) : [],
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

    const handlers = {
        mod_gns3lab_get_state: state,
        mod_gns3lab_start_session: async() => {
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
        mod_gns3lab_stop_session: async() => {
            session.status = 'stopping';
            session.at = Date.now();
            return view();
        },
        mod_gns3lab_get_facts: async args => {
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

    window.__stub = {
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
        // Calls an external function the way the page does, for a test.
        call: (name, args) => handlers[name](args),
        reveal: {
            items: revealed,
            clear: () => keepRevealed([]),
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
