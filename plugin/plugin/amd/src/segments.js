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
 * Segments of a lab definition gns3lab.lab/2: the addresses they give and the facts the lab will show after the
 * preconfiguration.
 *
 * Pure functions without DOM access. The lab manager is the authority: it checks the segments and compiles the
 * preconfiguration (manager/docs/CONTRACTS.md §2.6, lab constructor README §3 and §4). This module only repeats its
 * rules for addresses (the last working address is the gateway, members take the lowest free numbers) so that the
 * editor can preview the result before the lab is checked, and maps the paths of its findings to the editor.
 *
 * @module     mod_gns3lab/segments
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

export const FORMAT_2 = "gns3lab.lab/2";
export const NAME_RE = /^[A-Za-z][A-Za-z0-9_]{0,31}$/;
const SUBNET_RE = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/;
const PORT_NAMES = {iou: "Ethernet{adapter}/{port}", qemu: "Ethernet{adapter}", vpcs: "Ethernet0"};

/**
 * Whether the lab manager can preconfigure a node of this image: VPCS, or Linux with a known network mechanism.
 *
 * @param {object|null} image image of the catalog
 * @returns {boolean}
 */
export const isPreconfig = image => !!image && image.preconfig === true;

/**
 * The language string that tells why a node cannot join a segment, by the category of its image: a router is picked as
 * the gateway of the segment, a switch is found on the way by the cables, anything else lacks the network mechanism.
 *
 * @param {object|null} image image of the catalog
 * @returns {string} key of the string in mod_gns3lab, the node name is its parameter
 */
export const notMemberKey = image => {
    const category = (image || {}).category;
    return category === "router" ? "ed_nopreconfig_router" : category === "switch" ? "ed_nopreconfig_switch" : "ed_nopreconfig";
};

/**
 * Formats a 32-bit address.
 *
 * @param {number} v address
 * @returns {string}
 */
export const ipText = v => [Math.floor(v / 16777216) % 256, Math.floor(v / 65536) % 256, Math.floor(v / 256) % 256,
    v % 256].join(".");

/**
 * Parses "A.B.C.D/N" the way the lab manager does: a network without host bits, /8 to /30, no loopback, link-local,
 * multicast, reserved or 0.x.x.x network.
 *
 * @param {*} text value of the subnet field
 * @returns {{base: number, prefix: number, size: number, usable: number}|null}
 */
export const parseSubnet = text => {
    const m = typeof text === "string" ? SUBNET_RE.exec(text) : null;
    if (!m) {
        return null;
    }
    const o = m.slice(1, 5).map(Number), prefix = Number(m[5]);
    if (o.some(x => x > 255) || prefix < 8 || prefix > 30 || m.slice(1, 5).some(s => s.length > 1 && s[0] === "0")) {
        return null;
    }
    const base = ((o[0] * 256 + o[1]) * 256 + o[2]) * 256 + o[3], size = 2 ** (32 - prefix);
    const unicast = o[0] !== 0 && o[0] !== 127 && o[0] < 224 && !(o[0] === 169 && o[1] === 254);
    if (!unicast || base % size !== 0) {
        return null;
    }
    return {base, prefix, size, usable: size - 2};
};

/**
 * Addresses of a segment: the gateway takes its host or the last working address, explicit hosts keep theirs, the other
 * members take the lowest free numbers from .1.
 *
 * @param {{base: number, prefix: number, usable: number}} net subnet from parseSubnet
 * @param {object|null} gateway segment gateway ({host} optional) or null
 * @param {Array} members members of the segment ({host} optional)
 * @returns {{gateway: string|null, members: Array<string|null>, problems: Array<{where: (string|number), code: string}>}}
 */
export const plan = (net, gateway, members) => {
    const problems = [], taken = new Map(), size = net.usable;
    const out = {gateway: null, members: members.map(() => null), problems};
    if (size < 1 || members.length + (gateway ? 1 : 0) > size) {
        problems.push({where: "members", code: "subnet_too_small"});
        return out;
    }
    const inRange = h => Number.isInteger(h) && h >= 1 && h <= size;
    if (gateway) {
        const h = gateway.host === undefined || gateway.host === null ? size : gateway.host;
        if (inRange(h)) {
            taken.set(h, "gateway");
            out.gateway = ipText(net.base + h);
        } else {
            problems.push({where: "gateway", code: "host_out_of_range"});
        }
    }
    const number = new Array(members.length).fill(null);
    members.forEach((m, j) => {
        if (m.host === undefined || m.host === null) {
            return;
        }
        if (!inRange(m.host)) {
            problems.push({where: j, code: "host_out_of_range"});
        } else if (taken.has(m.host)) {
            problems.push({where: j, code: "host_in_use"});
        } else {
            taken.set(m.host, j);
            number[j] = m.host;
        }
    });
    let next = 1;
    members.forEach((m, j) => {
        if (m.host !== undefined && m.host !== null) {
            return;
        }
        while (taken.has(next)) {
            next++;
        }
        taken.set(next, j);
        number[j] = next;
    });
    out.members = number.map(n => n === null ? null : ipText(net.base + n));
    return out;
};

/**
 * Addresses of all members of all segments that have a usable subnet.
 *
 * @param {object} lab lab with segments
 * @returns {Array<{segment: number, member: number, node: string, adapter: number, port: number, ip: string,
 *     prefix: number, gateway: string|null}>}
 */
export const assignments = lab => {
    const out = [];
    (lab.segments || []).forEach((seg, i) => {
        const net = parseSubnet(seg.subnet && seg.subnet.fixed);
        if (!net) {
            return;
        }
        const p = plan(net, seg.gateway || null, seg.members || []);
        (seg.members || []).forEach((m, j) => {
            if (p.members[j]) {
                out.push({segment: i, member: j, node: m.node, adapter: m.adapter, port: m.port, ip: p.members[j],
                    prefix: net.prefix, gateway: p.gateway});
            }
        });
    });
    return out;
};

/**
 * Port name by the image template of the catalog.
 *
 * @param {object|null} image image of the catalog
 * @param {number} adapter adapter number
 * @param {number} port port number
 * @returns {string}
 */
export const portText = (image, adapter, port) => String((image && image.port_name) || PORT_NAMES[image && image.kind]
    || "{adapter}/{port}").replace("{adapter}", adapter).replace("{port}", port);

/**
 * The facts document the lab will show right after the preconfiguration (gns3lab.facts/1, docs/live-state.md §2).
 *
 * Nodes are keyed by their name, which the editor uses as node id in the preview topology. The name of an interface
 * is the port name of GNS3: the name inside the guest is not known before the lab runs.
 *
 * @param {object} lab lab with nodes and segments
 * @param {Map} images image catalog by id
 * @returns {{schema: string, ttl: number, nodes: object}}
 */
export const expectedFacts = (lab, images) => {
    const imageOf = name => {
        const n = (lab.nodes || []).find(x => x.name === name);
        return n ? images.get(n.image) || null : null;
    };
    const nodes = {};
    assignments(lab).forEach(a => {
        const node = nodes[a.node] = nodes[a.node] || {name: a.node, status: "started", interfaces: []};
        const iface = {name: portText(imageOf(a.node), a.adapter, a.port), kind: "physical",
            port: {adapter: a.adapter, port: a.port}, admin: "up", oper: "up", ipv4: [`${a.ip}/${a.prefix}`]};
        if (a.gateway) {
            iface.gateway = a.gateway;
        }
        node.interfaces.push(iface);
    });
    return {schema: "gns3lab.facts/1", ttl: 0, nodes};
};

/**
 * Gateways by router port: port key to [{segment, vlan}] (vlan 0: the segment has none). One router port may be the gateway
 * of several segments with different VLANs (router-on-a-stick, manager/docs/CONTRACTS.md §2.6).
 *
 * @param {object} lab lab with segments
 * @returns {Map}
 */
export const gatewayUse = lab => {
    const m = new Map();
    (lab.segments || []).forEach((seg, i) => {
        if (seg.gateway) {
            const key = portKey(seg.gateway);
            m.set(key, (m.get(key) || []).concat([{segment: i, vlan: seg.vlan ? +seg.vlan.fixed : 0}]));
        }
    });
    return m;
};

/**
 * Facts for the preview in the editor: the expected facts of the members plus the switches and the gateways as the
 * segments intend them (docs: lab constructor README §6, §15.4). The preview shows the intended lab whoever sets the
 * devices (the lab manager sets IOL, the student the rest): a switch port towards a segment member is an access port in
 * the VLAN of the segment (1 without a VLAN), a port towards another switch is a trunk, a port towards a gateway with
 * VLANs is a trunk of them; a gateway has the address of its segment on the port, or on the subinterface of the VLAN when
 * a switch is behind the port. Without them the viewer would report every subnet as torn apart on L2.
 *
 * @param {object} lab lab with nodes, links and segments
 * @param {Map} images image catalog by id
 * @returns {{schema: string, ttl: number, nodes: object}}
 */
export const previewFacts = (lab, images) => {
    const facts = expectedFacts(lab, images);
    const category = name => {
        const n = (lab.nodes || []).find(x => x.name === name);
        const image = n && images.get(n.image);
        return image ? image.category : "";
    };
    const taken = takenPorts(lab), gateways = gatewayUse(lab);
    const imageOf = name => {
        const n = (lab.nodes || []).find(x => x.name === name);
        return n ? images.get(n.image) || null : null;
    };
    const nodeFacts = name => facts.nodes[name] = facts.nodes[name] || {name, status: "started", interfaces: []};
    const switchportToward = peer => {
        if (category(peer.node) === "switch") {
            return {mode: "trunk", allowed: "all"};
        }
        const use = gateways.get(portKey(peer));
        if (use && use.some(u => u.vlan)) {
            const allowed = new Set(use.map(u => u.vlan || 1));
            return {mode: "trunk", allowed: [...allowed].sort((x, y) => x - y), native: 1};
        }
        const owner = taken.get(portKey(peer));
        const seg = owner ? lab.segments[owner.segment] : null;
        return {mode: "access", vlan: seg && seg.vlan ? seg.vlan.fixed : 1};
    };
    (lab.links || []).forEach(l => {
        [[l.a, l.b], [l.b, l.a]].forEach(([own, peer]) => {
            if (category(own.node) !== "switch") {
                return;
            }
            nodeFacts(own.node).interfaces.push({name: portText(imageOf(own.node), own.adapter, own.port), kind: "physical",
                port: {adapter: own.adapter, port: own.port}, admin: "up", oper: "up", switchport: switchportToward(peer)});
        });
    });
    (lab.segments || []).forEach(seg => {
        const net = seg.gateway ? parseSubnet(seg.subnet && seg.subnet.fixed) : null;
        const address = net ? plan(net, seg.gateway, seg.members || []).gateway : null;
        if (!address) {
            return;
        }
        const gw = seg.gateway, name = portText(imageOf(gw.node), gw.adapter, gw.port), node = nodeFacts(gw.node);
        const cable = (lab.links || []).find(l => portKey(l.a) === portKey(gw) || portKey(l.b) === portKey(gw));
        const far = cable ? (portKey(cable.a) === portKey(gw) ? cable.b : cable.a) : null;
        let own = node.interfaces.find(i => i.name === name);
        if (!own) {
            own = {name, kind: "physical", port: {adapter: gw.adapter, port: gw.port}, admin: "up", oper: "up"};
            node.interfaces.push(own);
        }
        const text = `${address}/${net.prefix}`;
        if (seg.vlan && far && category(far.node) === "switch") {
            node.interfaces.push({name: `${name}.${+seg.vlan.fixed}`, kind: "subif", parent: name, dot1q: +seg.vlan.fixed,
                admin: "up", oper: "up", ipv4: [text]});
        } else {
            own.ipv4 = [text];
        }
    });
    return facts;
};

/**
 * A .gns3-like document of the lab for the model of the viewer (the same as lab_definition::to_topology in PHP).
 *
 * @param {object} lab lab with nodes and links
 * @param {Map} images image catalog by id
 * @returns {object}
 */
export const previewDocument = (lab, images) => {
    const nodes = (lab.nodes || []).map(n => {
        const image = images.get(n.image) || null;
        const adapters = Number.isInteger(n.adapters) ? n.adapters : (image && image.adapters ? image.adapters.default : 1);
        const per = Math.max(1, (image && image.ports_per_adapter) || 1), ports = [];
        for (let a = 0; a < Math.min(adapters, 64); a++) {
            for (let p = 0; p < Math.min(per, 64); p++) {
                const name = portText(image, a, p);
                ports.push({adapter_number: a, port_number: p, name,
                    short_name: name.replace(/^GigabitEthernet/, "Gi").replace(/^FastEthernet/, "Fa").replace(/^Ethernet/, "e")});
            }
        }
        return {node_id: n.name, name: n.name, node_type: image ? image.kind : "", symbol: image ? image.symbol || "" : "",
            console_type: image ? image.console_type : "none", x: n.x, y: n.y, width: 60, height: 45, ports, properties: {}};
    });
    const links = (lab.links || []).map((l, i) => ({link_id: "link-" + i, nodes: [l.a, l.b].map(e => ({
        node_id: e.node, adapter_number: e.adapter, port_number: e.port}))}));
    return {name: "", topology: {nodes, links, drawings: []}};
};

/**
 * Where in the editor a finding of the lab manager belongs, by the path it gives.
 *
 * @param {string} path path such as segments[0].members[2], segments[1].gateway or nodes[3].adapters
 * @returns {{kind: string, i: number, part?: string, j?: number}|null}
 */
export const placeOf = path => {
    const s = /^segments\[(\d+)\](?:\.(members|gateway|vlan|subnet|name)(?:\[(\d+)\])?)?/.exec(path || "");
    if (s) {
        const place = {kind: "segment", i: +s[1]};
        if (s[2]) {
            place.part = s[2];
        }
        if (s[3] !== undefined) {
            place.j = +s[3];
        }
        return place;
    }
    const m = /^(nodes|links)\[(\d+)\]/.exec(path || "");
    return m ? {kind: m[1] === "nodes" ? "node" : "link", i: +m[2]} : null;
};

/**
 * Port key of a node.
 *
 * @param {{node: string, adapter: number, port: number}} e member or gateway
 * @returns {string}
 */
export const portKey = e => `${e.node}|${e.adapter}/${e.port}`;

/**
 * Ports of a lab that are taken by a segment, as a map from port key to {segment, member} (member -1: the gateway).
 *
 * @param {object} lab lab with segments
 * @returns {Map}
 */
export const takenPorts = lab => {
    const m = new Map();
    (lab.segments || []).forEach((seg, i) => {
        if (seg.gateway) {
            m.set(portKey(seg.gateway), {segment: i, member: -1});
        }
        (seg.members || []).forEach((x, j) => m.set(portKey(x), {segment: i, member: j}));
    });
    return m;
};

/**
 * Layer 2 of a lab as the cables make it: the other end of every port and the switches (category `switch`) that flood.
 *
 * @param {object} lab lab with nodes and links
 * @param {Map} images image catalog by id
 * @returns {{peer: Map, ports: Map, switches: Set}}
 */
const fabricOf = (lab, images) => {
    const peer = new Map(), ports = new Map();
    (lab.links || []).forEach(l => {
        peer.set(portKey(l.a), l.b);
        peer.set(portKey(l.b), l.a);
        [l.a, l.b].forEach(e => ports.set(e.node, (ports.get(e.node) || []).concat([e])));
    });
    const switches = new Set((lab.nodes || []).filter(n => {
        const image = images.get(n.image);
        return image && image.category === "switch";
    }).map(n => n.name));
    return {peer, ports, switches};
};

/**
 * The switches on the path of a segment: those between its end ports. Switches reached from the first end port by the
 * cables, minus spurs (no end port cabled to the switch and one cable at most to the rest); cycles stay whole. The same
 * rule as `Fabric.tree` of the lab manager.
 *
 * @param {object} lab lab with nodes, links and segments
 * @param {Map} images image catalog by id
 * @param {object} seg segment
 * @returns {Set<string>} names of the switches
 */
export const pathSwitches = (lab, images, seg) => {
    const {peer, ports, switches} = fabricOf(lab, images);
    const ends = (seg.gateway ? [seg.gateway] : []).concat(seg.members || []);
    const first = ends.map(portKey).find(k => peer.has(k));
    const passed = new Set(), stack = first ? [peer.get(first)] : [];
    while (stack.length) {
        const e = stack.pop();
        if (switches.has(e.node) && !passed.has(e.node)) {
            passed.add(e.node);
            (ports.get(e.node) || []).filter(o => portKey(o) !== portKey(e)).forEach(o => stack.push(peer.get(portKey(o))));
        }
    }
    const cables = [];
    passed.forEach(name => (ports.get(name) || []).forEach(o => {
        const far = peer.get(portKey(o));
        if (passed.has(far.node)) {
            cables.push([o.node, far.node]);
        }
    }));
    const attached = new Set(ends.map(e => peer.get(portKey(e))).filter(Boolean).map(e => e.node));
    const keep = new Set(passed);
    for (let spur = true; spur;) {
        const degree = name => cables.filter(c => c[0] === name && keep.has(c[1])).length;
        const out = [...keep].find(name => !attached.has(name) && degree(name) <= 1);
        spur = out !== undefined;
        if (spur) {
            keep.delete(out);
        }
    }
    return keep;
};

/**
 * Names of the nodes the lab manager sets up for a segment: the IOL switches (image of kind `iou`, category `switch`) on
 * its path and the IOL router (`iou`, category `router`) that is its gateway (docs: lab constructor README §15.2.1).
 * Other switches and routers stay the student's job.
 *
 * @param {object} lab lab with nodes, links and segments
 * @param {Map} images image catalog by id
 * @param {number} i index of the segment
 * @returns {string[]} node names in the order of the lab
 */
export const managedNodes = (lab, images, i) => {
    const seg = (lab.segments || [])[i];
    if (!seg) {
        return [];
    }
    const iol = name => {
        const n = (lab.nodes || []).find(x => x.name === name), image = n && images.get(n.image);
        return !!image && image.kind === "iou" ? image : null;
    };
    const names = new Set([...pathSwitches(lab, images, seg)].filter(iol));
    if (seg.gateway && iol(seg.gateway.node) && iol(seg.gateway.node).category === "router") {
        names.add(seg.gateway.node);
    }
    return (lab.nodes || []).map(n => n.name).filter(name => names.has(name));
};
