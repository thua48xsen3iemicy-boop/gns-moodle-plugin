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
 * Lab editor of the site lab library.
 *
 * Edits a lab definition gns3lab.lab/1 or /2: nodes from the image catalog of the lab manager, links between
 * their ports and, when the manager has the lab constructor, segments (a broadcast domain with a subnet, an optional
 * VLAN and gateway) of the ports of preconfigurable nodes. The definition is checked by the lab manager (validate),
 * saved as a draft or published through the external functions of the plugin. A lab without segments is sent as
 * lab/1. Node x, y are the top left corner of the node, as in .gns3. Texts and boxes with captions on the scheme
 * (`annotations`, lab_definition::ANNOTATIONS) are the plugin's alone: the students see them, the manager never does.
 *
 * @module     mod_gns3lab/editor
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';
import Notification from 'core/notification';
import {buildModel, ICONS} from 'mod_gns3lab/model';
import {derive, hostPart} from 'mod_gns3lab/facts';
import * as Seg from 'mod_gns3lab/segments';
import {init as initImages} from 'mod_gns3lab/editorimages';
import {store} from 'mod_gns3lab/dom';
import {create as createChecks} from 'mod_gns3lab/checksform';
import {create as createSetup} from 'mod_gns3lab/setupform';

const NS = "http://www.w3.org/2000/svg";
const FORMAT = "gns3lab.lab/1";
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/;
const MAX_VLAN = 4094;
const W = 60, H = 45;
const KMIN = 0.2, KMAX = 3;
const GRID = 10;
const UNDO_MAX = 50;
/* иконка и префикс имени по категории образа из каталога менеджера */
const ICON = {router: "router", switch: "switch", host: "pc", pc: "pc", server: "server", firewall: "firewall", cloud: "cloud"};
const PREFIX = {router: "R", switch: "SW", host: "PC", pc: "PC", server: "SRV", firewall: "FW", cloud: "NET"};
const CATEGORY_ORDER = ["router", "switch", "firewall", "server", "host", "pc", "cloud"];
/* texts and boxes on the scheme: the same limits and drawing as lab_definition::annotations() and ::drawings() */
const NOTE_MAX = 200, NOTE_TEXT_MAX = 500, NOTE_SIZE = [8, 72], NOTE_BOX = [20, 5000], NOTE_FILL = "#1e88e5";
const NOTE_COLORS = ["#d32f2f", "#f57c00", "#f9a825", "#388e3c", "#00897b", "#1e88e5", "#3949ab", "#8e24aa", "#6d4c41",
  "#757575"];

const esc = s => String(s === null || s === undefined ? "" : s)
  .replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const snap = v => Math.round(v / GRID) * GRID;
const shortName = s => s.replace(/^GigabitEthernet/, "Gi").replace(/^FastEthernet/, "Fa").replace(/^Ethernet/, "e");

/**
 * Starts the editor.
 *
 * @param {string} prefix unique id prefix of the template
 */
export const init = (prefix) => {
  const root = document.getElementById(prefix + "-app");
  if (!root || root.dataset.ready) {
    return;
  }
  root.dataset.ready = "1";
  const D = JSON.parse(root.dataset.json || "{}");
  const S = D.strings || {};
  const $ = r => root.querySelector(`[data-region="${r}"]`);
  const svg = $("svg"), vp = $("vp"), props = $("props"), palette = $("palette");
  const lyr = {links: $("links"), nodes: $("nodes"), labels: $("labels"), names: $("names")};
  const images = new Map((D.images || []).map(i => [i.id, i]));
  const maxNodes = Math.min(100, (D.limits && D.limits.max_nodes) || 100);
  /* the manager announces the lab constructor: segments can be edited (otherwise stored ones are only kept) */
  const withSegments = !!D.labconstructor;
  /* the manager also checks labs (CONTRACTS §2.9): segments set up by the student and the `checks` document */
  const withChecks = withSegments && !!D.labchecks;
  /* the checks are a setting of the course activity (docs/autochecks.md §12): the lab no longer has them */
  const activityChecks = withChecks && !!D.activitychecks;
  /* the manager also runs the teacher's scripts in the guests (CONTRACTS §2.9 `scripts`, `check-scripts`) */
  const withScripts = withChecks && !activityChecks && !!D.labscripts;
  /* a script may wait for earlier ones and take their values (CONTRACTS §2.9, `check-script-needs`) */
  const withNeeds = withScripts && !!D.labscriptneeds;
  /* the manager runs the teacher's own setup steps of a lab (CONTRACTS §2.6 `setup`, `lab-setup`) */
  const withSetup = withSegments && !!D.labsetup;

  const def = D.definition || {};
  const st = {
    lab: {
      nodes: Array.isArray(def.nodes) ? def.nodes : [],
      links: Array.isArray(def.links) ? def.links : [],
      segments: Array.isArray(def.segments) ? def.segments : [],
      checks: def.checks && typeof def.checks === "object" && !Array.isArray(def.checks) ? def.checks : null,
      setup: Array.isArray(def.setup) ? def.setup : [],
      annotations: Array.isArray(def.annotations) ? def.annotations : [],
    },
    sel: null,
    adding: false,
    pick: null,
    preview: false,
    pv: null,
    warnings: [],
    linking: false,
    from: null,
    undo: [],
    dirty: false,
    busy: false,
    problems: [],
    resources: null,
    version: D.version,
    status: D.status,
  };
  const view = {k: 1, tx: 0, ty: 0};

  /* ---------- тексты ---------- */
  const str = (key, a) => {
    let s = S[key] || key;
    if (a !== undefined && a !== null) {
      s = typeof a === "object" ? s.replace(/\{\$a->(\w+)\}/g, (m, k) => (k in a ? a[k] : ""))
        : s.split("{$a}").join(String(a));
    }
    return s;
  };

  /* ---------- модель ---------- */
  const imageOf = n => images.get(n.image) || null;
  const uplinkOf = n => {
    const img = imageOf(n);
    return img && Number.isInteger(img.uplink_port) ? img.uplink_port
      : (Number.isInteger(n.uplink_port) ? n.uplink_port : null);
  };
  const adapterOffset = n => uplinkOf(n) === null ? 0 : 1;
  // Restore the image's reserved port when opening older drafts without this node field.
  st.lab.nodes.forEach(n => {
    const uplink = uplinkOf(n);
    if (uplink !== null) {
      n.uplink_port = uplink;
    }
  });
  const range = (n, key) => {
    const img = imageOf(n);
    return img && img[key] && typeof img[key] === "object" ? img[key] : null;
  };
  const adaptersOf = n => {
    if (Number.isInteger(n.adapters)) {
      return n.adapters;
    }
    const r = range(n, "adapters");
    return r ? r.default : 1;
  };
  const perAdapter = n => Math.max(1, (imageOf(n) || {}).ports_per_adapter || 1);
  const portName = (n, a, p) => String((imageOf(n) || {}).port_name || "{adapter}/{port}")
    .replace("{adapter}", a).replace("{port}", p);
  const category = n => (imageOf(n) || {}).category || "";
  /* icon of the image category; a node may override it (lab_definition::ICON, plugin only, it only draws the node) */
  const imageIcon = n => adapterOffset(n) ? "cloud" : ICON[category(n)] || "server";
  const iconOf = n => ICONS.includes(n.icon) ? n.icon : imageIcon(n);
  const byName = name => st.lab.nodes.find(n => n.name === name) || null;
  const portsOf = n => {
    const out = [];
    for (let a = 0; a < Math.min(adaptersOf(n), 64); a++) {
      if (a === uplinkOf(n)) {
        continue;
      }
      for (let p = 0; p < Math.min(perAdapter(n), 64); p++) {
        out.push({adapter: a, port: p});
      }
    }
    return out;
  };
  /* какой линк занимает порт ноды: "имя|a/p" → индекс линка */
  const usedPorts = () => {
    const m = new Map();
    st.lab.links.forEach((l, i) => ["a", "b"].forEach(s => m.set(`${l[s].node}|${l[s].adapter}/${l[s].port}`, i)));
    return m;
  };
  const freePorts = (n, exceptLink) => {
    const used = usedPorts();
    return portsOf(n).filter(p => {
      const i = used.get(`${n.name}|${p.adapter}/${p.port}`);
      return i === undefined || i === exceptLink;
    });
  };
  const nextName = img => {
    const base = Number.isInteger(img.uplink_port) ? "ISP" : (PREFIX[img.category] || "N");
    for (let i = 1; ; i++) {
      if (!byName(base + i)) {
        return base + i;
      }
    }
  };
  /* a lab with segments, checks or setup steps is lab/2; the checks and the steps are kept even when this manager does
     not take them, and the checks stay out of the lab when they belong to the activities */
  const definition = (checks = activityChecks ? null : st.lab.checks, setup = st.lab.setup) => JSON.stringify(Object.assign(
    st.lab.segments.length || checks || setup.length
      ? Object.assign({format: Seg.FORMAT_2, nodes: st.lab.nodes, links: st.lab.links, segments: st.lab.segments},
        checks ? {checks} : {}, setup.length ? {setup} : {})
      : {format: FORMAT, nodes: st.lab.nodes, links: st.lab.links},
    st.lab.annotations.length ? {annotations: st.lab.annotations} : {}));

  /* ---------- изменения и отмена ---------- */
  const snapshot = () => JSON.stringify(st.lab);
  const pushUndo = before => {
    st.undo.push(before);
    if (st.undo.length > UNDO_MAX) {
      st.undo.shift();
    }
  };
  const change = fn => {
    pushUndo(snapshot());
    fn();
    st.dirty = true;
    st.problems = [];
    st.warnings = [];
    st.resources = null;
    render();
  };
  const undo = () => {
    if (!st.undo.length) {
      return;
    }
    st.lab = JSON.parse(st.undo.pop());
    st.sel = null;
    st.from = null;
    st.adding = false;
    st.pick = null;
    st.dirty = true;
    st.problems = [];
    st.warnings = [];
    st.resources = null;
    render();
  };

  const addNode = (img, cx, cy) => {
    if (st.lab.nodes.length >= maxNodes) {
      toast(str("ed_maxnodes", maxNodes), true);
      return;
    }
    /* the manager takes at most one node with an external port per lab (too_many_uplinks) */
    const uplinked = Number.isInteger(img.uplink_port) && st.lab.nodes.find(o => uplinkOf(o) !== null);
    if (uplinked) {
      toast(str("ed_oneuplink", uplinked.name), true);
      return;
    }
    change(() => {
      const n = {name: nextName(img), image: img.id, x: snap(cx - W / 2), y: snap(cy - H / 2)};
      if (img.adapters && typeof img.adapters === "object") {
        /* число адаптеров определяет порты и линки: храним его, а не полагаемся на умолчание образа */
        n.adapters = img.adapters.default;
      }
      if (Number.isInteger(img.uplink_port)) {
        n.uplink_port = img.uplink_port;
      }
      st.lab.nodes.push(n);
      st.sel = {kind: "node", i: st.lab.nodes.length - 1};
    });
  };
  const deleteSel = () => {
    const s = st.sel;
    if (!s) {
      return;
    }
    change(() => {
      if (s.kind === "node") {
        const name = st.lab.nodes[s.i].name;
        st.lab.nodes.splice(s.i, 1);
        if (st.from === name) {
          st.from = null;
        }
        st.lab.links = st.lab.links.filter(l => l.a.node !== name && l.b.node !== name);
        /* a deleted device leaves its segments; a segment keeps existing, the manager reports an empty one */
        st.lab.segments.forEach(seg => {
          seg.members = seg.members.filter(m => m.node !== name);
          if (seg.gateway && seg.gateway.node === name) {
            delete seg.gateway;
          }
        });
      } else if (s.kind === "note") {
        st.lab.annotations.splice(s.i, 1);
      } else if (s.kind === "segment") {
        st.lab.segments.splice(s.i, 1);
        st.adding = false;
        st.pick = null;
      } else {
        st.lab.links.splice(s.i, 1);
      }
      st.sel = null;
    });
  };
  /* a text or a box with a caption, in the middle of the view */
  const addNote = kind => {
    if (st.lab.annotations.length >= NOTE_MAX) {
      toast(str("ed_annmax", NOTE_MAX), true);
      return;
    }
    let [cx, cy] = viewCenter();
    if (kind === "text") {
      /* a text lies under the devices: put it where no device covers it */
      const covered = (x, y) => st.lab.nodes.some(n => x < n.x + W + 10 && x + 120 > n.x - 10 && y < n.y + H + 30 && y + 30 > n.y);
      for (let k = 0; k < 40 && covered(snap(cx - 40), snap(cy - 10)); k++) {
        cy += GRID * 3;
      }
    }
    change(() => {
      const a = kind === "box"
        ? {kind, x: snap(cx - 160), y: snap(cy - 100), w: 320, h: 200, text: str("ed_annboxdefault"), size: 14, fill: NOTE_FILL,
          bold: true}
        : {kind, x: snap(cx - 40), y: snap(cy - 10), text: str("ed_anntextdefault"), size: 14};
      st.lab.annotations.push(a);
      st.sel = {kind: "note", i: st.lab.annotations.length - 1};
      st.linking = false;
      st.from = null;
    });
  };
  const tapLinking = i => {
    const n = st.lab.nodes[i];
    if (st.from === null || st.from === n.name) {
      st.from = st.from === null ? n.name : null;
      render();
      return;
    }
    const A = byName(st.from);
    const fa = freePorts(A)[0], fb = freePorts(n)[0];
    if (!fa || !fb) {
      toast(str("ed_noport", (fa ? n : A).name), true);
      return;
    }
    change(() => {
      st.lab.links.push({
        a: {node: A.name, adapter: fa.adapter, port: fa.port},
        b: {node: n.name, adapter: fb.adapter, port: fb.port},
      });
      st.sel = {kind: "link", i: st.lab.links.length - 1};
      st.from = null;
    });
  };
  const setLinking = on => {
    st.linking = on;
    st.from = null;
    if (on) {
      st.adding = false;
    }
    render();
  };

  /* ---------- сегменты ---------- */
  const taken = () => Seg.takenPorts(st.lab);
  const canPreconfig = n => Seg.isPreconfig(imageOf(n));
  /* порты ноды, свободные для сегмента: ни в одном сегменте ещё не заняты */
  const segPorts = n => {
    const used = taken();
    return portsOf(n).filter(p => !used.has(Seg.portKey({node: n.name, adapter: p.adapter, port: p.port})));
  };
  const nextSegmentName = () => {
    for (let i = 1; ; i++) {
      if (!st.lab.segments.some(g => g.name === "LAN" + i)) {
        return "LAN" + i;
      }
    }
  };
  const addSegment = () => {
    change(() => {
      const name = nextSegmentName(), k = +name.slice(3);
      st.lab.segments.push({name, subnet: {fixed: `192.168.${Math.min(k * 10, 250)}.0/24`}, members: []});
      st.sel = {kind: "segment", i: st.lab.segments.length - 1};
      st.adding = true;
    });
  };
  const addMember = (i, nodeName, adapter, port) => {
    change(() => {
      st.lab.segments[i].members.push({node: nodeName, adapter, port});
      st.pick = null;
    });
  };
  /* клик по ноде в режиме «участники»: один свободный порт — берём сразу, иначе выбор в панели */
  const tapMember = idx => {
    const n = st.lab.nodes[idx], i = st.sel.i;
    if (!canPreconfig(n)) {
      toast(str(Seg.notMemberKey(imageOf(n)), n.name), true);
      return;
    }
    const free = segPorts(n);
    if (!free.length) {
      toast(str("ed_noport", n.name), true);
      return;
    }
    if (free.length === 1) {
      addMember(i, n.name, free[0].adapter, free[0].port);
      return;
    }
    st.pick = n.name;
    render();
  };
  const routerPorts = current => {
    const out = [], used = taken();
    st.lab.nodes.filter(n => category(n) === "router").forEach(n => portsOf(n).forEach(p => {
      const key = Seg.portKey({node: n.name, adapter: p.adapter, port: p.port});
      /* a port that is the gateway of another segment may be a gateway again (in another VLAN: router-on-a-stick) */
      if (!used.has(key) || used.get(key).member === -1 || key === current) {
        out.push({node: n.name, adapter: p.adapter, port: p.port, key});
      }
    }));
    return out;
  };

  /* ---------- замечания менеджера ---------- */
  const problemSets = () => {
    const nodes = new Set(), links = new Set(), segs = new Map();
    st.problems.forEach(p => {
      const pl = p.place;
      if (!pl) {
        return;
      }
      if (pl.kind === "node") {
        nodes.add(pl.i);
      } else if (pl.kind === "link") {
        links.add(pl.i);
      } else {
        const e = segs.get(pl.i) || {members: new Set(), parts: new Set()};
        segs.set(pl.i, e);
        if (pl.j !== undefined) {
          e.members.add(pl.j);
        } else if (pl.part) {
          e.parts.add(pl.part);
        }
      }
    });
    return {nodes, links, segs};
  };
  const withPlace = list => (list || []).map(p => Object.assign({}, p, {place: Seg.placeOf(p.path)}));
  const setProblems = list => {
    st.problems = withPlace(list);
  };
  const placeName = place => {
    if (!place) {
      return str("ed_lab");
    }
    if (place.kind === "node") {
      return (st.lab.nodes[place.i] || {}).name || "?";
    }
    if (place.kind === "segment") {
      const seg = st.lab.segments[place.i];
      if (!seg) {
        return "?";
      }
      const m = place.j !== undefined ? seg.members[place.j] : null;
      return seg.name + (m ? " · " + m.node : place.part === "gateway" ? " · " + str("ed_seggateway") : "");
    }
    const l = st.lab.links[place.i];
    return l ? `${l.a.node} — ${l.b.node}` : "?";
  };

  /* ---------- отрисовка ---------- */
  const el = (tag, attrs, parent) => {
    const e = document.createElementNS(NS, tag);
    Object.keys(attrs).forEach(k => {
      if (attrs[k] !== null && attrs[k] !== undefined) {
        e.setAttribute(k, attrs[k]);
      }
    });
    if (parent) {
      parent.appendChild(e);
    }
    return e;
  };
  const pill = (parent, x, y, text) => {
    const g = el("g", {class: "plabel"}, parent);
    const r = el("rect", {rx: 4}, g);
    const t = el("text", {x, y, "text-anchor": "middle", "dominant-baseline": "central"}, g);
    t.textContent = text;
    let w = text.length * 6.4, h = 14;
    try {
      const b = t.getBBox();
      w = b.width;
      h = b.height;
    } catch (e) {
      /* без раскладки (скрытый элемент) — оценка по длине */
    }
    r.setAttribute("x", x - w / 2 - 5);
    r.setAttribute("y", y - h / 2 - 2);
    r.setAttribute("width", w + 10);
    r.setAttribute("height", h + 4);
    return g;
  };
  lyr.preview = el("g", {"data-region": "preview"}, vp);
  /* texts and boxes lie under the cables and the devices */
  lyr.notes = el("g", {"data-region": "notes"});
  vp.insertBefore(lyr.notes, vp.firstChild);
  /* one text or box as lab_definition::drawings() draws it for the students */
  const drawNote = (a, i) => {
    const box = a.kind === "box", size = a.size || 14, step = size * 1.25, pad = box ? 8 : 0;
    const sel = st.sel && st.sel.kind === "note" && st.sel.i === i;
    const g = el("g", {class: "ed-ann" + (box ? " box" : "") + (sel ? " sel" : ""), "data-note": i,
      transform: `translate(${a.x} ${a.y})`}, lyr.notes);
    if (box) {
      const fill = a.fill || NOTE_FILL;
      el("rect", {x: 0, y: 0, width: a.w, height: a.h, rx: 8, fill, "fill-opacity": 0.12, stroke: fill, "stroke-width": 2}, g);
    }
    const color = a.color || (box ? a.fill || NOTE_FILL : null);
    String(a.text || "").split("\n").forEach((line, k) => {
      if (!line.trim()) {
        return;
      }
      const t = el("text", {x: pad, y: pad + size + k * step, "font-size": size, fill: color,
        "font-weight": a.bold ? "bold" : null, "font-style": a.italic ? "italic" : null}, g);
      t.textContent = line;
    });
    if (!sel) {
      return;
    }
    let b = {x: 0, y: 0, width: box ? a.w : 40, height: box ? a.h : size * 1.25};
    if (!box) {
      try {
        const r = g.getBBox();
        if (r.width) {
          b = r;
        }
      } catch (e) {
        /* без раскладки — рамка по оценке */
      }
    }
    el("rect", {class: "ed-ann-halo", x: b.x - 4, y: b.y - 4, width: b.width + 8, height: b.height + 8, rx: 4}, g);
    if (box) {
      el("rect", {class: "ed-ann-grip", "data-grip": i, x: a.w - 7, y: a.h - 7, width: 14, height: 14, rx: 3}, g);
    }
  };
  const center = n => [n.x + W / 2, n.y + H / 2];

  const renderScene = () => {
    Object.values(lyr).forEach(l => {
      l.textContent = "";
    });
    const bad = problemSets();
    /* параллельные линки одной пары нод разводятся на 16 px, как во вьюере */
    const groups = {};
    st.lab.links.forEach((l, i) => {
      const key = [l.a.node, l.b.node].sort().join("|");
      (groups[key] = groups[key] || []).push(i);
    });
    Object.values(groups).forEach(list => list.forEach((i, k) => {
      const l = st.lab.links[i], A = byName(l.a.node), B = byName(l.b.node);
      if (!A || !B) {
        return;
      }
      const [ax0, ay0] = center(A), [bx0, by0] = center(B);
      let dx = bx0 - ax0, dy = by0 - ay0;
      const len = Math.hypot(dx, dy) || 1;
      dx /= len;
      dy /= len;
      const off = (k - (list.length - 1) / 2) * 16;
      const ax = ax0 - dy * off, ay = ay0 + dx * off, bx = bx0 - dy * off, by = by0 + dx * off;
      const sel = st.sel && st.sel.kind === "link" && st.sel.i === i;
      const g = el("g", {class: "link" + (sel ? " sel" : "") + (bad.links.has(i) ? " bad" : ""), "data-link": i}, lyr.links);
      el("line", {class: "hit", x1: ax, y1: ay, x2: bx, y2: by}, g);
      el("line", {class: "wire", x1: ax, y1: ay, x2: bx, y2: by}, g);
      const d = Math.min(46, len * 0.32);
      pill(lyr.labels, ax + dx * d, ay + dy * d, shortName(portName(A, l.a.adapter, l.a.port)));
      pill(lyr.labels, bx - dx * d, by - dy * d, shortName(portName(B, l.b.adapter, l.b.port)));
    }));
    st.lab.nodes.forEach((n, i) => {
      const [cx, cy] = center(n);
      const sel = st.sel && st.sel.kind === "node" && st.sel.i === i;
      const segSel = st.sel && st.sel.kind === "segment" ? st.lab.segments[st.sel.i] : null;
      const role = !segSel ? "" : segSel.gateway && segSel.gateway.node === n.name ? " gw"
        : segSel.members.some(m => m.node === n.name) ? " member" : "";
      const pickable = st.adding && segSel ? (canPreconfig(n) ? " pickable" : " nopick") : "";
      const cls = "node" + (sel ? " sel" : "") + (st.from === n.name ? " from" : "") + (bad.nodes.has(i) ? " bad" : "")
        + (imageOf(n) ? "" : " unknown") + role + pickable;
      const g = el("g", {class: cls, "data-node": i, role: "button", "aria-label": n.name}, lyr.nodes);
      el("rect", {class: "halo", x: cx - 31, y: cy - 31, width: 62, height: 62, rx: 12}, g);
      el("use", {href: `#${prefix}-ico-${iconOf(n)}`, x: cx - 22, y: cy - 22, width: 44, height: 44}, g);
      /* имя — над подписями портов, как во вьюере */
      const t = el("text", {class: "nname", x: cx, y: cy + 42, "text-anchor": "middle", "data-node": i}, lyr.names);
      t.textContent = n.name;
    });
    st.lab.annotations.forEach(drawNote);
    drawPreview();
  };

  /* ---------- предпросмотр: адреса и подсети из сегментов тем же facts.js, что и у студента ---------- */
  const previewData = () => {
    if (!st.preview || !withSegments || !st.lab.segments.length) {
      return null;
    }
    const facts = Seg.previewFacts(st.lab, images);
    if (!Object.values(facts.nodes).some(n => n.interfaces.some(i => i.ipv4))) {
      return null;
    }
    return derive(buildModel(Seg.previewDocument(st.lab, images)), facts);
  };
  const drawPreview = () => {
    st.pv = previewData();
    lyr.preview.textContent = "";
    if (!st.pv) {
      return;
    }
    const {fx, der} = st.pv;
    /* the address lines under the names of the devices: the pills of the subnets keep off them */
    const rowsOf = f => (f.ifs || []).reduce((sum, i) => sum + (i.ipv4 || []).length, 0);
    const tags = [];
    fx.nodes.forEach(f => {
      const n = byName(f.id);
      if (n) {
        const [cx, cy] = center(n);
        tags.push({x: cx, y: cy + 56 + (rowsOf(f) - 1) * 6.5, h: rowsOf(f) * 13});
      }
    });
    der.l3.subnets.forEach((sub, k) => {
      const color = `var(--c${k % 6 + 1})`;
      for (let tries = 0; tries < 12 && tags.some(t =>
        Math.abs(sub.x - t.x) < 90 && Math.abs(sub.y - t.y) < t.h / 2 + 12); tries++) {
        sub.y += 16;
      }
      tags.push({x: sub.x, y: sub.y, h: 0});
      sub.members.forEach(mb => {
        const n = byName(mb.nodeId);
        if (n) {
          const [cx, cy] = center(n);
          el("line", {class: "pv-tie", x1: sub.x, y1: sub.y, x2: cx, y2: cy, style: `stroke:${color}`}, lyr.preview);
        }
      });
      const names = st.lab.segments.filter(g => g.subnet && g.subnet.fixed === sub.key).map(g => g.name).join(", ");
      const tag = pill(lyr.preview, sub.x, sub.y, (names ? names + " " : "") + sub.key);
      tag.classList.add("pv-subnet");
      tag.setAttribute("style", `--pv:${color}`);
    });
    fx.nodes.forEach(f => {
      const n = byName(f.id);
      if (!n) {
        return;
      }
      const [cx, cy] = center(n);
      let row = 0;
      (f.ifs || []).forEach(i => (i.ipv4 || []).forEach(a => {
        const c = der.l3.subnets.flatMap(sub => sub.members).find(mb => mb.iface === i);
        const t = el("text", {class: "pv-ip", x: cx, y: cy + 56 + row * 13, "text-anchor": "middle"}, lyr.preview);
        t.textContent = c ? hostPart(c.c) : a;
        row++;
      }));
    });
  };

  const renderPalette = () => {
    if (D.catalogerror) {
      palette.innerHTML = `<p class="ed-note">${esc(D.catalogerror)}</p>`;
      return;
    }
    const list = [...images.values()];
    if (!list.length) {
      palette.innerHTML = `<p class="ed-note">${esc(str("ed_catalog"))}: —</p>`;
      return;
    }
    const rank = c => {
      const i = CATEGORY_ORDER.indexOf(c);
      return i < 0 ? CATEGORY_ORDER.length : i;
    };
    list.sort((a, b) => rank(a.category) - rank(b.category) || String(a.title).localeCompare(String(b.title)));
    const superseded = new Set(list.filter(i => i.derived).map(i => i.derived.supersedes).filter(Boolean));
    const retired = list.filter(i => i.derived && i.retired && i.derived.author_user_id === D.userid);
    const visible = list.filter(i => !i.retired && !superseded.has(i.id));
    const entry = img => {
      const search = (img.title + " " + (img.derived?.description || "") + " " + (img.authorname || "")).toLowerCase();
      const icon = Number.isInteger(img.uplink_port) ? "cloud" : ICON[img.category] || "server";
      const hint = esc(str("authoringforcedhint"));
      const forced = img.derived?.forced
        ? `<span data-forced-image="${esc(img.id)}" role="img" aria-label="${hint}" title="${hint}">⚠</span>` : "";
      const edit = img.derived?.author_user_id === D.userid
        ? `<button type="button" class="gbtn" data-edit-image="${esc(img.id)}">${esc(str("authoringedit"))}</button>` : "";
      const feature = D.manageimages
        ? `<button type="button" class="gbtn" data-feature-image="${esc(img.id)}">`
          + `${esc(str(img.featured ? "authoringunfeature" : "authoringfeature"))}</button>` : "";
      const about = img.derived
        ? `<small>${esc(img.derived.description || "")} · ${esc(img.derived.base)} · ${esc(img.authorname || "")}</small>
        ${edit}
        ${feature}` : "";
      return `<div class="ed-image-entry" data-search="${esc(search)}">
      <button class="ed-img" type="button" draggable="${img.retired ? "false" : "true"}"
        data-image="${img.retired ? "" : esc(img.id)}" title="${esc(img.title)}" ${img.retired ? "disabled" : ""}>
        <svg viewBox="0 0 48 48" aria-hidden="true"><use href="#${prefix}-ico-${icon}"/></svg>
        <span>${esc(img.title || img.id)}</span>${forced}</button>
      ${about}
      </div>`;
    };
    const main = visible.filter(i => !i.derived || i.featured);
    const mine = visible.filter(i => i.derived && !i.featured && i.derived.author_user_id === D.userid);
    const others = visible.filter(i => i.derived && !i.featured && i.derived.author_user_id !== D.userid);
    const groups = new Map();
    others.forEach(img => {
      const author = img.derived.author_user_id;
      if (!groups.has(author)) {
        groups.set(author, []);
      }
      groups.get(author).push(img);
    });
    palette.innerHTML = `<p class="eyebrow">${esc(str("ed_catalog"))}</p>
      <input type="search" data-image-search aria-label="${esc(str("authoringsearch"))}"
        placeholder="${esc(str("authoringsearch"))}">`
      + main.map(entry).join("")
      + (mine.length ? `<details open data-default-open="1"><summary>${esc(str("authoringmine"))}</summary>`
        + `${mine.map(entry).join("")}</details>` : "")
      + (others.length ? `<details><summary>${esc(str("authoringothers"))}</summary>` + [...groups.values()].map(items =>
        `<details><summary>${esc(items[0].authorname || items[0].derived.author_user_id)}</summary>`
        + `${items.map(entry).join("")}</details>`
      ).join("") + "</details>" : "")
      + (retired.length ? `<details><summary>${esc(str("authoringretired"))}</summary>`
        + `${retired.map(entry).join("")}</details>` : "");
  };

  const fmtMem = mb => mb >= 1024 ? (mb / 1024).toFixed(1).replace(/\.0$/, "") + " GB" : mb + " MB";
  const problemsHTML = () => !st.problems.length ? "" : `<h3>${esc(str("ed_problems"))}</h3><ul class="ed-problems">` +
    st.problems.map(p => {
      const text = S["problem_" + p.code] || str("ed_problem", p.code);
      const attr = p.place ? ` data-goto="${p.place.kind}:${p.place.i}"` : "";
      /* a problem of the checks document names its path: there is no object of the scheme to go to */
      const where = !p.place && typeof p.path === "string" && p.path.startsWith("checks") ? p.path
        : (!p.place && setupForm.where(p.path)) || placeName(p.place);
      return `<li><button type="button" class="rowbtn"${attr} title="${esc(p.message)}"><span>` +
        `<b>${esc(where)}</b>: ${esc(text)}</span></button></li>`;
    }).join("") + "</ul>";

  const warningsHTML = () => !st.warnings.length ? "" : `<h3>${esc(str("ed_warnings"))}</h3><ul class="ed-problems warn">` +
    st.warnings.map(w => {
      const attr = w.place ? ` data-goto="${w.place.kind}:${w.place.i}"` : "";
      return `<li><button type="button" class="rowbtn"${attr} title="${esc(w.message)}"><span>` +
        `<b>${esc(placeName(w.place))}</b>: ${esc(S["warning_" + w.code] || str("ed_problem", w.code))}</span></button></li>`;
    }).join("") + "</ul>";
  /* what the addresses of a segment already say before the lab manager is asked (the rules of its address plan) */
  const localFindings = i => {
    const g = st.lab.segments[i], net = Seg.parseSubnet(g.subnet && g.subnet.fixed);
    return net ? Seg.plan(net, g.gateway || null, g.members).problems : [];
  };
  const previewHTML = () => {
    if (!st.preview || !withSegments || !st.lab.segments.length) {
      return "";
    }
    const own = [];
    st.lab.segments.forEach((g, i) => localFindings(i).forEach(p => own.push({
      sev: "bad",
      text: `${placeName({kind: "segment", i, part: p.where === "gateway" ? "gateway" : "members",
        j: typeof p.where === "number" ? p.where : undefined})}: ${S["problem_" + p.code] || p.code}`,
    })));
    const issues = own.concat(st.pv ? st.pv.der.l3.issues : []);
    if (!issues.length) {
      return `<h3>${esc(str("ed_preview"))}</h3>` + (st.pv ? `<p class="ed-ok">${esc(str("ed_previewok"))}</p>`
        : `<p class="muted">${esc(str("ed_previewempty"))}</p>`);
    }
    return `<h3>${esc(str("ed_preview"))}</h3><ul class="ed-problems ${issues.some(x => x.sev === "bad") ? "" : "warn"}">` +
      issues.map(x => `<li><span class="pv-issue ${esc(x.sev)}">${esc(x.text)}</span></li>`).join("") + "</ul>";
  };
  const segmentsListHTML = () => {
    if (!withSegments) {
      return st.lab.segments.length ? `<p class="ed-note">${esc(str("ed_segkept", st.lab.segments.length))}</p>` : "";
    }
    const rows = st.lab.segments.map((g, i) => `<li><button type="button" class="rowbtn" data-goto="segment:${i}"><span>` +
      `<b>${esc(g.name)}</b> ${esc((g.subnet && g.subnet.fixed) || "")}${g.vlan ? " · VLAN " + +g.vlan.fixed : ""}` +
      ` · ${g.members.length}</span></button></li>`).join("");
    return `<h3>${esc(str("ed_segments"))}</h3>` + (rows ? `<ul class="ed-problems seglist">${rows}</ul>`
      : `<p class="muted">${esc(str("ed_nosegments"))}</p>`) +
      `<button type="button" class="btn btn-sm btn-outline-primary" data-act="addsegment">${esc(str("ed_addsegment"))}</button>`;
  };

  const segmentHTML = i => {
    const g = st.lab.segments[i], bad = problemSets().segs.get(i) || {members: new Set(), parts: new Set()};
    const flag = part => bad.parts.has(part) ? " is-invalid" : "";
    const net = Seg.parseSubnet(g.subnet && g.subnet.fixed);
    const pl = net ? Seg.plan(net, g.gateway || null, g.members) : null;
    localFindings(i).forEach(p => {
      if (typeof p.where === "number") {
        bad.members.add(p.where);
      } else if (p.where === "gateway") {
        bad.parts.add("gateway");
      }
    });
    const gwOptions = routerPorts(g.gateway ? Seg.portKey(g.gateway) : "");
    const gwSel = g.gateway ? Seg.portKey(g.gateway) : "";
    const hasRouter = st.lab.nodes.some(n => category(n) === "router");
    const gwHTML = (hasRouter ? `<label class="ed-field"><span>${esc(str("ed_seggateway"))}</span>
      <select class="form-select form-select-sm${flag("gateway")}" data-seg="gateway"><option value="">${
        esc(str("ed_nogateway"))}</option>` +
      gwOptions.map(o => `<option value="${esc(o.key)}"${o.key === gwSel ? " selected" : ""}>${esc(o.node)} · ` +
        `${esc(portName(byName(o.node), o.adapter, o.port))}</option>`).join("") + `</select></label>` :
        `<div class="ed-field" data-region="nogatewayhelp"><span>${esc(str("ed_seggateway"))}</span>
      <small>${esc(str("ed_nogateway_norouter"))}</small></div>`) +
      (g.gateway ? `<label class="ed-field"><span>${esc(str("ed_hostnumber"))}</span>
        <input type="number" class="form-control form-control-sm" data-seg="gwhost" min="1" step="1"
          value="${Number.isInteger(g.gateway.host) ? g.gateway.host : ""}" placeholder="${
            pl && pl.gateway ? esc(pl.gateway) : ""}"></label>` : "");
    /* which IOL devices the lab manager sets up for this segment: the student need not touch them */
    const byStudent = g.configure === "student";
    const managed = withSegments && !byStudent ? Seg.managedNodes(st.lab, images, i) : [];
    const managedHTML = managed.length
      ? `<p class="ed-note" data-region="managed">${esc(str("ed_managed", managed.join(", ")))}</p>` : "";
    /* a segment of the task: the student sets it up, its address plan is what the checks expect */
    const configureHTML = withChecks ? `<label class="ed-field ed-check"><input type="checkbox" data-seg="configure"${
      byStudent ? " checked" : ""}> <span>${esc(str("ed_segconfigure"))}</span><small>${
      esc(str("ed_segconfigure_help"))}</small></label>` : "";
    const rows = g.members.map((m, j) => {
      const node = byName(m.node), ip = pl && pl.members[j] ? `${pl.members[j]}/${net.prefix}` : "";
      return `<tr class="${bad.members.has(j) ? "bad" : ""}"><td><button type="button" class="rowbtn" data-select-node="${
        esc(m.node)}">` +
        `${esc(m.node)}</button> <span class="mono">${esc(portName(node || {}, m.adapter, m.port))}</span></td>
        <td class="mono">${esc(ip)}</td>
        <td><input type="number" class="form-control form-control-sm ed-host" data-mhost="${j}" min="1" step="1"
          value="${Number.isInteger(m.host) ? m.host : ""}" aria-label="${esc(str("ed_hostnumber"))}"></td>
        <td><button type="button" class="rowbtn" data-mdel="${j}" aria-label="${
          esc(str("ed_removemember"))}">&times;</button></td></tr>`;
    }).join("");
    let pick = "";
    if (st.pick && byName(st.pick)) {
      const n = byName(st.pick);
      pick = `<div class="ed-pick"><label class="ed-field"><span>${esc(str("ed_pickport", n.name))}</span>
        <select class="form-select form-select-sm" data-pick>` + segPorts(n).map(p =>
        `<option value="${p.adapter}/${p.port}">${esc(portName(n, p.adapter, p.port))}</option>`).join("") +
        `</select></label><button type="button" class="btn btn-sm btn-primary" data-act="pickadd">${
          esc(str("ed_addport"))}</button></div>`;
    }
    return `<div class="ph"><div><p class="eyebrow">${esc(str("ed_segment"))}</p><h2>${esc(g.name)}</h2></div></div>
      <label class="ed-field"><span>${esc(str("ed_segname"))}</span>
        <input type="text" class="form-control form-control-sm${flag("name")}" data-seg="name" value="${esc(g.name)}" maxlength="32"
          autocomplete="off" spellcheck="false"></label>
      <label class="ed-field"><span>${esc(str("ed_segvlan"))}</span>
        <input type="number" class="form-control form-control-sm${flag("vlan")}" data-seg="vlan" min="2" max="${MAX_VLAN}" step="1"
          value="${g.vlan ? +g.vlan.fixed : ""}" placeholder="—"><small>${esc(str("ed_segvlan_help"))}</small></label>
      <label class="ed-field"><span>${esc(str("ed_segsubnet"))}</span>
        <input type="text" class="form-control form-control-sm${flag("subnet")}" data-seg="subnet" value="${
          esc((g.subnet || {}).fixed || "")}"
          placeholder="192.168.10.0/24" autocomplete="off" spellcheck="false"></label>
      ${gwHTML}
      ${configureHTML}
      ${managedHTML}
      <p class="ed-error" data-region="fielderror" role="alert" hidden></p>
      <h3>${esc(str("ed_members"))}</h3>
      ${rows ? `<table class="ed-ports ed-members"><tbody>${rows}</tbody></table>`
        : `<p class="muted">${esc(str("ed_nomembers"))}</p>`}
      ${pick}
      <button type="button" class="btn btn-sm ${st.adding ? "btn-primary" : "btn-outline-primary"}" data-act="addmembers"
        aria-pressed="${st.adding}">${esc(st.adding ? str("ed_addmembers_done") : str("ed_addmembers"))}</button>
      <button type="button" class="btn btn-sm btn-outline-danger" data-act="delete">${esc(str("ed_deletesegment"))}</button>
      ${problemsHTML()}${warningsHTML()}${previewHTML()}`;
  };

  const numberField = (n, key, label) => {
    const r = range(n, key);
    if (!r) {
      return "";
    }
    const offset = key === "adapters" ? adapterOffset(n) : 0;
    const v = Number.isInteger(n[key]) ? n[key] - offset : "";
    return `<label class="ed-field"><span>${esc(label)}</span>
      <input type="number" class="form-control form-control-sm" data-field="${key}" min="${r.min - offset}" max="${r.max - offset}"
        step="1" value="${v}" placeholder="${r.default - offset}"><small>${r.min - offset}…${r.max - offset}</small></label>`;
  };

  /* random guest interface names (CONTRACTS §2.6): only images that can take them offer the checkbox */
  const ifnamesField = (n, img) => !(img && img.random_ifnames) && !n.random_ifnames ? "" :
    `<label class="ed-field ed-check"><input type="checkbox" data-field="random_ifnames"${
      n.random_ifnames ? " checked" : ""}> <span>${esc(str("ed_randomifnames"))}</span><small>${
      esc(str("ed_randomifnames_help"))}</small></label>`;

  /* no pasting into the console (lab_definition::NOPASTE, plugin only): every node that has a console offers it */
  const nopasteField = (n, img) => img && img.console_type === "none" && !n.nopaste ? "" :
    `<label class="ed-field ed-check"><input type="checkbox" data-field="nopaste"${
      n.nopaste ? " checked" : ""}> <span>${esc(str("ed_nopaste"))}</span><small>${
      esc(str("ed_nopaste_help"))}</small></label>`;
  /* no console for students at all (lab_definition::NOCONSOLE, plugin only): they reach the device over the network */
  const noconsoleField = (n, img) => img && img.console_type === "none" && !n.noconsole ? "" :
    `<label class="ed-field ed-check"><input type="checkbox" data-field="noconsole"${
      n.noconsole ? " checked" : ""}> <span>${esc(str("ed_noconsole"))}</span><small>${
      esc(str("ed_noconsole_help"))}</small></label>`;
  /* additional console lines (manager CONTRACTS §2.6, §6.4): only images that take them offer the choice; a number an
     image no longer takes stays visible, so that it can be set back to none */
  const linesField = (n, img) => {
    const spec = img && img.extra_consoles, value = Number.isInteger(n.extra_consoles) ? n.extra_consoles : 0;
    if (!spec && !value) {
      return "";
    }
    const max = Math.max(spec ? spec.max : 0, value);
    const options = Array.from({length: max + 1}, (_, k) => `<option value="${k}"${k === value ? " selected" : ""}>${
      k ? k : esc(str("ed_extraconsoles_none"))}</option>`).join("");
    return `<label class="ed-field"><span>${esc(str("ed_extraconsoles"))}</span>
      <select class="form-select form-select-sm" data-field="extra_consoles">${options}</select>
      <small>${esc(str("ed_extraconsoles_help"))}</small></label>`;
  };
  /* icon picker: the first button goes back to the icon of the image */
  const iconField = n => {
    const button = (value, icon, title) => {
      const on = (n.icon || "") === value;
      return `<button type="button" class="ed-icon${on ? " on" : ""}" data-icon="${value}" aria-pressed="${on}"
        title="${esc(title)}" aria-label="${esc(title)}"><svg viewBox="0 0 48 48" aria-hidden="true">
        <use href="#${prefix}-ico-${icon}"/></svg></button>`;
    };
    return `<div class="ed-field"><span>${esc(str("ed_icon"))}</span><span class="ed-icons" role="group"
      aria-label="${esc(str("ed_icon"))}">${button("", imageIcon(n), str("ed_iconauto"))}${
      ICONS.map(icon => button(icon, icon, str("ui_cat_" + icon))).join("")}</span></div>`;
  };
  /* additional blank disks (CONTRACTS §2.6): only images that take them offer the block; disks an image no longer
     takes stay visible, so that they can be removed */
  const disksField = (n, img) => {
    const spec = img && img.extra_disks, disks = Array.isArray(n.disks) ? n.disks : [];
    if (!spec && !disks.length) {
      return "";
    }
    const size = spec ? spec.size_gb : {min: 1, max: 1024};
    const rows = disks.map((d, j) => `<label class="ed-field ed-disk"><span>${esc(str("ed_disk", j + 1))}</span>
      <span class="ed-diskrow"><input type="number" class="form-control form-control-sm" data-disk="${j}" min="${size.min}"
        max="${size.max}" step="1" value="${Number.isInteger(d.size_gb) ? d.size_gb : ""}">
      <button type="button" class="btn btn-sm btn-outline-danger" data-act="removedisk" data-disk="${j}"
        title="${esc(str("ed_removedisk"))}" aria-label="${esc(str("ed_removedisk"))}">×</button></span>
      <small>${size.min}…${size.max} ${esc(str("ed_diskgb"))}</small></label>`).join("");
    const add = spec && disks.length < spec.max
      ? `<button type="button" class="btn btn-sm btn-outline-primary" data-act="adddisk">${esc(str("ed_adddisk"))}</button>` : "";
    return `<h3>${esc(str("ed_disks"))}</h3>${rows}${add}<p class="ed-note">${esc(str("ed_disks_help"))}</p>`;
  };

  const nodeHTML = i => {
    const n = st.lab.nodes[i], img = imageOf(n), used = usedPorts();
    const ports = portsOf(n).map(p => {
      const li = used.get(`${n.name}|${p.adapter}/${p.port}`);
      const l = li === undefined ? null : st.lab.links[li];
      const peer = l ? (l.a.node === n.name && l.a.adapter === p.adapter && l.a.port === p.port ? l.b : l.a) : null;
      const peerPort = peer ? portName(byName(peer.node) || {}, peer.adapter, peer.port) : "";
      const right = peer ? `<button type="button" class="rowbtn" data-goto="link:${li}">${esc(peer.node)} ${esc(peerPort)}</button>`
        : `<span class="muted">${esc(str("ed_free"))}</span>`;
      return `<tr><td class="mono">${esc(portName(n, p.adapter, p.port))}</td><td>${right}</td></tr>`;
    }).join("");
    return `<div class="ph"><div><p class="eyebrow">${esc(img ? img.title : str("ed_unknownimage", n.image))}</p>
      <h2>${esc(n.name)}</h2></div></div>
      <label class="ed-field"><span>${esc(str("ed_name"))}</span>
        <input type="text" class="form-control form-control-sm" data-field="name" value="${esc(n.name)}" maxlength="80"
          autocomplete="off" spellcheck="false"></label>
      ${iconField(n)}
      ${numberField(n, "adapters", str(adapterOffset(n) ? "ed_ports" : "ed_adapters"))}
      ${numberField(n, "ram_mb", str("ed_ram"))}
      ${numberField(n, "cpus", str("ed_cpus"))}
      ${ifnamesField(n, img)}
      ${nopasteField(n, img)}
      ${noconsoleField(n, img)}
      ${linesField(n, img)}
      <p class="ed-error" data-region="fielderror" role="alert" hidden></p>
      ${disksField(n, img)}
      <h3>${esc(str("ed_ports"))}</h3>
      <table class="ed-ports"><tbody>${ports}</tbody></table>
      <button type="button" class="btn btn-sm btn-outline-danger" data-act="delete">${esc(str("ed_deletenode"))}</button>
      ${problemsHTML()}`;
  };

  const endSelect = (i, side) => {
    const l = st.lab.links[i], n = byName(l[side].node);
    const opts = freePorts(n, i).map(p => {
      const v = `${p.adapter}/${p.port}`;
      const on = p.adapter === l[side].adapter && p.port === l[side].port ? " selected" : "";
      return `<option value="${v}"${on}>${esc(portName(n, p.adapter, p.port))}</option>`;
    }).join("");
    return `<label class="ed-field"><span>${esc(n.name)}</span>
      <select class="form-select form-select-sm" data-end="${side}">${opts}</select></label>`;
  };

  const linkHTML = i => {
    const l = st.lab.links[i];
    return `<div class="ph"><div><p class="eyebrow">${esc(str("ed_link"))}</p>
      <h2>${esc(l.a.node)} — ${esc(l.b.node)}</h2></div></div>
      <h3>${esc(str("ed_linkends"))}</h3>
      ${endSelect(i, "a")}${endSelect(i, "b")}
      <button type="button" class="btn btn-sm btn-outline-danger" data-act="delete">${esc(str("ed_deletelink"))}</button>
      ${problemsHTML()}`;
  };


  /* a text or a box: text, size, bold and italic, colours, the size of a box */
  const noteHTML = i => {
    const a = st.lab.annotations[i], box = a.kind === "box";
    const swatches = (field, current, auto) => {
      const custom = current && !NOTE_COLORS.includes(current) ? current : "";
      const one = (value, title) => `<button type="button" class="ed-swatch${value === (current || "") ? " on" : ""}${
        value ? "" : " auto"}" data-ncolor="${field}" data-value="${value}" title="${esc(title)}" aria-label="${esc(title)}"${
        value ? ` style="background:${value}"` : ""}></button>`;
      return `<div class="ed-swatches">${auto ? one("", auto) : ""}${NOTE_COLORS.map(c => one(c, c)).join("")}
        <input type="color" class="ed-swatch-pick${custom ? " on" : ""}" data-nfield="${field}" value="${
          custom || current || (box ? a.fill || NOTE_FILL : "#000000")}" title="${esc(str("ed_anncustom"))}"></div>`;
    };
    const first = String(a.text || "").split("\n")[0].trim();
    return `<div class="ph"><div><p class="eyebrow">${esc(str(box ? "ed_annbox" : "ed_anntext"))}</p>
      <h2>${esc(first || "—")}</h2></div></div>
      <label class="ed-field"><span>${esc(str("ed_anntextfield"))}</span>
        <textarea class="form-control form-control-sm" data-nfield="text" rows="3" maxlength="${NOTE_TEXT_MAX}"
          spellcheck="true">${esc(a.text || "")}</textarea>
        <small>${esc(str(box ? "ed_annbox_help" : "ed_anntext_help"))}</small></label>
      <div class="ed-field"><span>${esc(str("ed_annstyle"))}</span><div class="ed-annfmt">
        <button type="button" class="gbtn ed-fmt" data-ntoggle="bold" aria-pressed="${!!a.bold}" title="${
          esc(str("ed_annbold"))}"><b>${esc(str("ed_annbold_short"))}</b></button>
        <button type="button" class="gbtn ed-fmt" data-ntoggle="italic" aria-pressed="${!!a.italic}" title="${
          esc(str("ed_annitalic"))}"><i>${esc(str("ed_annitalic_short"))}</i></button>
        <input type="number" class="form-control form-control-sm" data-nfield="size" min="${NOTE_SIZE[0]}" max="${
          NOTE_SIZE[1]}" value="${a.size || 14}" title="${esc(str("ed_annsize"))}" aria-label="${esc(str("ed_annsize"))}">
      </div></div>
      <div class="ed-field"><span>${esc(str("ed_anncolor"))}</span>${
        swatches("color", a.color || "", str(box ? "ed_anncolorbox" : "ed_anncolorauto"))}</div>
      ${box ? `<div class="ed-field"><span>${esc(str("ed_annfill"))}</span>${swatches("fill", a.fill || NOTE_FILL, "")}</div>
      <label class="ed-field"><span>${esc(str("ed_annwidth"))}</span><input type="number" class="form-control form-control-sm"
        data-nfield="w" min="${NOTE_BOX[0]}" max="${NOTE_BOX[1]}" value="${a.w}"></label>
      <label class="ed-field"><span>${esc(str("ed_annheight"))}</span><input type="number" class="form-control form-control-sm"
        data-nfield="h" min="${NOTE_BOX[0]}" max="${NOTE_BOX[1]}" value="${a.h}"></label>` : ""}
      <p class="ed-error" data-region="fielderror" role="alert" hidden></p>
      <p class="ed-note">${esc(str("ed_annstudents"))}</p>
      <button type="button" class="btn btn-sm btn-outline-danger" data-act="delete">${esc(str(box ? "ed_anndeletebox"
        : "ed_anndeletetext"))}</button>`;
  };

  /* the checks block (checksform.js): the nodes as the prompt and the script fields know them */
  const checksForm = createChecks({
    esc, str, S,
    get: () => st.lab.checks,
    set: v => change(() => {
      st.lab.checks = v && Object.keys(v).length > 1 ? v : null;
    }),
    lab: () => {
      const end = e => {
        const n = byName(e.node);
        return `${e.node} ${n ? portName(n, e.adapter, e.port) : ""}`.trim();
      };
      return {
        nodes: st.lab.nodes.map(n => {
          const img = imageOf(n) || {};
          return {name: n.name, image: img.title || n.image, kind: img.kind || "", category: img.category || "",
            scripts: img.kind === "qemu" && (img.uplink_port === undefined || img.uplink_port === null)};
        }),
        links: st.lab.links.map(l => [end(l.a), end(l.b)]),
        segments: st.lab.segments,
        setup: st.lab.setup.map(x => ({title: x.title, node: x.node})),
      };
    },
    editable: withChecks && !activityChecks,
    withScripts, withNeeds,
    prompt: D.checksprompt || "",
    validate: async doc => (await call("mod_gns3lab_validate_lab", {labid: D.labid, definition: definition(doc)})).problems || [],
    fieldError: text => fieldError(text),
    root,
  });
  /* the setup block (setupform.js): the devices with what they take, links by ports, segments with their planned
     addresses, so that the prompt knows the lab as the student gets it */
  const portEnd = e => {
    const n = byName(e.node);
    return `${e.node} ${n ? portName(n, e.adapter, e.port) : ""}`.trim();
  };
  const setupSegments = () => st.lab.segments.map(g => {
    const out = {name: g.name, subnet: g.subnet && g.subnet.fixed ? g.subnet.fixed : null};
    if (g.vlan && g.vlan.fixed !== undefined) {
      out.vlan = g.vlan.fixed;
    }
    out.configure = g.configure === "student" ? "student" : "manager";
    const net = Seg.parseSubnet(out.subnet);
    const plan = net ? Seg.plan(net, g.gateway || null, g.members || []) : null;
    const withIp = ip => (ip && net ? {ip: `${ip}/${net.prefix}`} : {});
    if (g.gateway) {
      out.gateway = Object.assign({port: portEnd(g.gateway)}, withIp(plan && plan.gateway));
    }
    out.members = (g.members || []).map((m, j) => Object.assign({port: portEnd(m)}, withIp(plan && plan.members[j])));
    return out;
  });
  const setupForm = createSetup({
    esc, str, S,
    get: () => st.lab.setup,
    set: list => change(() => {
      st.lab.setup = list;
    }),
    lab: () => ({
      nodes: st.lab.nodes.map(n => {
        const img = imageOf(n) || {};
        return {name: n.name, image: img.title || n.image, kind: img.kind || "", category: img.category || "",
          guest: img.guest || "", uplink: uplinkOf(n) !== null};
      }),
      links: st.lab.links.map(l => [portEnd(l.a), portEnd(l.b)]),
      segments: setupSegments(),
    }),
    editable: withSetup,
    prompt: D.setupprompt || "",
    validate: async list => (await call("mod_gns3lab_validate_lab", {labid: D.labid,
      definition: definition(undefined, list)})).problems || [],
    problems: () => st.problems,
    fieldError: text => fieldError(text),
    root,
  });
  /* an authoring run whose setup step failed asks for the window that fixes it (authoring page, lab.js) */
  const fixStep = data => {
    if (!data || data.type !== "gns3lab-setup-fix" || +data.labid !== +D.labid || !data.error) {
      return false;
    }
    const err = data.error;
    if (!setupForm.fix({step: String(err.step || ""), node: String(err.node || ""), exitcode: +err.exitcode || 0,
        message: String(err.message || "")})) {
      toast(str("ed_setup_fix_missing", String(err.step || "")), true);
    }
    return true;
  };
  window.addEventListener("message", e => {
    if (e.origin === window.location.origin && fixStep(e.data)) {
      e.source.postMessage({type: "gns3lab-setup-fix-ack"}, e.origin);
    }
  });
  if (typeof BroadcastChannel === "function") {
    const channel = new BroadcastChannel("gns3lab-editor-" + D.labid);
    channel.onmessage = e => {
      if (fixStep(e.data)) {
        channel.postMessage({type: "gns3lab-setup-fix-ack"});
      }
    };
  }

  /* the checks of a draft of an earlier version stay out of the next one: they are in the activities now */
  const checksHTML = () => !activityChecks ? checksForm.html() : !st.lab.checks ? "" : `<h3>${esc(str("ed_checks"))}</h3>
    <p class="ed-note" data-region="checksmoved">${esc(str("ed_checksmoved"))}</p>`;

  const labHTML = () => {
    const res = st.resources ? `<p class="ed-ok">${esc(str("ed_valid", {
      memory: fmtMem(st.resources.memorymb), cores: st.resources.cores}))}</p>` : "";
    return `<div class="ph"><div><p class="eyebrow">${esc(str("ed_lab"))}</p>
      <h2>${esc(str("ed_summary", {nodes: st.lab.nodes.length, links: st.lab.links.length}))}</h2></div></div>
      ${res}${segmentsListHTML()}${setupForm.html()}${checksHTML()}${problemsHTML()}${warningsHTML()}${previewHTML()}
      ${D.canpublish ? "" : `<p class="ed-note">${esc(str("ed_cannotpublish"))}</p>`}
      <p class="muted">${esc(str("ed_hint"))}</p>`;
  };

  const renderProps = () => {
    const s = st.sel;
    props.innerHTML = !s ? labHTML() : s.kind === "node" ? nodeHTML(s.i)
      : s.kind === "segment" ? segmentHTML(s.i) : s.kind === "note" ? noteHTML(s.i) : linkHTML(s.i);
  };

  const renderChrome = () => {
    const meta = st.status === "published" && !st.dirty ? str("ed_version", st.version) : str("ed_draft", st.version);
    $("meta").textContent = meta + (st.dirty ? " · " + str("ed_unsaved") : "");
    const btn = act => root.querySelector(`[data-act="${act}"]`);
    btn("undo").disabled = !st.undo.length || st.busy;
    btn("link").setAttribute("aria-pressed", String(st.linking));
    btn("save").disabled = st.busy;
    btn("check").disabled = st.busy || !D.canpublish;
    btn("publish").disabled = st.busy || !D.canpublish;
    const pv = btn("preview");
    if (pv) {
      pv.hidden = !withSegments;
      pv.disabled = !st.lab.segments.length;
      pv.setAttribute("aria-pressed", String(st.preview));
    }
    $("hint").textContent = st.adding ? str("ed_hintmembers") : !st.linking ? str("ed_hint")
      : st.from ? str("ed_hintlinkto", st.from) : str("ed_hintlink");
    svg.classList.toggle("linking", st.linking);
  };

  const render = () => {
    const pool = {node: st.lab.nodes, link: st.lab.links, segment: st.lab.segments, note: st.lab.annotations};
    if (st.sel && !(pool[st.sel.kind] || [])[st.sel.i]) {
      st.sel = null;
    }
    if (!st.sel || st.sel.kind !== "segment") {
      st.adding = false;
      st.pick = null;
    }
    renderScene();
    renderProps();
    renderChrome();
  };

  /* ---------- сообщения ---------- */
  let toastTimer = 0;
  const toast = (text, isError) => {
    const t = $("toast");
    t.textContent = text;
    t.classList.toggle("ok", !isError);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      t.hidden = true;
    }, 4000);
  };

  /* ---------- вид: сдвиг и масштаб ---------- */
  const applyView = () => vp.setAttribute("transform", `translate(${view.tx} ${view.ty}) scale(${view.k})`);
  const local = e => {
    const r = svg.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };
  const toModel = ([px, py]) => [(px - view.tx) / view.k, (py - view.ty) / view.k];
  const zoomAt = (px, py, f) => {
    const k = Math.min(KMAX, Math.max(KMIN, view.k * f));
    view.tx = px - (px - view.tx) * (k / view.k);
    view.ty = py - (py - view.ty) * (k / view.k);
    view.k = k;
    applyView();
  };
  const fit = () => {
    const r = svg.getBoundingClientRect();
    const boxes = st.lab.nodes.map(n => [n.x - 50, n.y - 50, n.x + W + 50, n.y + H + 70])
      .concat(st.lab.annotations.map(a => [a.x - 20, a.y - 20, a.x + (a.w || 200) + 20, a.y + (a.h || 40) + 20]));
    if (!boxes.length) {
      view.k = 1;
      view.tx = r.width / 2;
      view.ty = r.height / 2;
      applyView();
      return;
    }
    const x0 = Math.min(...boxes.map(b => b[0])), y0 = Math.min(...boxes.map(b => b[1]));
    const x1 = Math.max(...boxes.map(b => b[2])), y1 = Math.max(...boxes.map(b => b[3]));
    view.k = Math.min(1.4, Math.max(KMIN, Math.min(r.width / (x1 - x0), r.height / (y1 - y0))));
    view.tx = r.width / 2 - (x0 + x1) / 2 * view.k;
    view.ty = r.height / 2 - (y0 + y1) / 2 * view.k;
    applyView();
  };
  const viewCenter = () => {
    const r = svg.getBoundingClientRect();
    return toModel([r.width / 2, r.height / 2]);
  };

  /* ---------- указатель: перетаскивание нод, сдвиг, щипок, касания ---------- */
  const ptrs = new Map();
  let gest = null;
  svg.addEventListener("pointerdown", e => {
    if (e.button > 0) {
      return;
    }
    ptrs.set(e.pointerId, local(e));
    svg.setPointerCapture(e.pointerId);
    if (ptrs.size === 2) {
      const [p, q] = [...ptrs.values()];
      gest = {type: "pinch", d: Math.hypot(p[0] - q[0], p[1] - q[1]) || 1, moved: true};
      return;
    }
    const nodeEl = e.target.closest("[data-node]"), linkEl = e.target.closest("[data-link]");
    const gripEl = e.target.closest("[data-grip]"), noteEl = e.target.closest("[data-note]");
    const start = local(e);
    if (gripEl && !st.linking) {
      const i = +gripEl.dataset.grip, a = st.lab.annotations[i], [mx, my] = toModel(start);
      gest = {type: "grip", i, dx: a.x + a.w - mx, dy: a.y + a.h - my, start, moved: false, before: snapshot()};
    } else if (nodeEl) {
      const i = +nodeEl.dataset.node, n = st.lab.nodes[i], [mx, my] = toModel(start);
      gest = {type: "node", i, dx: mx - n.x, dy: my - n.y, start, moved: false, before: snapshot()};
    } else if (linkEl) {
      gest = {type: "link", i: +linkEl.dataset.link, start, moved: false};
    } else if (noteEl && !st.linking && !st.adding) {
      const i = +noteEl.dataset.note, a = st.lab.annotations[i], [mx, my] = toModel(start);
      gest = {type: "note", i, dx: mx - a.x, dy: my - a.y, start, moved: false, before: snapshot()};
    } else {
      gest = {type: "pan", start, tx: view.tx, ty: view.ty, moved: false};
    }
  });
  svg.addEventListener("pointermove", e => {
    if (!ptrs.has(e.pointerId) || !gest) {
      return;
    }
    const pos = local(e);
    ptrs.set(e.pointerId, pos);
    if (gest.type === "pinch") {
      if (ptrs.size === 2) {
        const [p, q] = [...ptrs.values()], d = Math.hypot(p[0] - q[0], p[1] - q[1]) || 1;
        zoomAt((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, d / gest.d);
        gest.d = d;
      }
      return;
    }
    if (!gest.moved && Math.hypot(pos[0] - gest.start[0], pos[1] - gest.start[1]) < 5) {
      return;
    }
    if (gest.type === "node" && !st.linking) {
      if (!gest.moved) {
        pushUndo(gest.before);
      }
      const n = st.lab.nodes[gest.i], [mx, my] = toModel(pos);
      n.x = snap(mx - gest.dx);
      n.y = snap(my - gest.dy);
      st.dirty = true;
      renderScene();
    } else if (gest.type === "note" || gest.type === "grip") {
      if (!gest.moved) {
        pushUndo(gest.before);
      }
      const a = st.lab.annotations[gest.i], [mx, my] = toModel(pos);
      if (gest.type === "note") {
        a.x = snap(mx - gest.dx);
        a.y = snap(my - gest.dy);
      } else {
        a.w = Math.min(NOTE_BOX[1], Math.max(NOTE_BOX[0], snap(mx + gest.dx - a.x)));
        a.h = Math.min(NOTE_BOX[1], Math.max(NOTE_BOX[0], snap(my + gest.dy - a.y)));
      }
      st.dirty = true;
      st.sel = {kind: "note", i: gest.i};
      renderScene();
    } else if (gest.type === "pan") {
      view.tx = gest.tx + pos[0] - gest.start[0];
      view.ty = gest.ty + pos[1] - gest.start[1];
      applyView();
      svg.classList.add("dragging");
    }
    gest.moved = true;
  });
  const endPtr = e => {
    if (!ptrs.delete(e.pointerId)) {
      return;
    }
    svg.classList.remove("dragging");
    const g = gest;
    if (ptrs.size || !g) {
      return;
    }
    gest = null;
    if (g.moved) {
      if (g.type === "node") {
        st.sel = {kind: "node", i: g.i};
        render();
      } else if (g.type === "note" || g.type === "grip") {
        st.sel = {kind: "note", i: g.i};
        render();
      }
      return;
    }
    if (g.type === "node") {
      if (st.linking) {
        tapLinking(g.i);
        return;
      }
      if (st.adding && st.sel && st.sel.kind === "segment") {
        tapMember(g.i);
        return;
      }
      st.sel = {kind: "node", i: g.i};
    } else if (g.type === "link") {
      st.sel = {kind: "link", i: g.i};
    } else if (g.type === "note" || g.type === "grip") {
      st.sel = {kind: "note", i: g.i};
    } else {
      st.sel = null;
      st.from = null;
    }
    render();
  };
  /* right click on a device, a link, a text or a box deletes it at once; Ctrl+Z brings it back */
  svg.addEventListener("contextmenu", e => {
    const nodeEl = e.target.closest("[data-node]"), linkEl = e.target.closest("[data-link]");
    const noteEl = !nodeEl && !linkEl && e.target.closest("[data-note]");
    if (!nodeEl && !linkEl && !noteEl) {
      return;
    }
    e.preventDefault();
    /* a long press on a touch screen also opens the context menu: there it must not delete */
    if (e.pointerType && e.pointerType !== "mouse" || gest) {
      return;
    }
    st.sel = nodeEl ? {kind: "node", i: +nodeEl.dataset.node} : linkEl ? {kind: "link", i: +linkEl.dataset.link}
      : {kind: "note", i: +noteEl.dataset.note};
    deleteSel();
  });
  svg.addEventListener("pointerup", endPtr);
  svg.addEventListener("pointercancel", endPtr);
  svg.addEventListener("wheel", e => {
    e.preventDefault();
    const [px, py] = local(e);
    zoomAt(px, py, Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0015)));
  }, {passive: false});

  /* перетаскивание образа из палитры на схему */
  svg.addEventListener("dragover", e => {
    if (e.dataTransfer.types.includes("text/plain")) {
      e.preventDefault();
    }
  });
  svg.addEventListener("drop", e => {
    const img = images.get(e.dataTransfer.getData("text/plain"));
    if (img) {
      e.preventDefault();
      const [x, y] = toModel(local(e));
      addNode(img, x, y);
    }
  });
  palette.addEventListener("dragstart", e => {
    const b = e.target.closest("[data-image]");
    if (b) {
      e.dataTransfer.setData("text/plain", b.dataset.image);
      e.dataTransfer.effectAllowed = "copy";
    }
  });
  palette.addEventListener("input", e => {
    if (!e.target.matches("[data-image-search]")) {
      return;
    }
    const query = e.target.value.toLowerCase();
    palette.querySelectorAll(".ed-image-entry").forEach(entry => { entry.hidden = !entry.dataset.search.includes(query); });
    palette.querySelectorAll("details").forEach(details => {
      details.open = query ? [...details.querySelectorAll(".ed-image-entry")].some(entry => !entry.hidden)
        : details.dataset.defaultOpen === "1";
    });
  });
  const updateCatalog = updated => {
    images.clear();
    updated.forEach(image => images.set(image.id, image));
    renderPalette();
    render();
  };
  const patchImage = async(imageid, args) => {
    const result = await Ajax.call([{methodname: "mod_gns3lab_authoring",
      args: {labid: D.labid, action: "patch", imageid, ...args}}])[0];
    updateCatalog(result.images);
  };
  palette.addEventListener("click", e => {
    const feature = e.target.closest("[data-feature-image]");
    if (feature) {
      const img = images.get(feature.dataset.featureImage);
      patchImage(img.id, {featured: img.featured ? 0 : 1}).catch(Notification.exception);
      return;
    }
    const edit = e.target.closest("[data-edit-image]");
    if (!edit) {
      return;
    }
    const img = images.get(edit.dataset.editImage);
    const dialog = document.createElement("dialog");
    dialog.className = "ed-capture-dialog";
    dialog.innerHTML = `<form><h2>${esc(str("authoringedit"))}</h2>
      <label>${esc(str("authoringtitle"))}<input name="title" required maxlength="100" value="${esc(img.title)}"></label>
      <label>${esc(str("authoringdescription"))}<textarea name="description" maxlength="2000">${
        esc(img.derived.description)}</textarea></label>
      <label><input name="retired" type="checkbox" ${img.retired ? "checked" : ""}> ${esc(str("authoringretire"))}</label>
      <button class="gbtn primary" type="submit">${esc(str("ed_save"))}</button>
      <button class="gbtn" type="button" data-cancel>${esc(str("authoringcancel"))}</button></form>`;
    dialog.querySelector("[data-cancel]").onclick = () => dialog.close();
    dialog.querySelector("form").onsubmit = event => {
      event.preventDefault();
      const form = new FormData(event.target);
      patchImage(img.id, {title: form.get("title"), description: form.get("description"), retired: form.has("retired") ? 1 : 0})
        .then(() => dialog.close()).catch(Notification.exception);
    };
    dialog.addEventListener("close", () => dialog.remove());
    root.append(dialog); dialog.showModal();
  });
  palette.addEventListener("click", e => {
    const b = e.target.closest("[data-image]");
    const img = b && images.get(b.dataset.image);
    if (img) {
      /* новые ноды не ложатся одна на другую */
      const [x, y] = viewCenter(), k = st.lab.nodes.length % 5;
      addNode(img, x + k * 20, y + k * 20);
    }
  });

  /* ---------- панель свойств ---------- */
  const fieldError = text => {
    const p = props.querySelector('[data-region="fielderror"]');
    if (p) {
      p.textContent = text;
      p.hidden = !text;
    }
  };
  const intField = (t, min, max) => {
    const raw = t.value.trim();
    if (raw === "") {
      return null;
    }
    const v = Number(raw);
    return Number.isInteger(v) && v >= min && v <= max ? v : undefined;
  };
  const segmentChange = t => {
    const i = st.sel.i, g = st.lab.segments[i];
    if (t.dataset.pick !== undefined) {
      return;
    }
    if (t.dataset.mhost !== undefined) {
      const j = +t.dataset.mhost, v = intField(t, 1, 65535);
      if (v === undefined) {
        fieldError(str("ed_hostinvalid"));
        t.value = Number.isInteger(g.members[j].host) ? g.members[j].host : "";
        return;
      }
      change(() => {
        if (v === null) {
          delete g.members[j].host;
        } else {
          g.members[j].host = v;
        }
      });
      return;
    }
    const field = t.dataset.seg;
    if (field === "configure") {
      change(() => {
        if (t.checked) {
          g.configure = "student";
        } else {
          delete g.configure;
        }
      });
      return;
    }
    if (field === "name") {
      const name = t.value.trim();
      if (name === g.name) {
        return;
      }
      if (!Seg.NAME_RE.test(name) || st.lab.segments.some(x => x.name === name)) {
        fieldError(str(Seg.NAME_RE.test(name) ? "ed_segnametaken" : "ed_segnameinvalid", name));
        t.value = g.name;
        return;
      }
      change(() => {
        g.name = name;
      });
    } else if (field === "vlan") {
      const v = intField(t, 2, MAX_VLAN);
      if (v === undefined || (v >= 1002 && v <= 1005)) {
        fieldError(str("ed_vlaninvalid"));
        t.value = g.vlan ? g.vlan.fixed : "";
        return;
      }
      change(() => {
        if (v === null) {
          delete g.vlan;
        } else {
          g.vlan = {fixed: v};
        }
      });
    } else if (field === "subnet") {
      const text = t.value.trim();
      if (!Seg.parseSubnet(text)) {
        fieldError(str("ed_subnetinvalid"));
        t.value = (g.subnet || {}).fixed || "";
        return;
      }
      change(() => {
        g.subnet = {fixed: text};
      });
    } else if (field === "gateway") {
      const pick = routerPorts(g.gateway ? Seg.portKey(g.gateway) : "").find(o => o.key === t.value);
      change(() => {
        if (pick) {
          g.gateway = {node: pick.node, adapter: pick.adapter, port: pick.port};
        } else {
          delete g.gateway;
        }
      });
    } else if (field === "gwhost" && g.gateway) {
      const v = intField(t, 1, 65535);
      if (v === undefined) {
        fieldError(str("ed_hostinvalid"));
        t.value = Number.isInteger(g.gateway.host) ? g.gateway.host : "";
        return;
      }
      change(() => {
        if (v === null) {
          delete g.gateway.host;
        } else {
          g.gateway.host = v;
        }
      });
    }
  };
  /* the text of a note follows the typing on the scheme; one undo step for the whole edit */
  let noteBefore = null;
  props.addEventListener("focusin", e => {
    noteBefore = e.target.dataset.nfield === "text" ? snapshot() : null;
  });
  props.addEventListener("input", e => {
    const s = st.sel, t = e.target;
    if (!s || s.kind !== "note" || t.dataset.nfield !== "text") {
      return;
    }
    if (noteBefore !== null) {
      pushUndo(noteBefore);
      noteBefore = null;
    }
    st.lab.annotations[s.i].text = t.value.slice(0, NOTE_TEXT_MAX);
    st.dirty = true;
    renderScene();
    renderChrome();
  });
  const noteChange = t => {
    const a = st.lab.annotations[st.sel.i], f = t.dataset.nfield;
    if (!a || !f) {
      return;
    }
    if (f === "text") {
      render(); // the text is already in the lab: the heading of the panel catches up
      return;
    }
    if (f === "color" || f === "fill") {
      if (/^#[0-9a-fA-F]{6}$/.test(t.value) && t.value.toLowerCase() !== a[f]) {
        change(() => {
          a[f] = t.value.toLowerCase();
        });
      }
      return;
    }
    const [min, max] = f === "size" ? NOTE_SIZE : NOTE_BOX;
    const v = intField(t, min, max);
    if (v === undefined || v === null) {
      fieldError(str("ed_range", `${min}…${max}`));
      t.value = a[f] || "";
      return;
    }
    if (v !== a[f]) {
      change(() => {
        a[f] = v;
      });
    }
  };
  props.addEventListener("change", e => {
    const s = st.sel, t = e.target;
    if (!s) {
      if (!setupForm.change(t)) {
        checksForm.change(t);
      }
      return;
    }
    if (s.kind === "segment") {
      segmentChange(t);
      return;
    }
    if (s.kind === "note") {
      noteChange(t);
      return;
    }
    if (s.kind === "link" && t.dataset.end) {
      const [a, p] = t.value.split("/").map(Number);
      const node = byName(st.lab.links[s.i][t.dataset.end].node);
      if (!freePorts(node, s.i).some(port => port.adapter === a && port.port === p)) {
        render();
        return;
      }
      change(() => {
        Object.assign(st.lab.links[s.i][t.dataset.end], {adapter: a, port: p});
      });
      return;
    }
    const n = st.lab.nodes[s.i], field = t.dataset.field;
    if (n && t.dataset.disk !== undefined && Array.isArray(n.disks) && n.disks[+t.dataset.disk]) {
      const img = imageOf(n), size = img && img.extra_disks ? img.extra_disks.size_gb : {min: 1, max: 1024};
      const v = Number(t.value.trim()), disk = n.disks[+t.dataset.disk];
      if (!Number.isInteger(v) || v < size.min || v > size.max) {
        fieldError(str("ed_range", `${size.min}…${size.max}`));
        t.value = disk.size_gb;
        return;
      }
      change(() => {
        disk.size_gb = v;
      });
      return;
    }
    if (!n || !field) {
      return;
    }
    if (field === "name") {
      const name = t.value.trim();
      if (name === n.name) {
        return;
      }
      if (!NAME_RE.test(name) || byName(name)) {
        fieldError(str(NAME_RE.test(name) ? "ed_nametaken" : "ed_nameinvalid", name));
        t.value = n.name;
        return;
      }
      change(() => {
        st.lab.links.forEach(l => ["a", "b"].forEach(side => {
          if (l[side].node === n.name) {
            l[side].node = name;
          }
        }));
        st.lab.segments.forEach(g => {
          g.members.concat(g.gateway ? [g.gateway] : []).forEach(m => {
            if (m.node === n.name) {
              m.node = name;
            }
          });
        });
        n.name = name;
      });
      return;
    }
    if (field === "extra_consoles") {
      const v = Number(t.value);
      change(() => {
        if (Number.isInteger(v) && v > 0) {
          n.extra_consoles = v;
        } else {
          delete n.extra_consoles;
        }
      });
      return;
    }
    if (field === "random_ifnames" || field === "nopaste" || field === "noconsole") {
      change(() => {
        if (t.checked) {
          n[field] = true;
        } else {
          delete n[field];
        }
      });
      return;
    }
    const r = range(n, field), raw = t.value.trim();
    const offset = field === "adapters" ? adapterOffset(n) : 0;
    const v = raw === "" ? null : Number(raw) + offset;
    if (v !== null && (!Number.isInteger(v) || v < r.min || v > r.max)) {
      fieldError(str("ed_range", `${r.min - offset}…${r.max - offset}`));
      t.value = Number.isInteger(n[field]) ? n[field] - offset : "";
      return;
    }
    if (field === "adapters") {
      const count = v === null ? r.default : v;
      const busy = st.lab.links.some(l => ["a", "b"].some(side => l[side].node === n.name && l[side].adapter >= count))
        || st.lab.segments.some(g => g.members.concat(g.gateway ? [g.gateway] : [])
          .some(m => m.node === n.name && m.adapter >= count));
      if (busy) {
        fieldError(str("ed_adaptersinuse"));
        t.value = Number.isInteger(n.adapters) ? n.adapters - offset : "";
        return;
      }
    }
    change(() => {
      if (v === null) {
        delete n[field];
      } else {
        n[field] = v;
      }
    });
  });
  props.addEventListener("click", e => {
    const go = e.target.closest("[data-goto]");
    if (go) {
      const [kind, i] = go.dataset.goto.split(":");
      st.sel = {kind, i: +i};
      render();
      return;
    }
    const mdel = e.target.closest("[data-mdel]");
    if (mdel && st.sel && st.sel.kind === "segment") {
      change(() => {
        st.lab.segments[st.sel.i].members.splice(+mdel.dataset.mdel, 1);
      });
      return;
    }
    const goNode = e.target.closest("[data-select-node]");
    if (goNode) {
      const idx = st.lab.nodes.findIndex(n => n.name === goNode.dataset.selectNode);
      if (idx >= 0) {
        st.sel = {kind: "node", i: idx};
        render();
      }
      return;
    }
    const toggle = e.target.closest("[data-ntoggle]"), swatch = e.target.closest("[data-ncolor]");
    if ((toggle || swatch) && st.sel && st.sel.kind === "note") {
      const a = st.lab.annotations[st.sel.i];
      change(() => {
        if (toggle) {
          const f = toggle.dataset.ntoggle;
          if (a[f]) {
            delete a[f];
          } else {
            a[f] = true;
          }
        } else if (swatch.dataset.value) {
          a[swatch.dataset.ncolor] = swatch.dataset.value;
        } else {
          delete a[swatch.dataset.ncolor]; // the colour of the theme or of the box
        }
      });
      return;
    }
    const iconButton = e.target.closest("[data-icon]");
    if (iconButton && st.sel && st.sel.kind === "node") {
      const n = st.lab.nodes[st.sel.i], icon = iconButton.dataset.icon;
      if ((n.icon || "") !== icon) {
        change(() => {
          if (icon) {
            n.icon = icon;
          } else {
            delete n.icon; // no field at all: the icon of the image
          }
        });
      }
      return;
    }
    const act = e.target.closest("[data-act]");
    const name = act && act.dataset.act;
    if (name === "delete") {
      deleteSel();
    } else if (name === "adddisk" && st.sel && st.sel.kind === "node") {
      const n = st.lab.nodes[st.sel.i], img = n && imageOf(n), spec = img && img.extra_disks;
      if (spec && (n.disks || []).length < spec.max) {
        change(() => {
          n.disks = (n.disks || []).concat([{"size_gb": spec.size_gb.default}]);
        });
      }
    } else if (name === "removedisk" && st.sel && st.sel.kind === "node") {
      const n = st.lab.nodes[st.sel.i];
      if (n && Array.isArray(n.disks)) {
        change(() => {
          n.disks.splice(+act.dataset.disk, 1);
          if (!n.disks.length) {
            delete n.disks; // no field at all, so the lab_def of a lab without disks does not change
          }
        });
      }
    } else if (checksForm.action(name, act)) {
      // The checks block took it (checksform.js).
    } else if (setupForm.action(name, act)) {
      // The setup block took it (setupform.js).
    } else if (name === "addsegment") {
      addSegment();
    } else if (name === "addmembers" && st.sel && st.sel.kind === "segment") {
      st.adding = !st.adding;
      st.pick = null;
      render();
    } else if (name === "pickadd" && st.sel && st.sel.kind === "segment" && st.pick) {
      const select = props.querySelector("[data-pick]");
      const [a, p] = (select ? select.value : "").split("/").map(Number);
      if (Number.isInteger(a) && Number.isInteger(p)) {
        addMember(st.sel.i, st.pick, a, p);
      }
    }
  });

  /* ---------- сохранение, проверка, публикация ---------- */
  const call = (methodname, args) => Ajax.call([{methodname, args}])[0];
  const busy = on => {
    st.busy = on;
    renderChrome();
  };
  const run = async(text, fn) => {
    if (st.busy) {
      return;
    }
    busy(true);
    toast(text);
    try {
      await fn();
    } catch (err) {
      $("toast").hidden = true;
      Notification.exception(err);
    } finally {
      busy(false);
      render();
    }
  };
  const save = () => run(str("ed_saving"), async() => {
    const r = await call("mod_gns3lab_save_lab_draft", {labid: D.labid, definition: definition()});
    st.version = r.version;
    st.status = "draft";
    st.dirty = false;
    toast(str("ed_saved"));
  });
  const check = () => run(str("ed_checking"), async() => {
    const r = await call("mod_gns3lab_validate_lab", {labid: D.labid, definition: definition()});
    setProblems(r.problems);
    st.warnings = withPlace(r.warnings);
    st.resources = r.valid ? {memorymb: r.memorymb, cores: r.cores} : null;
    st.sel = null;
    const text = r.valid ? str("ed_valid", {memory: fmtMem(r.memorymb), cores: r.cores}) : str("ed_invalid", r.problems.length);
    toast(text, !r.valid);
  });
  const publish = () => {
    if (!st.lab.nodes.length) {
      toast(str("ed_empty"), true);
      return;
    }
    run(str("ed_publishing"), async() => {
      const r = await call("mod_gns3lab_publish_lab", {labid: D.labid, definition: definition()});
      /* черновик сохранён в обоих случаях */
      st.dirty = false;
      st.warnings = [];
      setProblems(r.problems);
      st.sel = null;
      if (r.published) {
        st.status = "published";
        st.version = r.version;
        toast(str("ed_published", r.version));
      } else {
        st.status = "draft";
        toast(str("ed_invalid", r.problems.length), true);
      }
    });
  };

  root.querySelector(".tools").addEventListener("click", e => {
    const b = e.target.closest("[data-act]");
    const act = b && b.dataset.act;
    if (act === "undo") {
      undo();
    } else if (act === "link") {
      setLinking(!st.linking);
    } else if (act === "addtext" || act === "addbox") {
      addNote(act === "addtext" ? "text" : "box");
    } else if (act === "preview") {
      st.preview = !st.preview;
      render();
    } else if (act === "fit") {
      fit();
    } else if (act === "save") {
      save();
    } else if (act === "check") {
      check();
    } else if (act === "publish") {
      publish();
    }
  });
  root.addEventListener("keydown", e => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName);
    if (e.key === "Escape") {
      if (st.adding) {
        st.adding = false;
        st.pick = null;
        render();
      } else if (st.linking) {
        setLinking(false);
      } else if (!typing) {
        st.sel = null;
        render();
      }
    } else if (typing) {
      return;
    } else if ((e.key === "Delete" || e.key === "Backspace") && st.sel) {
      e.preventDefault();
      deleteSel();
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
      e.preventDefault();
      undo();
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
      e.preventDefault();
      save();
    }
  });
  window.addEventListener("beforeunload", e => {
    if (st.dirty) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  /* the side columns fold into tabs on the edges of the scheme; the browser remembers it, the scheme keeps its centre */
  const FOLD_KEY = "editor.fold";
  const folds = Object.assign({palette: false, props: false}, store.get(FOLD_KEY, {}));
  const applyFolds = () => {
    ["palette", "props"].forEach(side => {
      const hidden = folds[side] === true;
      root.classList.toggle("ed-no-" + side, hidden);
      const b = root.querySelector(`[data-fold="${side}"]`);
      if (b) {
        const label = str(`ed_fold_${side}_${hidden ? "show" : "hide"}`);
        b.setAttribute("aria-expanded", String(!hidden));
        b.title = label;
        b.setAttribute("aria-label", label);
      }
    });
  };
  root.addEventListener("click", e => {
    const b = e.target.closest("[data-fold]");
    if (!b) {
      return;
    }
    const [mx, my] = viewCenter();
    folds[b.dataset.fold] = !folds[b.dataset.fold];
    store.set(FOLD_KEY, folds);
    applyFolds();
    const r = svg.getBoundingClientRect();
    view.tx = r.width / 2 - mx * view.k;
    view.ty = r.height / 2 - my * view.k;
    applyView();
  });
  applyFolds();

  /* the edges of the side columns are dragged (or moved with the arrow keys) to set their width; the browser remembers
     it, a double click brings the usual width back, the scheme keeps its centre */
  const WIDTH_KEY = "editor.widths";
  const WIDTH = {palette: {def: 220, min: 160, max: 420}, props: {def: 440, min: 320, max: 900}};
  const MIN_SCHEME = 240;
  const widths = Object.assign({}, store.get(WIDTH_KEY, {}));
  const column = side => root.querySelector(side === "palette" ? ".ed-palette" : ".ed-props");
  /* the widest a column may get: its own limit, and the scheme keeps at least MIN_SCHEME pixels */
  const maxWidth = side => {
    const stage = root.querySelector(".stage").getBoundingClientRect().width;
    const other = side === "palette" ? "props" : "palette";
    const otherCol = column(other);
    const taken = otherCol && otherCol.offsetParent !== null ? otherCol.getBoundingClientRect().width : 0;
    return Math.max(WIDTH[side].min, Math.min(WIDTH[side].max, stage - taken - MIN_SCHEME));
  };
  const clampWidth = (side, w) => Math.round(Math.min(maxWidth(side), Math.max(WIDTH[side].min, w)));
  const applyWidths = () => {
    ["palette", "props"].forEach(side => {
      if (typeof widths[side] === "number") {
        root.style.setProperty(`--ed-${side}-w`, `${widths[side]}px`);
      } else {
        root.style.removeProperty(`--ed-${side}-w`);
      }
      const h = root.querySelector(`[data-resize="${side}"]`);
      if (h) {
        h.setAttribute("aria-valuemin", String(WIDTH[side].min));
        h.setAttribute("aria-valuemax", String(WIDTH[side].max));
        h.setAttribute("aria-valuenow", String(widths[side] ?? WIDTH[side].def));
      }
    });
  };
  /* sets a width and keeps the point of the scheme under its centre where it was */
  const setWidth = (side, w) => {
    const [mx, my] = viewCenter();
    if (w === null) {
      delete widths[side];
    } else {
      widths[side] = clampWidth(side, w);
    }
    applyWidths();
    const r = svg.getBoundingClientRect();
    view.tx = r.width / 2 - mx * view.k;
    view.ty = r.height / 2 - my * view.k;
    applyView();
  };
  root.querySelectorAll("[data-resize]").forEach(h => {
    const side = h.dataset.resize;
    /* the device list is on the left: dragging right widens it; the properties are on the right: dragging left */
    const sign = side === "palette" ? 1 : -1;
    h.addEventListener("pointerdown", e => {
      if (e.button !== 0) {
        return;
      }
      e.preventDefault();
      const x0 = e.clientX, w0 = column(side).getBoundingClientRect().width;
      h.setPointerCapture(e.pointerId);
      h.classList.add("on");
      root.classList.add("ed-resizing");
      const move = ev => setWidth(side, w0 + sign * (ev.clientX - x0));
      const up = () => {
        h.removeEventListener("pointermove", move);
        h.removeEventListener("pointerup", up);
        h.removeEventListener("pointercancel", up);
        h.classList.remove("on");
        root.classList.remove("ed-resizing");
        store.set(WIDTH_KEY, widths);
      };
      h.addEventListener("pointermove", move);
      h.addEventListener("pointerup", up);
      h.addEventListener("pointercancel", up);
    });
    h.addEventListener("dblclick", () => {
      setWidth(side, null);
      store.set(WIDTH_KEY, widths);
    });
    h.addEventListener("keydown", e => {
      const step = e.shiftKey ? 64 : 16;
      const w = column(side).getBoundingClientRect().width;
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        setWidth(side, w + sign * (e.key === "ArrowRight" ? step : -step));
      } else if (e.key === "Home" || e.key === "End") {
        setWidth(side, e.key === "Home" ? WIDTH[side].min : WIDTH[side].max);
      } else if (e.key === "Enter") {
        setWidth(side, null);
      } else {
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      store.set(WIDTH_KEY, widths);
    });
  });
  /* a saved width larger than a narrower window allows shrinks back with it */
  window.addEventListener("resize", () => {
    ["palette", "props"].forEach(side => {
      if (typeof widths[side] === "number" && widths[side] > maxWidth(side)) {
        widths[side] = clampWidth(side, widths[side]);
      }
    });
    applyWidths();
  });
  applyWidths();

  initImages(root, D, {
    definition,
    catalog: updateCatalog,
    nodes: () => st.lab.nodes,
    image: id => images.get(id),
    saved: version => { st.version = version; st.status = "draft"; st.dirty = false; renderChrome(); },
    applied: result => {
      pushUndo(snapshot());
      const replacements = new Map(result.capture.images.map(item => [item.name, item.image]));
      st.lab.nodes.forEach(node => { node.image = replacements.get(node.name) || node.image; });
      st.version = result.version; st.status = "draft"; st.dirty = false;
      updateCatalog(result.images);
    },
  });
  renderPalette();
  render();
  fit();
};
