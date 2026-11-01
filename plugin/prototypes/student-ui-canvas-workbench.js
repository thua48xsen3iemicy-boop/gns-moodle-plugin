/* Canvas workbench. The data/reveal rules come from the plugin's generated pure-model bundle. */
const canvasState = {
  mode: "live", effectiveMode: "live", teacher: false, source: "fresh", mac: "learned", scenario: "base",
  at: Date.now(), base: null, draw: null, view: null, doc: null, result: null, subnets: [],
  mask: CanvasCore.reveal.emptyMask(), seen: new Map(), overviewTab: "objects", output: new Map(), scenarioBackup: null
};
const explorationKey = "exploration.v1." + GNS3.project_id;
function initializeCanvasState(){
  const saved = store.get(explorationKey, []);
  canvasState.mask = CanvasCore.reveal.maskFromItems(Array.isArray(saved) ? saved : []);
  $("overviewToggle").onclick = () => toggleCanvasPanel("overviewPanel");
  $("demoToggle").onclick = () => toggleCanvasPanel("demoPanel");
  $("overviewClose").onclick = () => closeCanvasPanels("overviewToggle");
  $("demoClose").onclick = () => closeCanvasPanels("demoToggle");
  $("viewMode").onchange = e => {
    canvasState.mode = e.target.value; canvasState.teacher = false; $("teacherView").checked = false;
    [...cons.values()].forEach(c => closeConsole(c.n));
    closeInsight(); select(null); selectLink(null); refreshPrototypeScene(); Object.assign(view, fitTarget()); applyView();
  };
  $("sourceState").onchange = e => { canvasState.source = e.target.value; refreshPrototypeScene(); };
  $("teacherView").onchange = e => {
    canvasState.teacher = e.target.checked;
    [...cons.values()].forEach(c => closeConsole(c.n));
    closeInsight(); select(null); selectLink(null); refreshPrototypeScene(); Object.assign(view, fitTarget()); applyView();
  };
  $("demoScenario").onchange = e => setCanvasScenario(e.target.value);
  $("macState").onchange = e => { canvasState.mac = e.target.value; refreshPrototypeScene(); };
  $("resetExploration").onclick = () => {
    canvasState.mask = CanvasCore.reveal.emptyMask(); canvasState.seen = new Map(); canvasState.output.clear();
    store.set(explorationKey, []); closeInsight(); refreshPrototypeScene(); Object.assign(view, fitTarget()); applyView();
  };
  $("refreshFacts").onclick = () => {
    canvasState.source = "fresh"; canvasState.at = Date.now(); $("sourceState").value = "fresh";
    canvasState.seen = new Map(); refreshPrototypeScene(); toast("Демонстрационный снимок обновлён");
  };
  ["overviewPanel", "demoPanel"].forEach(id => $(id).addEventListener("keydown", e => {
    if (e.key === "Escape") { e.stopPropagation(); closeCanvasPanels(id === "overviewPanel" ? "overviewToggle" : "demoToggle"); }
  }));
}
function closeCanvasPanels(focusId){
  ["overviewPanel", "demoPanel"].forEach(id => $(id).hidden = true);
  ["overviewToggle", "demoToggle"].forEach(id => $(id).setAttribute("aria-expanded", "false"));
  if (focusId) $(focusId).focus();
  scheduleLabels();
}
function toggleCanvasPanel(id){
  const open = $(id).hidden;
  closeCanvasPanels(); closeTasks(false); closeInsight(); closeRadial();
  if (open) {
    $(id).hidden = false;
    $(id === "overviewPanel" ? "overviewToggle" : "demoToggle").setAttribute("aria-expanded", "true");
    if (id === "overviewPanel") renderOverview();
    $(id).querySelector("button").focus();
  }
  scheduleLabels();
}
function sourceSnapshot(){
  if (canvasState.source === "none") return null;
  const at = canvasState.source === "stale" ? canvasState.at - 10 * 60000 : canvasState.at;
  const doc = {schema: "gns3lab.facts/1", rev: 1, generated_at: new Date(at).toISOString(), ttl: 90, nodes: {}};
  nodes.forEach(n => {
    const f = {...(n.facts || {}), status: n.status, collected_at: new Date(at).toISOString()};
    if (canvasState.source === "error" && n.name === "SW1") {
      f.error = {code: "timeout", message: "Истекло время ожидания коллектора"};
      f.collected_at = new Date(at - 10 * 60000).toISOString();
    }
    if (n.cat === "switch" && canvasState.mac !== "missing") {
      f.mac_table = {entries: canvasState.mac === "empty" ? [] : (exampleMac[n.name] || []).map(([vlan, mac, iface]) => ({vlan: Number(vlan), mac, iface})), truncated: canvasState.mac === "truncated"};
    }
    doc.nodes[n.id] = f;
  });
  return doc;
}
function updateCanvasModel(){
  const base = CanvasCore.model.buildModel(GNS3);
  // Include local scenario links while keeping the production model's endpoint format.
  base.links = links.map(l => ({id: l.id, suspend: l.suspend, filters: l.filters, a: {nodeId: l.ends[0].node.id, adapter: l.ends[0].adapter, port: l.ends[0].port, text: l.ends[0].label}, b: {nodeId: l.ends[1].node.id, adapter: l.ends[1].adapter, port: l.ends[1].port, text: l.ends[1].label}}));
  base.nodes.forEach(n => n.status = byId.get(n.id).status);
  canvasState.base = base; canvasState.effectiveMode = canvasState.teacher ? "live" : canvasState.mode;
  const source = sourceSnapshot();
  canvasState.sourceDoc = source;
  const R = CanvasCore.reveal;
  if (canvasState.effectiveMode === "fog") {
    canvasState.mask = R.settleReveal({mode: "fog", model: base, factsDoc: source, seen: canvasState.seen, mask: canvasState.mask}).mask;
    store.set(explorationKey, R.maskItems(canvasState.mask));
  }
  const merged = CanvasCore.consolefacts.mergeSeen(base, source, canvasState.seen);
  const {view: revealView, doc} = R.applyReveal({mode: canvasState.effectiveMode, model: base, doc: merged, mask: canvasState.mask, start: ["R1"]});
  canvasState.view = revealView; canvasState.doc = doc;
  canvasState.draw = R.drawModel(base, revealView);
  canvasState.result = CanvasCore.facts.derive(R.layerModel(base, revealView), doc);
  canvasState.subnets = (canvasState.result?.der.l3.subnets || []).map(s => ({...s, subnet: true, id: s.key, cx: s.x, cy: s.y}));
}
function visibleNodes(){ return canvasState.draw ? nodes.filter(n => canvasState.draw.nodes.has(n.id)) : nodes; }
function visibleLinks(){ return canvasState.draw ? links.filter(l => canvasState.draw.links.some(x => x.id === l.id)) : links; }
function nodeState(n){ return canvasState.view?.states.get(n.id) || "open"; }
function displayName(n){ return nodeState(n) === "silhouette" ? "Неизвестный узел" : n.name; }
function canvasFacts(n){ return canvasState.doc?.nodes[n.id] || null; }
function canvasGroupOpen(n, layer){ return canvasState.effectiveMode === "live" || !!canvasState.view?.groups.get(n.id)?.has(layer); }
function canvasIfaceAt(n, adapter, port){ return (canvasFacts(n)?.interfaces || []).find(i => i.port && i.port.adapter === adapter && i.port.port === port) || null; }
function canvasEndShown(e){ return !canvasState.view || canvasState.view.ends.has(`${e.node.id}|${e.adapter}/${e.port}`); }
function canvasPortState(e){
  const state = CanvasCore.facts.endState(canvasState.result?.der, {nodeId: e.node.id, adapter: e.adapter, port: e.port});
  const iface = canvasIfaceAt(e.node, e.adapter, e.port);
  return {down: !!state?.down || e.node.status !== "started", unknown: !!state?.unknown || !!canvasFacts(e.node)?.error || !iface || (!iface.admin && !iface.oper)};
}
function canvasLinkState(l){
  const derived = canvasState.result?.der.l1.links.get(l.id);
  const halted = l.suspend || l.ends.some(e => e.node.status !== "started");
  if (derived?.down || halted) return {down: true, unknown: false, why: derived?.why || ["Устройство остановлено или соединение приостановлено"]};
  const uncertain = !derived || l.ends.some(e => {
    const i = canvasIfaceAt(e.node, e.adapter, e.port);
    return canvasFacts(e.node)?.error || !i || (!i.admin && !i.oper);
  });
  return {down: false, unknown: !!(uncertain || derived?.unknown), why: uncertain ? ["Нет актуальных сведений об одном из портов"] : []};
}
function refreshPrototypeScene(){
  updateCanvasModel();
  svg.dataset.factlayer = insight.layer;
  const pinned = insight.pinned;
  renderDrawings(); renderLinks(); renderNodes(); renderCanvasL2(); renderCanvasL3();
  if (pinned?.subnet) insight.pinned = canvasState.subnets.find(s => s.key === pinned.key) || null;
  if (pinned?.domainObject) insight.pinned = domainObject(pinned.id);
  renderCanvasHud(); renderCanvasCard(); updateCanvasChrome(); renderOverview();
  applyExplorationVisibility(); updateCanvasScreenGeometry(); refreshCards(); scheduleLabels();
}
function applyExplorationVisibility(){
  nodes.forEach(n => {
    const state = nodeState(n), silhouette = state === "silhouette";
    [n.g, n.l2g].filter(Boolean).forEach(g => {
      g.classList.toggle("silhouette", silhouette);
      if (silhouette) {
        g.setAttribute("aria-label", "Неисследованное устройство");
        const name = g.querySelector(".nname"); if (name) name.textContent = "Неизвестный узел";
      }
      g.setAttribute("tabindex", (g === n.l2g ? insight.layer === "l2" : insight.layer !== "l2") && state !== "hidden" ? "0" : "-1");
    });
  });
  document.querySelectorAll(".l2-virtual,.l2-path").forEach(g => g.setAttribute("tabindex", insight.layer === "l2" ? "0" : "-1"));
  document.querySelectorAll(".subnet-object").forEach(g => g.setAttribute("tabindex", insight.layer === "l3" && g.style.display !== "none" ? "0" : "-1"));
  document.querySelectorAll("#lyrLinks > .link").forEach(g => g.setAttribute("tabindex", insight.layer === "l1" ? "0" : "-1"));
}
function updateCanvasScreenGeometry(){
  document.querySelectorAll(".subnet-pill").forEach(g => g.setAttribute("transform", `scale(${1 / view.k})`));
  const w = wrap.clientWidth, h = wrap.clientHeight;
  const busy = visibleNodes().map(n => { const p = toScreen(n.cx, n.cy), r = Math.max(14, 26 * view.k); return {x: p.x-r, y: p.y-r, w: 2*r, h: 2*r}; });
  busy.push({x: 0, y: 0, w: Math.min(396, w), h: 168}, {x: 0, y: h-112, w, h: 112});
  const intersects = (a, b) => a.x < b.x+b.w+5 && a.x+a.w+5 > b.x && a.y < b.y+b.h+5 && a.y+a.h+5 > b.y;
  canvasState.subnets.forEach(s => {
    if (!s.g) return;
    const origin = toScreen(s.x, s.y);
    const offsets = [[0, 0]];
    for (const r of [24, 48, 80, 120, 176, 240]) for (let a=0; a<360; a+=45) offsets.push([r*Math.cos(a*Math.PI/180), r*Math.sin(a*Math.PI/180)]);
    const offscreen = origin.x < -80 || origin.x > w+80 || origin.y < -20 || origin.y > h+20;
    const offset = offscreen ? [0, 0] : offsets.find(([dx, dy]) => {
      const b = {x: origin.x+dx-78, y: origin.y+dy-16, w: 156, h: 32};
      return b.x >= 8 && b.y >= 8 && b.x+b.w <= w-8 && b.y+b.h <= h-8 && !busy.some(o => intersects(b, o));
    });
    s.g.style.display = offset ? "" : "none";
    s.g.setAttribute("tabindex", offset && insight.layer === "l3" ? "0" : "-1");
    const [dx, dy] = offset || [0, 0];
    s.cx = s.x + dx/view.k; s.cy = s.y + dy/view.k;
    s.g.setAttribute("transform", `translate(${s.cx} ${s.cy})`);
    if (offset && !offscreen) busy.push({x: origin.x+dx-78, y: origin.y+dy-16, w: 156, h: 32});
    (s.edges || []).forEach(({line, label, node}) => {
      line.style.display = offset ? "" : "none"; label.parentNode.style.display = offset ? "" : "none";
      line.setAttribute("x2", s.cx); line.setAttribute("y2", s.cy);
      label.setAttribute("x", node.cx+(s.cx-node.cx)*.4); label.setAttribute("y", node.cy+(s.cy-node.cy)*.4-5/view.k);
    });
  });
}
function renderCanvasL3(){
  const layer = $("lyrL3"); layer.textContent = "";
  canvasState.subnets.forEach(s => {
    s.edges = [];
    s.members.forEach(m => {
      const n = byId.get(m.nodeId);
      const line = el("line", {x1: n.cx, y1: n.cy, x2: s.cx, y2: s.cy, class: "subnet-edge" + (m.down || m.dup ? " down" : "")}, layer);
      const g = el("g", {class: "auto-label", "data-priority": 2}, layer);
      const t = el("text", {x: n.cx + (s.cx - n.cx) * .4, y: n.cy + (s.cy - n.cy) * .4 - 5 / view.k, class: "pip"}, g);
      t.textContent = `${m.iface.name} · ${m.c.ip}`;
      s.edges.push({line, label: t, node: n});
    });
    const g = el("g", {class: "subnet-object" + (s.issues.length ? " warn" : ""), transform: `translate(${s.cx} ${s.cy})`, "data-subnet": s.key, tabindex: "0", role: "button", "aria-label": `Подсеть ${s.key}, ${s.members.length} интерфейсов`}, layer);
    const pill = el("g", {class: "subnet-pill", transform: `scale(${1 / view.k})`}, g);
    el("rect", {x: -78, y: -16, width: 156, height: 32, rx: 9}, pill);
    el("text", {y: 1}, pill).textContent = `${s.issues.length ? "! " : ""}${s.key}`;
    s.g = g;
  });
}
function layerIssues(layer = insight.layer){
  if (layer === "l4" || !canvasState.result) return [];
  const issues = [...(canvasState.result.der[layer]?.issues || [])];
  visibleNodes().filter(n => canvasFacts(n)?.error && nodeState(n) !== "silhouette").forEach(n => issues.unshift({sev: "warn", text: `${n.name}: сбор завершился ошибкой. Показаны последние известные сведения.`, go: {kind: "node", id: n.id}}));
  return issues;
}
function snapshotLabel(n){
  if (canvasState.source === "none" && !canvasState.seen.size) return "Факты ещё не получены";
  if (n && canvasFacts(n)?.error) return "Ошибка сбора · последние известные сведения";
  if (canvasState.source === "error") return "Ошибка сбора части сведений · последний снимок сохранён";
  if (canvasState.source === "stale") return "Снимок устарел · обновлён 10 мин назад";
  if (canvasState.effectiveMode === "seen") return "Только сведения, увиденные в консоли";
  if (canvasState.effectiveMode === "fog") return "Исследование · начните с доступного устройства";
  return `Учебный снимок · ${canvasState.seen.size ? "есть сведения из консоли" : "собран только что"}`;
}
function updateCanvasChrome(){
  svg.dataset.source = canvasState.source;
  $("factStatus").dataset.state = canvasState.source;
  $("factStatusText").textContent = (canvasState.teacher ? "Преподаватель · " : "") + snapshotLabel();
  $("refreshFacts").hidden = canvasState.effectiveMode !== "live";
  $("issueCount").textContent = String(layerIssues().length);
  $("overviewToggle").setAttribute("aria-label", `Обзор слоя ${insight.layer.toUpperCase()}, сообщений: ${layerIssues().length}`);
  const empty = $("emptySurface");
  let text = "";
  if (insight.layer === "l4") text = "L4 · место для следующего уровня. Данные и представление добавим позже.";
  else if (insight.layer === "l3" && !canvasState.subnets.length) text = canvasState.effectiveMode === "live" ? "Подсети пока не определены: нужны IP-адреса из фактов или вывода консоли." : "Адреса ещё не исследованы. Откройте устройство на L1 и выполните команду просмотра интерфейсов.";
  else if (insight.layer === "l2" && !l2Model?.virtuals.size && !l2Model?.segments.length) text = canvasState.effectiveMode === "live" ? "Пока нет данных о VLAN и L2-связях." : "Сведения о VLAN ещё не открыты. Исследуйте порты устройства на L1.";
  empty.textContent = text; empty.hidden = !text;
  applyExplorationVisibility();
}
function domainObject(id){
  const d = canvasState.result?.der.l2.domains.find(d => d.id === id);
  if (!d) return null;
  const points = d.members.map(m => byId.get(m.nodeId)).filter(Boolean);
  return {...d, domainObject: true, cx: points.reduce((v, n) => v + n.cx, 0) / Math.max(1, points.length), cy: points.reduce((v, n) => v + n.cy, 0) / Math.max(1, points.length)};
}
function overviewObjects(){
  if (insight.layer === "l1") return visibleLinks().map(l => ({kind: "link", id: l.id, label: l.name, note: canvasLinkState(l).unknown ? "Состояние неизвестно" : canvasLinkState(l).down ? "Не работает" : "Соединение работает"}));
  if (insight.layer === "l2") return (canvasState.result?.der.l2.domains || []).filter(d => d.shown).map(d => ({kind: "vlan", id: d.id, label: d.label, note: `${d.where} · ${d.members.length} интерфейсов`}));
  if (insight.layer === "l3") return canvasState.subnets.map(s => ({kind: "subnet", id: s.key, label: s.key, note: `${s.members.length} интерфейсов · ${s.issues.length ? "есть замечания" : "без замечаний"}`}));
  return [];
}
function renderOverview(){
  if ($("overviewPanel").hidden) return;
  const body = $("overviewBody"), issues = layerIssues(), objects = overviewObjects();
  $("overviewTitle").textContent = `Обзор · ${insight.layer.toUpperCase()}`;
  $("overviewLead").textContent = insight.layer === "l4" ? "Следующий уровень пока не определён" : layerHelp[insight.layer];
  body.innerHTML = `<div class="overview-summary"><span>${objects.length} объектов</span><span>${issues.length} сообщений</span></div>`
    + `<div class="panel-tabs"><button type="button" data-overview-tab="objects" aria-pressed="${canvasState.overviewTab === "objects"}">Объекты</button><button type="button" data-overview-tab="issues" aria-pressed="${canvasState.overviewTab === "issues"}">Проблемы · ${issues.length}</button></div>`
    + (canvasState.overviewTab === "objects" ? objects.map(o => `<button class="overview-row" type="button" data-go-kind="${o.kind}" data-go-id="${esc(o.id)}">${esc(o.label)}<small>${esc(o.note)}</small></button>`).join("") || `<p>Объекты этого слоя пока не открыты или для них нет данных.</p>` : issues.map(i => `<button class="issue-row ${esc(i.sev)}" type="button" data-go-kind="${esc(i.go?.kind || "")}" data-go-id="${esc(i.go?.id || "")}">${esc(i.text)}<small>${i.sev === "bad" ? "Проблема" : i.sev === "warn" ? "Предупреждение" : "Состояние"} · перейти к объекту</small></button>`).join("") || `<p>${canvasState.doc ? "В доступных сведениях этого слоя проблем не найдено." : "Нет данных для проверки. Это не означает, что проблем нет."}</p>`);
  body.querySelectorAll("[data-overview-tab]").forEach(b => b.onclick = () => { canvasState.overviewTab = b.dataset.overviewTab; renderOverview(); });
  bindNavigation(body);
  scheduleLabels();
}
function navigateObject(kind, id){
  let item = null;
  if (kind === "subnet") { setFactLayer("l3"); item = canvasState.subnets.find(s => s.key === id); }
  else if (kind === "vlan") { setFactLayer("l2"); item = domainObject(id); }
  else if (kind === "link") { if (!["l1", "l2"].includes(insight.layer)) setFactLayer("l1"); item = linkById.get(id); }
  else if (kind === "node") item = byId.get(id);
  if (!item) return;
  closeCanvasPanels(); pinInsight(item, "ports");
  const k = Math.max(view.k, 1.25), w = wrap.clientWidth, h = wrap.clientHeight;
  const x = mobile ? w / 2 : Math.max(100, (w - insight.cardShape.w - 48) / 2);
  const y = mobile ? Math.max(80, (h - insight.cardShape.h) / 2) : h / 2;
  animateTo({k, tx: x - k * item.cx, ty: y - k * item.cy});
}
function bindNavigation(root){
  root.querySelectorAll("[data-go-kind]").forEach(b => b.onclick = () => navigateObject(b.dataset.goKind, b.dataset.goId));
}
function factBannerHTML(n){
  const state = n ? nodeState(n) : "open";
  if (state === "silhouette") return '<p class="fact-banner">Устройство ещё не исследовано. Имя, тип и состояние скрыты.</p>';
  const warn = canvasState.source !== "fresh" || (n && canvasFacts(n)?.error);
  return `<p class="fact-banner${warn ? " warn" : ""}">${esc(snapshotLabel(n))}${n && canvasState.seen.has(n.id) ? " · увидено в консоли" : ""}</p>`;
}
function buildObjectCard(item){
  const layer = insight.cardLayer;
  if (item.subnet) return {kind: "L3 / IP-ПОДСЕТЬ", title: item.key, sub: `${item.members.length} интерфейсов`, heads: ["УЗЕЛ", "ИНТЕРФЕЙС", "IPV4"], rows: item.members.map(m => [byId.get(m.nodeId).name, m.iface.name, `${m.c.ip}/${m.c.pre}`]), foot: "Подсеть сверяется с L2-доменами её участников.", related: item.members.map(m => ({kind: "node", id: m.nodeId, label: byId.get(m.nodeId).name})), messages: item.issues};
  if (item.domainObject) return {kind: "L2 / ШИРОКОВЕЩАТЕЛЬНЫЙ ДОМЕН", title: item.label, sub: item.where, heads: ["УЗЕЛ", "ИНТЕРФЕЙС", "IPV4"], rows: item.members.map(m => [byId.get(m.nodeId).name, m.iface.name, (m.iface.ipv4 || []).join(", ") || "—"]), related: item.members.map(m => ({kind: "node", id: m.nodeId, label: byId.get(m.nodeId).name})), foot: "Домен рассчитан общими правилами рабочего интерфейса."};
  if (item.virtual && layer !== "l2") item = item.orig;
  if (item.virtualLink && layer !== "l2") item = item.link;
  if (item.virtual && insight.panel !== "mac") {
    const d = item.domain;
    return {kind: "L2 / VLAN КОММУТАТОРА", title: item.name, sub: d ? `${d.id} · ${d.members.length} участников` : "Нет активных участников", heads: ["УЗЕЛ", "ИНТЕРФЕЙС", "IPV4"], rows: (d?.members || []).map(m => [m.node.name, m.iface.name, (m.iface.ipv4 || []).join(", ") || "—"]), related: [{kind: "node", id: item.orig.id, label: "Физическое устройство"}], foot: d?.vlans.length > 1 ? "Разные VLAN объединены в одном L2-домене." : "Пунктирная рамка на канвасе объединяет VLAN одного корпуса."};
  }
  if (item.virtualLink) {
    const [a, b] = item.link.ends;
    return {kind: "L2 / ЛОГИЧЕСКОЕ СОЕДИНЕНИЕ", title: `${a.node.name} ↔ ${b.node.name}`, sub: item.active ? "Кадры проходят" : item.unknown ? "Нет фактов о втором конце" : "Кадры не проходят", heads: ["УЗЕЛ", "ПОРТ", "ТЕГ"], rows: [[a.node.name, item.aname, item.tag === "u" ? "без тега" : item.tag], [b.node.name, item.bname, item.tag === "u" ? "без тега" : item.tag]], foot: item.reason || "Логический путь рассчитан по тегам и настройкам портов.", related: [{kind: "link", id: item.link.id, label: "Физическое соединение"}]};
  }
  if (item.isLink) {
    const state = canvasLinkState(item), [a, b] = item.ends, ia = canvasIfaceAt(a.node, a.adapter, a.port), ib = canvasIfaceAt(b.node, b.adapter, b.port);
    const base = {kind: `${layer.toUpperCase()} / СОЕДИНЕНИЕ`, title: `${a.node.name} ↔ ${b.node.name}`, sub: state.unknown ? "Состояние неизвестно" : state.down ? "Соединение не работает" : "Соединение работает", related: item.ends.map(e => ({kind: "node", id: e.node.id, label: e.node.name})), foot: state.why.join("; ") || "Сведения получены из учебного снимка."};
    const port = e => canvasEndShown(e) ? e.label : "?";
    if (layer === "l1") return {...base, heads: ["УЗЕЛ", "ПОРТ", "СОСТОЯНИЕ"], rows: [[a.node.name, port(a), ia?.oper || "неизвестно"], [b.node.name, port(b), ib?.oper || "неизвестно"]], directions: [[`${a.node.name} ${port(a)}`, `${b.node.name} ${port(b)}`], [`${b.node.name} ${port(b)}`, `${a.node.name} ${port(a)}`]]};
    if (layer === "l2") return {...base, heads: ["УЗЕЛ", "ПОРТ", "НАСТРОЙКА"], rows: [[a.node.name, port(a), switchportText(ia)], [b.node.name, port(b), switchportText(ib)]]};
    return {...base, heads: ["УЗЕЛ", "ПОРТ", "IPV4"], rows: [[a.node.name, port(a), (ia?.ipv4 || []).join(", ") || "—"], [b.node.name, port(b), (ib?.ipv4 || []).join(", ") || "—"]]};
  }
  const n = item.virtual ? item.orig : item, f = canvasFacts(n), ints = f?.interfaces || [];
  const base = {kind: `${layer.toUpperCase()} / ${nodeState(n) === "silhouette" ? "ИССЛЕДОВАНИЕ" : CAT[n.cat].toUpperCase()}`, title: displayName(n), sub: nodeState(n) === "silhouette" ? "Сведения ещё не открыты" : `${STATUS[n.status]} · ${ints.length} известных интерфейсов`, device: n};
  if (nodeState(n) === "silhouette" || !canvasGroupOpen(n, layer)) return {...base, heads: [], rows: [], empty: "Эти сведения ещё не исследованы. Посмотрите их в консоли устройства.", inspect: true};
  if (layer === "l2" && insight.panel === "mac") {
    const table = CanvasCore.facts.macTable(canvasState.result?.model || canvasState.base, canvasState.result?.fx, n.id);
    return {...base, kind: "L2 / MAC-ТАБЛИЦА", heads: ["MAC", "ПОРТ", "VLAN"], rows: (table?.rows || []).map(r => [r.mac + (r.owner ? ` · ${r.owner.node}` : ""), r.iface, r.vlan]), empty: table ? "Коммутатор ещё не выучил MAC-адреса." : "MAC-таблица не получена. Это не означает, что она пуста.", foot: table?.truncated ? "Таблица неполная: коллектор прервал чтение." : "Учебный снимок MAC-таблицы. Запись позволяет перейти к связанному линку.", related: (table?.rows || []).filter(r => r.link && visibleLinks().some(l => l.id === r.link)).map(r => ({kind: "link", id: r.link, label: `${r.iface} · линк`}))};
  }
  if (!f || !ints.length) return {...base, heads: [], rows: [], empty: "Сведения об интерфейсах ещё не получены.", inspect: true};
  const result = {...base, foot: "Показаны только доступные сведения текущего слоя.", related: []};
  if (layer === "l1") {
    result.heads = ["ПОРТ", "АДМИН", "ЛИНК"];
    result.rows = ints.filter(i => i.port).map(i => {
      const l = visibleLinks().find(l => l.ends.some(e => e.node === n && e.adapter === i.port.adapter && e.port === i.port.port));
      if (l) result.related.push({kind: "link", id: l.id, label: i.name});
      const end = l?.ends.find(e => e.node === n && e.adapter === i.port.adapter && e.port === i.port.port);
      const state = end && canvasPortState(end);
      return [i.name, i.admin || "неизвестно", state?.unknown ? "неизвестно" : state?.down ? "down" : i.oper || "—"];
    });
  } else if (layer === "l2") {
    result.heads = ["ПОРТ", "НАСТРОЙКА", "STP"];
    result.rows = ints.filter(i => i.kind !== "loopback").map(i => [i.name, switchportText(i), i.stp?.state || "—"]);
    const der = canvasState.result?.der;
    ints.forEach(i => {
      const d = der?.l2.domFor(n.id, i);
      if (d) result.related.push({kind: "vlan", id: d.id, label: `${i.name} · ${d.label}`});
    });
  } else {
    result.heads = ["ИНТЕРФЕЙС", "IPV4", "СОСТОЯНИЕ"];
    result.rows = ints.filter(i => i.ipv4?.length || i.kind === "physical").map(i => [i.name, (i.ipv4 || []).join(", ") || "—", i.admin === "down" ? "shutdown" : i.oper || "неизвестно"]);
    canvasState.subnets.filter(s => s.members.some(m => m.nodeId === n.id)).forEach(s => result.related.push({kind: "subnet", id: s.key, label: s.key}));
    const gateways = [...new Set(ints.map(i => i.gateway).filter(Boolean))];
    if (gateways.length) result.sections = [{title: "Шлюзы", heads: ["АДРЕС"], rows: gateways.map(g => [g])}];
  }
  result.empty = "Для этого слоя в снимке нет сведений.";
  result.inspect = true;
  return result;
}
function switchportText(i){
  if (!i) return "нет данных";
  const sp = i.switchport;
  if (sp?.mode === "access") return `access · VLAN ${sp.vlan}`;
  if (sp?.mode === "trunk") return `trunk · ${Array.isArray(sp.allowed) ? sp.allowed.join(", ") : "все VLAN"} · native ${sp.native ?? "—"}`;
  if (sp?.mode === "none") return "L2 отключён";
  if (i.dot1q != null) return `802.1Q · VLAN ${i.dot1q}`;
  if (i.kind === "svi") return `SVI · VLAN ${i.vlan}`;
  return "нет L2-настроек";
}
function inspectCommands(n){
  const kind = n.type === "vpcs" ? "vpcs" : /router|switch/.test(n.symbol) || n.type === "iou" ? "ios" : "linux";
  return kind === "ios" ? ["show ip interface brief", "show interfaces switchport"] : kind === "vpcs" ? ["show ip"] : ["ip -br address", "bridge vlan show"];
}
function renderObjectExtras(item, data){
  let html = (data.sections || []).map(s => `<h3>${esc(s.title)}</h3>${cardTable(s.heads, s.rows)}`).join("");
  const messages = data.messages || [];
  if (messages.length) html += `<h3>Замечания</h3>${messages.map(m => `<p class="fact-banner warn">${esc(m)}</p>`).join("")}`;
  if (data.related?.length) html += `<h3>Связанные объекты</h3><div class="object-links">${data.related.map(r => `<button type="button" data-go-kind="${r.kind}" data-go-id="${esc(r.id)}">${esc(r.label)}</button>`).join("")}</div>`;
  const n = data.device;
  if (n?.cat === "hypervisor" && nodeState(n) === "open") {
    const guests = LIVE.guests?.[n.id];
    if (guests) html += `<h3>Гости Proxmox</h3>${cardTable(["ID", "ИМЯ", "ТИП", "СОСТОЯНИЕ"], guests.items.map(g => [g.vmid, g.name, g.type, g.status]))}`;
  }
  if (n && !canvasState.teacher && n.console === "telnet" && n.status === "started") {
    html += `<h3>Консоль устройства</h3><div class="object-links"><button type="button" data-console>Открыть консоль</button>${inspectCommands(n).map(cmd => `<button type="button" data-inspect="${esc(cmd)}">${esc(cmd)}</button>`).join("")}</div><p>Команды в прототипе выполняются симулятором. Их вывод открывает факты по правилам плагина.</p>`;
  } else if (n?.console === "vnc") html += '<p class="fact-banner">Экран устройства · место графической консоли. Подключение noVNC будет использовать существующий модуль плагина.</p>';
  if (n && canvasState.output.has(n.id)) html += `<details><summary>Последний вывод команды</summary><pre class="inspect-output">${esc(canvasState.output.get(n.id))}</pre></details>`;
  return html;
}
function bindObjectExtras(root, item){
  bindNavigation(root);
  const n = item.virtual ? item.orig : item;
  root.querySelectorAll("[data-inspect]").forEach(b => b.onclick = () => runInspection(n, b.dataset.inspect));
  const consoleButton = root.querySelector("[data-console]"); if (consoleButton) consoleButton.onclick = () => openConsole(n);
}
function revealConsoleNode(n){
  if (canvasState.teacher || canvasState.effectiveMode === "live") return;
  canvasState.mask = CanvasCore.reveal.maskAdd(canvasState.mask, [{nodeid: n.id, kind: "node", item: ""}]).mask;
  store.set(explorationKey, CanvasCore.reveal.maskItems(canvasState.mask));
  refreshPrototypeScene();
}
function recordConsoleResult(n, kind, command, output){
  if (canvasState.teacher || !command) return;
  const result = CanvasCore.consolefacts.interpret(kind, command, String(output).split(/\r?\n/));
  if (result && !result.save) canvasState.seen = CanvasCore.consolefacts.addSeen(canvasState.seen, n.id, result, {at: Date.now(), fp: null});
  const items = CanvasCore.reveal.revealByCommand({mode: canvasState.effectiveMode, model: canvasState.base, factsDoc: canvasState.sourceDoc, nodeId: n.id, result});
  canvasState.mask = CanvasCore.reveal.maskAdd(canvasState.mask, items).mask;
  store.set(explorationKey, CanvasCore.reveal.maskItems(canvasState.mask));
  if (output) canvasState.output.set(n.id, `${command}\n${output}`);
  if (result || items.length) refreshPrototypeScene();
}
function runInspection(n, command){
  if (canvasState.teacher || n.status !== "started" || nodeState(n) === "hidden") return;
  const sim = new Sim(n, () => {}); sim.on = true; sim.mode = "priv";
  const output = sim.exec(command);
  sim.clearTimers();
  if (typeof output !== "string" || !output || output.startsWith("%")) { toast("Для этой команды в симуляторе пока нет вывода"); return; }
  recordConsoleResult(n, sim.kind, command, output);
  pinInsight(n, "ports");
  toast("Вывод команды добавлен к доступным сведениям");
}
function setCanvasScenario(value){
  closeInsight();
  if (canvasState.scenarioBackup) {
    canvasState.scenarioBackup.forEach(([i, ipv4]) => i.ipv4 = ipv4); canvasState.scenarioBackup = null;
  }
  if (value === "faults" && !faultDemo || value === "base" && faultDemo) toggleFaultDemo();
  if (value === "faults") {
    const pc = nodes.find(n => n.name === "PC2").facts.interfaces[0];
    const router = nodes.find(n => n.name === "R3").facts.interfaces.find(i => i.name === "Gi0/1");
    canvasState.scenarioBackup = [[pc, pc.ipv4], [router, router.ipv4]];
    pc.ipv4 = ["10.1.10.11/24"]; router.ipv4 = ["10.0.23.2/24"];
  }
  canvasState.scenario = value; canvasState.seen = new Map(); refreshPrototypeScene();
}
window.CanvasPrototype = {state: canvasState, refresh: refreshPrototypeScene, inspect: runInspection, navigate: navigateObject, layer: setFactLayer, scenario: setCanvasScenario};
