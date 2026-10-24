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
 * Details card of the student interface: a node, a link, a subnet, an L2 domain, an OSPF adjacency, and the overview
 * of the lab or of the current layer with its findings.
 *
 * The card is a floating panel on a computer and a sheet from the bottom on a phone. The texts of the tables are
 * still Russian literals, as they were in the first viewer; moving them to lang strings is a follow-up.
 *
 * @module     mod_gns3lab/details
 * @copyright  2026 mod_gns3lab contributors
 * @license    https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */

// Straight port of the first viewer; these style rules stay relaxed until the texts move to lang strings.
/* eslint-disable curly, max-len, jsdoc/require-jsdoc, no-eq-null */

import {esc, fmtFilters, natSort} from 'mod_gns3lab/model';
import {ipStr, v4, acolor, vcolor} from 'mod_gns3lab/facts';

const GiB = 1024 ** 3, MiB = 1024 ** 2;

const TYPE_NAME = { qemu: "QEMU VM", iou: "IOU", dynamips: "Dynamips", vpcs: "VPCS", docker: "Docker",
  ethernet_switch: "Встроенный свитч", ethernet_hub: "Хаб", cloud: "Cloud", nat: "NAT",
  virtualbox: "VirtualBox", vmware: "VMware", frame_relay_switch: "Frame Relay", atm_switch: "ATM-свитч", traceng: "TraceNG" };
const fmtMem = b => !b ? "—" : b >= GiB ? (b / GiB).toFixed(1).replace(".", ",") + " ГБ" : Math.round(b / MiB) + " МБ";
const CAT_NAME = { hypervisor: ["Гипервизор", "Гипервизоры"], router: ["Маршрутизатор", "Маршрутизаторы"], switch: ["Коммутатор", "Коммутаторы"], pc: ["Хост", "Хосты"],
  server: ["Сервер / VM", "Серверы и VM"], cloud: ["Внешняя сеть", "Внешние сети"], firewall: ["Межсетевой экран", "Межсетевые экраны"] };
const STATUS_NAME = { started: "Запущено", stopped: "Остановлено", suspended: "Приостановлено" };
const CONSOLE_NAME = { telnet: "Терминал (telnet → xterm.js)", vnc: "Экран (VNC → noVNC)", spice: "Экран (SPICE)", http: "Веб-интерфейс", https: "Веб-интерфейс", none: "нет" };

const typeName = t => TYPE_NAME[t] || t || "неизвестно";
const plural = (n, f) => { const a = n % 10, b = n % 100; return f[(a === 1 && b !== 11) ? 0 : (a >= 2 && a <= 4 && (b < 10 || b >= 20)) ? 1 : 2]; };
const GSTATUS = { running: "работает", stopped: "остановлена", paused: "пауза" };
const FACTS_NAME = { ok: "конфиг верный", broken: "конфиг с ошибками" };
const LAYER_NAME = { l1: "L1 · физика", l2: "L2 · VLAN", l3: "L3 · IP", rt: "OSPF" };
const SEV = { bad: 0, warn: 1, info: 2 };
const NODE_OFF = { stopped: "остановлен", suspended: "на паузе" };


/**
 * Builds the HTML of the cards for the current state of the stage.
 *
 * @param {object} stage the stage: model, layers, statuses
 * @param {object} strings language strings
 * @param {object} host canConsole(node) tells whether a console can be opened now
 * @returns {object} builders: summary, node, link, subnet, vlan, adj, issues
 */
const makeHtml = (stage, strings, host) => {
const st = {
  get model() { return stage.model; }, get der() { return stage.der; }, get fx() { return stage.fx; },
  get layer() { return stage.layer; }, get statusOn() { return stage.statusKnown; }, get factsKey() { return stage.factsKey; },
};
const nodeStatus = n => stage.nodeStatus(n);
const guestsOf = n => (stage.statusKnown && stage.guests && stage.guests[n.id]) || null;
const vc = v => vcolor(st.der, v);
const ac = a => acolor(st.der, a);
/* buttons of the consoles of both ends of a link */
const linkConsoles = l => {
  const btns = [l.a, l.b].map(e => st.model.nodes.get(e.nodeId)).filter(n => n && host.canConsole(n))
    .map(n => `<button class="gbtn" type="button" data-act="console" data-node="${esc(n.id)}">${esc(strings.ui_console_of.replace("{$a}", n.name))}</button>`).join("");
  return btns ? `<div class="dacts">${btns}</div>` : "";
};

function statusLine(n){
  const s = nodeStatus(n);
  if (!s) return `<p class="st st-none">${esc(strings.ui_status_nodata)}</p>`;
  return `<p class="st st-${s}">${STATUS_NAME[s] || esc(s)}</p>`;
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
  const rows = doms.map(d => `<li><button class="rowbtn" type="button" data-goto-vlan="${d.id}"><span class="sw" style="background:${d.vlans.length ? vc(d.vlans[0]) : "var(--wire)"}"></span><span>${esc(d.label)} <small>${esc(d.where)}</small></span><small>${d.members.length} ${plural(d.members.length, ["участник", "участника", "участников"])}</small></button></li>`).join("");
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
  const R = st.der.rt, rows = R.edges.map(e => `<li><button class="rowbtn" type="button" data-goto-adj="${esc(e.id)}"><span class="sw" style="background:${e.miss ? "var(--bad)" : ac(e.area)}"></span><span>${nodeName(e.a)} — ${nodeName(e.b)}</span><small>${e.miss ? "нет соседства" : "area " + esc(e.area) + " · " + esc(e.state)}</small></button></li>`).join("");
  return layerHead("Маршрутизация", "Соседства OSPF", "Рёбра — соседства, которые маршрутизаторы сообщили сами; цвет — area. Красный пунктир — соседство, которое должно было подняться (OSPF на обоих концах одного сегмента), но его нет.") + `
    <h3>Соседства <span class="count">${R.edges.filter(e => !e.miss).length}</span></h3><ul class="list">${rows}</ul>`;
}
function summaryHTML(){
  const m = st.model, counts = {};
  m.nodes.forEach(n => { const c = n.cat; counts[c] = (counts[c] || 0) + 1; });
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
  const m = st.model, p = n.props || {}, c = n.cat;
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
    <div class="ph"><div><p class="eyebrow">${esc(CAT_NAME[c][0])} · ${esc(typeName(n.type))}</p><h2>${esc(n.name)}</h2></div></div>
    ${statusLine(n)}
    ${layered ? nodeLayerHTML(n) : ""}
    <dl class="kv">${kv.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>
    ${layered ? "" : `<h3>Интерфейсы <span class="count">${ifs.length}</span></h3>
    ${ifs.length ? `<div class="tblwrap"><table class="ifs"><thead><tr><th>Порт</th><th>Сосед</th></tr></thead><tbody>${ifRows}</tbody></table></div>` : `<p class="glead">Ни один порт не подключён.</p>`}
    ${vlan}`}
    ${guests}
    ${hasConsole ? `<div><button class="gbtn primary" type="button" data-act="console" data-node="${esc(n.id)}">${esc(strings.ui_open_console)}</button></div>` : ""}
    <p class="idline">node_id ${esc(n.id)}</p>`;
}
function linkHTML(l){
  const m = st.model, A = m.nodes.get(l.a.nodeId), B = m.nodes.get(l.b.nodeId);
  const fl = fmtFilters(l.filters);
  const card = (N, end) => `<button class="endcard" type="button" data-goto-node="${esc(N.id)}"><b>${esc(N.name)}</b><span class="mono peer">${esc(end.text)}</span></button>`;
  return `
    <div class="ph"><div><p class="eyebrow">Линк · ${esc(l.type)}</p><h2>${esc(A.name)} ↔ ${esc(B.name)}</h2></div></div>
    <div class="ends">${card(A, l.a)}${card(B, l.b)}</div>
    <dl class="kv">
      <dt>Состояние</dt><dd>${linkState(l)}</dd>
      ${fl.length ? fl.map(f => `<dt>${f.k}</dt><dd class="mono">${esc(f.v)}</dd>`).join("") : `<dt>Фильтры</dt><dd>нет</dd>`}
    </dl>
    ${st.der && st.layer === "l2" ? linkL2HTML(l) : ""}
    ${linkConsoles(l)}
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
    : r.tags.length ? (r.vlans.length ? r.vlans.map(v => `<span class="tag" style="border-left:3px solid ${vc(v)}">VLAN ${v}</span>`).join(" ") : "кадры без тега (точка-точка)") : `<span class="tag bad">ничего</span>`;
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
      const dom = d ? `<button class="tag" type="button" data-goto-vlan="${d.id}" style="cursor:pointer;border:0;border-left:3px solid ${d.vlans.length ? vc(d.vlans[0]) : "var(--wire)"}">${esc(d.label)}</button>` : "";
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
const closeBtn = '';
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

return {
  summary: () => (st.layer === "l2" && st.der ? l2SummaryHTML() : st.layer === "l3" && st.der ? l3SummaryHTML()
    : st.layer === "rt" && st.der ? rtSummaryHTML() : summaryHTML()),
  node: nodeHTML, link: linkHTML, subnet: subnetHTML, vlan: vlanHTML, adj: adjHTML,
  issues: () => layerIssues().length,
  layerName: () => LAYER_NAME[st.layer],
};
};

export class Details {
    /**
     * Constructor.
     *
     * @param {HTMLElement} el the card element
     * @param {object} options
     * @param {object} options.stage the stage
     * @param {object} options.strings language strings
     * @param {object} options.host openConsole(node) and canConsole(node)
     */
    constructor(el, {stage, strings, host}) {
        this.el = el;
        this.stage = stage;
        this.strings = strings;
        this.host = host;
        this.sel = null;
        this.close = document.createElement('button');
        this.close.type = 'button';
        this.close.className = 'x';
        this.close.dataset.act = 'close';
        this.close.textContent = '×';
        this.close.setAttribute('aria-label', strings.ui_close);
        this.body = document.createElement('div');
        this.body.className = 'dcontent';
        el.append(this.close, this.body);
        el.addEventListener('click', e => this.clicked(e));
        el.addEventListener('keydown', e => {
            const t = e.target.closest && e.target.closest('tr[data-goto-link]');
            if (t && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault();
                this.goto({kind: 'link', id: t.dataset.gotoLink});
            }
        });
    }

    /**
     * Whether the card is on the screen.
     *
     * @returns {boolean}
     */
    get visible() {
        return !this.el.hidden;
    }

    /**
     * Shows the card of an object.
     *
     * @param {object} sel {kind: node|link|subnet|vlan|adj, id}
     */
    show(sel) {
        this.sel = sel;
        this.render();
    }

    /**
     * Shows the overview of the lab (or of the current layer, with its findings).
     */
    showSummary() {
        this.sel = null;
        this.render();
    }

    /**
     * Shows the overview, or hides it when it is already shown.
     */
    toggleSummary() {
        if (this.visible && !this.sel) {
            this.hide();
        } else {
            this.stage.select(null);
            this.showSummary();
        }
    }

    hide() {
        this.el.hidden = true;
        this.sel = null;
    }

    /**
     * Draws the card again after the lab, the layer or the facts changed.
     */
    refresh() {
        if (this.visible) {
            this.render();
        }
    }

    render() {
        const stage = this.stage, m = stage.model, D = stage.der, s = this.sel;
        if (!m) {
            return;
        }
        const h = makeHtml(stage, this.strings, this.host);
        let html = null, focus = null;
        if (!s) {
            html = h.summary();
        } else if (s.kind === 'node') {
            const n = m.nodes.get(s.id);
            html = n ? h.node(n) : null;
            focus = n;
        } else if (s.kind === 'subnet') {
            const x = D && D.l3.subnets.find(v => v.key === s.id);
            html = x ? h.subnet(x) : null;
            focus = x;
        } else if (s.kind === 'vlan') {
            const d = D && D.l2.domains.find(v => v.id === s.id);
            html = d ? h.vlan(d) : null;
        } else if (s.kind === 'adj') {
            const e = D && D.rt.edges.find(v => v.id === s.id);
            html = e ? h.adj(e) : null;
        } else {
            const l = m.links.find(x => x.id === s.id);
            html = l ? h.link(l) : null;
            if (l) {
                const A = m.nodes.get(l.a.nodeId), B = m.nodes.get(l.b.nodeId);
                focus = {cx: (A.cx + B.cx) / 2, cy: (A.cy + B.cy) / 2};
            }
        }
        if (html === null) {
            this.hide();
            return;
        }
        this.body.innerHTML = html;
        this.el.hidden = false;
        this.el.dataset.kind = s ? s.kind : 'summary';
        if (focus && focus.cx !== undefined) {
            stage.revealAbove(focus.cx, focus.cy);
        } else if (focus && focus.x !== undefined) {
            stage.revealAbove(focus.x, focus.y);
        }
    }

    /**
     * Goes to another object from a list in the card.
     *
     * @param {object} sel {kind, id}
     */
    goto(sel) {
        this.stage.select(sel);
        this.show(this.stage.sel);
    }

    clicked(e) {
        const t = e.target.closest('[data-goto-node],[data-goto-link],[data-goto-subnet],[data-goto-vlan],[data-goto-adj],[data-act]');
        if (!t) {
            return;
        }
        if (t.dataset.gotoNode) {
            this.goto({kind: 'node', id: t.dataset.gotoNode});
        } else if (t.dataset.gotoLink) {
            this.goto({kind: 'link', id: t.dataset.gotoLink});
        } else if (t.dataset.gotoSubnet) {
            this.goto({kind: 'subnet', id: t.dataset.gotoSubnet});
        } else if (t.dataset.gotoVlan) {
            this.goto({kind: 'vlan', id: t.dataset.gotoVlan});
        } else if (t.dataset.gotoAdj) {
            this.goto({kind: 'adj', id: t.dataset.gotoAdj});
        } else if (t.dataset.act === 'close') {
            this.hide();
            this.stage.select(null);
        } else if (t.dataset.act === 'console') {
            const id = t.dataset.node || (this.sel && this.sel.kind === 'node' ? this.sel.id : null);
            const n = id && this.stage.model.nodes.get(id);
            if (n) {
                this.hide();
                this.host.openConsole(n);
            }
        }
    }
}
