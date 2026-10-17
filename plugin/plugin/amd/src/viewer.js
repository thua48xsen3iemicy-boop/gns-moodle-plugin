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
 * Topology viewer: .gns3 rendered as SVG, layers L1/L2/L3/OSPF from device facts, details panel.
 *
 * Ported from prototypes/topology-viewer.html; the prototype stays the reference for behaviour.
 * Talks to mod_gns3lab/lab through events on the root element: listens to gns3lab:session (show the live
 * topology and node states) and gns3lab:showport (a port name clicked in a console), sends gns3lab:openconsole.
 * UI strings are still Russian literals here; moving them to lang strings is a follow-up.
 *
 * @module     mod_gns3lab/viewer
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

// Straight port of the prototype; these style rules stay relaxed until it is split into modules.
/* eslint-disable curly, max-len, jsdoc/require-jsdoc, no-eq-null */

/**
 * Starts the viewer.
 *
 * @param {string} prefix id prefix of the template rendering ({{uniqid}})
 */
export const init = (prefix) => {
const root = document.getElementById(prefix + "-app");
if (!root || root.dataset.ready) {
  return;
}
root.dataset.ready = "1";
const fullscreen = root.classList.contains("is-fullscreen");
const DATA = JSON.parse(root.dataset.json || "{}");
const NS = "http://www.w3.org/2000/svg";
const GiB = 1024 ** 3, MiB = 1024 ** 2;

/* ---------- справочники ---------- */
const TYPE_NAME = { qemu: "QEMU VM", iou: "IOU", dynamips: "Dynamips", vpcs: "VPCS", docker: "Docker",
  ethernet_switch: "Встроенный свитч", ethernet_hub: "Хаб", cloud: "Cloud", nat: "NAT",
  virtualbox: "VirtualBox", vmware: "VMware", frame_relay_switch: "Frame Relay", atm_switch: "ATM-свитч", traceng: "TraceNG" };
const GSTATUS = { running: "работает", stopped: "остановлена", paused: "пауза" };
const fmtMem = b => !b ? "—" : b >= GiB ? (b / GiB).toFixed(1).replace(".", ",") + " ГБ" : Math.round(b / MiB) + " МБ";
const CAT_NAME = { hypervisor: ["Гипервизор", "Гипервизоры"], router: ["Маршрутизатор", "Маршрутизаторы"], switch: ["Коммутатор", "Коммутаторы"], pc: ["Хост", "Хосты"],
  server: ["Сервер / VM", "Серверы и VM"], cloud: ["Внешняя сеть", "Внешние сети"], firewall: ["Межсетевой экран", "Межсетевые экраны"] };
const STATUS_NAME = { started: "Запущено", stopped: "Остановлено", suspended: "Приостановлено" };
const CONSOLE_NAME = { telnet: "Терминал (telnet → xterm.js)", vnc: "Экран (VNC → noVNC)", spice: "Экран (SPICE)", http: "Веб-интерфейс", https: "Веб-интерфейс", none: "нет" };

const typeName = t => TYPE_NAME[t] || t || "неизвестно";
const plural = (n, f) => { const a = n % 10, b = n % 100; return f[(a === 1 && b !== 11) ? 0 : (a >= 2 && a <= 4 && (b < 10 || b >= 20)) ? 1 : 2]; };
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sel$ = s => document.getElementById(prefix + "-" + s);
function el(tag, attrs, parent){
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) if (attrs[k] != null) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}
function cat(n){
  const s = (n.symbol || "").toLowerCase(), t = n.type;
  const img = String((n.props && (n.props.hda_disk_image || n.props.path || n.props.image)) || "");
  if (/proxmox|\bpve/i.test(s + " " + img)) return "hypervisor";
  if (t === "cloud" || t === "nat" || /cloud|nat\b|nat\./.test(s)) return "cloud";
  if (/firewall|asa/.test(s)) return "firewall";
  if (t === "ethernet_switch" || t === "ethernet_hub" || t === "frame_relay_switch" || t === "atm_switch" || /switch|hub/.test(s)) return "switch";
  if (/router/.test(s)) return "router";
  if (t === "vpcs" || /vpcs|computer|\bpc|laptop|workstation/.test(s)) return "pc";
  return "server";
}
function portName(node, adapter, port){
  /* в живой топологии сессии properties пустые, но GNS3 отдаёт ports с именами портов устройства */
  const gp = node.ports && node.ports.find(x => x.adapter_number === adapter && x.port_number === port);
  if (gp && (gp.short_name || gp.name)) return gp.short_name || gp.name;
  const pm = node.props && node.props.ports_mapping;
  if (Array.isArray(pm)) { const p = pm.find(x => x.port_number === port); if (p && p.name) return p.name; }
  if (node.type === "ethernet_switch" || node.type === "ethernet_hub") return "Ethernet" + port;
  return adapter + "/" + port;
}
function fmtFilters(f){
  const out = [];
  if (!f) return out;
  if (f.delay && f.delay.length) out.push({ k: "Задержка", v: f.delay[0] + " мс" + (f.delay[1] ? " ± " + f.delay[1] : ""), s: f.delay[0] + (f.delay[1] ? "±" + f.delay[1] : "") + " мс" });
  if (f.packet_loss && f.packet_loss.length) out.push({ k: "Потери", v: f.packet_loss[0] + " %", s: "loss " + f.packet_loss[0] + "%" });
  if (f.corrupt && f.corrupt.length) out.push({ k: "Порча пакетов", v: f.corrupt[0] + " %", s: "corrupt " + f.corrupt[0] + "%" });
  if (f.frequency_drop && f.frequency_drop.length) out.push({ k: "Дроп", v: "каждый " + f.frequency_drop[0] + "-й пакет", s: "drop 1/" + f.frequency_drop[0] });
  if (f.bpf && f.bpf.length) out.push({ k: "BPF-фильтр", v: String(f.bpf[0]), s: "bpf" });
  return out;
}
const natSort = (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true });

/* ---------- факты из лабы и слои L1/L2/L3/маршрутизация (формат и правила — docs/live-state.md) ---------- */
const FACTS = DATA.facts || {};
const FACTS_NAME = { ok: "конфиг верный", broken: "конфиг с ошибками" };
const LAYER_NAME = { l1: "L1 · физика", l2: "L2 · VLAN", l3: "L3 · IP", rt: "OSPF" };
const SEV = { bad: 0, warn: 1, info: 2 };
const NODE_OFF = { stopped: "остановлен", suspended: "на паузе" };

function ip4(s){
  const p = String(s).split("."); if (p.length !== 4) return null;
  let v = 0;
  for (const x of p) { if (!/^\d{1,3}$/.test(x) || +x > 255) return null; v = v * 256 + +x; }
  return v;
}
const ipStr = v => [Math.floor(v / 16777216) % 256, Math.floor(v / 65536) % 256, Math.floor(v / 256) % 256, v % 256].join(".");
function cidr(s){
  const [a, p] = String(s).split("/"), addr = ip4(a), pre = p == null ? 32 : +p;
  if (addr == null || !(pre >= 0 && pre <= 32)) return null;
  const size = 2 ** (32 - pre), net = Math.floor(addr / size) * size;
  return { addr, pre, net, size, ip: a, key: ipStr(net) + "/" + pre };
}
const hostPart = c => c.pre >= 24 ? "." + (c.addr % 256) : c.ip;
const v4 = i => (i && Array.isArray(i.ipv4) ? i.ipv4.map(cidr).filter(Boolean) : []);
const overlap = (A, B) => A.net < B.net + B.size && B.net < A.net + A.size;

function indexFacts(m, facts){
  if (!facts || !facts.nodes) return null;
  const fx = { nodes: new Map(), byPort: new Map(), byRid: new Map(), at: facts.generated_at || null };
  m.nodes.forEach(n => {
    const f = facts.nodes[n.id]; if (!f) return;
    const ifs = Array.isArray(f.interfaces) ? f.interfaces.filter(i => i && i.name) : null;
    const rec = { id: n.id, status: f.status || null, ifs, byName: new Map(), rid: f.router_id || null,
      adj: Array.isArray(f.adjacencies) ? f.adjacencies : [], vlans: Array.isArray(f.vlans) ? f.vlans : [] };
    (ifs || []).forEach(i => {
      rec.byName.set(i.name, i);
      if (i.port) fx.byPort.set(n.id + "|" + i.port.adapter + "/" + i.port.port, i);
    });
    rec.subs = name => (ifs || []).filter(i => i.parent === name);
    if (rec.rid) fx.byRid.set(rec.rid, n.id);
    fx.nodes.set(n.id, rec);
  });
  return fx;
}
const endIf = (fx, e) => (fx && fx.byPort.get(e.nodeId + "|" + e.adapter + "/" + e.port)) || null;
const portOf = (f, i) => i.port || (i.parent && f.byName.get(i.parent) && f.byName.get(i.parent).port) || null;
const endIs = (e, nodeId, p) => e.nodeId === nodeId && e.adapter === p.adapter && e.port === p.port;

/* L1: линк не работает, если приостановлен, нода остановлена или интерфейс выключен / без несущей */
function deriveL1(m, fx){
  const links = new Map(), issues = [];
  m.links.forEach(l => {
    const hard = [], soft = [];
    let cfg = false;                                   // причина в настройке устройства, а не в состоянии лабы
    if (l.suspend) hard.push("линк приостановлен в GNS3");
    [l.a, l.b].forEach(e => {
      const N = m.nodes.get(e.nodeId), f = fx.nodes.get(e.nodeId), i = endIf(fx, e);
      if (f && NODE_OFF[f.status]) hard.push(`${N.name} ${NODE_OFF[f.status]}`);
      else if (i && i.admin === "down") { hard.push(`${N.name} ${i.name}: shutdown`); cfg = true; }
      else if (i && i.oper === "down") soft.push(`${N.name} ${i.name}: нет несущей`);
    });
    const why = hard.length ? hard : soft;
    if (!hard.length && soft.length) cfg = true;
    links.set(l.id, { down: why.length > 0, why });
    if (why.length) issues.push({ sev: cfg ? "bad" : "info",
      text: `Линк ${epName(m, l.a)} — ${epName(m, l.b)} не работает: ${why.join("; ")}.`, go: { kind: "link", id: l.id } });
  });
  return { links, issues };
}
const epName = (m, e) => m.nodes.get(e.nodeId).name + " " + e.text;

/* L2: что порт шлёт и принимает на проводе — "u" (без тега) и номера тегов dot1q */
function wireTags(f, i){
  const sp = i.switchport;
  if (sp && sp.mode === "access") return { sw: true, sp, set: new Set(["u"]), any: false, map: t => t === "u" ? +sp.vlan : null };
  if (sp && sp.mode === "trunk") {
    const nat = sp.native != null ? +sp.native : 1, all = sp.allowed == null || sp.allowed === "all";
    const allowed = all ? null : new Set(sp.allowed.map(Number)), set = new Set(["u"]);
    if (allowed) allowed.forEach(v => { if (v !== nat) set.add(v); });
    return { sw: true, sp, set, any: all, nat, map: t => t === "u" ? nat : t !== nat && (all || allowed.has(t)) ? t : null };
  }
  if (sp) return null;
  const subs = f.subs(i.name), set = new Set();
  if (v4(i).length || !subs.length) set.add("u");
  subs.forEach(s => { if (s.dot1q != null && s.admin !== "down") set.add(+s.dot1q); });
  return { sw: false, set, any: false, subs };
}
function endVertex(nodeId, i, w, t){
  if (w.sw) { const v = w.map(t); return v == null ? null : `S|${nodeId}|${v}`; }
  if (!w.set.has(t)) return null;
  if (t === "u") return `E|${nodeId}|${i.name}`;
  const s = w.subs.find(x => +x.dot1q === t); return s ? `E|${nodeId}|${s.name}` : null;
}
/* транк всегда может слать native без тега — это не ошибка, если на другом конце его никто не ждёт */
const sends = (w, t) => w.set.has(t) && !(t === "u" && w.sw && w.sp.mode === "trunk");
function switchVlans(f){
  const out = new Set(f.vlans.map(v => +v.id));
  (f.ifs || []).forEach(i => {
    const sp = i.switchport;
    if (sp && sp.mode === "access") out.add(+sp.vlan);
    if (sp && sp.mode === "trunk" && Array.isArray(sp.allowed)) sp.allowed.forEach(v => out.add(+v));
    if (i.kind === "svi") out.add(+i.vlan);
  });
  return out;
}
function tagIssue(m, t, from, to, wTo){
  const F = epName(m, from), T = epName(m, to);
  if (t === "u") return `${F} шлёт кадры без тега, а у ${T} только сабинтерфейсы с dot1q — такие кадры он отбросит.`;
  const why = !wTo.sw ? `у ${T} нет сабинтерфейса с dot1q ${t}`
    : wTo.sp.mode === "access" ? `${T} — access-порт в VLAN ${wTo.sp.vlan}, кадры с тегом он не принимает`
    : t === wTo.nat ? `на транке ${T} это native VLAN — его кадры должны идти без тега`
    : `на транке ${T} он не разрешён (allowed ${Array.isArray(wTo.sp.allowed) ? wTo.sp.allowed.join(",") : "—"})`;
  return `VLAN ${t} не проходит: ${F} шлёт его с тегом, а ${why}.`;
}
function deriveL2(m, fx, l1){
  const par = new Map(), members = new Map(), issues = [];
  const add = x => { if (!par.has(x)) par.set(x, x); return x; };
  const find = x => { add(x); while (par.get(x) !== x) { par.set(x, par.get(par.get(x))); x = par.get(x); } return x; };
  const unite = (a, b) => { const ra = find(a), rb = find(b); if (ra !== rb) par.set(ra, rb); };
  const member = (v, nodeId, i) => { add(v); (members.get(v) || members.set(v, []).get(v)).push({ nodeId, iface: i }); };
  fx.nodes.forEach(f => (f.ifs || []).forEach(i => {
    if (i.kind === "loopback" || i.admin === "down" || i.switchport) return;
    if (i.kind === "svi") { if (i.vlan != null) member(`S|${f.id}|${+i.vlan}`, f.id, i); return; }
    if (v4(i).length || i.kind === "subif" || (i.kind === "physical" && !f.subs(i.name).length)) member(`E|${f.id}|${i.name}`, f.id, i);
  }));

  const links = new Map();
  m.links.forEach(l => {
    const ia = endIf(fx, l.a), ib = endIf(fx, l.b);
    const wa = ia && wireTags(fx.nodes.get(l.a.nodeId), ia), wb = ib && wireTags(fx.nodes.get(l.b.nodeId), ib);
    const trunk = [wa, wb].some(w => w && w.sw && w.sp.mode === "trunk"), sw = [wa, wb].some(w => w && w.sw);
    const rec = { id: l.id, kind: trunk ? "trunk" : sw ? "access" : "p2p", wa, wb, ia, ib, known: !!(wa && wb), vlans: [], tags: [], issues: [] };
    links.set(l.id, rec);
    if (!rec.known || l1.links.get(l.id).down) return;
    const cand = new Set([...wa.set, ...wb.set]);
    if (wa.any && wb.any) [l.a.nodeId, l.b.nodeId].forEach(idv => switchVlans(fx.nodes.get(idv)).forEach(v => cand.add(v)));
    cand.forEach(t => {
      const va = endVertex(l.a.nodeId, ia, wa, t), vb = endVertex(l.b.nodeId, ib, wb, t);
      if (va && vb) {
        unite(va, vb); rec.tags.push({ t, v: va });
        const v = wa.sw ? wa.map(t) : wb.sw ? wb.map(t) : null;
        if (v != null && !rec.vlans.includes(v)) rec.vlans.push(v);
      } else if (va ? sends(wa, t) : vb && sends(wb, t)) {
        rec.issues.push(va ? tagIssue(m, t, l.a, l.b, wb) : tagIssue(m, t, l.b, l.a, wa));
      }
    });
    if (wa.sw && wb.sw && wa.map("u") !== wb.map("u"))
      rec.issues.push(`Кадры без тега склеивают VLAN ${wa.map("u")} на ${epName(m, l.a)} и VLAN ${wb.map("u")} на ${epName(m, l.b)}: native или access VLAN на концах не совпадают.`);
    rec.vlans.sort((a, b) => a - b);
    rec.issues.forEach(text => issues.push({ sev: "warn", text, go: { kind: "link", id: l.id } }));
  });

  const comps = new Map();
  par.forEach((_, v) => { const r = find(v); (comps.get(r) || comps.set(r, []).get(r)).push(v); });
  const domains = [], domOf = new Map();
  comps.forEach(verts => {
    const mem = verts.flatMap(v => members.get(v) || []);
    if (!mem.length) return;
    const sv = verts.filter(v => v[0] === "S").map(v => v.split("|"));
    const d = { id: "d" + domains.length, vlans: [...new Set(sv.map(x => +x[2]))].sort((a, b) => a - b),
      switches: [...new Set(sv.map(x => x[1]))], members: mem, links: [] };
    d.nodes = new Set([...d.switches, ...mem.map(x => x.nodeId)]);
    d.label = d.vlans.length ? "VLAN " + d.vlans.join(" + ") : "точка-точка";
    d.where = (d.vlans.length ? d.switches : [...new Set(mem.map(x => x.nodeId))]).map(idv => m.nodes.get(idv).name).join(d.vlans.length ? ", " : " — ");
    d.shown = d.switches.length > 0 || mem.length > 1;
    domains.push(d); verts.forEach(v => domOf.set(v, d));
  });
  links.forEach(rec => rec.tags.forEach(({ v }) => { const d = domOf.get(v); if (d && !d.links.includes(rec.id)) d.links.push(rec.id); }));
  domains.forEach(d => {
    if (d.vlans.length > 1) issues.push({ sev: "warn", text: `VLAN ${d.vlans.join(" и ")} склеены в один домен (${d.where}).`, go: { kind: "vlan", id: d.id } });
    if (d.switches.length && d.members.length === 1) {
      const x = d.members[0];
      issues.push({ sev: "warn", text: `${m.nodes.get(x.nodeId).name} ${x.iface.name} один в своём L2-домене (${d.label} на ${d.where}): кроме него там никого нет.`, go: { kind: "vlan", id: d.id } });
    }
  });
  const domFor = (nodeId, i) => domOf.get(i.kind === "svi" ? `S|${nodeId}|${+i.vlan}` : `E|${nodeId}|${i.name}`) || null;
  const vlanIds = [...new Set([...domains.flatMap(d => d.vlans), ...[...links.values()].flatMap(r => r.vlans)])].sort((a, b) => a - b);
  return { links, domains, domFor, vlanIds, issues };
}
/* цвет VLAN: начинаем со второго цвета палитры — первый (синий) совпадает с цветом выделения */
const vcolor = v => { const i = st.der ? st.der.l2.vlanIds.indexOf(v) : -1; return i < 0 ? "var(--wire)" : `var(--c${(i + 1) % 6 + 1})`; };
const acolor = a => { const i = st.der ? st.der.rt.areas.indexOf(a) : -1; return i < 0 ? "var(--wire)" : `var(--c${i % 6 + 1})`; };

/* L3: адрес & маска = подсеть; проверки — дубли, пересечения, сверка с L2-доменами */
function deriveL3(m, fx, l2){
  const subs = new Map(), byIp = new Map(), issues = [], nodes = new Set();
  const nm = mb => `${m.nodes.get(mb.nodeId).name} ${mb.iface.name}`;
  fx.nodes.forEach(f => (f.ifs || []).forEach(i => {
    if (i.kind === "loopback") return;
    v4(i).forEach(c => {
      if (!subs.has(c.key)) subs.set(c.key, { key: c.key, net: c.net, pre: c.pre, size: c.size, members: [], issues: [] });
      const mb = { nodeId: f.id, iface: i, c, down: i.oper === "down" || i.admin === "down", dom: l2.domFor(f.id, i) };
      subs.get(c.key).members.push(mb); nodes.add(f.id);
      const k = ipStr(c.addr); (byIp.get(k) || byIp.set(k, []).get(k)).push(mb);
    });
  }));
  const list = [...subs.values()].sort((a, b) => a.net - b.net || a.pre - b.pre);
  const note = (s, sev, text) => { issues.push({ sev, text, go: { kind: "subnet", id: s.key } }); s.issues.push(text); };
  byIp.forEach((arr, ip) => {
    if (arr.length < 2) return;
    arr.forEach(mb => { mb.dup = true; });
    note(subs.get(arr[0].c.key), "bad", `Адрес ${ip} назначен дважды: ${arr.map(nm).join(" и ")}.`);
  });
  for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
    const A = list[i], B = list[j]; if (!overlap(A, B)) continue;
    const text = `Подсети ${A.key} и ${B.key} пересекаются (${[...A.members, ...B.members].map(mb => nm(mb) + " /" + mb.c.pre).join(", ")}) — похоже на разные маски на одном сегменте.`;
    issues.push({ sev: "bad", text, go: { kind: "subnet", id: A.key } }); A.issues.push(text); B.issues.push(text);
  }
  const domText = d => d.vlans.length ? `${d.label} на ${d.where}` : "отдельно";
  list.forEach(s => {
    const groups = new Map();
    s.members.forEach(mb => { if (mb.dom && !mb.down) (groups.get(mb.dom) || groups.set(mb.dom, []).get(mb.dom)).push(mb); });
    if (groups.size > 1) note(s, "bad", `Подсеть ${s.key} разорвана на L2: ${[...groups].map(([d, arr]) => `${arr.map(nm).join(", ")} (${domText(d)})`).join(" | ")}. Узлы одной подсети не видят друг друга.`);
  });
  l2.domains.forEach(d => {
    const ss = list.filter(s => s.members.some(mb => mb.dom === d));
    if (ss.length > 1 && !ss.some((A, i) => ss.some((B, j) => j > i && overlap(A, B))))
      note(ss[0], "warn", `В одном L2-домене (${domText(d)}) несколько подсетей: ${ss.map(s => s.key).join(", ")}.`);
  });

  /* положение подсети: между участниками; одиночная — между узлом и соседом по кабелю; не поверх узлов и других подсетей */
  const busy = [...m.nodes.values()].map(n => ({ x: n.cx, y: n.cy, rx: 78, ry: 50 }));
  list.forEach(s => {
    const ns = [...new Set(s.members.map(mb => mb.nodeId))].map(idv => m.nodes.get(idv));
    let x0 = ns.reduce((a, n) => a + n.cx, 0) / ns.length, y0 = ns.reduce((a, n) => a + n.cy, 0) / ns.length;
    if (ns.length === 1) {
      const mb = s.members[0], p = portOf(fx.nodes.get(mb.nodeId), mb.iface);
      const l = p && m.links.find(x => endIs(x.a, mb.nodeId, p) || endIs(x.b, mb.nodeId, p));
      const o = l && m.nodes.get(l.a.nodeId === mb.nodeId ? l.b.nodeId : l.a.nodeId);
      if (o) { x0 = (x0 + o.cx) / 2; y0 = (y0 + o.cy) / 2; } else y0 -= 80;
    }
    let best = null;
    for (let r = 0; r <= 260 && !best; r += 22) for (let a = 0; a < 360; a += r ? 30 : 360) {
      const x = x0 + r * Math.cos(a * Math.PI / 180), y = y0 + r * Math.sin(a * Math.PI / 180);
      if (busy.every(o => Math.abs(x - o.x) > o.rx || Math.abs(y - o.y) > o.ry)) { best = { x, y }; break; }
    }
    s.x = best ? best.x : x0; s.y = best ? best.y : y0;
    busy.push({ x: s.x, y: s.y, rx: 104, ry: 26 });
  });
  return { subnets: list, nodes, issues };
}

/* маршрутизация: соседства из фактов + ожидаемые (OSPF на обоих концах одного L2-домена или кабеля) */
function deriveRT(m, fx, l1, l2){
  const issues = [], edges = [], seen = new Map();
  const routers = [...fx.nodes.values()].filter(f => (f.ifs || []).some(i => i.ospf));
  const pair = (a, b) => [a, b].sort().join("|");
  const nm = f => m.nodes.get(f.id).name;
  routers.forEach(f => f.adj.forEach(a => {
    const peer = fx.byRid.get(a.peer_router_id); if (!peer || peer === f.id) return;
    const key = pair(f.id, peer);
    let e = seen.get(key);
    if (!e) { const [x, y] = key.split("|"); e = { id: key, a: x, b: y, proto: a.proto || "ospf", area: String(a.area), state: a.state || "full", ifs: {} }; seen.set(key, e); edges.push(e); }
    e.ifs[f.id] = a.iface;
    if (a.state && a.state !== "full") e.state = a.state;
  }));
  const act = f => (f.ifs || []).filter(i => i.ospf && !i.ospf.passive && i.kind !== "loopback");
  for (let x = 0; x < routers.length; x++) for (let y = x + 1; y < routers.length; y++) {
    const A = routers[x], B = routers[y], key = pair(A.id, B.id);
    if (seen.has(key)) continue;
    let hit = null;
    for (const ia of act(A)) for (const ib of act(B)) {
      if (hit) break;
      const da = l2.domFor(A.id, ia), pa = portOf(A, ia), pb = portOf(B, ib);
      const l = pa && pb && m.links.find(k => (endIs(k.a, A.id, pa) && endIs(k.b, B.id, pb)) || (endIs(k.b, A.id, pa) && endIs(k.a, B.id, pb)));
      if ((da && da === l2.domFor(B.id, ib)) || l) hit = { ia, ib, l };
    }
    if (!hit) continue;
    const reason = missReason(hit, l1);
    const e = { id: "miss|" + key, a: A.id, b: B.id, miss: true, reason, area: String(hit.ia.ospf.area), ifs: { [A.id]: hit.ia.name, [B.id]: hit.ib.name } };
    edges.push(e);
    issues.push({ sev: "bad", text: `Нет OSPF-соседства ${nm(A)} — ${nm(B)} (${hit.ia.name} / ${hit.ib.name}): ${reason}.`, go: { kind: "adj", id: e.id } });
  }
  routers.forEach(f => {
    if (act(f).length && !edges.some(e => !e.miss && (e.a === f.id || e.b === f.id)))
      issues.push({ sev: "bad", text: `${nm(f)} без соседей OSPF: его сети не попадут к остальным маршрутизаторам, а их — к нему.`, go: { kind: "node", id: f.id } });
  });
  const areas = [...new Set(edges.map(e => e.area))].sort(natSort);
  return { edges, areas, nodes: new Set(routers.map(f => f.id)), issues };
}
function missReason(h, l1){
  const L = h.l && l1.links.get(h.l.id);
  if (L && L.down) return "линк не работает — " + L.why.join("; ");
  if (h.ia.admin === "down" || h.ib.admin === "down") return "интерфейс выключен";
  const ca = v4(h.ia)[0], cb = v4(h.ib)[0];
  if (ca && cb && ca.key !== cb.key) return `адреса в разных подсетях: ${ca.ip}/${ca.pre} и ${cb.ip}/${cb.pre} — проверьте маски`;
  if (String(h.ia.ospf.area) !== String(h.ib.ospf.area)) return `разные area: ${h.ia.ospf.area} и ${h.ib.ospf.area}`;
  return "по собранным фактам причина не видна — проверьте таймеры hello/dead, аутентификацию и MTU";
}

/* ---------- состояние ---------- */
const svg = sel$("svg"), vp = sel$("vp"), wrap = sel$("wrap"), panel = sel$("panel"), body = sel$("panelBody");
const lyr = { draw: sel$("lyrDraw"), links: sel$("lyrLinks"), nodes: sel$("lyrNodes"), labels: sel$("lyrLabels"), names: sel$("lyrNames") };
const view = { k: 1, tx: 0, ty: 0 };
const st = { model: null, sel: null, statusOn: false, demoStatus: null, sourceLabel: "пример",
  layer: "l1", factsKey: "ok", factsAvail: false, fx: null, der: null };
const mobile = () => window.matchMedia("(max-width: 760px)").matches;

/* ---------- модель ---------- */
function buildModel(doc){
  const t = doc.topology || {};
  const nodes = new Map();
  for (const n of (t.nodes || [])) {
    if (!n || !n.node_id) continue;
    const w = +n.width || 60, h = +n.height || 45, x = +n.x || 0, y = +n.y || 0;
    nodes.set(n.node_id, { id: n.node_id, name: n.name || "без имени", type: n.node_type || "", symbol: n.symbol || "",
      x, y, w, h, cx: x + w / 2, cy: y + h / 2, props: n.properties || {}, ports: Array.isArray(n.ports) ? n.ports : null,
      consoleType: n.console_type || (n.properties && n.properties.console_type) || null,
      console: n.console != null ? n.console : null, status: n.status || null });
  }
  const links = [];
  for (const l of (t.links || [])) {
    const ends = (l.nodes || []).slice(0, 2).map(e => ({ nodeId: e.node_id, adapter: e.adapter_number, port: e.port_number, text: e.label && e.label.text ? String(e.label.text) : null }));
    if (ends.length < 2 || !nodes.has(ends[0].nodeId) || !nodes.has(ends[1].nodeId)) continue;
    ends.forEach(e => { if (!e.text) e.text = portName(nodes.get(e.nodeId), e.adapter, e.port); });
    links.push({ id: l.link_id || ("link-" + links.length), a: ends[0], b: ends[1], filters: l.filters || {}, suspend: !!l.suspend, type: l.link_type || "ethernet" });
  }
  return { name: doc.name || "Проект без имени", version: doc.version || "", nodes, links, drawings: Array.isArray(t.drawings) ? t.drawings : [] };
}
const nodeStatus = n => st.statusOn ? (n.status || (st.demoStatus && st.demoStatus[n.id]) || null) : null;
const guestsOf = n => (st.statusOn && st.guests && st.guests[n.id]) || null;

/* ---------- рисунки из .gns3 (санитайзинг: только безопасная SVG-геометрия и текст) ---------- */
const OK_TAGS = new Set(["svg", "g", "rect", "ellipse", "circle", "line", "polyline", "polygon", "path", "text", "tspan"]);
function sanitize(node){
  for (const ch of Array.from(node.children)) {
    if (!OK_TAGS.has(ch.localName)) { ch.remove(); continue; }
    for (const a of Array.from(ch.attributes)) {
      const n = a.name.toLowerCase(), v = a.value;
      if (n.startsWith("on") || n === "href" || n.endsWith(":href") || /javascript:/i.test(v) || (n === "style" && /url\s*\(/i.test(v))) ch.removeAttribute(a.name);
    }
    if (ch.localName === "text" && !ch.hasAttribute("y")) ch.setAttribute("y", (parseFloat(ch.getAttribute("font-size")) || 12) * 0.95);
    sanitize(ch);
  }
}
function drawingNode(d){
  let s = String(d.svg || "");
  if (!/^\s*<svg[\s>]/i.test(s)) return null;
  if (!/xmlns=/.test(s)) s = s.replace(/<svg/i, '<svg xmlns="http://www.w3.org/2000/svg"');
  const doc = new DOMParser().parseFromString(s, "image/svg+xml");
  const root = doc.documentElement;
  if (!root || root.localName !== "svg" || doc.getElementsByTagName("parsererror").length) return null;
  const imp = document.importNode(root, true);
  for (const a of Array.from(imp.attributes)) if (a.name.toLowerCase().startsWith("on")) imp.removeAttribute(a.name);
  sanitize(imp);
  imp.setAttribute("overflow", "visible");
  imp.setAttribute("pointer-events", "none");
  return { node: imp, w: parseFloat(root.getAttribute("width")) || 0, h: parseFloat(root.getAttribute("height")) || 0 };
}

/* ---------- отрисовка сцены ---------- */
let bounds = null;
function renderScene(){
  const m = st.model;
  for (const k in lyr) lyr[k].textContent = "";
  let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
  const grow = (x0, y0, x1, y1) => { bx0 = Math.min(bx0, x0); by0 = Math.min(by0, y0); bx1 = Math.max(bx1, x1); by1 = Math.max(by1, y1); };

  [...m.drawings].sort((a, b) => (a.z || 0) - (b.z || 0)).forEach(d => {
    const r = drawingNode(d); if (!r) return;
    const x = +d.x || 0, y = +d.y || 0;
    const g = el("g", { transform: `translate(${x} ${y})` + (d.rotation ? ` rotate(${+d.rotation})` : "") }, lyr.draw);
    g.appendChild(r.node);
    if (r.w && r.h) grow(x, y, x + r.w, y + r.h);
  });

  const L = st.layer, D = st.der, geo = linkGeometry(m);
  if (L === "l3" || L === "rt") geo.forEach(g => el("line", { class: "wire ghost", x1: g.ax, y1: g.ay, x2: g.bx, y2: g.by }, lyr.links));
  else m.links.forEach(l => drawLink(l, geo.get(l.id), L === "l2" ? D : null));
  if (L === "l3" && D) drawL3(D.l3, grow);
  if (L === "rt" && D) drawRT(D.rt);

  m.nodes.forEach(n => {
    const status = nodeStatus(n);
    const ghost = D && ((L === "l3" && !D.l3.nodes.has(n.id)) || (L === "rt" && !D.rt.nodes.has(n.id)));
    const g = el("g", { class: "node dimmable" + (ghost ? " ghost" : ""), "data-node": n.id, tabindex: "0", role: "button",
      "aria-label": `${n.name}, ${typeName(n.type)}${status ? ", " + STATUS_NAME[status] : ""}` }, lyr.nodes);
    el("rect", { class: "halo", x: n.cx - 31, y: n.cy - 31, width: 62, height: 62, rx: 12 }, g);
    el("use", { href: "#" + prefix + "-ico-" + cat(n), x: n.cx - 22, y: n.cy - 22, width: 44, height: 44 }, g);
    /* имя — в своём слое: без выделения оно над подписями портов, при выделении — под ними (applySel) */
    const ng = el("g", { class: "nname-g dimmable" + (ghost ? " ghost" : ""), "data-node": n.id }, lyr.names);
    const t = el("text", { class: "nname", x: n.cx, y: n.cy + 42, "text-anchor": "middle" }, ng);
    t.textContent = n.name;
    if (status) el("circle", { class: "led led-" + status, cx: n.cx + 21, cy: n.cy - 20, r: 5.5 }, g);
    const gs = guestsOf(n), f = st.fx && st.fx.nodes.get(n.id);
    if (gs) {
      const vm = gs.items.filter(x => x.type === "qemu").length, ct = gs.items.filter(x => x.type === "lxc").length;
      pill(g, n.cx, n.cy + 61, `${vm} VM · ${ct} CT`, "gpill", null);
    } else if (L === "rt" && f && f.rid && !ghost) pill(g, n.cx, n.cy + 60, "RID " + f.rid, "rpill", null);
    grow(n.cx - 40, n.cy - 36, n.cx + 40, n.cy + (gs ? 72 : 50));
  });
  bounds = isFinite(bx0) ? { x: bx0, y: by0, w: bx1 - bx0, h: by1 - by0 } : { x: -200, y: -150, w: 400, h: 300 };
  applySel();
}
/* параллельные линки между одной парой нод разводятся на 16 px */
function linkGeometry(m){
  const groups = {}, out = new Map();
  m.links.forEach(l => { const key = [l.a.nodeId, l.b.nodeId].sort().join("|"); (groups[key] = groups[key] || []).push(l); });
  Object.values(groups).forEach(list => list.forEach((l, i) => {
    const A = m.nodes.get(l.a.nodeId), B = m.nodes.get(l.b.nodeId);
    let dx = B.cx - A.cx, dy = B.cy - A.cy; const len = Math.hypot(dx, dy) || 1; dx /= len; dy /= len;
    const off = (i - (list.length - 1) / 2) * 16, nx = -dy * off, ny = dx * off;
    out.set(l.id, { ax: A.cx + nx, ay: A.cy + ny, bx: B.cx + nx, by: B.cy + ny, dx, dy, len });
  }));
  return out;
}
/* линк на слоях L1 и L2 (D — производные данные, только на L2) */
function drawLink(l, g, D){
  const f1 = st.der && st.der.l1.links.get(l.id), down = !!(f1 && f1.down);
  const r2 = D && D.l2.links.get(l.id);
  let cls = "link dimmable" + (l.suspend ? " susp" : "") + (down && !l.suspend ? " down" : "");
  if (r2 && !down) cls += " " + r2.kind + (r2.known && !r2.tags.length ? " dead" : "");
  const G = el("g", { class: cls, "data-link": l.id }, lyr.links);
  const line = (o, extra) => el("line", Object.assign({ x1: g.ax - g.dy * o, y1: g.ay + g.dx * o, x2: g.bx - g.dy * o, y2: g.by + g.dx * o }, extra), G);
  line(0, { class: "hit" });
  if (r2 && !down && r2.vlans.length && r2.kind !== "p2p") {
    const k = r2.vlans.length, step = k > 1 ? 3.4 : 0;
    r2.vlans.forEach((v, j) => line((j - (k - 1) / 2) * step, { class: "wire vl", style: `stroke:${vcolor(v)}` }));
  } else line(0, { class: "wire" });

  const mx = (g.ax + g.bx) / 2, my = (g.ay + g.by) / 2, fl = fmtFilters(l.filters);
  if (r2) {
    if (down) pill(lyr.labels, mx, my, "down", "bpill dimmable", l.id);
    else if (r2.issues.length) pill(lyr.labels, mx, my, "⚠ " + (r2.vlans.length ? r2.vlans.join(" · ") : "нет VLAN"), "wpill dimmable", l.id);
    else if (r2.known && !r2.tags.length) pill(lyr.labels, mx, my, "нет общих VLAN", "bpill dimmable", l.id);
    else if (r2.kind === "trunk" && g.len > 240) pill(lyr.labels, mx, my, r2.vlans.join(" · "), "vpill dimmable", l.id);
  } else if (fl.length || (down && !l.suspend)) {
    pill(lyr.labels, mx, my, [...fl.map(x => x.s), ...(down && !l.suspend ? ["down"] : [])].join(" · "), down && !l.suspend ? "bpill dimmable" : "fpill dimmable", l.id);
  }
  const d = Math.min(46, g.len * 0.32);
  [[l.a, 1, r2 && r2.ia], [l.b, -1, r2 && r2.ib]].forEach(([e, s, i]) => {
    const fi = st.fx && endIf(st.fx, e), bad = !!(fi && (fi.admin === "down" || fi.oper === "down"));
    const sp = i && i.switchport, text = sp ? e.text + (sp.mode === "access" ? " · " + sp.vlan : " · T") : e.text;
    pill(lyr.labels, (s > 0 ? g.ax : g.bx) + s * g.dx * d, (s > 0 ? g.ay : g.by) + s * g.dy * d, text, "plabel dimmable" + (bad ? " bad" : ""), l.id);
  });
}
/* L3: подсети отдельными объектами, к ним — интерфейсы с адресами; у интерфейса подписан номер хоста */
function drawL3(S, grow){
  const m = st.model;
  S.subnets.forEach(s => s.members.forEach(mb => {
    const N = m.nodes.get(mb.nodeId);
    const G = el("g", { class: "edge dimmable" + (mb.down ? " down" : "") + (mb.dup ? " dup" : ""), "data-subnet": s.key }, lyr.links);
    el("line", { class: "hit", x1: N.cx, y1: N.cy, x2: s.x, y2: s.y }, G);
    el("line", { class: "wire", x1: N.cx, y1: N.cy, x2: s.x, y2: s.y }, G);
    const dx = s.x - N.cx, dy = s.y - N.cy, len = Math.hypot(dx, dy) || 1, d = Math.min(dy / len > 0.5 ? 66 : 50, len * 0.5);   // вниз — ниже имени узла
    pill(lyr.labels, N.cx + dx / len * d, N.cy + dy / len * d, hostPart(mb.c), "plabel dimmable" + (mb.dup || mb.down ? " bad" : ""), null).setAttribute("data-subnet", s.key);
  }));
  S.subnets.forEach(s => {
    pill(lyr.labels, s.x, s.y, (s.issues.length ? "⚠ " : "") + s.key, "subnet dimmable" + (s.issues.length ? " warn" : ""), null).setAttribute("data-subnet", s.key);
    grow(s.x - 60, s.y - 14, s.x + 60, s.y + 14);
  });
}
/* маршрутизация: рёбра соседств, цвет — area; несостоявшееся соседство — красный пунктир */
function drawRT(R){
  const m = st.model;
  R.edges.forEach(e => {
    const A = m.nodes.get(e.a), B = m.nodes.get(e.b);
    const G = el("g", { class: "adj dimmable" + (e.miss ? " miss" : ""), "data-adj": e.id }, lyr.links);
    el("line", { class: "hit", x1: A.cx, y1: A.cy, x2: B.cx, y2: B.cy }, G);
    el("line", { class: "wire", x1: A.cx, y1: A.cy, x2: B.cx, y2: B.cy, style: e.miss ? null : `stroke:${acolor(e.area)}` }, G);
    const text = e.miss ? "нет соседства" : `area ${e.area}` + (e.state !== "full" ? " · " + e.state : "");
    pill(lyr.labels, (A.cx + B.cx) / 2, (A.cy + B.cy) / 2, text, (e.miss ? "bpill" : "vpill") + " dimmable", null).setAttribute("data-adj", e.id);
  });
}
function pill(parent, x, y, text, cls, linkId){
  const g = el("g", { class: cls, "data-link": linkId }, parent);
  const r = el("rect", { rx: 4 }, g);
  const t = el("text", { x, y, "text-anchor": "middle", "dominant-baseline": "central" }, g);
  t.textContent = text;
  let w = 0, h = 14;
  try { const b = t.getBBox(); w = b.width; h = b.height; } catch (e) { w = text.length * 6.4; }
  r.setAttribute("x", x - w / 2 - 5); r.setAttribute("y", y - h / 2 - 2);
  r.setAttribute("width", w + 10); r.setAttribute("height", h + 4);
  return g;
}

/* ---------- вид: сдвиг и масштаб ---------- */
const KMIN = 0.15, KMAX = 4;
function applyView(){
  vp.setAttribute("transform", `translate(${view.tx} ${view.ty}) scale(${view.k})`);
  sel$("dots").setAttribute("patternTransform", `translate(${view.tx} ${view.ty}) scale(${view.k})`);
  sel$("zoomPct").textContent = Math.round(view.k * 100) + "%";
}
function zoomAt(px, py, f){
  stopInertia();
  const k = Math.min(KMAX, Math.max(KMIN, view.k * f));
  const sx = (px - view.tx) / view.k, sy = (py - view.ty) / view.k;
  view.k = k; view.tx = px - k * sx; view.ty = py - k * sy;
  applyView();
}
function fit(){
  stopInertia();
  const r = wrap.getBoundingClientRect();
  const padX = 48, padTop = 40, padBot = mobile() ? 100 : 56;
  const k = Math.min(2, Math.max(KMIN, Math.min((r.width - 2 * padX) / bounds.w, (r.height - padTop - padBot) / bounds.h)));
  view.k = k;
  view.tx = r.width / 2 - k * (bounds.x + bounds.w / 2);
  view.ty = padTop + (r.height - padTop - padBot) / 2 - k * (bounds.y + bounds.h / 2);
  applyView();
}
function center(){ const r = wrap.getBoundingClientRect(); return [r.width / 2, r.height / 2]; }

/* жесты: один палец/мышь — сдвиг, два пальца — щипок, короткое касание — выбор */
const ptrs = new Map(); let gest = null, pinch = null, lastTap = null;

/* инерция: после быстрого свайпа схема ещё немного едет и плавно тормозит */
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
let raf = 0;
function stopInertia(){ if (raf) cancelAnimationFrame(raf); raf = 0; }
function fling(samples){
  if (reduceMotion.matches || samples.length < 2) return;
  const now = performance.now(), last = samples[samples.length - 1];
  if (now - last.t > 60) return;                       // палец остановился перед отпусканием
  const first = samples.find(p => last.t - p.t <= 90) || samples[0];
  const dt = last.t - first.t; if (dt <= 0) return;
  let vx = (last.x - first.x) / dt, vy = (last.y - first.y) / dt;   // px/мс
  if (Math.hypot(vx, vy) < 0.3) return;
  let prev = now;
  const step = t => {
    const d = Math.min(32, t - prev); prev = t;
    view.tx += vx * d; view.ty += vy * d;
    const f = Math.pow(0.994, d); vx *= f; vy *= f;
    applyView();
    raf = Math.hypot(vx, vy) > 0.02 ? requestAnimationFrame(step) : 0;
  };
  raf = requestAnimationFrame(step);
}
function local(e){ const r = svg.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
svg.addEventListener("pointerdown", e => {
  if (e.button > 0) return;
  stopInertia();
  try { svg.setPointerCapture(e.pointerId); } catch (_) {}
  ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
  const t0 = performance.now();
  if (ptrs.size === 1) gest = { sx: e.clientX, sy: e.clientY, t: t0, moved: false, pinched: false, samples: [{ t: t0, x: e.clientX, y: e.clientY }] };
  else { if (gest) { gest.moved = true; gest.pinched = true; } pinch = null; }
});
svg.addEventListener("pointermove", e => {
  const prev = ptrs.get(e.pointerId); if (!prev) return;
  ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
  if (ptrs.size === 1 && gest) {
    if (!gest.moved && Math.hypot(e.clientX - gest.sx, e.clientY - gest.sy) > 6) { gest.moved = true; svg.classList.add("dragging"); }
    if (gest.moved) { view.tx += e.clientX - prev.x; view.ty += e.clientY - prev.y; applyView(); }
    const now = performance.now();
    gest.samples.push({ t: now, x: e.clientX, y: e.clientY });
    while (gest.samples.length > 2 && now - gest.samples[0].t > 120) gest.samples.shift();
  } else if (ptrs.size === 2) {
    const [p1, p2] = [...ptrs.values()], r = svg.getBoundingClientRect();
    const mid = { x: (p1.x + p2.x) / 2 - r.left, y: (p1.y + p2.y) / 2 - r.top }, dist = Math.hypot(p1.x - p2.x, p1.y - p2.y);
    if (pinch && pinch.dist > 0) { zoomAt(mid.x, mid.y, dist / pinch.dist); view.tx += mid.x - pinch.mid.x; view.ty += mid.y - pinch.mid.y; applyView(); }
    pinch = { mid, dist };
  }
});
function endPtr(e){
  if (!ptrs.has(e.pointerId)) return;
  ptrs.delete(e.pointerId);
  if (ptrs.size < 2) pinch = null;
  if (ptrs.size === 0) {
    svg.classList.remove("dragging");
    if (e.type === "pointerup" && gest && !gest.moved && performance.now() - gest.t < 600) tap(e);
    else if (e.type === "pointerup" && gest && gest.moved && !gest.pinched) fling(gest.samples);
    gest = null;
  } else if (gest) gest.moved = true;
}
svg.addEventListener("pointerup", endPtr);
svg.addEventListener("pointercancel", endPtr);
function tap(e){
  const t = document.elementFromPoint(e.clientX, e.clientY);
  const hit = attr => t && t.closest && t.closest(`[${attr}]`);
  for (const [attr, kind] of [["data-node", "node"], ["data-subnet", "subnet"], ["data-adj", "adj"], ["data-link", "link"]]) {
    const h = hit(attr);
    if (h) { select({ kind, id: h.getAttribute(attr) }); lastTap = null; return; }
  }
  const now = performance.now();
  if (lastTap && now - lastTap.t < 330 && Math.hypot(e.clientX - lastTap.x, e.clientY - lastTap.y) < 28) {
    const [x, y] = local(e); zoomAt(x, y, 1.7); lastTap = null;
  } else { lastTap = { t: now, x: e.clientX, y: e.clientY }; select(null); }
}
svg.addEventListener("wheel", e => {
  e.preventDefault();
  let d = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
  if (e.ctrlKey) d *= 3;
  const [x, y] = local(e); zoomAt(x, y, Math.exp(-d * 0.0018));
}, { passive: false });

/* клавиатура */
document.addEventListener("keydown", e => {
  // Клавиши схемы — только когда фокус в приложении (или на пустой полноэкранной странице), не в полях ввода.
  if (!root.contains(e.target) && !(fullscreen && e.target === document.body)) return;
  if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT" || e.target.isContentEditable)) return;
  const tgtNode = e.target && e.target.closest && e.target.closest("[data-node]");
  if (tgtNode && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select({ kind: "node", id: tgtNode.getAttribute("data-node") }); return; }
  const [cx, cy] = center();
  if (e.key === "+" || e.key === "=") zoomAt(cx, cy, 1.25);
  else if (e.key === "-" || e.key === "_") zoomAt(cx, cy, 0.8);
  else if (e.key === "0") fit();
  else if (e.key >= "1" && e.key <= "4" && !e.ctrlKey && !e.metaKey && !e.altKey) setLayer(["l1", "l2", "l3", "rt"][+e.key - 1]);
  else if (e.key === "Escape") select(null);
  else if (document.activeElement === svg && e.key.startsWith("Arrow")) {
    e.preventDefault();
    const s = 40; view.tx += e.key === "ArrowLeft" ? s : e.key === "ArrowRight" ? -s : 0; view.ty += e.key === "ArrowUp" ? s : e.key === "ArrowDown" ? -s : 0; applyView();
  }
});

/* ---------- выделение ---------- */
/* на каком слое живёт объект: подсеть — L3, домен — L2, соседство — маршрутизация; нода — везде */
const LAYER_OF = { subnet: ["l3"], vlan: ["l2"], adj: ["rt"], link: ["l1", "l2"] };
function select(s){
  if (s && LAYER_OF[s.kind] && !LAYER_OF[s.kind].includes(st.layer)) { st.layer = LAYER_OF[s.kind][0]; syncLayerUI(); st.sel = s; renderScene(); }
  st.sel = s;
  applySel();
  renderPanel();
  if (mobile()) setSheet(!!s);
  const focus = s && selCenter(s);
  if (focus && mobile()) {
    const r = wrap.getBoundingClientRect();
    const sy = view.ty + focus.y * view.k, limit = r.height * 0.36;
    if (sy > limit) { view.ty -= sy - limit; applyView(); }
  }
}
function selCenter(s){
  const m = st.model, D = st.der;
  if (s.kind === "node") { const n = m.nodes.get(s.id); return n && { y: n.cy }; }
  if (s.kind === "subnet" && D) { const x = D.l3.subnets.find(v => v.key === s.id); return x && { y: x.y }; }
  return null;
}
function setLayer(L){
  if (L !== "l1" && !st.der) return;
  if (L === st.layer) return;
  st.layer = L; syncLayerUI();
  const s = st.sel;
  if (s && LAYER_OF[s.kind] && !LAYER_OF[s.kind].includes(L)) st.sel = null;
  renderScene(); renderPanel();
  if (mobile() && !st.sel) setSheet(false);
}
function syncLayerUI(){
  sel$("layerSeg").querySelectorAll("button").forEach(b => {
    b.setAttribute("aria-pressed", String(b.dataset.layer === st.layer));
    b.disabled = b.dataset.layer !== "l1" && !st.der;
  });
  sel$("factsSel").disabled = !st.factsAvail;
  sel$("factsSel").value = st.factsKey;
}
function applySel(){
  const s = st.sel, m = st.model, D = st.der;
  svg.classList.toggle("has-sel", !!s);
  /* общий вид: имена узлов поверх подписей портов; выбран объект — его подписи портов поверх имён */
  vp.appendChild(s ? lyr.labels : lyr.names);
  svg.querySelectorAll(".on,.sel").forEach(e => e.classList.remove("on", "sel"));
  if (!s) return;
  const mark = (attr, idv, cls) => svg.querySelectorAll(`[${attr}="${CSS.escape(idv)}"]`).forEach(e => e.classList.add(...cls));
  if (s.kind === "node") {
    mark("data-node", s.id, ["on", "sel"]);
    if (st.layer === "l3" && D) D.l3.subnets.forEach(x => { if (x.members.some(mb => mb.nodeId === s.id)) mark("data-subnet", x.key, ["on"]); });
    else if (st.layer === "rt" && D) D.rt.edges.forEach(e => { if (e.a === s.id || e.b === s.id) { mark("data-adj", e.id, ["on"]); mark("data-node", e.a === s.id ? e.b : e.a, ["on"]); } });
    else m.links.forEach(l => {
      if (l.a.nodeId === s.id || l.b.nodeId === s.id) {
        mark("data-link", l.id, ["on"]);
        mark("data-node", l.a.nodeId === s.id ? l.b.nodeId : l.a.nodeId, ["on"]);
      }
    });
  } else if (s.kind === "subnet" && D) {
    const x = D.l3.subnets.find(v => v.key === s.id); if (!x) return;
    mark("data-subnet", x.key, ["on", "sel"]);
    x.members.forEach(mb => mark("data-node", mb.nodeId, ["on"]));
  } else if (s.kind === "vlan" && D) {
    const d = D.l2.domains.find(v => v.id === s.id); if (!d) return;
    d.links.forEach(idv => mark("data-link", idv, ["on", "sel"]));
    d.nodes.forEach(idv => mark("data-node", idv, ["on"]));
  } else if (s.kind === "adj" && D) {
    const e = D.rt.edges.find(v => v.id === s.id); if (!e) return;
    mark("data-adj", e.id, ["on", "sel"]); mark("data-node", e.a, ["on"]); mark("data-node", e.b, ["on"]);
  } else if (s.kind === "link") {
    const l = m.links.find(x => x.id === s.id); if (!l) return;
    mark("data-link", l.id, ["on", "sel"]);
    mark("data-node", l.a.nodeId, ["on"]); mark("data-node", l.b.nodeId, ["on"]);
  }
}

/* ---------- панель сведений ---------- */
function statusLine(n){
  const s = nodeStatus(n);
  if (!s) return `<p class="st st-none">Статус: нет данных — файл .gns3 не хранит состояние нод</p>`;
  return `<p class="st st-${s}">${STATUS_NAME[s] || esc(s)}</p>`;
}
function renderPanel(){
  const m = st.model, s = st.sel, D = st.der, L = st.layer;
  const issueCount = D ? layerIssues().length : 0;
  sel$("handleText").textContent = s ? handleTitle(s) : L === "l1"
    ? `Состав лабы · ${m.nodes.size} ${plural(m.nodes.size, ["устройство", "устройства", "устройств"])}`
    : `${LAYER_NAME[L]} · ${issueCount ? issueCount + " " + plural(issueCount, ["проблема", "проблемы", "проблем"]) : "без проблем"}`;
  if (!s) { body.innerHTML = L === "l2" && D ? l2SummaryHTML() : L === "l3" && D ? l3SummaryHTML() : L === "rt" && D ? rtSummaryHTML() : summaryHTML(); return; }
  if (s.kind === "node") { const n = m.nodes.get(s.id); body.innerHTML = n ? nodeHTML(n) : ""; return; }
  if (s.kind === "subnet") { const x = D && D.l3.subnets.find(v => v.key === s.id); body.innerHTML = x ? subnetHTML(x) : ""; return; }
  if (s.kind === "vlan") { const d = D && D.l2.domains.find(v => v.id === s.id); body.innerHTML = d ? vlanHTML(d) : ""; return; }
  if (s.kind === "adj") { const e = D && D.rt.edges.find(v => v.id === s.id); body.innerHTML = e ? adjHTML(e) : ""; return; }
  const l = m.links.find(x => x.id === s.id); body.innerHTML = l ? linkHTML(l) : "";
}
function handleTitle(s){
  const m = st.model, nn = idv => (m.nodes.get(idv) || {}).name || "?";
  if (s.kind === "node") return nn(s.id);
  if (s.kind === "subnet") return "Подсеть " + s.id;
  if (s.kind === "vlan") { const d = st.der.l2.domains.find(v => v.id === s.id); return d ? d.label : "L2-домен"; }
  if (s.kind === "adj") { const e = st.der.rt.edges.find(v => v.id === s.id); return e ? `${nn(e.a)} ↔ ${nn(e.b)}` : "Соседство"; }
  return "Линк";
}
const layerIssues = () => { const D = st.der; return !D ? [] : { l1: D.l1.issues, l2: D.l2.issues, l3: D.l3.issues, rt: D.rt.issues }[st.layer]; };
const gotoAttr = g => g ? `data-goto-${g.kind}="${esc(g.id)}"` : "";
function issuesHTML(list, okText){
  if (!list.length) return `<p class="okline">${esc(okText)}</p>`;
  return `<ul class="issues">${[...list].sort((a, b) => SEV[a.sev] - SEV[b.sev]).map(x =>
    `<li><button class="issue ${x.sev}" type="button" ${gotoAttr(x.go)}>${esc(x.text)}</button></li>`).join("")}</ul>`;
}
function factsLine(){
  const at = st.fx && st.fx.at ? new Date(st.fx.at) : null;
  return `<p class="idline">Факты из лабы: ${esc(FACTS_NAME[st.factsKey])}${at && !isNaN(at) ? " · собраны " + at.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }) : ""} · демо</p>`;
}
function layerHead(eyebrow, title, lead){
  const list = layerIssues();
  return `<div><p class="eyebrow">${esc(eyebrow)}</p><h2>${esc(title)}</h2></div>
    <p class="glead">${lead}</p>${factsLine()}
    <h3>Проблемы <span class="count">${list.length}</span></h3>${issuesHTML(list, "Проблем на этом слое не найдено.")}`;
}
const nodeName = idv => esc((st.model.nodes.get(idv) || {}).name || "?");
function l2SummaryHTML(){
  const D = st.der.l2, doms = D.domains.filter(d => d.shown).sort((a, b) => (b.vlans.length > 0) - (a.vlans.length > 0) || (a.vlans[0] || 0) - (b.vlans[0] || 0));
  const rows = doms.map(d => `<li><button class="rowbtn" type="button" data-goto-vlan="${d.id}"><span class="sw" style="background:${d.vlans.length ? vcolor(d.vlans[0]) : "var(--wire)"}"></span><span>${esc(d.label)} <small>${esc(d.where)}</small></span><small>${d.members.length} ${plural(d.members.length, ["участник", "участника", "участников"])}</small></button></li>`).join("");
  return layerHead("Слой L2", "VLAN и L2-домены", "Схема построена из настроек портов: access и trunk, разрешённые VLAN, сабинтерфейсы dot1q. Цвет линка — VLAN, который по нему реально проходит; несколько полос — транк. Подпись порта: номер VLAN у access, «T» у транка.") + `
    <h3>L2-домены <span class="count">${doms.length}</span></h3><ul class="list">${rows}</ul>
    <div class="legend"><span><i class="wirekey t"></i>транк</span><span><i class="wirekey bad"></i>линк не работает</span><span><i class="wirekey d"></i>ничего не проходит</span></div>`;
}
function l3SummaryHTML(){
  const S = st.der.l3;
  const rows = S.subnets.map(s => `<li><button class="rowbtn" type="button" data-goto-subnet="${esc(s.key)}"><span class="sw" style="background:${s.issues.length ? "var(--warn)" : "var(--accent)"}"></span><span class="mono">${esc(s.key)}</span><small>${s.members.length} ${plural(s.members.length, ["адрес", "адреса", "адресов"])}</small></button></li>`).join("");
  return layerHead("Слой L3", "IP-подсети", "Каждая подсеть — отдельный объект; к ней тянутся интерфейсы, у которых в ней адрес, у интерфейса подписан номер хоста. Коммутаторы без адресов приглушены. Подсеть сверяется с L2: все её участники должны быть в одном широковещательном домене.") + `
    <h3>Подсети <span class="count">${S.subnets.length}</span></h3><ul class="list">${rows}</ul>
    <div class="legend"><span><i class="wirekey acc"></i>адрес в подсети</span><span><i class="wirekey bad"></i>дубль или интерфейс down</span></div>`;
}
function rtSummaryHTML(){
  const R = st.der.rt, rows = R.edges.map(e => `<li><button class="rowbtn" type="button" data-goto-adj="${esc(e.id)}"><span class="sw" style="background:${e.miss ? "var(--bad)" : acolor(e.area)}"></span><span>${nodeName(e.a)} — ${nodeName(e.b)}</span><small>${e.miss ? "нет соседства" : "area " + esc(e.area) + " · " + esc(e.state)}</small></button></li>`).join("");
  return layerHead("Маршрутизация", "Соседства OSPF", "Рёбра — соседства, которые маршрутизаторы сообщили сами; цвет — area. Красный пунктир — соседство, которое должно было подняться (OSPF на обоих концах одного сегмента), но его нет.") + `
    <h3>Соседства <span class="count">${R.edges.filter(e => !e.miss).length}</span></h3><ul class="list">${rows}</ul>`;
}
function summaryHTML(){
  const m = st.model, counts = {};
  m.nodes.forEach(n => { const c = cat(n); counts[c] = (counts[c] || 0) + 1; });
  const order = ["router", "switch", "firewall", "hypervisor", "server", "pc", "cloud"];
  const kv = order.filter(c => counts[c]).map(c => `<dt>${CAT_NAME[c][1]}</dt><dd class="mono">${counts[c]}</dd>`).join("");
  const nodes = [...m.nodes.values()].sort((a, b) => natSort(a.name, b.name));
  const rows = nodes.map(n => { const s = nodeStatus(n);
    return `<li><button class="rowbtn" type="button" data-goto-node="${esc(n.id)}"><span class="sdot ${s || ""}"></span><span>${esc(n.name)}</span><small>${esc(typeName(n.type))}</small></button></li>`; }).join("");
  const susp = m.links.filter(l => l.suspend).length, filt = m.links.filter(l => fmtFilters(l.filters).length).length;
  return `
    <div><p class="eyebrow">Обзор</p><h2>Состав лабы</h2></div>
    <p class="glead">Нажмите на устройство или линк, чтобы увидеть порты, соседей и параметры. Схема только для просмотра: здесь ничего нельзя сдвинуть или удалить.</p>
    ${st.der ? `<p class="glead">Слои L2, L3 и OSPF строятся из того, как устройства реально настроены. Переключите конфиг на «с ошибками», чтобы увидеть, как ошибки выглядят на схеме.</p>${factsLine()}<h3>Проблемы L1 <span class="count">${st.der.l1.issues.length}</span></h3>${issuesHTML(st.der.l1.issues, "Все линки работают.")}` : ""}
    <dl class="kv">${kv}<dt>Линки</dt><dd class="mono">${m.links.length}${susp ? ` · ${susp} приост.` : ""}${filt ? ` · ${filt} с фильтрами` : ""}</dd></dl>
    <div class="legend">
      <span><i class="wirekey"></i>линк</span><span><i class="wirekey d"></i>приостановлен</span>
      ${st.statusOn ? `<span><i class="sdot started"></i>запущено</span><span><i class="sdot stopped"></i>остановлено</span><span><i class="sdot suspended"></i>пауза</span>` : ""}
    </div>
    <h3>Устройства <span class="count">${m.nodes.size}</span></h3>
    <ul class="list">${rows}</ul>`;
}
function nodeHTML(n){
  const m = st.model, p = n.props || {}, c = cat(n);
  const kv = [];
  const image = p.hda_disk_image || p.path || p.image || p.cdrom_image;
  if (image) kv.push(["Образ", `<span class="mono">${esc(image)}</span>`]);
  if (p.ram) kv.push(["RAM", `${esc(p.ram)} МБ`]);
  if (p.cpus) kv.push(["vCPU", esc(p.cpus)]);
  const ad = p.adapters != null ? p.adapters : p.ethernet_adapters;
  if (ad != null) kv.push(["Адаптеры", esc(ad) + (p.adapter_type ? ` · <span class="mono">${esc(p.adapter_type)}</span>` : "")]);
  if (p.options) kv.push(["Опции QEMU", `<span class="mono">${esc(p.options)}</span>`]);
  if (c === "hypervisor") kv.push(["Вложенная VM", "нужна: <span class=\"mono\">-cpu host</span> и nested KVM на хосте"]);
  if (n.type === "nat") kv.push(["Назначение", "Выход в сеть хоста через NAT"]);
  if (n.type === "cloud") kv.push(["Назначение", "Мост на интерфейс хоста"]);
  kv.push(["Консоль", esc(CONSOLE_NAME[n.consoleType] || n.consoleType || "нет")]);

  const ifs = [];
  m.links.forEach(l => {
    const mine = l.a.nodeId === n.id ? l.a : l.b.nodeId === n.id ? l.b : null; if (!mine) return;
    const other = mine === l.a ? l.b : l.a, peer = m.nodes.get(other.nodeId);
    ifs.push({ l, port: mine.text, peer: peer ? peer.name : "?", peerPort: other.text });
  });
  ifs.sort((a, b) => natSort(a.port, b.port));
  const ifRows = ifs.map(r => `<tr data-goto-link="${esc(r.l.id)}" tabindex="0"><td class="mono">${esc(r.port)}</td><td>${esc(r.peer)} <span class="mono peer">${esc(r.peerPort)}</span>${r.l.suspend ? ` <span class="count">приост.</span>` : ""}${fmtFilters(r.l.filters).length ? ` <span class="count">фильтр</span>` : ""}</td></tr>`).join("");

  let vlan = "";
  if (Array.isArray(p.ports_mapping) && p.ports_mapping.length) {
    vlan = `<h3>Порты и VLAN</h3><div class="tblwrap"><table class="ifs"><thead><tr><th>Порт</th><th>Режим</th><th>VLAN</th></tr></thead><tbody>${
      p.ports_mapping.map(x => `<tr><td class="mono">${esc(x.name)}</td><td>${x.type === "dot1q" ? "trunk (dot1q)" : x.type === "qinq" ? "QinQ" : "access"}</td><td class="mono">${esc(x.vlan)}${x.type === "dot1q" ? " native" : ""}</td></tr>`).join("")}</tbody></table></div>`;
  }
  let guests = "";
  if (c === "hypervisor") {
    const gs = guestsOf(n);
    if (!gs) {
      guests = `<h3>Гости Proxmox</h3><p class="glead">${st.statusOn ? "Гостевой агент ноды пока не ответил." : "Появятся с живыми данными: шлюз спрашивает гостевой агент ноды, какие VM и контейнеры есть внутри и что из них запущено."}</p>`;
    } else {
      const items = [...gs.items].sort((a, b) => a.vmid - b.vmid);
      const run = items.filter(x => x.status === "running").length;
      const rows = items.map(x => `<tr><td class="mono">${esc(x.vmid)}</td><td>${esc(x.name)} <span class="peer">${x.type === "lxc" ? "CT" : "VM"}</span></td><td><span class="gs"><i class="sdot ${esc(x.status)}"></i>${esc(GSTATUS[x.status] || x.status)}</span></td><td class="mono">${fmtMem(x.mem)}&#8239;/&#8239;${fmtMem(x.maxmem)}</td></tr>`).join("");
      guests = `<h3>Гости Proxmox <span class="count">${run} из ${items.length} работают</span></h3>
        <div class="tblwrap"><table class="ifs"><thead><tr><th>ID</th><th>Имя</th><th>Статус</th><th>RAM</th></tr></thead><tbody>${rows}</tbody></table></div>
        <p class="note">Источник: гостевой агент ноды, команда <code>${esc(gs.source)}</code>. ${esc(gs.note)}</p>`;
    }
  }
  const hasConsole = n.consoleType && n.consoleType !== "none";
  const layered = st.der && st.layer !== "l1";
  return `
    <div class="ph"><div><p class="eyebrow">${esc(CAT_NAME[c][0])} · ${esc(typeName(n.type))}</p><h2>${esc(n.name)}</h2></div>
      <button class="x" type="button" data-act="close" aria-label="Закрыть">×</button></div>
    ${statusLine(n)}
    ${layered ? nodeLayerHTML(n) : ""}
    <dl class="kv">${kv.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>
    ${layered ? "" : `<h3>Интерфейсы <span class="count">${ifs.length}</span></h3>
    ${ifs.length ? `<div class="tblwrap"><table class="ifs"><thead><tr><th>Порт</th><th>Сосед</th></tr></thead><tbody>${ifRows}</tbody></table></div>` : `<p class="glead">Ни один порт не подключён.</p>`}
    ${vlan}`}
    ${guests}
    ${hasConsole ? `<div><button class="gbtn primary" type="button" data-act="console">Открыть консоль</button></div>` : ""}
    <p class="idline">node_id ${esc(n.id)}</p>`;
}
function linkHTML(l){
  const m = st.model, A = m.nodes.get(l.a.nodeId), B = m.nodes.get(l.b.nodeId);
  const fl = fmtFilters(l.filters);
  const card = (N, end) => `<button class="endcard" type="button" data-goto-node="${esc(N.id)}"><b>${esc(N.name)}</b><span class="mono peer">${esc(end.text)}</span></button>`;
  return `
    <div class="ph"><div><p class="eyebrow">Линк · ${esc(l.type)}</p><h2>${esc(A.name)} ↔ ${esc(B.name)}</h2></div>
      <button class="x" type="button" data-act="close" aria-label="Закрыть">×</button></div>
    <div class="ends">${card(A, l.a)}${card(B, l.b)}</div>
    <dl class="kv">
      <dt>Состояние</dt><dd>${linkState(l)}</dd>
      ${fl.length ? fl.map(f => `<dt>${f.k}</dt><dd class="mono">${esc(f.v)}</dd>`).join("") : `<dt>Фильтры</dt><dd>нет</dd>`}
    </dl>
    ${st.der && st.layer === "l2" ? linkL2HTML(l) : ""}
    <p class="idline">link_id ${esc(l.id)}</p>`;
}
function linkState(l){
  const f1 = st.der && st.der.l1.links.get(l.id);
  if (f1 && f1.down) return `<span class="tag bad">не работает</span> ${esc(f1.why.join("; "))}`;
  return l.suspend ? "Приостановлен — кадры не передаются" : f1 ? "Работает" : "Активен";
}
/* что настроено на портах и что проходит по линку */
function modeText(f, i){
  if (!i) return "фактов нет";
  if (i.admin === "down") return "shutdown";
  const sp = i.switchport;
  if (sp && sp.mode === "access") return `access · VLAN ${sp.vlan}`;
  if (sp && sp.mode === "trunk") return `trunk · ${Array.isArray(sp.allowed) ? sp.allowed.join(",") : "все VLAN"} · native ${sp.native != null ? sp.native : 1}`;
  if (i.kind === "subif") return `dot1q ${i.dot1q}`;
  if (i.kind === "svi") return `SVI VLAN ${i.vlan}`;
  if (i.kind === "loopback") return "loopback";
  const subs = f ? f.subs(i.name) : [];
  return subs.length ? `теги ${subs.map(s => s.dot1q).join(", ")}${v4(i).length ? " + без тега" : ""}` : "без тега";
}
function linkL2HTML(l){
  const r = st.der.l2.links.get(l.id), fx = st.fx, m = st.model;
  const row = (e, i) => `<tr><td>${esc(m.nodes.get(e.nodeId).name)} <span class="mono peer">${esc(i ? i.name : e.text)}</span></td><td class="mono">${esc(modeText(fx.nodes.get(e.nodeId), i))}</td></tr>`;
  const pass = !r.known ? "нет фактов об одном из портов" : st.der.l1.links.get(l.id).down ? "ничего — линк не работает"
    : r.tags.length ? (r.vlans.length ? r.vlans.map(v => `<span class="tag" style="border-left:3px solid ${vcolor(v)}">VLAN ${v}</span>`).join(" ") : "кадры без тега (точка-точка)") : `<span class="tag bad">ничего</span>`;
  return `<h3>L2 на концах</h3><div class="tblwrap"><table class="ifs"><thead><tr><th>Порт</th><th>Настройка</th></tr></thead><tbody>${row(l.a, r.ia)}${row(l.b, r.ib)}</tbody></table></div>
    <dl class="kv"><dt>Проходит</dt><dd>${pass}</dd></dl>
    ${r.issues.length ? issuesHTML(r.issues.map(text => ({ sev: "warn", text })), "") : ""}`;
}
/* раздел ноды для текущего слоя */
function nodeLayerHTML(n){
  const f = st.fx.nodes.get(n.id), L = st.layer, D = st.der;
  if (!f || !f.ifs) return `<p class="note">Фактов об интерфейсах нет${f && NODE_OFF[f.status] ? ` — узел ${NODE_OFF[f.status]}, бэкенд не может его опросить` : ""}.</p>`;
  const nodeIssues = layerIssues().filter(x => x.text.includes(n.name + " ") || (x.go && x.go.kind === "node" && x.go.id === n.id));
  const iss = nodeIssues.length ? issuesHTML(nodeIssues, "") : "";
  if (L === "l2") {
    const rows = f.ifs.filter(i => i.kind !== "loopback").map(i => {
      const d = i.switchport ? null : D.l2.domFor(n.id, i);
      const dom = d ? `<button class="tag" type="button" data-goto-vlan="${d.id}" style="cursor:pointer;border:0;border-left:3px solid ${d.vlans.length ? vcolor(d.vlans[0]) : "var(--wire)"}">${esc(d.label)}</button>` : "";
      return `<tr><td class="mono">${esc(i.name)}</td><td class="mono">${esc(modeText(f, i))}</td><td>${dom}${i.stp ? ` <span class="peer">STP ${esc(i.stp.state)}</span>` : ""}</td></tr>`;
    }).join("");
    return `<h3>L2: порты и VLAN</h3>${iss}<div class="tblwrap"><table class="ifs"><thead><tr><th>Интерфейс</th><th>Настройка</th><th>Домен</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  if (L === "l3") {
    const rows = f.ifs.filter(i => v4(i).length || i.kind === "physical").map(i => {
      const ips = v4(i).map(c => `<button class="tag" type="button" data-goto-subnet="${esc(c.key)}" style="cursor:pointer;border:0">${esc(c.ip + "/" + c.pre)}</button>`).join(" ") || `<span class="peer">—</span>`;
      const state = i.admin === "down" ? `<span class="tag bad">shutdown</span>` : i.oper === "down" ? `<span class="tag bad">down</span>` : "up";
      return `<tr><td class="mono">${esc(i.name)}</td><td>${ips}</td><td>${state}</td></tr>`;
    }).join("");
    const gw = f.ifs.map(i => i.gateway).filter(Boolean);
    return `<h3>L3: адреса</h3>${iss}<div class="tblwrap"><table class="ifs"><thead><tr><th>Интерфейс</th><th>Адрес</th><th>Состояние</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${gw.length ? `<dl class="kv"><dt>Шлюз</dt><dd class="mono">${esc(gw.join(", "))}</dd></dl>` : ""}`;
  }
  const oi = f.ifs.filter(i => i.ospf);
  if (!oi.length) return `<p class="note">OSPF на этом узле не настроен.</p>`;
  const adj = D.rt.edges.filter(e => e.a === n.id || e.b === n.id);
  const arows = adj.map(e => { const o = e.a === n.id ? e.b : e.a;
    return `<tr data-goto-adj="${esc(e.id)}" style="cursor:pointer"><td>${nodeName(o)}</td><td class="mono">${esc(e.ifs[n.id] || "—")}</td><td>${e.miss ? `<span class="tag bad">нет</span>` : "area " + esc(e.area) + " · " + esc(e.state)}</td></tr>`; }).join("");
  const irows = oi.map(i => `<tr><td class="mono">${esc(i.name)}</td><td class="mono">${esc(v4(i).map(c => c.ip + "/" + c.pre).join(", ") || "—")}</td><td>area ${esc(i.ospf.area)}${i.ospf.passive ? " · passive" : ""}</td></tr>`).join("");
  return `<dl class="kv"><dt>Router ID</dt><dd class="mono">${esc(f.rid || "—")}</dd></dl>${iss}
    <h3>Соседи <span class="count">${adj.filter(e => !e.miss).length}</span></h3>
    ${adj.length ? `<div class="tblwrap"><table class="ifs"><thead><tr><th>Сосед</th><th>Интерфейс</th><th>Состояние</th></tr></thead><tbody>${arows}</tbody></table></div>` : `<p class="glead">Соседей нет.</p>`}
    <h3>Интерфейсы в OSPF</h3><div class="tblwrap"><table class="ifs"><thead><tr><th>Интерфейс</th><th>Адрес</th><th>Area</th></tr></thead><tbody>${irows}</tbody></table></div>`;
}
const closeBtn = `<button class="x" type="button" data-act="close" aria-label="Закрыть">×</button>`;
function memberRows(list, cols){
  return `<div class="tblwrap"><table class="ifs"><thead><tr>${cols.map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${list.join("")}</tbody></table></div>`;
}
function subnetHTML(s){
  const mask = s.pre === 0 ? "0.0.0.0" : ipStr(2 ** 32 - 2 ** (32 - s.pre));
  const rows = s.members.map(mb => `<tr data-goto-node="${esc(mb.nodeId)}" style="cursor:pointer"><td>${nodeName(mb.nodeId)} <span class="mono peer">${esc(mb.iface.name)}</span></td><td class="mono">${esc(mb.c.ip)}${mb.dup ? ` <span class="tag bad">дубль</span>` : ""}</td><td>${mb.dom ? esc(mb.dom.label) : "—"}${mb.down ? ` <span class="tag bad">down</span>` : ""}</td></tr>`);
  return `<div class="ph"><div><p class="eyebrow">Подсеть IPv4</p><h2 class="mono" style="font-family:var(--mono);font-size:21px">${esc(s.key)}</h2></div>${closeBtn}</div>
    <dl class="kv"><dt>Маска</dt><dd class="mono">${mask}</dd><dt>Адресов</dt><dd class="mono">${s.pre >= 31 ? s.size : Math.max(0, s.size - 2)}</dd><dt>Занято</dt><dd class="mono">${s.members.length}</dd></dl>
    ${s.issues.length ? issuesHTML(s.issues.map(text => ({ sev: "bad", text })), "") : `<p class="okline">Все участники в одном L2-домене, дублей нет.</p>`}
    <h3>Участники <span class="count">${s.members.length}</span></h3>${memberRows(rows, ["Интерфейс", "Адрес", "L2-домен"])}`;
}
function vlanHTML(d){
  const rows = d.members.map(x => `<tr data-goto-node="${esc(x.nodeId)}" style="cursor:pointer"><td>${nodeName(x.nodeId)} <span class="mono peer">${esc(x.iface.name)}</span></td><td class="mono">${esc(v4(x.iface).map(c => c.ip + "/" + c.pre).join(", ") || "—")}</td></tr>`);
  const iss = st.der.l2.issues.filter(x => x.go && x.go.kind === "vlan" && x.go.id === d.id);
  return `<div class="ph"><div><p class="eyebrow">L2-домен · широковещательный</p><h2>${esc(d.label)}</h2></div>${closeBtn}</div>
    <dl class="kv"><dt>${d.vlans.length ? "Коммутаторы" : "Между"}</dt><dd>${esc(d.where)}</dd><dt>Линков</dt><dd class="mono">${d.links.length}</dd></dl>
    ${iss.length ? issuesHTML(iss, "") : ""}
    <p class="glead">Кадр, отправленный любым участником на широковещательный адрес, получат все остальные участники этого домена — и только они.</p>
    <h3>Участники <span class="count">${d.members.length}</span></h3>${memberRows(rows, ["Интерфейс", "Адрес"])}`;
}
function adjHTML(e){
  const f = idv => st.fx.nodes.get(idv);
  const card = idv => `<button class="endcard" type="button" data-goto-node="${esc(idv)}"><b>${nodeName(idv)}</b><span class="mono peer">${esc(e.ifs[idv] || "—")} · RID ${esc(f(idv).rid || "—")}</span></button>`;
  return `<div class="ph"><div><p class="eyebrow">${e.miss ? "Ожидаемое соседство" : "Соседство " + esc(String(e.proto).toUpperCase())}</p><h2>${nodeName(e.a)} ↔ ${nodeName(e.b)}</h2></div>${closeBtn}</div>
    <div class="ends">${card(e.a)}${card(e.b)}</div>
    <dl class="kv"><dt>Area</dt><dd class="mono">${esc(e.area)}</dd><dt>Состояние</dt><dd>${e.miss ? `<span class="tag bad">не установлено</span>` : esc(e.state)}</dd></dl>
    ${e.miss ? issuesHTML([{ sev: "bad", text: "Причина по фактам: " + e.reason + "." }], "") + `<p class="glead">OSPF включён на обоих концах одного сегмента, значит соседство должно было подняться.</p>` : ""}`;
}
body.addEventListener("click", e => {
  const t = e.target.closest("[data-goto-node],[data-goto-link],[data-goto-subnet],[data-goto-vlan],[data-goto-adj],[data-act]"); if (!t) return;
  if (t.dataset.gotoNode) select({ kind: "node", id: t.dataset.gotoNode });
  else if (t.dataset.gotoLink) select({ kind: "link", id: t.dataset.gotoLink });
  else if (t.dataset.gotoSubnet) select({ kind: "subnet", id: t.dataset.gotoSubnet });
  else if (t.dataset.gotoVlan) select({ kind: "vlan", id: t.dataset.gotoVlan });
  else if (t.dataset.gotoAdj) select({ kind: "adj", id: t.dataset.gotoAdj });
  else if (t.dataset.act === "close") select(null);
  else if (t.dataset.act === "console" && st.sel && st.sel.kind === "node") {
    /* консоли открывает mod_gns3lab/lab: он знает сессию и решает, можно ли */
    const n = st.model.nodes.get(st.sel.id);
    if (n) root.dispatchEvent(new CustomEvent("gns3lab:openconsole", { detail: { nodeId: n.id, name: n.name, consoleType: n.consoleType, nodeType: n.type } }));
  }
});
body.addEventListener("keydown", e => {
  const t = e.target.closest && e.target.closest("tr[data-goto-link]");
  if (t && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select({ kind: "link", id: t.dataset.gotoLink }); }
});

/* нижняя шторка на телефоне */
function setSheet(open){ panel.classList.toggle("open", open); sel$("handle").setAttribute("aria-expanded", String(open)); }
/* шторку можно тянуть за заголовок: вниз — закрыть, вверх — открыть; короткий тап — переключить */
const handle = sel$("handle");
let sd = null, suppressClick = false;
handle.addEventListener("pointerdown", e => {
  if (!mobile()) return;
  const now = performance.now();
  sd = { y: e.clientY, open: panel.classList.contains("open"), off: panel.offsetHeight - 50, moved: false, last: e.clientY, lt: now, v: 0, cur: 0 };
  sd.cur = sd.open ? 0 : sd.off;
  try { handle.setPointerCapture(e.pointerId); } catch (_) {}
});
handle.addEventListener("pointermove", e => {
  if (!sd) return;
  const dy = e.clientY - sd.y;
  if (!sd.moved && Math.abs(dy) > 6) { sd.moved = true; panel.classList.add("dragging"); }
  if (!sd.moved) return;
  const now = performance.now();
  sd.v = (e.clientY - sd.last) / Math.max(1, now - sd.lt); sd.last = e.clientY; sd.lt = now;
  sd.cur = Math.min(sd.off, Math.max(0, (sd.open ? 0 : sd.off) + dy));
  panel.style.transform = `translateY(${sd.cur}px)`;
});
function endSheet(){
  if (!sd) return;
  if (sd.moved) {
    suppressClick = true;
    const open = sd.v < -0.4 ? true : sd.v > 0.4 ? false : sd.cur < sd.off / 2;
    panel.classList.remove("dragging");
    void panel.offsetHeight;                                   // включить transition с текущей позиции пальца
    panel.style.transform = `translateY(${open ? 0 : sd.off}px)`;
    setSheet(open);
    setTimeout(() => { panel.style.transform = ""; }, 260);
  }
  sd = null;
}
handle.addEventListener("pointerup", endSheet);
handle.addEventListener("pointercancel", endSheet);
handle.addEventListener("click", () => {
  if (suppressClick) { suppressClick = false; return; }
  setSheet(!panel.classList.contains("open"));
});

/* ---------- загрузка ---------- */
function toast(msg){
  const t = sel$("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 6000);
}
/* пересчитать слои из выбранной демо-фикстуры фактов */
function recompute(){
  const m = st.model, fx = st.factsAvail ? indexFacts(m, FACTS[st.factsKey]) : null;
  st.fx = fx;
  if (!fx) { st.der = null; return; }
  const l1 = deriveL1(m, fx), l2 = deriveL2(m, fx, l1);
  st.der = { l1, l2, l3: deriveL3(m, fx, l2), rt: deriveRT(m, fx, l1, l2) };
}
function load(doc, label, demoStatus, guests, withFacts){
  if (!doc || typeof doc !== "object" || !doc.topology) throw new Error("В файле нет блока topology — это не файл топологии GNS3.");
  st.model = buildModel(doc);
  st.guests = guests || null;
  st.sourceLabel = label;
  /* придуманные статусы — только в демо; у лабы менеджера статусы приходят лишь от запущенной сессии */
  st.demoStatus = demoStatus || (DATA.live ? pseudoStatus(st.model) : null);
  st.sel = null;
  st.factsAvail = !!withFacts && !!FACTS.ok;
  if (!st.factsAvail) st.layer = "l1";
  recompute(); syncLayerUI();
  sel$("projName").textContent = st.model.name;
  const nN = st.model.nodes.size, nL = st.model.links.length;
  sel$("meta").innerHTML = `<span>${nN} ${plural(nN, ["устройство", "устройства", "устройств"])} · ${nL} ${plural(nL, ["линк", "линка", "линков"])}</span>` +
    (st.model.version ? `<span>GNS3 ${esc(st.model.version)}</span>` : "") + `<span class="src">${esc(label)}</span>`;
  renderScene(); renderPanel(); fit();
  if (mobile()) setSheet(false);
}
function pseudoStatus(m){
  const out = {}; let i = 0;
  m.nodes.forEach(n => { out[n.id] = (i++ % 5 === 3) ? "stopped" : "started"; });
  return out;
}
function readFile(file){
  if (!file) return;
  const fr = new FileReader();
  fr.onload = () => {
    const txt = String(fr.result || "");
    if (txt.startsWith("PK")) { toast("Это архив .gns3project. Распакуйте его и откройте лежащий внутри файл .gns3."); return; }
    try { load(JSON.parse(txt), file.name); }
    catch (err) { toast(err instanceof SyntaxError ? "Файл не читается как JSON. Проверьте, что это .gns3 из папки проекта." : err.message); }
  };
  fr.onerror = () => toast("Не удалось прочитать файл.");
  fr.readAsText(file);
}
sel$("fileInput").addEventListener("change", e => { readFile(e.target.files[0]); e.target.value = ""; });
let dragDepth = 0;
/* открыть свой .gns3 можно только в демо: у лабы менеджера схема — эталон активности */
const demoData = !!DATA.live;
wrap.addEventListener("dragenter", e => { if (!demoData) return; e.preventDefault(); dragDepth++; sel$("drop").hidden = false; });
wrap.addEventListener("dragover", e => e.preventDefault());
wrap.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; sel$("drop").hidden = true; } });
wrap.addEventListener("drop", e => { if (!demoData) return; e.preventDefault(); dragDepth = 0; sel$("drop").hidden = true; readFile(e.dataTransfer.files[0]); });

/* ---------- кнопки ---------- */
sel$("btnFit").addEventListener("click", fit);
sel$("btnIn").addEventListener("click", () => { const [x, y] = center(); zoomAt(x, y, 1.25); });
sel$("btnOut").addEventListener("click", () => { const [x, y] = center(); zoomAt(x, y, 0.8); });
sel$("btnStatus").addEventListener("click", e => {
  st.statusOn = !st.statusOn; e.currentTarget.setAttribute("aria-pressed", String(st.statusOn));
  const keep = st.sel; renderScene(); st.sel = keep; applySel(); renderPanel();
});
sel$("layerSeg").addEventListener("click", e => { const b = e.target.closest("button[data-layer]"); if (b && !b.disabled) setLayer(b.dataset.layer); });
sel$("factsSel").addEventListener("change", e => {
  st.factsKey = e.target.value;
  recompute();
  const s = st.sel;
  if (s && ((s.kind === "subnet" && !st.der.l3.subnets.some(x => x.key === s.id)) || (s.kind === "vlan" && !st.der.l2.domains.some(x => x.id === s.id)) || (s.kind === "adj" && !st.der.rt.edges.some(x => x.id === s.id)))) st.sel = null;
  renderScene(); renderPanel();
});
let lastW = 0;
window.addEventListener("resize", () => { const w = wrap.clientWidth; if (Math.abs(w - lastW) > 40) { lastW = w; fit(); } });

/* ---------- сессия менеджера (события от mod_gns3lab/lab) ---------- */
const loadPreview = () => load(DATA.topology, DATA.source || "демо", DATA.live && DATA.live.node_status, DATA.live && DATA.live.guests, true);
let liveKey = null;
root.addEventListener("gns3lab:session", e => {
  const s = e.detail.session;
  if (!s || s.status !== "running" || !s.topology) {
    if (liveKey) { liveKey = null; loadPreview(); }
    return;
  }
  const statuses = {}, types = {};
  s.nodes.forEach(n => { statuses[n.nodeid] = n.status; types[n.nodeid] = n.consoletype; });
  if (!st.statusOn) { st.statusOn = true; sel$("btnStatus").setAttribute("aria-pressed", "true"); }
  const key = s.sessionid + "\n" + s.topology;
  if (key !== liveKey) {
    let doc;
    try { doc = JSON.parse(s.topology); } catch (err) { toast("Менеджер прислал топологию, которая не читается как JSON."); return; }
    /* статусы нод — из живых данных сессии, а не из сохранённого проекта */
    ((doc.topology && doc.topology.nodes) || []).forEach(n => { delete n.status; if (types[n.node_id]) n.console_type = types[n.node_id]; });
    liveKey = key;
    load(doc, "сессия", statuses, null, false);
  } else {
    st.demoStatus = statuses;
    const keep = st.sel; renderScene(); st.sel = keep; applySel(); renderPanel();
  }
});
/* имя порта, нажатое в консоли: показать линк этого порта */
const portKey = v => {
  const m = /^([a-z]*)\s*(\d+(?:\/\d+)*)$/i.exec(String(v).trim()); if (!m) return null;
  const p = m[1].toLowerCase();
  return { k: ["g", "f", "e", "s", "l"].find(c => p.startsWith(c)) || p, num: m[2] };
};
root.addEventListener("gns3lab:showport", e => {
  const want = portKey(e.detail.port); if (!want || !st.model) return;
  const same = end => { const k = portKey(end.text); return k && k.num === want.num && (k.k === want.k || !k.k); };
  const l = st.model.links.find(x => (x.a.nodeId === e.detail.nodeId && same(x.a)) || (x.b.nodeId === e.detail.nodeId && same(x.b)));
  if (l) { select({ kind: "link", id: l.id }); if (mobile()) setSheet(true); }
  else toast(`Порт ${e.detail.port} не подключён на схеме.`);
});

loadPreview();
lastW = wrap.clientWidth;
if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { const keep = st.sel; renderScene(); st.sel = keep; applySel(); });
};
