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
 * The modes `seen` and `fog` of the scheme: what a student has uncovered and what the scheme shows of it
 * (docs/lab-constructor/README.md §2).
 *
 * Pure functions without DOM access. Everything is computed from the model of the lab (`.gns3`), the merged facts
 * (the deciding facts of the manager with what the student saw in the consoles, `consolefacts.mergeSeen`), the mask of
 * what is uncovered, the mode and the starting nodes. Nothing here is a protection: the topology and the facts reach the
 * browser anyway, the mask only decides what the scheme draws.
 *
 * The mask holds items `{nodeid, kind, item}` as the server keeps them (`mod_gns3lab_reveal`):
 *  - `node`: the node is open, its console printed something; `known`: found by a cable that works;
 *  - `iface`: an interface shown in the output of a command (its name in the merged facts);
 *  - `group`: a group of data of the node a command showed: `l1` state of the ports, `l2` VLANs and switchports,
 *    `l3` addresses, `routes` routes and routing protocols;
 *  - `link`: a cable that works at an end that is open (`nodeid` empty, `item` is the link id).
 *
 * @module     mod_gns3lab/reveal
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import {addSeen, mergeSeen} from 'mod_gns3lab/consolefacts';

/** Modes of the scheme. */
export const MODES = ['live', 'seen', 'fog'];

/** Kinds of the items of the mask. */
export const KINDS = ['node', 'known', 'iface', 'group', 'link'];

/** Groups of data of a node. */
export const GROUPS = ['l1', 'l2', 'l3', 'routes'];

/** The server takes this many items in a call (`mod_gns3lab_reveal`). */
export const MAX_PER_CALL = 500;

/** The server takes an interface name of this shape and length only. */
const IFACE_RE = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$/;
const ITEM_MAX = 100;

/**
 * The groups a command uncovers, by the name `consolefacts.interpret` gives it (docs/lab-constructor/README.md §2.1).
 * A command this table does not know uncovers the groups its output has fields of.
 */
const LABEL_GROUPS = {
    'show ip interface brief': ['l1', 'l3'],
    'show ip interface': ['l1', 'l3'],
    'show interfaces status': ['l1', 'l2'],
    'show interfaces trunk': ['l2'],
    'show vlan brief': ['l2'],
    'ip addr': ['l1', 'l3'],
    'ip route': ['routes'],
    'show ip': ['l3'],
};

/** Fields of an interface that always come with it: they say which interface it is and where it sits. */
const IDENTITY = ['name', 'kind', 'port', 'parent', 'dot1q', 'vlan', 'mirror', 'seen'];

/** Fields of an interface by the group they belong to. */
const IFACE_FIELDS = {
    l1: ['admin', 'oper', 'mac'],
    l2: ['switchport', 'stp'],
    l3: ['ipv4', 'gateway'],
    routes: ['ospf'],
};

/** Fields of a node that come with it in any case: the state of the collection, not data of the device. */
const NODE_BASE = ['status', 'name', 'collected_at', 'error', 'seen'];

/** Fields of a node by the group they belong to. `mac_table` has no group yet: no command shows it. */
const NODE_FIELDS = {
    l1: [],
    l2: ['vlans'],
    l3: [],
    routes: ['routes', 'router_id', 'adjacencies'],
};

/* ---------------- the mask ---------------- */

/**
 * An empty mask.
 *
 * @returns {{nodes: Set, known: Set, ifaces: Map, groups: Map, links: Set}}
 */
export const emptyMask = () => ({
    nodes: new Set(), known: new Set(), ifaces: new Map(), groups: new Map(), links: new Set(),
});

/**
 * A copy of a mask: the sets and maps are new, so that a mask that was handed out never changes.
 *
 * @param {object} mask the mask
 * @returns {object}
 */
const cloneMask = mask => ({
    nodes: new Set(mask.nodes),
    known: new Set(mask.known),
    ifaces: new Map([...mask.ifaces].map(([id, set]) => [id, new Set(set)])),
    groups: new Map([...mask.groups].map(([id, set]) => [id, new Set(set)])),
    links: new Set(mask.links),
});

/**
 * An item as the server wants it, or null for what is not an item.
 *
 * @param {object} raw {nodeid, kind, item}
 * @returns {{nodeid: string, kind: string, item: string}|null}
 */
const normalize = raw => {
    if (!raw || !KINDS.includes(raw.kind)) {
        return null;
    }
    const nodeid = String(raw.nodeid || ''), item = String(raw.item || '');
    if (raw.kind === 'link') {
        return !nodeid && item ? {nodeid: '', kind: 'link', item} : null;
    }
    if (!nodeid) {
        return null;
    }
    if (raw.kind === 'node' || raw.kind === 'known') {
        return {nodeid, kind: raw.kind, item: ''};
    }
    if (raw.kind === 'group') {
        return GROUPS.includes(item) ? {nodeid, kind: 'group', item} : null;
    }
    return item ? {nodeid, kind: 'iface', item} : null;
};

/**
 * Puts an item into a mask that is being built.
 *
 * @param {object} mask the mask, changed
 * @param {{nodeid: string, kind: string, item: string}} item a normalized item
 * @returns {boolean} whether it was not there yet
 */
const put = (mask, {nodeid, kind, item}) => {
    const into = (map, id, value) => {
        if (!map.has(id)) {
            map.set(id, new Set());
        }
        if (map.get(id).has(value)) {
            return false;
        }
        map.get(id).add(value);
        return true;
    };
    const once = (set, value) => (set.has(value) ? false : Boolean(set.add(value)));
    switch (kind) {
        case 'node': return once(mask.nodes, nodeid);
        case 'known': return once(mask.known, nodeid);
        case 'link': return once(mask.links, item);
        case 'group': return into(mask.groups, nodeid, item);
        default: return into(mask.ifaces, nodeid, item);
    }
};

/**
 * Adds items to a mask. The mask only grows.
 *
 * @param {object} mask the mask; not changed
 * @param {object[]} items {nodeid, kind, item}
 * @returns {{mask: object, added: object[]}} the new mask (the same one if nothing was new) and the items that were new
 */
export const maskAdd = (mask, items) => {
    const next = cloneMask(mask), added = [];
    for (const raw of items || []) {
        const item = normalize(raw);
        if (item && put(next, item)) {
            added.push(item);
        }
    }
    return {mask: added.length ? next : mask, added};
};

/**
 * The mask from the items the server keeps.
 *
 * @param {object[]} items {nodeid, kind, item}
 * @returns {object}
 */
export const maskFromItems = items => maskAdd(emptyMask(), items).mask;

/**
 * The items of a mask.
 *
 * @param {object} mask the mask
 * @returns {object[]} {nodeid, kind, item}
 */
export const maskItems = mask => {
    const out = [];
    mask.nodes.forEach(id => out.push({nodeid: id, kind: 'node', item: ''}));
    mask.known.forEach(id => out.push({nodeid: id, kind: 'known', item: ''}));
    mask.ifaces.forEach((set, id) => set.forEach(name => out.push({nodeid: id, kind: 'iface', item: name})));
    mask.groups.forEach((set, id) => set.forEach(group => out.push({nodeid: id, kind: 'group', item: group})));
    mask.links.forEach(id => out.push({nodeid: '', kind: 'link', item: id}));
    return out;
};

/**
 * Whether the server takes an item: an interface name it does not accept would fail the whole call.
 *
 * @param {object} item {nodeid, kind, item}
 * @returns {boolean}
 */
export const sendable = item => item.kind !== 'iface' || (item.item.length <= ITEM_MAX && IFACE_RE.test(item.item));

/* ---------------- what a command uncovers ---------------- */

/**
 * The groups of data a command uncovered.
 *
 * @param {{label: string, fragment: object}} result answer of consolefacts.interpret()
 * @returns {string[]}
 */
export const groupsOf = result => {
    if (LABEL_GROUPS[result.label]) {
        return LABEL_GROUPS[result.label];
    }
    const frag = result.fragment || {}, found = new Set();
    (frag.interfaces || []).forEach(i => {
        if ('admin' in i || 'oper' in i || 'mac' in i) {
            found.add('l1');
        }
        if ('switchport' in i) {
            found.add('l2');
        }
        if ('ipv4' in i || 'gateway' in i) {
            found.add('l3');
        }
    });
    if (frag.vlans) {
        found.add('l2');
    }
    if (frag.routes) {
        found.add('routes');
    }
    return GROUPS.filter(g => found.has(g));
};

/**
 * Names of the interfaces of the node a command showed, as the merged facts call them.
 *
 * The record of this one command is merged over the deciding facts exactly as the page merges them, so an interface
 * the device calls `Et0/1` and the snapshot `e0/1` is one, and a VPCS `eth0` is the `e0` of its port.
 *
 * @param {object} model model of the lab (not the one with the masked names)
 * @param {object|null} factsDoc deciding facts
 * @param {string} nodeId node id
 * @param {object} result answer of consolefacts.interpret()
 * @returns {string[]}
 */
const shownInterfaces = (model, factsDoc, nodeId, result) => {
    const one = addSeen(new Map(), nodeId, result, {at: 0, fp: null});
    const doc = mergeSeen(model, factsDoc, one);
    const node = doc && doc.nodes && doc.nodes[nodeId];
    return ((node && node.interfaces) || [])
        .filter(i => Array.isArray(i.seen) && i.seen.length)
        .map(i => i.name);
};

/**
 * What a command run in the console of a node uncovers.
 *
 * `seen` uncovers the groups of data the command shows. `fog` uncovers the node itself, whatever was run (its console
 * printed a prompt: that is the first output), and the interfaces and the groups of a command that shows data.
 *
 * @param {object} args
 * @param {string} args.mode seen or fog
 * @param {object} args.model model of the lab (every node under its own name)
 * @param {object|null} args.factsDoc deciding facts of the manager
 * @param {string} args.nodeId node whose console it was
 * @param {object|null} args.result answer of consolefacts.interpret(); null for a command nothing is taken from
 * @returns {object[]} items {nodeid, kind, item}; some may be in the mask already
 */
export const revealByCommand = ({mode, model, factsDoc, nodeId, result}) => {
    const items = [];
    if (mode === 'fog') {
        items.push({nodeid: nodeId, kind: 'node', item: ''});
    }
    if (!result || result.save || (mode !== 'seen' && mode !== 'fog')) {
        return items;
    }
    groupsOf(result).forEach(group => items.push({nodeid: nodeId, kind: 'group', item: group}));
    if (mode === 'fog') {
        shownInterfaces(model, factsDoc, nodeId, result)
            .forEach(name => items.push({nodeid: nodeId, kind: 'iface', item: name}));
    }
    return items;
};

/* ---------------- what the facts uncover ---------------- */

/**
 * The key of a link end: the port of a node.
 *
 * @param {{nodeId: string, adapter: number, port: number}} e end of a link
 * @returns {string}
 */
const endKey = e => e.nodeId + '|' + e.adapter + '/' + e.port;

/**
 * The interface of the facts that sits on a port of the lab.
 *
 * @param {object|null} doc facts document
 * @param {{nodeId: string, adapter: number, port: number}} end end of a link
 * @returns {object|null}
 */
const ifaceAt = (doc, end) => {
    const node = doc && doc.nodes && doc.nodes[end.nodeId];
    const list = (node && Array.isArray(node.interfaces)) ? node.interfaces : [];
    return list.find(i => i && i.port && i.port.adapter === end.adapter && i.port.port === end.port) || null;
};

/**
 * Whether an interface works: it is on and the line has not been seen down. Missing `oper` (saved configuration of an
 * IOU node) is not "down", as in the layer L1.
 *
 * @param {object|null} doc facts document
 * @param {string} nodeId node id
 * @param {object|null} iface interface of the facts
 * @returns {boolean}
 */
const works = (doc, nodeId, iface) => {
    const node = doc && doc.nodes && doc.nodes[nodeId];
    if (!iface || iface.admin !== 'up' || iface.oper === 'down') {
        return false;
    }
    return !(node && ['stopped', 'suspended'].includes(node.status));
};

/**
 * What the facts uncover in `fog`: a cable at an open interface that works, the node on its other end, and, for a
 * node without a console, its port on this end.
 *
 * "Works" is read from the merged facts or from the deciding ones, whichever says so: a student who raised an interface
 * and did not repeat the command uncovers the cable as soon as the deciding facts show it.
 *
 * @param {object} args
 * @param {object} args.model model of the lab
 * @param {object|null} args.doc merged facts (the deciding facts with what was seen)
 * @param {object|null} args.factsDoc deciding facts
 * @param {object} args.mask the mask
 * @returns {object[]} new items {nodeid, kind, item}
 */
export const revealByFacts = ({model, doc, factsDoc, mask}) => {
    const items = [], next = cloneMask(mask);
    const add = item => {
        if (put(next, item)) {
            items.push(item);
        }
    };
    for (const link of model.links) {
        if (mask.links.has(link.id)) {
            continue;
        }
        for (const [here, there] of [[link.a, link.b], [link.b, link.a]]) {
            const open = mask.ifaces.get(here.nodeId);
            const seen = ifaceAt(doc, here), decided = ifaceAt(factsDoc, here);
            const named = [seen, decided].find(i => i && open && open.has(i.name));
            if (!named || !(works(doc, here.nodeId, seen) || works(factsDoc, here.nodeId, decided))) {
                continue;
            }
            add({nodeid: '', kind: 'link', item: link.id});
            const far = model.nodes.get(there.nodeId);
            if (!mask.nodes.has(there.nodeId)) {
                add({nodeid: there.nodeId, kind: 'known', item: ''});
            }
            if (far && far.consoleType === 'none') {
                // A device without a console cannot be explored: the cable shows its port on this end.
                const port = ifaceAt(doc, there);
                if (port) {
                    add({nodeid: there.nodeId, kind: 'iface', item: port.name});
                }
            }
            break;
        }
    }
    return items;
};

/**
 * Uncovers what the facts uncover until nothing new comes: the page calls it after every new snapshot, every command
 * and every change of the mask.
 *
 * @param {object} args
 * @param {string} args.mode live, seen or fog; only `fog` has anything to uncover
 * @param {object} args.model model of the lab
 * @param {object|null} args.factsDoc deciding facts
 * @param {Map} args.seen what the student saw in the consoles (consolefacts store)
 * @param {object} args.mask the mask
 * @returns {{mask: object, added: object[]}}
 */
export const settleReveal = ({mode, model, factsDoc, seen, mask}) => {
    let current = mask;
    const added = [];
    if (mode !== 'fog') {
        return {mask, added};
    }
    // A cable uncovers a node, a node's port uncovers nothing more: two rounds are enough, the bound is for safety.
    for (let round = 0; round < 4; round++) {
        const doc = mergeSeen(model, factsDoc, seen);
        const step = maskAdd(current, revealByFacts({model, doc, factsDoc, mask: current}));
        if (!step.added.length) {
            break;
        }
        current = step.mask;
        added.push(...step.added);
    }
    return {mask: current, added};
};

/* ---------------- what the scheme shows ---------------- */

/**
 * Ids of the starting nodes of a fog.
 *
 * @param {object} model model of the lab
 * @param {string[]} names names of the nodes the activity starts the fog from
 * @returns {Set|null} null if there are none: every node starts as a silhouette (so it is if no name matches)
 */
export const startIds = (model, names) => {
    if (!Array.isArray(names) || !names.length) {
        return null;
    }
    const wanted = new Set(names), ids = new Set();
    model.nodes.forEach(n => {
        if (wanted.has(n.realName || n.name)) {
            ids.add(n.id);
        }
    });
    return ids.size ? ids : null;
};

/**
 * Whether a node in this state is named on the scheme.
 *
 * @param {string} state hidden, silhouette, known or open
 * @returns {boolean}
 */
const shown = state => state === 'known' || state === 'open';

/**
 * The mask applied to the lab: what the scheme shows and the facts it is built from.
 *
 * States of a node: `hidden` (not drawn at all), `silhouette` (a place with no name, type, facts or cables), `known`
 * (a cable that works leads to it: name and type), `open` (its console printed something: name, type and the groups of
 * data its commands showed). `seen` shows every node, cable and port and only filters the groups of data.
 *
 * @param {object} args
 * @param {string} args.mode live, seen or fog
 * @param {object} args.model model of the lab
 * @param {object|null} args.doc merged facts
 * @param {object} args.mask the mask
 * @param {string[]} [args.start] names of the starting nodes
 * @returns {{view: object|null, doc: object|null}} view null for `live`: nothing is filtered
 */
export const applyReveal = ({mode, model, doc, mask, start = []}) => {
    if (mode !== 'seen' && mode !== 'fog') {
        return {view: null, doc};
    }
    const fog = mode === 'fog', ids = fog ? startIds(model, start) : null;
    const states = new Map();
    model.nodes.forEach(n => {
        let state = 'open';
        if (fog) {
            if (mask.nodes.has(n.id)) {
                state = 'open';
            } else if (mask.known.has(n.id)) {
                state = 'known';
            } else {
                state = !ids || ids.has(n.id) ? 'silhouette' : 'hidden';
            }
        }
        states.set(n.id, state);
    });
    const links = new Set(), ends = new Set();
    model.links.forEach(l => {
        if (fog && !(mask.links.has(l.id) && shown(states.get(l.a.nodeId)) && shown(states.get(l.b.nodeId)))) {
            return;
        }
        links.add(l.id);
        [l.a, l.b].forEach(e => {
            const node = model.nodes.get(e.nodeId), iface = ifaceAt(doc, e);
            const open = mask.ifaces.get(e.nodeId);
            // The port of a device without a console is public; any other port is known once its interface is shown.
            if (!fog || (node && node.consoleType === 'none') || (iface && open && open.has(iface.name))) {
                ends.add(endKey(e));
            }
        });
    });
    const groups = new Map();
    states.forEach((state, id) => groups.set(id, state === 'open' ? new Set(mask.groups.get(id) || []) : new Set()));
    const view = {mode, states, links, ends, groups};
    return {view, doc: filterDoc(view, doc, mask)};
};

/**
 * The lab as the scheme draws it: no hidden node, only the cables that are uncovered.
 *
 * @param {object} model model of the lab
 * @param {object|null} view result of applyReveal(), null for `live`
 * @returns {object} the model itself without a view
 */
export const drawModel = (model, view) => {
    if (!view) {
        return model;
    }
    return {
        ...model,
        nodes: new Map([...model.nodes].filter(([id]) => view.states.get(id) !== 'hidden')),
        links: model.links.filter(l => view.links.has(l.id)),
    };
};

/**
 * The lab the layers and the findings are built from: only the nodes that are named on the scheme (`known` and `open`),
 * so that no text can name a silhouette, a node that is not found or a cable that is not uncovered.
 *
 * @param {object} model model of the lab
 * @param {object|null} view result of applyReveal(), null for `live`
 * @returns {object} the model itself without a view
 */
export const layerModel = (model, view) => {
    if (!view) {
        return model;
    }
    return {
        ...model,
        nodes: new Map([...model.nodes].filter(([id]) => shown(view.states.get(id)))),
        links: model.links.filter(l => view.links.has(l.id)),
    };
};

/**
 * The facts without what the mask keeps closed.
 *
 * @param {object} view result of applyReveal()
 * @param {object|null} doc facts document
 * @param {object} mask the mask
 * @returns {object|null} a new document; nodes that are not shown are not in it
 */
const filterDoc = (view, doc, mask) => {
    if (!doc || !doc.nodes) {
        return doc;
    }
    const nodes = {};
    Object.entries(doc.nodes).forEach(([id, src]) => {
        const state = view.states.get(id);
        if (shown(state) && src && typeof src === 'object') {
            const open = view.mode === 'seen' ? null : (mask.ifaces.get(id) || new Set());
            nodes[id] = filterNode(src, view.groups.get(id), open);
        }
    });
    return {...doc, nodes};
};

/**
 * Copies the fields that exist.
 *
 * @param {object} from source
 * @param {string[]} keys names of the fields
 * @param {object} into target, changed
 */
const pick = (from, keys, into) => {
    keys.forEach(k => {
        if (k in from) {
            into[k] = from[k];
        }
    });
};

/**
 * One node of the facts: the collection state, the open groups of data, the interfaces that are open.
 *
 * @param {object} src node of the facts
 * @param {Set} groups groups of data that are open on it
 * @param {Set|null} open names of the interfaces that are open; null: all of them
 * @returns {object}
 */
const filterNode = (src, groups, open) => {
    const out = {};
    pick(src, NODE_BASE, out);
    GROUPS.filter(g => groups.has(g)).forEach(g => pick(src, NODE_FIELDS[g], out));
    if (Array.isArray(src.interfaces)) {
        const list = src.interfaces.filter(i => i && (!open || open.has(i.name)));
        // A sub-interface finds its port through its parent: the parent comes along as a bare place, without data.
        const parents = new Set(list.map(i => i.parent).filter(Boolean));
        const bare = src.interfaces.filter(i => i && parents.has(i.name) && !list.includes(i));
        out.interfaces = src.interfaces.filter(i => list.includes(i) || bare.includes(i)).map(i => {
            const copy = {};
            pick(i, IDENTITY, copy);
            if (list.includes(i)) {
                GROUPS.filter(g => groups.has(g)).forEach(g => pick(i, IFACE_FIELDS[g], copy));
            }
            return copy;
        });
    }
    return out;
};
