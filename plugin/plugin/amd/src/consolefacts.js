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
 * Facts "seen in the console": what the student ran in a console of the lab page, parsed in the browser
 * (docs/live-state.md §8).
 *
 * Pure functions without DOM access. The deciding facts come from the manager's snapshot (docs/live-state.md §2–§6);
 * what the student saw in the console only makes the scheme react at once. Seen facts live on the page: they are not
 * sent, not stored and not graded.
 *
 * - `interpret()` recognizes the command (IOS abbreviations, `do`, filters) and parses its output into a fragment of
 *   a node in the format of §3;
 * - `addSeen()` folds fragments into the per-node store; `pruneSeen()` drops what a new snapshot has outdated;
 * - `mergeSeen()` merges the deciding facts with the store into the document the layers (facts.js) are built from.
 *
 * @module     mod_gns3lab/consolefacts
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

/** A list of VLANs that covers this range is written as "all". */
const VLAN_MAX = 4094;

/** Longest output taken for facts, in lines (§8.1). */
export const MAX_OUTPUT_LINES = 2000;

/**
 * Prompts as the terminal shows them left of the cursor (§8.1): they only tell that a command has ended.
 * The Linux prompt needs its trailing space, so that a line of output ending in "#" is not taken for one.
 */
export const PROMPTS = {
    ios: /^[A-Za-z0-9._-]+(\([A-Za-z0-9-]+\))?[>#] ?$/,
    linux: /\S[$#] $/,
    vpcs: /^[A-Za-z0-9_-]+> ?$/,
};

/**
 * The family of a device by its prompt.
 *
 * VPCS is told by the node, not by the prompt: its prompt looks like the user mode of IOS. Any other node is taken
 * for what its category says first and for the other family second: a virtual "switch" may run Linux.
 *
 * @param {string} hint kind of the node: ios, linux, vpcs or routeros (snippets.js kindOf)
 * @param {string} text the line left of the cursor
 * @returns {string|null} ios, linux, vpcs, or null if the line is not a prompt
 */
export const promptKind = (hint, text) => {
    if (hint === 'routeros') {
        return null; // No parser reads RouterOS output yet: its console adds nothing to the facts.
    }
    if (hint === 'vpcs') {
        return PROMPTS.vpcs.test(text) ? 'vpcs' : null;
    }
    const order = hint === 'ios' ? ['ios', 'linux'] : ['linux', 'ios'];
    return order.find(kind => PROMPTS[kind].test(text)) || null;
};

/* ---------------- names, numbers, small parsers ---------------- */

const IOS_TYPES = [
    ['Ethernet', 1], ['FastEthernet', 1], ['GigabitEthernet', 1], ['TenGigabitEthernet', 2], ['Loopback', 2],
    ['Vlan', 1], ['Serial', 1], ['Port-channel', 2], ['Tunnel', 2],
];

/**
 * Full name of an IOS interface: Et0/1 → Ethernet0/1, as in ios.config/1 (CONTRACTS §4.6).
 *
 * @param {string} name name as the device printed or the student typed it
 * @returns {string}
 */
export const iosName = name => {
    const m = /^([A-Za-z-]+?)\s*(\d[\d/.:]*)$/.exec(String(name).trim());
    if (!m) {
        return String(name).trim();
    }
    const type = m[1].toLowerCase();
    const full = IOS_TYPES.find(([word, min]) => type.length >= min && word.toLowerCase().startsWith(type));
    return (full ? full[0] : m[1]) + m[2];
};

/**
 * Kind of an IOS interface by the rules of ios.config/1; interfaces without a kind there (Tunnel, Port-channel …)
 * have none here either.
 *
 * @param {string} name full name
 * @returns {{kind: string, parent?: string, vlan?: number}|null}
 */
const iosKind = name => {
    let m = /^(?:Ethernet|FastEthernet|GigabitEthernet|TenGigabitEthernet|Serial)\d+(?:\/\d+)*$/.exec(name);
    if (m) {
        return {kind: 'physical'};
    }
    m = /^((?:Ethernet|FastEthernet|GigabitEthernet|TenGigabitEthernet|Serial)\d+(?:\/\d+)*)\.\d+$/.exec(name);
    if (m) {
        return {kind: 'subif', parent: m[1]};
    }
    m = /^Vlan(\d+)$/.exec(name);
    if (m) {
        return {kind: 'svi', vlan: +m[1]};
    }
    return /^Loopback\d+$/.test(name) ? {kind: 'loopback'} : null;
};

/**
 * Numbers of a list of VLANs as the device prints it: "1,10,20-30".
 *
 * @param {string} text the list
 * @returns {number[]|string|null} numbers by ascending, "all" if it covers every VLAN, null if it is not a list
 */
export const parseVlanList = text => {
    const s = String(text).trim().toLowerCase();
    if (s === 'none') {
        return [];
    }
    if (s === 'all') {
        return 'all';
    }
    if (!/^\d+(-\d+)?(,\s*\d+(-\d+)?)*,?$/.test(s)) {
        return null;
    }
    const set = new Set();
    for (const part of s.split(',').filter(Boolean)) {
        const [a, b] = part.split('-').map(Number);
        for (let v = a; v <= (b === undefined ? a : b) && v <= VLAN_MAX; v++) {
            set.add(v);
        }
    }
    return set.size >= VLAN_MAX ? 'all' : [...set].sort((x, y) => x - y);
};

const cidrOf = (ip, pre) => ip + '/' + (pre === undefined || pre === '' ? 32 : +pre);

const isIp = s => /^\d{1,3}(\.\d{1,3}){3}$/.test(s) && s.split('.').every(x => +x <= 255);

const MAC_RE = /^[0-9a-f]{2}(:[0-9a-f]{2}){5}$/i;

const macInt = mac => (typeof mac === 'string' && MAC_RE.test(mac) ? parseInt(mac.replace(/:/g, ''), 16) : null);

const lines2 = lines => lines.map(l => String(l).replace(/\s+$/, ''));

/* ---------------- recognizing the command ---------------- */

const words = text => text.trim().split(/\s+/).filter(Boolean);

/**
 * Number of tokens, from the index, that name one interface: "Ethernet0/1", "Et0/1" or "Ethernet 0/1".
 *
 * @param {string[]} toks tokens
 * @param {number} i index
 * @returns {number} 0, 1 or 2
 */
const interfaceTokens = (toks, i) => {
    const t = toks[i];
    if (!t) {
        return 0;
    }
    if (/^[a-z][a-z-]*\d[\d/.:]*$/i.test(t)) {
        return 1;
    }
    const type = /^(e|et|eth|ethernet|fa|fast\w*|gi|gig\w*|te|ten\w*|lo|loop\w*|vl|vlan|se|ser\w*|po|port\S*)$/i;
    return type.test(t) && toks[i + 1] && /^\d[\d/.:]*$/.test(toks[i + 1]) ? 2 : 0;
};

/**
 * Matches the words of a command, each as an abbreviation of the keyword that is not shorter than its minimum.
 *
 * @param {object} def {words: [[keyword, minimum]], intfAfter?: index of the word after which an interface may stand,
 *      exact?: nothing may follow}
 * @param {string[]} toks tokens of the command
 * @returns {{partial: boolean}|null} partial: the command asks for less than everything (an interface, a filter …)
 */
const matchWords = (def, toks) => {
    let i = 0;
    let partial = false;
    for (let w = 0; w < def.words.length; w++) {
        const [keyword, min] = def.words[w];
        const token = toks[i] === undefined ? '' : toks[i].toLowerCase();
        if (token.length < min || !keyword.startsWith(token)) {
            return null;
        }
        i++;
        if (def.intfAfter === w) {
            const n = interfaceTokens(toks, i);
            i += n;
            partial = partial || n > 0;
        }
    }
    if (i < toks.length && def.exact) {
        return null;
    }
    return {partial: partial || i < toks.length};
};

/* ---------------- parsers of the output: Cisco IOS ---------------- */

/**
 * `show ip interface brief`
 *
 * @param {string[]} lines output
 * @returns {object[]} interface fragments
 */
const parseIpBrief = lines => {
    const out = [];
    for (const line of lines2(lines)) {
        const m = /^(\S+)\s+(\S+)\s+(?:YES|NO)\s+(\S+)\s+(.+?)\s+(\S+)$/.exec(line);
        if (!m) {
            continue;
        }
        const name = iosName(m[1]);
        if (!iosKind(name)) {
            continue;
        }
        const down = /^administratively/i.test(m[4]);
        const up = !down && /^up$/i.test(m[4]) && /^up/i.test(m[5]);
        const frag = {name, admin: down ? 'down' : 'up', oper: up ? 'up' : 'down'};
        // An address without a mask cannot be written (L3 needs a prefix): the deciding facts keep theirs.
        if (m[2].toLowerCase() === 'unassigned') {
            frag.ipv4 = [];
        }
        out.push(frag);
    }
    return out;
};

/**
 * `show ip interface`
 *
 * @param {string[]} lines output
 * @returns {object[]} interface fragments
 */
const parseIpInterface = lines => {
    const out = [];
    let cur = null;
    for (const line of lines2(lines)) {
        const head = /^(\S+) is (administratively down|[^,]+), line protocol is (\S+)/.exec(line);
        if (head) {
            const name = iosName(head[1]);
            cur = iosKind(name) ? {name, admin: /^administratively/i.test(head[2]) ? 'down' : 'up', addrs: []} : null;
            if (cur) {
                cur.oper = cur.admin === 'up' && /^up$/i.test(head[2]) && /^up/i.test(head[3]) ? 'up' : 'down';
                out.push(cur);
            }
            continue;
        }
        if (!cur) {
            continue;
        }
        const addr = /^\s+(?:Internet address is|Secondary address)\s+(\d+\.\d+\.\d+\.\d+)\/(\d+)/i.exec(line);
        if (addr) {
            cur.addrs.push(cidrOf(addr[1], addr[2]));
        } else if (/^\s+Internet protocol processing disabled/i.test(line)) {
            cur.disabled = true;
        }
    }
    return out.map(f => {
        const {addrs, disabled, ...frag} = f;
        // A block cut off before its address lines (the student left the pager) says nothing about the address.
        if (addrs.length) {
            frag.ipv4 = addrs;
        } else if (disabled) {
            frag.ipv4 = [];
        }
        return frag;
    });
};

const STATUS_WORDS = 'connected|notconnect|disabled|err-disabled|inactive|monitoring|suspended|sfp-mismatch|faulty';

/**
 * `show interfaces status`
 *
 * @param {string[]} lines output
 * @returns {object[]} interface fragments
 */
const parseStatus = lines => {
    const out = [];
    const row = new RegExp('^(\\S+)\\s+(?:.*?\\s+)?(' + STATUS_WORDS + ')\\s+(\\d+|trunk|routed|unassigned|--)(?:\\s|$)', 'i');
    for (const line of lines2(lines)) {
        const m = row.exec(line);
        if (!m) {
            continue;
        }
        const name = iosName(m[1]);
        const info = iosKind(name);
        if (!info || info.kind !== 'physical') {
            continue;
        }
        const frag = {name};
        const status = m[2].toLowerCase();
        if (status === 'connected') {
            frag.admin = 'up';
            frag.oper = 'up';
        } else if (status === 'notconnect' || status === 'err-disabled' || status === 'inactive') {
            frag.admin = 'up';
            frag.oper = 'down';
        } else if (status === 'disabled') {
            frag.admin = 'down';
            frag.oper = 'down';
        }
        const vlan = m[3].toLowerCase();
        if (/^\d+$/.test(vlan)) {
            frag.switchport = {mode: 'access', vlan: +vlan};
        } else if (vlan === 'routed') {
            frag.switchport = null;
        } else if (vlan === 'trunk') {
            frag.switchport = {mode: 'trunk'};
        }
        out.push(frag);
    }
    return out;
};

/**
 * `show interfaces trunk`
 *
 * @param {string[]} lines output
 * @returns {object[]} interface fragments
 */
const parseTrunk = lines => {
    const ports = new Map();
    let section = '';
    let last = null;
    for (const line of lines2(lines)) {
        if (/^Port\s+Mode\s+Encapsulation/i.test(line)) {
            section = 'mode';
            continue;
        }
        if (/^Port\s+Vlans allowed on trunk/i.test(line)) {
            section = 'allowed';
            last = null;
            continue;
        }
        if (/^Port\s+/.test(line) || !line.trim()) {
            section = /^Port\s+/.test(line) ? '' : section;
            continue;
        }
        if (section === 'mode') {
            const m = /^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)$/.exec(line);
            if (m && m[4].toLowerCase() === 'trunking') {
                ports.set(iosName(m[1]), {'native': +m[5]});
            }
        } else if (section === 'allowed') {
            const m = /^(\S+)\s+(\S.*)$/.exec(line);
            if (/^\s/.test(line) && last) {
                last.text += line.trim();
            } else if (m) {
                last = {name: iosName(m[1]), text: m[2].trim()};
                const port = ports.get(last.name);
                if (port) {
                    port.list = last;
                }
            }
        }
    }
    const out = [];
    ports.forEach((info, name) => {
        const sw = {mode: 'trunk', 'native': info.native};
        const allowed = info.list ? parseVlanList(info.list.text) : null;
        if (allowed !== null) {
            sw.allowed = allowed;
        }
        out.push({name, switchport: sw});
    });
    return out;
};

/**
 * `show vlan brief`
 *
 * @param {string[]} lines output
 * @returns {{vlans: object[], interfaces: object[]}}
 */
const parseVlanBrief = lines => {
    const vlans = [];
    let last = null;
    for (const line of lines2(lines)) {
        const m = /^(\d{1,4})\s+(\S+)\s+(\S+)(?:\s+(.*))?$/.exec(line);
        if (m && /^(active|suspended|act\/\w+|sus\/\w+)$/i.test(m[3])) {
            last = {id: +m[1], name: m[2], ports: m[4] || ''};
            vlans.push(last);
        } else if (last && /^\s{8,}\S/.test(line)) {
            last.ports += ', ' + line.trim();
        }
    }
    const interfaces = [];
    for (const v of vlans) {
        for (const p of v.ports.split(',').map(s => s.trim()).filter(Boolean)) {
            const name = iosName(p);
            const info = iosKind(name);
            if (info && info.kind === 'physical') {
                interfaces.push({name, switchport: {mode: 'access', vlan: v.id}});
            }
        }
    }
    // The VLANs 1002-1005 are legacy ones the configuration of a device never lists.
    return {
        vlans: vlans.filter(v => v.id < 1002 || v.id > 1005).map(v => ({id: v.id, name: v.name})),
        interfaces,
    };
};

/* ---------------- spanning tree (IOS, docs/live-state.md §8.2) ---------------- */

const STP_ROLES = {root: 'root', desg: 'designated', altn: 'alternate', back: 'backup', mstr: 'master', dis: 'disabled',
    disb: 'disabled', none: 'disabled'};
const STP_STATES = {fwd: 'forwarding', blk: 'blocking', lis: 'listening', lrn: 'learning', dis: 'disabled', bkn: 'broken'};
const STP_MODES = {ieee: 'pvst', rstp: 'rapid-pvst', mstp: 'mst'};

/** "VLAN0010" → 10; anything else (MST0, a port name) → null. */
const stpVlan = text => {
    const m = /^VLAN0*(\d{1,4})$/i.exec(text || '');
    return m && +m[1] >= 1 && +m[1] <= VLAN_MAX ? +m[1] : null;
};

/** "aabb.cc00.0100" → "aa:bb:cc:00:01:00". */
const dottedMac = text => {
    const hex = String(text).toLowerCase().replace(/\./g, '');
    return /^[0-9a-f]{12}$/.test(hex) ? hex.match(/../g).join(':') : null;
};

/**
 * A row of a table of ports: `Et0/1  Altn BLK 100  128.2  Shr` (`show spanning-tree`) or the same with a VLAN
 * in the first column (`show spanning-tree interface`). A state with `*` (`BKN*`) is an inconsistency: broken.
 *
 * @param {string} line a line
 * @returns {{first: string, port: object}|null}
 */
const stpRow = line => {
    const m = /^(\S+)\s+(Root|Desg|Altn|Back|Mstr|Disb?|None)\s+(FWD|BLK|LIS|LRN|DIS|BKN)(\*?)\s*(\d+)\s+(\d+\.\d+)/i.exec(line);
    if (!m) {
        return null;
    }
    const port = {role: STP_ROLES[m[2].toLowerCase()], state: m[4] ? 'broken' : STP_STATES[m[3].toLowerCase()], cost: +m[5]};
    return {first: m[1], port};
};

/**
 * An empty result of the spanning tree commands.
 *
 * @returns {{ports: Map, trees: Map, mode: string|null, complete: Set}}
 */
const stpResult = () => ({ports: new Map(), trees: new Map(), mode: null, complete: new Set()});

/** Puts what a port does in a VLAN into the result. */
const stpPort = (res, name, vlan, info) => {
    const key = iosName(name);
    if (!res.ports.has(key)) {
        res.ports.set(key, {});
    }
    res.ports.get(key)[vlan] = Object.assign(res.ports.get(key)[vlan] || {}, info);
};

/** Puts what the bridge knows of a VLAN tree into the result. */
const stpTree = (res, vlan, info) => {
    res.trees.set(vlan, Object.assign(res.trees.get(vlan) || {}, info));
};

/**
 * `show spanning-tree [vlan …] [active]`: per VLAN the root, this bridge, and the role and state of each port.
 *
 * A VLAN whose block was shown to its end (no filter, no pager) lists all its ports: `complete` holds it, and a port
 * seen in it before but missing now has left the tree (down, or no longer in the VLAN).
 *
 * @param {string[]} lines output
 * @param {boolean} whole the output may be taken as complete
 * @returns {object} see stpResult()
 */
const parseStp = (lines, whole) => {
    const res = stpResult();
    let vlan = null;
    let id = null;
    let table = false;
    for (const line of lines2(lines)) {
        const head = /^(VLAN\d{1,4}|MST\d+)\s*$/i.exec(line);
        if (head) {
            vlan = stpVlan(head[1]);
            id = null;
            table = false;
            if (vlan !== null) {
                stpTree(res, vlan, {});
                if (whole) {
                    res.complete.add(vlan);
                }
            }
            continue;
        }
        if (vlan === null) {
            continue;
        }
        let m = /Spanning tree enabled protocol (\w+)/i.exec(line);
        if (m) {
            res.mode = STP_MODES[m[1].toLowerCase()] || res.mode;
            continue;
        }
        m = /^\s*(Root|Bridge) ID\s+Priority\s+(\d+)/i.exec(line);
        if (m) {
            id = m[1].toLowerCase() === 'root' ? 'root' : 'bridge';
            stpTree(res, vlan, {[id]: {priority: +m[2]}});
            continue;
        }
        if (/^Interface\s+Role\s+Sts/i.test(line)) {
            table = true;
            id = null;
            continue;
        }
        if (table) {
            const row = stpRow(line);
            if (row && iosKind(iosName(row.first))) {
                stpPort(res, row.first, vlan, row.port);
            }
            continue;
        }
        if (!id) {
            continue;
        }
        m = /^\s+Address\s+([0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})/i.exec(line);
        if (m && dottedMac(m[1])) {
            const tree = res.trees.get(vlan);
            tree[id] = {...tree[id], mac: dottedMac(m[1])};
        } else if (/This bridge is the root/i.test(line)) {
            stpTree(res, vlan, {'is_root': true, 'root_cost': 0});
        } else if ((m = /^\s+Cost\s+(\d+)/i.exec(line)) && id === 'root') {
            stpTree(res, vlan, {'is_root': false, 'root_cost': +m[1]});
        } else if ((m = /^\s+Port\s+\d+\s+\((\S+)\)/i.exec(line)) && id === 'root') {
            stpTree(res, vlan, {'is_root': false});
            stpPort(res, m[1], vlan, {role: 'root'});
        }
    }
    return res;
};

/**
 * `show spanning-tree interface X`: a row per VLAN the port is in.
 *
 * @param {string} name the interface of the command
 * @param {string[]} lines output
 * @returns {object} see stpResult()
 */
const parseStpInterface = (name, lines) => {
    const res = stpResult();
    for (const line of lines2(lines)) {
        const row = stpRow(line);
        const vlan = row && stpVlan(row.first);
        if (vlan !== null && row) {
            stpPort(res, name, vlan, row.port);
        }
    }
    return res;
};

/**
 * `show spanning-tree root`: the root of each VLAN, the cost to it and the root port (none on the root itself).
 *
 * @param {string[]} lines output
 * @returns {object} see stpResult()
 */
const parseStpRoot = lines => {
    const res = stpResult();
    for (const line of lines2(lines)) {
        const m = /^(VLAN\d{1,4})\s+(\d+)\s+([0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})\s+(\d+)\s+\d+\s+\d+\s+\d+(?:\s+(\S+))?\s*$/i
            .exec(line);
        const vlan = m && stpVlan(m[1]);
        if (vlan === null || !m) {
            continue;
        }
        const cost = +m[4];
        stpTree(res, vlan, {root: {priority: +m[2], mac: dottedMac(m[3])}, 'root_cost': cost, 'is_root': !m[5] && cost === 0});
        if (m[5]) {
            stpPort(res, m[5], vlan, {role: 'root'});
        }
    }
    return res;
};

/**
 * `show spanning-tree blockedports`: the ports that block, by VLAN.
 *
 * @param {string[]} lines output
 * @returns {object} see stpResult()
 */
const parseStpBlocked = lines => {
    const res = stpResult();
    let vlan = null;
    for (const line of lines2(lines)) {
        if (/^(Name|-{3,}|Number of blocked)/i.test(line.trim()) || !line.trim()) {
            vlan = /^\s/.test(line) && line.trim() ? vlan : null;
            continue;
        }
        const m = /^(\S+)\s+(.*)$/.exec(line);
        let list = '';
        if (m && stpVlan(m[1]) !== null) {
            vlan = stpVlan(m[1]);
            list = m[2];
        } else if (vlan !== null && /^\s/.test(line)) {
            list = line;
        }
        if (vlan === null) {
            continue;
        }
        list.split(',').map(p => p.trim()).filter(Boolean).forEach(p => {
            if (iosKind(iosName(p))) {
                stpPort(res, p, vlan, {state: 'blocking'});
            }
        });
    }
    return res;
};

/**
 * `show spanning-tree summary`: the mode, and which VLANs this bridge is the root of. The table at the end names
 * every VLAN of the bridge, so with the whole output a VLAN missing from "Root bridge for" is not rooted here.
 *
 * @param {string[]} lines output
 * @param {boolean} whole the output may be taken as complete
 * @returns {object} see stpResult()
 */
const parseStpSummary = (lines, whole) => {
    const res = stpResult();
    const rooted = new Set();
    const listed = new Set();
    let inRoots = false;
    for (const line of lines2(lines)) {
        let m = /Switch is in (\S+) mode/i.exec(line);
        if (m) {
            res.mode = ({'pvst': 'pvst', 'rapid-pvst': 'rapid-pvst', 'mst': 'mst'})[m[1].toLowerCase()] || res.mode;
            continue;
        }
        m = /^Root bridge for:?\s*(.*)$/i.exec(line);
        if (m || (inRoots && /^\s+VLAN\d/i.test(line))) {
            inRoots = true;
            (m ? m[1] : line).split(/[\s,]+/).map(stpVlan).filter(v => v !== null).forEach(v => rooted.add(v));
            continue;
        }
        inRoots = false;
        m = /^(VLAN\d{1,4})\s+\d+\s+\d+\s+\d+\s+\d+\s+\d+\s*$/i.exec(line);
        if (m && stpVlan(m[1]) !== null) {
            listed.add(stpVlan(m[1]));
        }
    }
    rooted.forEach(v => stpTree(res, v, {'is_root': true}));
    if (whole) {
        listed.forEach(v => {
            if (!rooted.has(v)) {
                stpTree(res, v, {'is_root': false});
            }
        });
    }
    return res;
};

/**
 * The fragment of facts from a result of the spanning tree commands.
 *
 * @param {object} res see stpResult()
 * @returns {{interfaces: object[], stp: object}}
 */
const stpFragment = res => {
    const interfaces = [];
    res.ports.forEach((vlans, name) => {
        if (iosKind(name)) {
            interfaces.push({name, stp: {vlans}});
        }
    });
    const stp = {vlans: Object.fromEntries(res.trees), complete: [...res.complete].sort((a, b) => a - b)};
    if (res.mode) {
        stp.mode = res.mode;
    }
    return {interfaces, stp};
};

/**
 * A spanning tree command of IOS: `show spanning-tree` and its forms the facts are taken from.
 *
 * @param {string[]} toks tokens of the command, without `do` and the filter
 * @returns {object|null} as recognizeIos()
 */
const recognizeStp = toks => {
    const word = (t, keyword, min) => !!t && t.length >= min && keyword.startsWith(t.toLowerCase());
    if (!word(toks[0], 'show', 2) || !word(toks[1], 'spanning-tree', 4)) {
        return null;
    }
    const rest = toks.slice(2);
    const label = 'show spanning-tree';
    if (!rest.length) {
        return {label, parse: (lines, whole) => stpFragment(parseStp(lines, whole))};
    }
    if (word(rest[0], 'vlan', 1) && rest.length === 2 && /^[\d,-]+$/.test(rest[1])) {
        return {label, parse: (lines, whole) => stpFragment(parseStp(lines, whole))};
    }
    if (word(rest[0], 'active', 2) && rest.length === 1) {
        // Only the active ports: the blocks are not complete.
        return {label, parse: lines => stpFragment(parseStp(lines, false))};
    }
    if (word(rest[0], 'interface', 2) && interfaceTokens(rest, 1) && 1 + interfaceTokens(rest, 1) === rest.length) {
        const name = iosName(rest.slice(1).join(''));
        return {label: label + ' interface', partial: true, parse: lines => stpFragment(parseStpInterface(name, lines))};
    }
    if (word(rest[0], 'root', 2) && rest.length === 1) {
        return {label: label + ' root', parse: lines => stpFragment(parseStpRoot(lines))};
    }
    if (word(rest[0], 'blockedports', 2) && rest.length === 1) {
        return {label: label + ' blockedports', parse: lines => stpFragment(parseStpBlocked(lines))};
    }
    if (word(rest[0], 'summary', 2) && rest.length === 1) {
        return {label: label + ' summary', parse: (lines, whole) => stpFragment(parseStpSummary(lines, whole))};
    }
    return null;
};

/* ---------------- parsers of the output: Linux and VPCS ---------------- */

/**
 * `ip addr` in the full and in the brief (-br) form.
 *
 * @param {string[]} lines output
 * @param {{v6only: boolean}} opts options of the command
 * @returns {object[]} interface fragments
 */
const parseIpAddr = (lines, opts) => {
    const out = [];
    let cur = null;
    for (const line of lines2(lines)) {
        let m = /^\d+:\s+([^\s:@]+)(?:@(\S+?))?:\s+<([^>]*)>/.exec(line);
        if (m) {
            const flags = m[3].split(',');
            cur = {name: m[1], admin: flags.includes('UP') ? 'up' : 'down', oper: flags.includes('LOWER_UP') ? 'up' : 'down'};
            if (m[2]) {
                cur.parent = m[2];
            }
            if (!opts.v6only) {
                cur.ipv4 = [];
            }
            out.push(cur);
            continue;
        }
        m = /^\s+link\/ether\s+([0-9a-f:]{17})\b/i.exec(line);
        if (m && cur) {
            cur.mac = m[1].toLowerCase();
            continue;
        }
        m = /^\s+inet\s+(\d+\.\d+\.\d+\.\d+)(?:\/(\d+))?/.exec(line);
        if (m && cur && cur.ipv4) {
            cur.ipv4.push(cidrOf(m[1], m[2]));
            continue;
        }
        // Brief form: "ens3  UP  10.0.10.11/24 fe80::1/64".
        m = /^([^\s:@]+)(?:@(\S+))?\s+(UP|DOWN|UNKNOWN|LOWERLAYERDOWN|NOTPRESENT|DORMANT|TESTING)(?:\s+(.*))?$/.exec(line);
        if (m && !cur) {
            const state = m[3];
            const frag = {name: m[1]};
            if (m[2]) {
                frag.parent = m[2];
            }
            if (state === 'UP') {
                frag.admin = 'up';
                frag.oper = 'up';
            } else if (state === 'UNKNOWN') {
                frag.admin = 'up';
            } else {
                frag.oper = 'down';
            }
            if (!opts.v6only) {
                frag.ipv4 = (m[4] || '').split(/\s+/).filter(a => /^\d+\.\d+\.\d+\.\d+(\/\d+)?$/.test(a))
                    .map(a => cidrOf(...a.split('/')));
            }
            out.push(frag);
        }
    }
    return out;
};

/**
 * `ip route`
 *
 * @param {string[]} lines output
 * @returns {object[]} routes in the format of §3.3
 */
const parseRoutes = lines => {
    const out = [];
    for (const line of lines2(lines)) {
        if (!line || /^\s/.test(line)) {
            continue;
        }
        const toks = words(line);
        if (toks[0] === 'unicast') {
            toks.shift();
        }
        const dst = toks[0];
        if (!dst || (dst !== 'default' && !/^\d+\.\d+\.\d+\.\d+(\/\d+)?$/.test(dst))) {
            continue;
        }
        const route = {dst: dst === 'default' ? dst : cidrOf(...dst.split('/')), proto: 'boot'};
        for (let k = 1; k < toks.length - 1; k++) {
            if (toks[k] === 'via' && isIp(toks[k + 1])) {
                route.via = toks[k + 1];
            } else if (toks[k] === 'dev') {
                route.dev = toks[k + 1];
            } else if (toks[k] === 'proto') {
                route.proto = toks[k + 1];
            } else if (toks[k] === 'metric' && /^\d+$/.test(toks[k + 1])) {
                route.metric = +toks[k + 1];
            }
        }
        if (route.dev) {
            out.push(route);
        }
    }
    return out;
};

/**
 * `show ip` of VPCS, one PC: a block of "NAME : value" lines, or a table for `show ip all`.
 *
 * @param {string[]} lines output
 * @returns {object[]} interface fragments
 */
const parseVpcs = lines => {
    const frag = {name: 'eth0'};
    let found = false;
    const gw = value => (value && isIp(value) && value !== '0.0.0.0' ? value : null);
    const text = lines2(lines);
    for (const line of text) {
        const m = /^(IP\/MASK|GATEWAY|MAC)\s*:\s*(.*)$/.exec(line);
        if (!m) {
            continue;
        }
        const value = m[2].trim();
        if (m[1] === 'IP/MASK') {
            const a = /^(\d+\.\d+\.\d+\.\d+)(?:\/(\d+)|\s+(\d+\.\d+\.\d+\.\d+))?$/.exec(value);
            if (a) {
                const ones = mask => mask.split('.').reduce((n, x) => n + (+x).toString(2).replace(/0/g, '').length, 0);
                let pre = 0;
                if (a[2] !== undefined) {
                    pre = +a[2];
                } else if (a[3]) {
                    pre = ones(a[3]);
                }
                frag.ipv4 = a[1] === '0.0.0.0' ? [] : [cidrOf(a[1], pre)];
                found = true;
            }
        } else if (m[1] === 'GATEWAY') {
            frag.gateway = gw(value) || undefined;
            found = true;
        } else if (m[1] === 'MAC' && MAC_RE.test(value)) {
            frag.mac = value.toLowerCase();
            found = true;
        }
    }
    if (!found) {
        const i = text.findIndex(l => /^NAME\s+IP\/MASK\s+GATEWAY/.test(l));
        const cols = /^(\S+)\s+(\d+\.\d+\.\d+\.\d+)\/(\d+)\s+(\S+)\s+([0-9a-f:]{17})/i;
        const row = i >= 0 && text.slice(i + 1).map(l => cols.exec(l)).find(Boolean);
        if (row) {
            frag.ipv4 = row[2] === '0.0.0.0' ? [] : [cidrOf(row[2], row[3])];
            frag.gateway = gw(row[4]) || undefined;
            frag.mac = row[5].toLowerCase();
            found = true;
        }
    }
    if (!found) {
        return [];
    }
    if (frag.gateway === undefined) {
        delete frag.gateway;
    }
    return [frag];
};

/* ---------------- the commands ---------------- */

const IOS_COMMANDS = [
    {label: 'show ip interface brief', words: [['show', 2], ['ip', 2], ['interface', 3], ['brief', 1]],
        parse: lines => ({interfaces: parseIpBrief(lines)})},
    {label: 'show ip interface', words: [['show', 2], ['ip', 2], ['interface', 3]],
        parse: lines => ({interfaces: parseIpInterface(lines)})},
    {label: 'show interfaces status', words: [['show', 2], ['interfaces', 3], ['status', 3]], intfAfter: 1,
        parse: lines => ({interfaces: parseStatus(lines)})},
    {label: 'show interfaces trunk', words: [['show', 2], ['interfaces', 3], ['trunk', 2]], intfAfter: 1,
        parse: lines => ({interfaces: parseTrunk(lines)})},
    {label: 'show vlan brief', words: [['show', 2], ['vlan', 2], ['brief', 1]], parse: lines => parseVlanBrief(lines), lists: true},
    {label: 'write memory', words: [['write', 2], ['memory', 1]], exact: true, save: true},
    {label: 'write memory', words: [['write', 2]], exact: true, save: true},
    {label: 'copy running-config startup-config', words: [['copy', 3], ['running-config', 3], ['startup-config', 3]],
        exact: true, save: true},
];

/**
 * A command of Cisco IOS: abbreviations, the `do` of the configuration mode, a filter after a pipe.
 *
 * @param {string} text the command line
 * @returns {object|null}
 */
const recognizeIos = text => {
    // A filter (| include …) shows a part of the output.
    const [body, ...filters] = text.split('|');
    const toks = words(body.replace(/^\s*do\s+/i, ''));
    for (const def of IOS_COMMANDS) {
        const m = matchWords(def, toks);
        if (m) {
            return {...def, partial: m.partial || filters.length > 0};
        }
    }
    const stp = recognizeStp(toks);
    return stp ? {...stp, partial: !!stp.partial || filters.length > 0} : null;
};

/**
 * A command of VPCS.
 *
 * @param {string} text the command line
 * @returns {object|null}
 */
const recognizeVpcs = text => {
    const toks = words(text);
    const m = matchWords({words: [['show', 2], ['ip', 2]], exact: true}, toks)
        || matchWords({words: [['show', 2], ['ip', 2], ['all', 1]], exact: true}, toks);
    return m ? {label: 'show ip', partial: false, parse: lines => ({interfaces: parseVpcs(lines)})} : null;
};

const SHOW_VERBS = ['show', 'list', 'lst', 'sh', 's', 'l', 'ls'];

/**
 * A command of Linux: `ip addr` and `ip route` in their spellings (options, `sudo`, a verb, a filter).
 *
 * @param {string} text the command line
 * @returns {object|null}
 */
const recognizeLinux = text => {
    if (/[|;&<>`$()]/.test(text)) {
        return null;
    }
    const toks = words(text);
    if (toks[0] === 'sudo') {
        toks.shift();
    }
    if (toks[0] !== 'ip') {
        return null;
    }
    let i = 1;
    const options = [];
    while (toks[i] && toks[i].startsWith('-')) {
        options.push(toks[i++].replace(/^--?/, '').toLowerCase());
    }
    const object = (toks[i] || '').toLowerCase();
    const rest = toks.slice(i + 1).map(t => t.toLowerCase());
    const opts = {v6only: options.includes('6')};
    // What follows the verb narrows the listing down: `show dev ens3`, `default`, an address.
    const filtered = rest.length > (SHOW_VERBS.includes(rest[0]) ? 1 : 0);
    if (object && 'address'.startsWith(object)) {
        if (/^(add|del|delete|flush|change|replace|save|restore|help|showdump)$/.test(rest[0])) {
            return null;
        }
        return {label: 'ip addr', partial: filtered, parse: lines => ({interfaces: parseIpAddr(lines, opts)})};
    }
    if (object && 'route'.startsWith(object)) {
        if (/^(add|append|replace|change|del|delete|get|flush|save|restore|showdump|help)$/.test(rest[0])) {
            return null;
        }
        return {
            label: 'ip route', partial: filtered || opts.v6only, parse: lines => ({interfaces: [], routes: parseRoutes(lines)}),
        };
    }
    return null;
};

/**
 * What a command line of a device is, if it is one whose output is taken for facts.
 *
 * @param {string} kind ios, linux or vpcs
 * @param {string} command what the student typed after the prompt
 * @returns {{label: string, partial: boolean, save?: boolean, parse?: Function}|null}
 */
export const recognize = (kind, command) => {
    const text = String(command).trim();
    const byKind = {ios: recognizeIos, vpcs: recognizeVpcs, linux: recognizeLinux};
    return byKind[kind] ? byKind[kind](text) : null;
};

/**
 * Facts from the output of a command.
 *
 * @param {string} kind ios, linux or vpcs
 * @param {string} command what the student typed after the prompt
 * @param {string[]} lines the output, without the command line and the prompt
 * @param {boolean} [paged] the pager (--More--) stopped the output: it may be incomplete
 * @returns {{save: true, ok: boolean}|{kind: string, label: string, partial: boolean, fragment: object}|null} null:
 *      not a command of the first stage, or its output gave nothing
 */
export const interpret = (kind, command, lines, paged = false) => {
    const cmd = recognize(kind, command);
    if (!cmd || !Array.isArray(lines)) {
        return null;
    }
    if (cmd.save) {
        return {save: true, ok: lines.some(l => /\[OK\]/.test(l))};
    }
    const partial = cmd.partial || !!paged;
    const parsed = cmd.parse(lines, !partial);
    const fragment = {interfaces: parsed.interfaces || []};
    // The spanning tree is merged by VLAN: a VLAN the output did not show keeps what was seen of it.
    if (parsed.stp && (Object.keys(parsed.stp.vlans).length || parsed.stp.mode)) {
        fragment.stp = parsed.stp;
    }
    // The lists are replaced as a whole, and only by a full output (§8.3).
    if (!partial && parsed.vlans && parsed.vlans.length) {
        fragment.vlans = parsed.vlans;
    }
    if (!partial && parsed.routes && parsed.routes.length) {
        fragment.routes = parsed.routes;
    }
    if (!fragment.interfaces.length && !fragment.vlans && !fragment.routes && !fragment.stp) {
        return null;
    }
    return {kind, label: cmd.label, partial, fragment};
};

/* ---------------- the store of what was seen, and the merge ---------------- */

/** Fields of an interface a command can give. */
const IF_FIELDS = ['admin', 'oper', 'ipv4', 'mac', 'gateway', 'switchport', 'parent', 'stp'];

/**
 * The spanning tree of a port merged by VLAN: what `seen` tells of a VLAN goes over what `base` tells of it, and the
 * VLANs in `drop` (shown in full since) lose what `base` told.
 *
 * @param {object|undefined} base `stp` of the interface before
 * @param {object|undefined} seen `stp` seen now
 * @param {number[]} [drop] VLANs whose old entries are outdated
 * @returns {{vlans: object}}
 */
const mergePortStp = (base, seen, drop = []) => {
    const vlans = {};
    Object.entries((base && base.vlans) || {}).forEach(([v, info]) => {
        if (!drop.includes(+v)) {
            vlans[v] = {...info};
        }
    });
    Object.entries((seen && seen.vlans) || {}).forEach(([v, info]) => {
        vlans[v] = {...vlans[v], ...info};
    });
    return {...(base || {}), vlans};
};

/**
 * The spanning tree of a node merged by VLAN.
 *
 * @param {object|undefined} base `stp` of the node before
 * @param {object} seen `stp` seen now
 * @returns {{mode?: string, vlans: object}}
 */
const mergeNodeStp = (base, seen) => {
    const vlans = {...((base && base.vlans) || {})};
    Object.entries(seen.vlans || {}).forEach(([v, info]) => {
        vlans[v] = {...vlans[v], ...info};
    });
    const mode = seen.mode || (base && base.mode);
    return mode ? {mode, vlans} : {vlans};
};

const keyOf = name => String(name).toLowerCase();

/**
 * The key the interfaces of the deciding facts and the seen ones are matched by: IOS names in any spelling are one.
 *
 * @param {string} kind ios, linux or vpcs
 * @param {string} name name of the interface
 * @returns {string}
 */
const matchKey = (kind, name) => (kind === 'ios' ? iosName(name) : String(name)).toLowerCase();

/**
 * Stable text of a value: the same for objects whose keys come in another order.
 *
 * @param {*} v value
 * @returns {string}
 */
const stable = v => {
    if (Array.isArray(v)) {
        return '[' + v.map(stable).join(',') + ']';
    }
    if (v && typeof v === 'object') {
        return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ':' + stable(v[k])).join(',') + '}';
    }
    return JSON.stringify(v === undefined ? null : v);
};

/**
 * Fingerprint of a node in a snapshot of the manager: its fields as text.
 *
 * `collected_at` is left out because it grows with every collection of some collectors (ios.config/1) without the
 * facts changing; so is `error`, which comes and goes with a failed collection and leaves the facts as they were.
 *
 * @param {object|null} factsDoc snapshot, gns3lab.facts/1
 * @param {string} nodeId node id
 * @returns {string|null} null if the snapshot has no such node
 */
export const fingerprint = (factsDoc, nodeId) => {
    const node = factsDoc && factsDoc.nodes && factsDoc.nodes[nodeId];
    if (!node || typeof node !== 'object') {
        return null;
    }
    const rest = {...node};
    delete rest.collected_at;
    delete rest.error;
    delete rest.l1_ports; // Live L1 has its own priority; it must not erase console IP/VLAN observations.
    return stable(rest);
};

/**
 * Adds what a command showed to the store.
 *
 * @param {Map} seen node id → record; not changed
 * @param {string} nodeId node id
 * @param {{kind: string, label: string, fragment: object}} result answer of interpret()
 * @param {{at: number, fp: string|null}} info when (ms) and the fingerprint of the node in the deciding facts now
 * @returns {Map} the store with the new record
 */
export const addSeen = (seen, nodeId, result, {at, fp}) => {
    const next = new Map(seen);
    const old = next.get(nodeId);
    // The deciding facts of the node changed since the last command: what was seen before is outdated.
    const rec = old && old.fp === fp
        ? {...old, ifs: {...old.ifs}, order: [...old.order]}
        : {fp, ifs: {}, order: [], vlans: null, routes: null, stp: null, stpFull: []};
    rec.at = at;
    rec.kind = result.kind;
    rec.command = result.label;
    const stp = result.fragment.stp;
    if (stp) {
        // A VLAN shown in full lists all its ports: what was seen of it before on the other ports is outdated.
        const full = stp.complete || [];
        if (full.length) {
            rec.order.forEach(key => {
                if (rec.ifs[key].stp) {
                    rec.ifs[key] = {...rec.ifs[key], stp: mergePortStp(rec.ifs[key].stp, null, full)};
                }
            });
            rec.stpFull = [...new Set([...(rec.stpFull || []), ...full])];
        }
        rec.stp = mergeNodeStp(rec.stp, stp);
    }
    for (const frag of result.fragment.interfaces) {
        const key = keyOf(frag.name);
        const before = rec.ifs[key];
        if (!before) {
            rec.order.push(key);
        }
        const cur = Object.assign({}, before, frag);
        if (frag.stp) {
            cur.stp = mergePortStp(before && before.stp, frag.stp);
        }
        if (frag.switchport && before && before.switchport && before.switchport.mode === frag.switchport.mode) {
            // A trunk named by `show interfaces status` keeps what `show interfaces trunk` told about it.
            cur.switchport = Object.assign({}, before.switchport, frag.switchport);
        }
        rec.ifs[key] = cur;
    }
    if (result.fragment.vlans) {
        rec.vlans = result.fragment.vlans;
    }
    if (result.fragment.routes) {
        rec.routes = result.fragment.routes;
    }
    next.set(nodeId, rec);
    return next;
};

/**
 * Drops what a new snapshot has outdated: the records of the nodes whose deciding facts are no longer those
 * the record was made against (§8.3).
 *
 * @param {Map} seen node id → record
 * @param {object|null} factsDoc the new snapshot
 * @returns {Map} the store without the outdated records
 */
export const pruneSeen = (seen, factsDoc) => {
    const next = new Map();
    seen.forEach((rec, nodeId) => {
        if (rec.fp === fingerprint(factsDoc, nodeId)) {
            next.set(nodeId, rec);
        }
    });
    return next.size === seen.size ? seen : next;
};

/**
 * The port of GNS3 an interface seen on the device belongs to.
 *
 * By name (IOU: Ethernet0/1 is adapter 0 port 1; Docker), by MAC (QEMU: the MAC of adapter N is the `mac_address`
 * of the node + N, CONTRACTS §4.6), the only port of a VPCS. Nothing is guessed by order.
 *
 * @param {object} node node of the model
 * @param {object} iface interface with `name` and `mac`
 * @param {Map} macs MAC → how many of the seen interfaces have it
 * @returns {{adapter: number, port: number}|null}
 */
const portOf = (node, iface, macs) => {
    const ports = Array.isArray(node.ports) ? node.ports : [];
    const named = ports.find(p => p.name === iface.name || p.short_name === iface.name);
    if (named) {
        return {adapter: named.adapter_number, port: named.port_number};
    }
    const base = macInt(node.props && node.props.mac_address);
    const mac = macInt(iface.mac);
    if (base !== null && mac !== null && macs.get(iface.mac) === 1 && mac >= base && mac - base < 64) {
        const adapter = mac - base;
        if (!ports.length || ports.some(p => p.adapter_number === adapter)) {
            return {adapter, port: 0};
        }
    }
    if (node.type === 'vpcs') {
        if (ports.length === 1) {
            return {adapter: ports[0].adapter_number, port: ports[0].port_number};
        }
        return ports.length ? null : {adapter: 0, port: 0};
    }
    return null;
};

/**
 * A switchport as the facts want it: what a command left out comes from the deciding facts, then from the defaults.
 *
 * @param {object} seenPort what was seen
 * @param {object|undefined} known switchport of the deciding facts
 * @returns {object}
 */
const switchportOf = (seenPort, known) => {
    if (seenPort.mode !== 'trunk') {
        return {...seenPort};
    }
    const base = known && known.mode === 'trunk' ? known : {};
    const sw = {mode: 'trunk', allowed: 'all', 'native': 1};
    ['allowed', 'native'].forEach(k => {
        if (seenPort[k] !== undefined) {
            sw[k] = seenPort[k];
        } else if (base[k] !== undefined) {
            sw[k] = base[k];
        }
    });
    return sw;
};

/**
 * Kind of an interface the deciding facts do not have, by the kind of the device.
 *
 * @param {string} kind ios, linux or vpcs
 * @param {object} s the interface as seen
 * @returns {{kind: string}|null} null for what the output does not describe enough (a VLAN sub-interface of Linux)
 */
const kindOnDevice = (kind, s) => {
    if (kind === 'ios') {
        return iosKind(s.name);
    }
    if (s.name === 'lo') {
        return {kind: 'loopback'};
    }
    return s.parent ? null : {kind: 'physical'};
};

/**
 * Lays the fields a command gave over an interface of the merged facts.
 *
 * @param {object} item the interface, changed
 * @param {object} s the interface as seen
 * @param {object|null} known the interface in the deciding facts
 * @returns {string[]} the fields that came from the console
 */
const applyFields = (item, s, known, full = []) => {
    const given = [];
    for (const field of IF_FIELDS) {
        if (field === 'parent' || (s[field] === undefined && !(field === 'switchport' && s.switchport === null))) {
            continue;
        }
        if (field === 'stp') {
            item.stp = mergePortStp(known && known.stp, s.stp, full);
        } else if (field === 'switchport') {
            if (s.switchport === null) {
                delete item.switchport;
            } else {
                item.switchport = switchportOf(s.switchport, known && known.switchport);
            }
        } else {
            item[field] = Array.isArray(s[field]) ? [...s[field]] : s[field];
        }
        given.push(field);
    }
    return given;
};

/**
 * The node of the merged facts: the deciding facts of the node, over them what was seen.
 *
 * @param {object} node node of the model
 * @param {object|null} base the node in the deciding facts
 * @param {object} rec the record of the store
 * @returns {object}
 */
const mergeNode = (node, base, rec) => {
    const merged = {status: 'started', ...(base || {})};
    const list = Array.isArray(merged.interfaces) ? [...merged.interfaces] : [];
    const index = new Map(list.map((i, k) => [matchKey(rec.kind, i.name), k]));
    // A VLAN sub-interface has the MAC of its parent: only the others tell a port by their MAC.
    const macs = new Map();
    rec.order.forEach(key => {
        const s = rec.ifs[key];
        if (s.mac && !s.parent) {
            macs.set(s.mac, (macs.get(s.mac) || 0) + 1);
        }
    });
    for (const key of rec.order) {
        const s = rec.ifs[key];
        let at = index.get(matchKey(rec.kind, s.name));
        // What the device calls an interface may differ from the snapshot (VPCS eth0 against e0): the port is the same.
        let info = null;
        let port = null;
        if (at === undefined) {
            info = kindOnDevice(rec.kind, s);
            // A sub-interface needs its tag, which the output does not tell.
            if (!info || info.kind === 'subif') {
                continue;
            }
            port = info.kind === 'physical' ? portOf(node, s, macs) : null;
            const same = port ? list.findIndex(i => i.port && i.port.adapter === port.adapter && i.port.port === port.port) : -1;
            at = same >= 0 ? same : undefined;
        }
        const known = at === undefined ? null : list[at];
        const item = known ? {...known} : {name: s.name, ...info, ...(port ? {port} : {})};
        const given = applyFields(item, s, known, rec.stpFull || []);
        item.seen = given;
        if (known) {
            list[at] = item;
        } else {
            index.set(matchKey(rec.kind, s.name), list.length);
            list.push(item);
        }
    }
    // A VLAN of the spanning tree shown in full: the ports the console did not list in it are no longer in it.
    if (rec.stpFull && rec.stpFull.length) {
        list.forEach((i, k) => {
            if (i.stp && i.stp.vlans && !(Array.isArray(i.seen) && i.seen.includes('stp'))) {
                list[k] = {...i, stp: mergePortStp(i.stp, null, rec.stpFull)};
            }
        });
    }
    merged.interfaces = list;
    if (rec.stp) {
        merged.stp = mergeNodeStp(merged.stp, rec.stp);
    }
    if (rec.vlans) {
        merged.vlans = rec.vlans.map(v => ({...v}));
    }
    if (rec.routes) {
        merged.routes = rec.routes.map(r => ({...r}));
    }
    merged.seen = {at: rec.at, command: rec.command};
    return merged;
};

/**
 * The facts the layers are built from: the deciding facts of the manager with what the student saw over them.
 *
 * What a command gave replaces the same fields of the interface (matched by name); the rest of the interface and
 * the interfaces the command did not show stay. `vlans` and `routes` are replaced by a full output only. A node
 * with seen facts gets `seen: {at, command}`, and its interfaces `seen: [fields]`, for the details card.
 *
 * @param {object} model model of the lab (model.js buildModel)
 * @param {object|null} factsDoc snapshot of the manager, gns3lab.facts/1
 * @param {Map} seen node id → record of the store
 * @returns {object|null} a new document; the snapshot itself if nothing was seen
 */
export const mergeSeen = (model, factsDoc, seen) => {
    if (!seen || !seen.size || !model) {
        return factsDoc;
    }
    const nodes = {...((factsDoc && factsDoc.nodes) || {})};
    let any = false;
    seen.forEach((rec, nodeId) => {
        const node = model.nodes.get(nodeId);
        if (node) {
            nodes[nodeId] = mergeNode(node, nodes[nodeId] || null, rec);
            any = true;
        }
    });
    return any ? {schema: 'gns3lab.facts/1', rev: 0, ...(factsDoc || {}), nodes} : factsDoc;
};
