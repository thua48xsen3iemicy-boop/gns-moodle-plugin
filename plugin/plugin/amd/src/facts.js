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
 * Device facts of a lab and the layers derived from them: L1 physical, L2 VLAN, L3 IP with the static and default
 * routes, OSPF. Also the MAC table of a switch, for display only.
 *
 * Pure functions without DOM access. The format of the facts and the rules of every layer are described in
 * docs/live-state.md. The texts of the findings are still Russian literals, as they were in the first viewer;
 * moving them to lang strings is a follow-up.
 *
 * @module     mod_gns3lab/facts
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

// Straight port of the first viewer; these style rules stay relaxed until the finding texts move to lang strings.
/* eslint-disable curly, max-len, jsdoc/require-jsdoc, no-eq-null */

import {isHost, isProvider, natSort} from 'mod_gns3lab/model';

const NODE_OFF = {stopped: "остановлен", suspended: "на паузе"};

export function ip4(s){
  const p = String(s).split("."); if (p.length !== 4) return null;
  let v = 0;
  for (const x of p) { if (!/^\d{1,3}$/.test(x) || +x > 255) return null; v = v * 256 + +x; }
  return v;
}
export const ipStr = v => [Math.floor(v / 16777216) % 256, Math.floor(v / 65536) % 256, Math.floor(v / 256) % 256, v % 256].join(".");
export function cidr(s){
  const [a, p] = String(s).split("/"), addr = ip4(a), pre = p == null ? 32 : +p;
  if (addr == null || !(pre >= 0 && pre <= 32)) return null;
  const size = 2 ** (32 - pre), net = Math.floor(addr / size) * size;
  return { addr, pre, net, size, ip: a, key: ipStr(net) + "/" + pre };
}
export const hostPart = c => c.pre >= 24 ? "." + (c.addr % 256) : c.ip;
export const v4 = i => (i && Array.isArray(i.ipv4) ? i.ipv4.map(cidr).filter(Boolean) : []);
const overlap = (A, B) => A.net < B.net + B.size && B.net < A.net + A.size;

function indexFacts(m, facts){
  if (!facts || !facts.nodes) return null;
  const fx = { nodes: new Map(), byPort: new Map(), byL1: new Map(), byRid: new Map(), at: facts.generated_at || null };
  m.nodes.forEach(n => {
    const f = facts.nodes[n.id]; if (!f) return;
    const ifs = Array.isArray(f.interfaces) ? f.interfaces.filter(i => i && i.name) : null;
    const rec = { id: n.id, status: f.status || null, ifs, byName: new Map(), rid: f.router_id || null,
      adj: Array.isArray(f.adjacencies) ? f.adjacencies : [], vlans: Array.isArray(f.vlans) ? f.vlans : [],
      // No `routes` means "not collected", which is not the same as an empty table (docs/live-state.md §3.3).
      routes: Array.isArray(f.routes) ? f.routes.filter(r => r && typeof r === "object") : null,
      // The last collection failed: the facts beside it are the last known ones, not the current ones.
      err: f.error && typeof f.error === "object" ? f.error : null, at: f.collected_at || null,
      // No `mac_table` (or one without a list) means "not collected", which is not the same as an empty table.
      macs: f.mac_table && typeof f.mac_table === "object" && Array.isArray(f.mac_table.entries) ? f.mac_table : null,
      // Set by consolefacts.mergeSeen(): when and by which command the student saw some of these facts in a console.
      seen: f.seen && typeof f.seen === "object" ? f.seen : null,
      // Spanning tree of the bridge by VLAN (root, this bridge, is it the root); its ports have it in `stp` too.
      stp: f.stp && typeof f.stp === "object" && f.stp.vlans && typeof f.stp.vlans === "object" ? f.stp : null };
    (ifs || []).forEach(i => {
      rec.byName.set(i.name, i);
      if (i.port) fx.byPort.set(n.id + "|" + i.port.adapter + "/" + i.port.port, i);
    });
    const live = f.l1_ports;
    if (n.type === 'iou' && live && Array.isArray(live.ports)) {
      const age = Date.now() - Date.parse(live.observed_at);
      const fresh = live.ok === true && Number.isFinite(age) && age >= 0 && Number.isFinite(live.ttl)
        && live.ttl > 0 && age <= live.ttl * 1000;
      live.ports.forEach(p => {
        if (p && p.port) fx.byL1.set(n.id + '|' + p.port.adapter + '/' + p.port.port,
          {state: fresh && ['up', 'down'].includes(p.state) ? p.state : 'unknown', reason: p.reason});
      });
    }
    rec.subs = name => (ifs || []).filter(i => i.parent === name);
    if (rec.rid) fx.byRid.set(rec.rid, n.id);
    fx.nodes.set(n.id, rec);
  });
  return fx;
}
const endKey = e => e.nodeId + "|" + e.adapter + "/" + e.port;
export const endIf = (fx, e) => (fx && fx.byPort.get(endKey(e))) || null;
const portOf = (f, i) => i.port || (i.parent && f.byName.get(i.parent) && f.byName.get(i.parent).port) || null;
const endIs = (e, nodeId, p) => e.nodeId === nodeId && e.adapter === p.adapter && e.port === p.port;

/**
 * State of a link end as the scheme shows it (L1 `ends`).
 *
 * @param {object|null} der derived layers
 * @param {{nodeId: string, adapter: number, port: number}} e end of a link of the model
 * @returns {{down: boolean}|null} null without facts or for a port that is not on a link
 */
export const endState = (der, e) => (der && der.l1.ends.get(endKey(e))) || null;

/**
 * State of the link end an interface sits on.
 *
 * @param {object|null} der derived layers
 * @param {object} f facts record of the node
 * @param {object} i interface of the node
 * @returns {{down: boolean}|null} null if the interface is not on a link
 */
export const portEnd = (der, f, i) => {
  const p = portOf(f, i);
  return p ? endState(der, {nodeId: f.id, adapter: p.adapter, port: p.port}) : null;
};

/**
 * The cable an interface of a node sits on (a subinterface: the cable of its parent).
 *
 * @param {object} m topology model
 * @param {object|null} f facts record of the node
 * @param {object} i interface of the node
 * @returns {object|null} link of the model, null if the interface is on no cable
 */
export const linkOfIf = (m, f, i) => {
  const p = f && portOf(f, i);
  return (p && m.links.find(l => endIs(l.a, f.id, p) || endIs(l.b, f.id, p))) || null;
};

/*
 * L1: линк не работает, если приостановлен, нода остановлена или интерфейс выключен / без несущей.
 * ends — состояние каждого конца линка. У конца с mirror: true собственные admin / oper не читаются: порт коммутатора,
 * которым студент не управляет, повторяет линк, который падает только по остальным причинам. Порт без mirror — в том
 * числе порт Cisco-коммутатора с switchport — управляется на устройстве и читается как раньше. Остальные концы падают,
 * если остановлена нода или линк приостановлен, либо их собственный интерфейс выключен / без несущей.
 */
function deriveL1(m, fx){
  const links = new Map(), ends = new Map(), issues = [];
  m.links.forEach(l => {
    const hard = [], soft = [], mir = [], own = [], uncertain = [], liveEnd = [], off = [];
    let cfg = false;                                   // причина в настройке устройства, а не в состоянии лабы
    let halted = !!l.suspend;                          // линк приостановлен или нода любого конца остановлена
    if (l.suspend) hard.push("линк приостановлен в GNS3");
    const iou = [l.a, l.b].map(e => m.nodes.get(e.nodeId).type === 'iou');
    [l.a, l.b].forEach((e, k) => {
      const N = m.nodes.get(e.nodeId), f = fx.nodes.get(e.nodeId), i = endIf(fx, e);
      // Cisco–Cisco: the live pair state. Cisco–QEMU: only what the IOL end itself answers; until it is known
      // (the guest port is not up yet, no reply yet) the saved configuration and the guest facts stay in charge.
      let live = iou[0] && iou[1] ? fx.byL1.get(endKey(e)) : null;
      if (iou[k] && !iou[1 - k]) {
        const own = fx.byL1.get(endKey(e));
        live = own && own.state !== 'unknown' ? own : null;
      }
      liveEnd[k] = !!live;
      mir[k] = !!(i && i.mirror); own[k] = false;
      if (f && NODE_OFF[f.status]) { hard.push(`${N.name} ${NODE_OFF[f.status]}`); halted = true; }
      else if (live) {
        uncertain[k] = live.state === 'unknown';
        if (live.state === 'down') { soft.push(`${N.name} ${e.text}: соединение недоступно (L1)`); own[k] = off[k] = true; }
      }
      else if (i && !mir[k] && i.admin === "down") { hard.push(`${N.name} ${i.name}: shutdown`); cfg = true; own[k] = off[k] = true; }
      else if (i && !mir[k] && i.oper === "down") { soft.push(`${N.name} ${i.name}: нет несущей`); own[k] = true; }
    });
    // An end whose far end is off (silent IOL port, shutdown, a guest port admin down) has no carrier either:
    // - a QEMU NIC: the slot drops its carrier (slot 0.16.0); shown at once, not when the guest's own facts arrive;
    // - an IOL port the slot keeps alive (it has an iol.l1 record): the slot stops the keepalives while the guest
    //   port is down, and the IOL loses carrier ("line protocol down"), whatever its saved configuration says.
    // A fresh live L1 `up` comes first: such an end is not off.
    [l.a, l.b].forEach((e, k) => {
      const type = m.nodes.get(e.nodeId).type;
      const carrier = type === 'qemu' || (type === 'iou' && fx.byL1.has(endKey(e)));
      if (carrier && !mir[k] && off[1 - k] && !(liveEnd[k] && fx.byL1.get(endKey(e)).state === 'up')) own[k] = true;
    });
    const why = hard.length ? hard : soft;
    if (!hard.length && soft.length) cfg = true;
    const unknown = !why.length && uncertain.some(Boolean);
    links.set(l.id, { down: why.length > 0, unknown, why: unknown ? ['нет актуальных L1-данных'] : why });
    [l.a, l.b].forEach((e, k) => ends.set(endKey(e), {
      down: mir[k] ? why.length > 0 : halted || own[k], unknown: !halted && !own[k] && !!uncertain[k], live: liveEnd[k] }));
    if (why.length) issues.push({ sev: cfg ? "bad" : "info",
      text: `Линк ${epName(m, l.a)} — ${epName(m, l.b)} не работает: ${why.join("; ")}.`, go: { kind: "link", id: l.id } });
  });
  return { links, ends, issues };
}
const epName = (m, e) => m.nodes.get(e.nodeId).name + " " + e.text;

/* L2: что порт шлёт и принимает на проводе — "u" (без тега) и номера тегов dot1q */
function wireTags(f, i){
  const sp = i.switchport;
  if (sp && sp.mode === "access") return { sw: true, sp, set: new Set(["u"]), any: false, map: t => t === "u" ? +sp.vlan : null };
  if (sp && sp.mode === "trunk") {
    // no native at all (null): the trunk neither takes nor sends frames without a tag; a missing native is VLAN 1
    const nat = sp.native === null ? null : sp.native != null ? +sp.native : 1, all = sp.allowed == null || sp.allowed === "all";
    const allowed = all ? null : new Set(sp.allowed.map(Number)), set = new Set(nat === null ? [] : ["u"]);
    if (allowed) allowed.forEach(v => { if (v !== nat) set.add(v); });
    return { sw: true, sp, set, any: all, nat, map: t => t === "u" ? nat : t !== nat && (all || allowed.has(t)) ? t : null };
  }
  // a port without any VLAN forwards nothing: no L2 for it
  if (sp && sp.mode === "none") return { sw: true, sp, set: new Set(), any: false, none: true, map: () => null };
  if (sp) return null;
  const subs = f.subs(i.name), set = new Set();
  if (v4(i).length || !subs.length) set.add("u");
  subs.forEach(s => { if (s.dot1q != null && s.admin !== "down") set.add(+s.dot1q); });
  return { sw: false, set, any: false, subs };
}
export function endVertex(nodeId, i, w, t){
  if (w.sw) { const v = w.map(t); return v == null ? null : `S|${nodeId}|${v}`; }
  if (!w.set.has(t)) return null;
  if (t === "u") return `E|${nodeId}|${i.name}`;
  const s = w.subs.find(x => +x.dot1q === t); return s ? `E|${nodeId}|${s.name}` : null;
}
/* транк всегда может слать native без тега — это не ошибка, если на другом конце его никто не ждёт */
const sends = (w, t) => w.set.has(t) && !(t === "u" && w.sw && w.sp.mode === "trunk");
/**
 * What a port does in the spanning tree of a VLAN: `stp.vlans[vlan]`, or the one tree of a bridge that has no
 * per-VLAN trees (`stp: {state, role}`, docs/live-state.md §3.1). Null when nothing is known.
 *
 * @param {object} i interface of the facts
 * @param {number} vlan VLAN
 * @returns {object|null}
 */
export const portStp = (i, vlan) => {
  const stp = i && i.stp;
  if (!stp || typeof stp !== "object") return null;
  if (stp.vlans && typeof stp.vlans === "object" && stp.vlans[vlan]) return stp.vlans[vlan];
  return stp.state || stp.role ? { state: stp.state, role: stp.role } : null;
};
/**
 * STP cuts a loop at this port: an alternate or backup port, an inconsistent one (BKN*), or one `show spanning-tree
 * blockedports` named without its role. A designated or root port that is not forwarding yet (LIS, LRN, the BLK of
 * RSTP for 30 s after its link came up) is on its way to forwarding: no cut, only its state in the tooltip.
 *
 * @param {object|null} p see portStp()
 * @returns {boolean}
 */
export const stpBlocks = p => !!p && (p.state === "broken"
  || (["alternate", "backup"].includes(p.role) && p.state !== "forwarding") || (!p.role && p.state === "blocking"));
/**
 * The bridge is the root of the tree of the VLAN: true, false, or null when not known.
 *
 * @param {object} f facts of a node
 * @param {number} vlan VLAN
 * @returns {boolean|null}
 */
export const stpRoot = (f, vlan) => {
  const t = f && f.stp && f.stp.vlans && f.stp.vlans[vlan];
  return t && typeof t.is_root === "boolean" ? t.is_root : null;
};
const STP_SHORT = { root: "Root", designated: "Desg", alternate: "Altn", backup: "Back", master: "Mstr", disabled: "Dis",
  forwarding: "FWD", blocking: "BLK", listening: "LIS", learning: "LRN", broken: "BKN" };
/**
 * Role and state of a port in the IOS notation: "Altn BLK".
 *
 * @param {object} p see portStp()
 * @returns {string}
 */
export const stpShort = p => [p.role, p.state].filter(Boolean).map(x => STP_SHORT[x] || x).join(" ");
/**
 * The spanning tree of a port for a table: "VLAN 1: Altn BLK · VLAN 10: Desg FWD", or "—".
 *
 * @param {object} i interface of the facts
 * @returns {string}
 */
export const stpText = i => {
  const stp = i && i.stp;
  if (!stp || typeof stp !== "object") return "—";
  const per = stp.vlans && typeof stp.vlans === "object" ? Object.keys(stp.vlans).sort((a, b) => a - b) : [];
  if (per.length) return per.map(v => `VLAN ${v}: ${stpShort(stp.vlans[v])}`).join(" · ");
  return stp.state || stp.role ? stpShort(stp) : "—";
};
export function switchVlans(f){
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
  if (t === "u") return wTo.sw ? `${F} шлёт кадры без тега, а на транке ${T} native нет — такие кадры он отбросит.`
    : `${F} шлёт кадры без тега, а у ${T} только сабинтерфейсы с dot1q — такие кадры он отбросит.`;
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
    if (!rec.known || l1.links.get(l.id).down || l1.links.get(l.id).unknown || wa.none || wb.none) return;
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
    if (wa.sw && wb.sw && wa.map("u") != null && wb.map("u") != null && wa.map("u") !== wb.map("u"))
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
  return { links, domains, domFor, domOf, vlanIds, issues };
}

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
  return { subnets: list, nodes, issues, byIp };
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

/*
 * Маршруты на L3 (docs/live-state.md §4, «Маршруты»): статические маршруты и маршрут по умолчанию — стрелка от узла к
 * следующему шагу. Подключённые сети и маршруты без via (через интерфейс) не рисуются: их показывает сама подсеть.
 * Нет `routes` (не собирались) — маршрут по умолчанию берётся из `gateway` интерфейсов.
 * Маршрут, который устройство не установит (шлюз вне своих подсетей, свой адрес, выходной интерфейс не работает),
 * в выборе не участвует. Из установленных к одной сети побеждает меньшая метрика (у IOS — административное
 * расстояние, без метрики 1; у остальных без метрики 0); при равной IOS делит нагрузку (ECMP), другие берут первый.
 */
const DEFAULT_DST = "0.0.0.0/0";
const NOT_INSTALLED = new Set(["self", "offnet", "down"]);
export const routeInstalled = r => !NOT_INSTALLED.has(r.status);
export const routeActive = r => routeInstalled(r) && r.status !== "unused";
const dstName = r => r.def ? "по умолчанию" : `в ${r.dst}`;
const ifUp = (fx, l1, f, i) => {
  if (!i || i.admin === "down" || i.oper === "down") return false;
  const p = portOf(f, i), e = p && l1.ends.get(f.id + "|" + p.adapter + "/" + p.port);
  return !(e && e.down);
};
/* интерфейсы узла, в подсети которых адрес v: сначала dev маршрута, затем самая длинная маска */
const connectedTo = (f, v, dev) => (f.ifs || []).filter(i => i.kind !== "loopback")
  .flatMap(i => v4(i).filter(c => v >= c.net && v < c.net + c.size).map(c => ({ i, c })))
  .sort((a, b) => (b.i.name === dev) - (a.i.name === dev) || b.c.pre - a.c.pre);
const ownsIp = (f, v) => (f.ifs || []).some(i => v4(i).some(c => c.addr === v));

function nodeRoutes(f){
  const list = [];
  (f.routes || []).forEach(r => {
    const def = r.dst === "default" || r.dst === DEFAULT_DST, c = cidr(def ? DEFAULT_DST : r.dst);
    if (!c || r.via == null || ip4(r.via) == null) return;
    list.push({ dst: c.key, c, def: c.pre === 0, via: String(r.via), dev: r.dev || null, proto: r.proto || null,
      metric: Number.isFinite(+r.metric) && r.metric !== null && r.metric !== "" ? +r.metric : null, from: "routes" });
  });
  if (!f.routes) (f.ifs || []).forEach(i => {
    if (i.gateway == null || ip4(i.gateway) == null || list.some(r => r.via === String(i.gateway))) return;
    list.push({ dst: DEFAULT_DST, c: cidr(DEFAULT_DST), def: true, via: String(i.gateway), dev: i.name, proto: "gateway", metric: null, from: "gateway" });
  });
  return list;
}

function deriveRoutes(m, fx, l1, l2, l3){
  const routes = [], issues = [], byNode = new Map();
  const partial = [...m.nodes.keys()].some(id => !fx.nodes.has(id) || !fx.nodes.get(id).ifs);
  const nm = id => m.nodes.get(id).name;
  const owners = v => l3.byIp.get(ipStr(v)) || [];
  fx.nodes.forEach(f => {
    if (!f.ifs) return;
    const N = m.nodes.get(f.id), ios = N.type === "iou" || N.type === "dynamips", mine = [];
    nodeRoutes(f).forEach((r, k) => {
      const v = ip4(r.via), rec = { ...r, id: f.id + "|" + k, nodeId: f.id, order: k, to: null, toward: null, ifc: null, status: "ok", sev: null, why: "" };
      mine.push(rec); routes.push(rec);
      if (ownsIp(f, v)) { rec.status = "self"; rec.sev = "bad"; rec.why = `${r.via} — собственный адрес ${N.name}`; return; }
      const hit = connectedTo(f, v, r.dev)[0];
      if (!hit) { rec.status = "offnet"; rec.sev = "bad"; rec.why = `шлюз ${r.via} не входит ни в одну подсеть ${N.name}`; return; }
      rec.ifc = hit.i; rec.toward = hit.c.key;
      if (!ifUp(fx, l1, f, hit.i)) { rec.status = "down"; rec.sev = "warn"; rec.why = `интерфейс ${hit.i.name} выключен или без несущей — маршрут не действует`; return; }
      const dom = l2.domFor(f.id, hit.i), all = owners(v).filter(mb => mb.nodeId !== f.id);
      const mb = all.find(x => x.dom && x.dom === dom) || all[0];
      if (!mb) {
        rec.status = "nohop"; rec.sev = partial ? "warn" : "bad";
        rec.why = partial ? `адрес ${r.via} не найден среди собранных адресов` : `адреса ${r.via} нет ни у одного устройства`;
        return;
      }
      rec.to = { nodeId: mb.nodeId, iface: mb.iface };
      if (mb.down) { rec.status = "hopdown"; rec.sev = "warn"; rec.why = `интерфейс шлюза ${nm(mb.nodeId)} ${mb.iface.name} выключен или без несущей`; return; }
      if (dom && mb.dom && dom !== mb.dom) { rec.status = "l2"; rec.sev = "bad"; rec.why = `шлюз ${nm(mb.nodeId)} ${mb.iface.name} в той же подсети, но в другом L2-домене — кадры до него не дойдут`; }
    });
    // IOS resolves a next hop outside its subnets through its other routes (a recursive static route); Linux and the
    // other hosts refuse such a route.
    if (ios) mine.filter(r => r.status === "offnet").forEach(r => {
      const v = ip4(r.via);
      const via = mine.filter(o => o !== r && o.status === "ok" && v >= o.c.net && v < o.c.net + o.c.size).sort((a, b) => b.c.pre - a.c.pre)[0];
      if (!via) return;
      const mb = owners(v).find(x => x.nodeId !== f.id);
      Object.assign(r, { status: "recursive", sev: null, ifc: via.ifc, toward: null, to: mb ? { nodeId: mb.nodeId, iface: mb.iface } : via.to,
        why: `рекурсивно: ${r.via} — через ${via.via}` });
    });
    // Several installed routes to one network: the lower metric wins, a tie is ECMP on IOS and the first route elsewhere.
    const groups = new Map();
    mine.filter(routeInstalled).forEach(r => (groups.get(r.dst) || groups.set(r.dst, []).get(r.dst)).push(r));
    groups.forEach(list => {
      if (list.length < 2) return;
      const metric = r => r.metric != null ? r.metric : ios ? 1 : 0, best = Math.min(...list.map(metric));
      const top = list.filter(r => metric(r) === best);
      list.forEach(r => {
        if (metric(r) > best) {
          r.why = `${ios ? "AD" : "metric"} ${metric(r)} > ${best} — резервный` + (r.why ? "; " + r.why : "");
          r.status = "unused"; r.sev = null;
        }
      });
      if (ios && top.length > 1) top.forEach(r => { r.ecmp = true; });
      else top.slice(1).forEach(r => { r.status = "unused"; r.sev = null; r.why = `та же ${ios ? "AD" : "metric"}, работает маршрут через ${top[0].via}, он раньше в таблице`; });
    });
    byNode.set(f.id, mine);
    const where = r => `${N.name}: маршрут ${dstName(r)} через ${r.via}`;
    mine.filter(r => r.sev).forEach(r => issues.push({ sev: r.sev, text: `${where(r)} — ${r.why}.`, go: { kind: "node", id: f.id } }));
    const defs = mine.filter(r => r.def);
    if (defs.length > 1) {
      const live = defs.filter(routeActive);
      issues.push({ sev: "warn", go: { kind: "node", id: f.id }, text: `${N.name}: ${defs.length} маршрута по умолчанию (${defs.map(r => r.via).join(", ")}); `
        + (!live.length ? "ни один не работает." : live.length > 1 ? `нагрузка делится между ${live.map(r => r.via).join(" и ")} (ECMP).` : `работает только через ${live[0].via}.`) });
    }
    if (isHost(N) && !isProvider(N) && f.routes && !defs.length && f.ifs.some(i => i.kind !== "loopback" && v4(i).length))
      issues.push({ sev: "warn", text: `${N.name}: нет шлюза по умолчанию — за пределы своих подсетей узел не выйдет.`, go: { kind: "node", id: f.id } });
  });
  // A loop of default routes: a packet to an unknown network goes round it until its TTL runs out.
  const next = id => (byNode.get(id) || []).filter(r => r.def && routeActive(r) && r.to).map(r => r.to.nodeId);
  const loops = new Set();
  byNode.forEach((_, start) => {
    const path = [start];
    const walk = id => {
      for (const n of next(id)) {
        const at = path.indexOf(n);
        if (at >= 0) {
          const cyc = path.slice(at), key = [...cyc].sort().join("|");
          if (!loops.has(key)) {
            loops.add(key);
            cyc.forEach((x, k) => (byNode.get(x) || []).forEach(r => { if (r.def && r.to && r.to.nodeId === cyc[(k + 1) % cyc.length]) r.loop = true; }));
            issues.push({ sev: "bad", text: `Петля маршрутов по умолчанию: ${[...cyc, cyc[0]].map(nm).join(" → ")} — пакет в неизвестную сеть будет ходить по кругу.`, go: { kind: "node", id: cyc[0] } });
          }
        } else if (path.length < 32) { path.push(n); walk(n); path.pop(); }
      }
    };
    walk(start);
  });
  return { routes, byNode, issues };
}

/**
 * Follows a packet from a node to an address by the facts: on each node the connected subnet or the active route with
 * the longest prefix, then the owner of the next hop. Hosts and routers alike forward.
 *
 * @param {object} m model from buildModel
 * @param {object} fx index from derive
 * @param {object} der derived layers
 * @param {string} fromId node the packet starts at
 * @param {string} dst destination address ("10.0.2.10")
 * @returns {{ok: boolean, path: string[], hops: object[], src: string|null, fail: {nodeId: string, text: string, unknown: boolean}|null}}
 *   path: node ids from the start to where it ended; hops: per step {nodeId, route|null, iface}; src: address the packet
 *   leaves the start with.
 */
export function trace(m, fx, der, fromId, dst){
  const v = ip4(dst), path = [fromId], hops = [];
  const out = (ok, fail, src) => ({ ok, path, hops, src, fail });
  const nm = id => (m.nodes.get(id) || { name: "?" }).name;
  let at = fromId, src = null;
  for (let ttl = 0; ttl < 32; ttl++) {
    const f = fx.nodes.get(at);
    if (!f || !f.ifs) return out(false, { nodeId: at, text: `о ${nm(at)} нет собранных фактов`, unknown: true }, src);
    if (ownsIp(f, v)) return out(true, null, src);
    // Connected subnets of working interfaces and the active routes, the longest prefix first; a connected subnet
    // wins a tie.
    const conn = connectedTo(f, v).filter(x => ifUp(fx, der.l1, f, x.i)).map(x => ({ pre: x.c.pre, conn: x }));
    const rts = (der.l3.rt.byNode.get(at) || []).filter(r => routeActive(r) && v >= r.c.net && v < r.c.net + r.c.size).map(r => ({ pre: r.c.pre, r }));
    const best = [...conn, ...rts].sort((a, b) => b.pre - a.pre || !!a.r - !!b.r)[0];
    if (!best) {
      const unknown = !f.routes && !f.ifs.some(i => i.gateway);
      return out(false, { nodeId: at, unknown, text: unknown ? `маршруты ${nm(at)} не собраны` : `у ${nm(at)} нет маршрута до ${dst}` }, src);
    }
    const iface = best.conn ? best.conn.i : best.r.ifc;
    if (at === fromId) src = (v4(iface)[0] || {}).ip || null;
    hops.push({ nodeId: at, route: best.r || null, iface });
    let to = null;
    if (best.conn) {
      const here = at, dom = der.l2.domFor(here, iface);
      const mb = (der.l3.byIp.get(dst) || []).find(x => x.nodeId !== here && (!dom || !x.dom || x.dom === dom));
      if (!mb) return out(false, { nodeId: at, unknown: false, text: `в подсети ${best.conn.c.key} адрес ${dst} не виден по L2` }, src);
      if (mb.down) return out(false, { nodeId: at, unknown: false, text: `интерфейс ${nm(mb.nodeId)} ${mb.iface.name} выключен или без несущей` }, src);
      to = mb.nodeId;
    } else {
      if (!best.r.to || best.r.status !== "ok" && best.r.status !== "recursive")
        return out(false, { nodeId: at, unknown: best.r.status === "nohop" && best.r.sev === "warn", text: `${nm(at)}: маршрут ${dstName(best.r)} через ${best.r.via} — ${best.r.why}` }, src);
      to = best.r.to.nodeId;
    }
    if (path.includes(to)) { path.push(to); return out(false, { nodeId: to, unknown: false, text: `петля: ${path.map(nm).join(" → ")}` }, src); }
    path.push(to); at = to;
  }
  return out(false, { nodeId: at, unknown: false, text: "путь длиннее 32 шагов" }, src);
}

/**
 * What a node reaches: per subnet of L3, connected, or a trace there and back. The trace goes to a member of the
 * subnet whose routes are known, if there is one: a node without them could not say whether it answers.
 *
 * @param {object} m model from buildModel
 * @param {object} fx index from derive
 * @param {object} der derived layers
 * @param {string} fromId node
 * @returns {Map<string, {kind: string, there: object|null, back: object|null, target: object|null, nat: object|null}>} subnet
 *   key: kind is `direct` (a working interface in it), `ok` (there and back), `oneway` (there, not back), `fail` or
 *   `unknown`; nat is {nodeId, ip} when the target is the provider (isProvider): the way back goes to the address of the
 *   border router it got the packet from, not to the start. Empty for a provider itself.
 */
export function reach(m, fx, der, fromId){
  const out = new Map();
  // The provider does not care what its client reaches, and the networks of the client are not its business.
  if (isProvider(m.nodes.get(fromId))) return out;
  der.l3.subnets.forEach(s => {
    if (s.members.some(mb => mb.nodeId === fromId && !mb.down)) { out.set(s.key, { kind: "direct", there: null, back: null, target: null }); return; }
    // The member that can answer: a working interface of a node whose routes are known (a table or a gateway).
    const knows = mb => { const f = fx.nodes.get(mb.nodeId); return !!f && (!!f.routes || f.ifs.some(i => i.gateway)); };
    const others = s.members.filter(mb => mb.nodeId !== fromId);
    const target = others.find(mb => !mb.down && knows(mb)) || others.find(mb => !mb.down) || others[0];
    if (!target) { out.set(s.key, { kind: "unknown", there: null, back: null, target: null }); return; }
    const there = trace(m, fx, der, fromId, target.c.ip);
    if (!there.ok) { out.set(s.key, { kind: there.fail && there.fail.unknown ? "unknown" : "fail", there, back: null, target }); return; }
    // The provider has no routes to the networks of its client: it answers the address the packet came to it from,
    // that of the border router in their common subnet (NAT on the border).
    const last = there.hops[there.hops.length - 1], hop = last && (v4(last.iface)[0] || {}).ip;
    const nat = isProvider(m.nodes.get(target.nodeId)) && last && last.nodeId !== fromId && hop ? { nodeId: last.nodeId, ip: hop } : null;
    const to = nat ? nat.ip : there.src;
    const back = to ? trace(m, fx, der, target.nodeId, to) : null;
    out.set(s.key, { kind: back && back.ok ? "ok" : back && back.fail && back.fail.unknown ? "unknown" : "oneway", there, back, target, nat });
  });
  return out;
}

const MAC_RE = /^[0-9a-f]{2}([:-][0-9a-f]{2}){5}$/i;
const normMac = s => (typeof s === "string" && MAC_RE.test(s.trim()) ? s.trim().toLowerCase().replace(/-/g, ":") : null);

/**
 * The MAC table of a switch as the details card shows it (docs/live-state.md section 3.4).
 *
 * Display only: nothing here feeds the layers or the findings. The owner of an address is the other node that has an
 * interface with this MAC; if several nodes have it, nobody is named (the collector does not guess either). Of several
 * interfaces of one node with the same MAC (a sub-interface repeats its parent) the physical one is named.
 *
 * @param {object} m model from buildModel
 * @param {object|null} fx index from derive
 * @param {string} nodeId the switch
 * @returns {{rows: object[], truncated: boolean}|null} null if the table was not collected; no rows is an empty table.
 *   A row is {mac, iface, vlan, port, link, label, owner}: port is {adapter, port} of the switch interface, link the id
 *   of the cable on it and label the name of that port on the scheme (null if unknown); owner is {nodeId, node, iface}
 *   or null for an address nobody here has.
 */
export const macTable = (m, fx, nodeId) => {
  const f = fx && fx.nodes.get(nodeId), t = f && f.macs;
  if (!t) {
    return null;
  }
  const byMac = new Map();
  fx.nodes.forEach(o => {
    if (o.id === nodeId) {
      return;
    }
    (o.ifs || []).forEach(i => {
      const mac = normMac(i.mac);
      if (!mac) {
        return;
      }
      const per = byMac.get(mac) || byMac.set(mac, new Map()).get(mac), cur = per.get(o.id);
      if (!cur || (i.kind === "physical" && cur.kind !== "physical")) {
        per.set(o.id, i);
      }
    });
  });
  const rows = t.entries.filter(e => e && typeof e.mac === "string").map(e => {
    const mac = normMac(e.mac) || e.mac, sw = f.byName.get(e.iface), per = byMac.get(mac);
    const port = sw && sw.port ? {adapter: sw.port.adapter, port: sw.port.port} : null;
    const l = port && m.links.find(k => endIs(k.a, nodeId, port) || endIs(k.b, nodeId, port));
    const end = l && (endIs(l.a, nodeId, port) ? l.a : l.b);
    let owner = null;
    if (per && per.size === 1) {
      const [[id, i]] = per, N = m.nodes.get(id);
      owner = N ? {nodeId: id, node: N.name, iface: i.name} : null;
    }
    return {mac, iface: e.iface, vlan: e.vlan, port, link: l ? l.id : null, label: end ? end.text : null, owner};
  });
  return {rows, truncated: t.truncated === true};
};

/**
 * Colour of a VLAN: starts with the second colour of the palette, the first (blue) is the selection colour.
 *
 * @param {object|null} der derived layers
 * @param {number} v VLAN id
 * @returns {string} CSS colour
 */
export const vcolor = (der, v) => {
  const i = der ? der.l2.vlanIds.indexOf(v) : -1;
  return i < 0 ? "var(--wire)" : `var(--c${(i + 1) % 6 + 1})`;
};

/**
 * Colour of an OSPF area.
 *
 * @param {object|null} der derived layers
 * @param {string} a area
 * @returns {string} CSS colour
 */
export const acolor = (der, a) => {
  const i = der ? der.rt.areas.indexOf(a) : -1;
  return i < 0 ? "var(--wire)" : `var(--c${i % 6 + 1})`;
};

/**
 * Indexes the facts of a lab by node and port and derives the four layers.
 *
 * @param {object} m model from buildModel
 * @param {object|null} factsDoc facts document ("gns3lab.facts/1") or null
 * @returns {{fx: object, der: object}|null} index and layers, null without facts
 */
export const derive = (m, factsDoc) => {
  const fx = indexFacts(m, factsDoc);
  if (!fx) {
    return null;
  }
  const l1 = deriveL1(m, fx), l2 = deriveL2(m, fx, l1), l3 = deriveL3(m, fx, l2);
  l3.rt = deriveRoutes(m, fx, l1, l2, l3);
  l3.issues.push(...l3.rt.issues);
  return {fx, der: {l1, l2, l3, rt: deriveRT(m, fx, l1, l2)}};
};
