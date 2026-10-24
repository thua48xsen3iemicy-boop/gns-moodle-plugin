/**
 * Stand-in for core/ajax: plays the external functions of the plugin without Moodle.
 *
 * One lab session in memory: start → starting → running, stop → stopping → stopped. The live topology is the demo
 * lab. Tests reach into window.__stub to stop and start nodes.
 */
define([], function() {
    const cfg = window.GNS3LAB_STUB || {};
    const now = () => Math.floor(Date.now() / 1000);
    let session = cfg.observer ? {status: 'running', stage: 'ready', progress: 100, at: 0, start: Math.floor(Date.now() / 1000)} : null;
    let demo = null;
    const nodeStatus = {};

    const loadDemo = async() => {
        if (!demo) {
            demo = await (await fetch('demo.json')).json();
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
        };
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
        mod_gns3lab_get_console_ticket: async args => {
            await loadDemo();
            if ((nodeStatus[args.nodeid] || 'stopped') !== 'started') {
                const e = new Error('The device is stopped.');
                e.errorcode = 'managererror_node_not_running';
                throw e;
            }
            return {ticket: 't' + Math.random().toString(36).slice(2), gatewayurl: `ws://${location.host}/gw`, expiresin: 30, readonly: !!cfg.observer};
        },
    };

    window.__stub = {
        setNode: (id, status) => {
            nodeStatus[id] = status;
        },
        nodeStatus,
        get session() {
            return session;
        },
    };

    return {
        call: requests => requests.map(r => {
            if (!handlers[r.methodname]) {
                return Promise.reject(new Error('Unknown external function ' + r.methodname));
            }
            return handlers[r.methodname](r.args);
        }),
    };
});
