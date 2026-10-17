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
 * Edits a lab definition gns3lab.lab/1: nodes from the image catalog of the lab manager and links between
 * their ports. The definition is checked by the lab manager (validate), saved as a draft or published
 * through the external functions of the plugin. Node x, y are the top left corner of the node, as in .gns3.
 *
 * @module     mod_gns3lab/editor
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

import Ajax from 'core/ajax';
import Notification from 'core/notification';

const NS = "http://www.w3.org/2000/svg";
const FORMAT = "gns3lab.lab/1";
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;
const W = 60, H = 45;
const KMIN = 0.2, KMAX = 3;
const GRID = 10;
const UNDO_MAX = 50;
/* иконка и префикс имени по категории образа из каталога менеджера */
const ICON = {router: "router", switch: "switch", host: "pc", pc: "pc", server: "server", firewall: "firewall", cloud: "cloud"};
const PREFIX = {router: "R", switch: "SW", host: "PC", pc: "PC", server: "SRV", firewall: "FW", cloud: "NET"};
const CATEGORY_ORDER = ["router", "switch", "firewall", "server", "host", "pc", "cloud"];

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

  const def = D.definition || {};
  const st = {
    lab: {nodes: Array.isArray(def.nodes) ? def.nodes : [], links: Array.isArray(def.links) ? def.links : []},
    sel: null,
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
  const byName = name => st.lab.nodes.find(n => n.name === name) || null;
  const portsOf = n => {
    const out = [];
    for (let a = 0; a < Math.min(adaptersOf(n), 64); a++) {
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
    const base = PREFIX[img.category] || "N";
    for (let i = 1; ; i++) {
      if (!byName(base + i)) {
        return base + i;
      }
    }
  };
  const definition = () => JSON.stringify({format: FORMAT, nodes: st.lab.nodes, links: st.lab.links});

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
    st.dirty = true;
    st.problems = [];
    st.resources = null;
    render();
  };

  const addNode = (img, cx, cy) => {
    if (st.lab.nodes.length >= maxNodes) {
      toast(str("ed_maxnodes", maxNodes), true);
      return;
    }
    change(() => {
      const n = {name: nextName(img), image: img.id, x: snap(cx - W / 2), y: snap(cy - H / 2)};
      if (img.adapters && typeof img.adapters === "object") {
        /* число адаптеров определяет порты и линки: храним его, а не полагаемся на умолчание образа */
        n.adapters = img.adapters.default;
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
        st.lab.links = st.lab.links.filter(l => l.a.node !== name && l.b.node !== name);
      } else {
        st.lab.links.splice(s.i, 1);
      }
      st.sel = null;
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
    render();
  };

  /* ---------- замечания менеджера ---------- */
  const placeOf = path => {
    const m = /^(nodes|links)\[(\d+)\]/.exec(path || "");
    return m ? {kind: m[1] === "nodes" ? "node" : "link", i: +m[2]} : null;
  };
  const problemSets = () => {
    const nodes = new Set(), links = new Set();
    st.problems.forEach(p => {
      if (p.place && p.place.kind === "node") {
        nodes.add(p.place.i);
      } else if (p.place) {
        links.add(p.place.i);
      }
    });
    return {nodes, links};
  };
  const setProblems = list => {
    st.problems = (list || []).map(p => Object.assign({}, p, {place: placeOf(p.path)}));
  };
  const placeName = place => {
    if (!place) {
      return str("ed_lab");
    }
    if (place.kind === "node") {
      return (st.lab.nodes[place.i] || {}).name || "?";
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
      const cls = "node" + (sel ? " sel" : "") + (st.from === n.name ? " from" : "") + (bad.nodes.has(i) ? " bad" : "")
        + (imageOf(n) ? "" : " unknown");
      const g = el("g", {class: cls, "data-node": i, role: "button", "aria-label": n.name}, lyr.nodes);
      el("rect", {class: "halo", x: cx - 31, y: cy - 31, width: 62, height: 62, rx: 12}, g);
      el("use", {href: `#${prefix}-ico-${ICON[category(n)] || "server"}`, x: cx - 22, y: cy - 22, width: 44, height: 44}, g);
      /* имя — над подписями портов, как во вьюере */
      const t = el("text", {class: "nname", x: cx, y: cy + 42, "text-anchor": "middle", "data-node": i}, lyr.names);
      t.textContent = n.name;
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
    palette.innerHTML = `<p class="eyebrow">${esc(str("ed_catalog"))}</p>` + list.map(img =>
      `<button class="ed-img" type="button" draggable="true" data-image="${esc(img.id)}" title="${esc(img.title)}">
        <svg viewBox="0 0 48 48" aria-hidden="true"><use href="#${prefix}-ico-${ICON[img.category] || "server"}"/></svg>
        <span>${esc(img.title || img.id)}</span></button>`).join("");
  };

  const fmtMem = mb => mb >= 1024 ? (mb / 1024).toFixed(1).replace(/\.0$/, "") + " GB" : mb + " MB";
  const problemsHTML = () => !st.problems.length ? "" : `<h3>${esc(str("ed_problems"))}</h3><ul class="ed-problems">` +
    st.problems.map(p => {
      const text = S["problem_" + p.code] || str("ed_problem", p.code);
      const attr = p.place ? ` data-goto="${p.place.kind}:${p.place.i}"` : "";
      return `<li><button type="button" class="rowbtn"${attr} title="${esc(p.message)}">` +
        `<b>${esc(placeName(p.place))}</b>: ${esc(text)}</button></li>`;
    }).join("") + "</ul>";

  const numberField = (n, key, label) => {
    const r = range(n, key);
    if (!r) {
      return "";
    }
    const v = Number.isInteger(n[key]) ? n[key] : "";
    return `<label class="ed-field"><span>${esc(label)}</span>
      <input type="number" class="form-control form-control-sm" data-field="${key}" min="${+r.min}" max="${+r.max}"
        step="1" value="${v}" placeholder="${+r.default}"><small>${+r.min}…${+r.max}</small></label>`;
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
        <input type="text" class="form-control form-control-sm" data-field="name" value="${esc(n.name)}" maxlength="64"
          autocomplete="off" spellcheck="false"></label>
      ${numberField(n, "adapters", str("ed_adapters"))}
      ${numberField(n, "ram_mb", str("ed_ram"))}
      ${numberField(n, "cpus", str("ed_cpus"))}
      <p class="ed-error" data-region="fielderror" role="alert" hidden></p>
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

  const labHTML = () => {
    const res = st.resources ? `<p class="ed-ok">${esc(str("ed_valid", {
      memory: fmtMem(st.resources.memorymb), cores: st.resources.cores}))}</p>` : "";
    return `<div class="ph"><div><p class="eyebrow">${esc(str("ed_lab"))}</p>
      <h2>${esc(str("ed_summary", {nodes: st.lab.nodes.length, links: st.lab.links.length}))}</h2></div></div>
      ${res}${problemsHTML()}
      ${D.canpublish ? "" : `<p class="ed-note">${esc(str("ed_cannotpublish"))}</p>`}
      <p class="muted">${esc(str("ed_hint"))}</p>`;
  };

  const renderProps = () => {
    const s = st.sel;
    props.innerHTML = !s ? labHTML() : s.kind === "node" ? nodeHTML(s.i) : linkHTML(s.i);
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
    $("hint").textContent = !st.linking ? str("ed_hint") : st.from ? str("ed_hintlinkto", st.from) : str("ed_hintlink");
    svg.classList.toggle("linking", st.linking);
  };

  const render = () => {
    if (st.sel && !(st.sel.kind === "node" ? st.lab.nodes : st.lab.links)[st.sel.i]) {
      st.sel = null;
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
    if (!st.lab.nodes.length) {
      view.k = 1;
      view.tx = r.width / 2;
      view.ty = r.height / 2;
      applyView();
      return;
    }
    const xs = st.lab.nodes.map(n => n.x), ys = st.lab.nodes.map(n => n.y);
    const x0 = Math.min(...xs) - 50, y0 = Math.min(...ys) - 50, x1 = Math.max(...xs) + W + 50, y1 = Math.max(...ys) + H + 70;
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
    const start = local(e);
    if (nodeEl) {
      const i = +nodeEl.dataset.node, n = st.lab.nodes[i], [mx, my] = toModel(start);
      gest = {type: "node", i, dx: mx - n.x, dy: my - n.y, start, moved: false, before: snapshot()};
    } else if (linkEl) {
      gest = {type: "link", i: +linkEl.dataset.link, start, moved: false};
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
      }
      return;
    }
    if (g.type === "node") {
      if (st.linking) {
        tapLinking(g.i);
        return;
      }
      st.sel = {kind: "node", i: g.i};
    } else if (g.type === "link") {
      st.sel = {kind: "link", i: g.i};
    } else {
      st.sel = null;
      st.from = null;
    }
    render();
  };
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
  props.addEventListener("change", e => {
    const s = st.sel, t = e.target;
    if (!s) {
      return;
    }
    if (s.kind === "link" && t.dataset.end) {
      const [a, p] = t.value.split("/").map(Number);
      change(() => {
        Object.assign(st.lab.links[s.i][t.dataset.end], {adapter: a, port: p});
      });
      return;
    }
    const n = st.lab.nodes[s.i], field = t.dataset.field;
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
        n.name = name;
      });
      return;
    }
    const r = range(n, field), raw = t.value.trim();
    const v = raw === "" ? null : Number(raw);
    if (v !== null && (!Number.isInteger(v) || v < r.min || v > r.max)) {
      fieldError(str("ed_range", `${r.min}…${r.max}`));
      t.value = Number.isInteger(n[field]) ? n[field] : "";
      return;
    }
    if (field === "adapters") {
      const count = v === null ? r.default : v;
      const busy = st.lab.links.some(l => ["a", "b"].some(side => l[side].node === n.name && l[side].adapter >= count));
      if (busy) {
        fieldError(str("ed_adaptersinuse"));
        t.value = Number.isInteger(n.adapters) ? n.adapters : "";
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
    if (e.target.closest('[data-act="delete"]')) {
      deleteSel();
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
      if (st.linking) {
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

  renderPalette();
  render();
  fit();
};
