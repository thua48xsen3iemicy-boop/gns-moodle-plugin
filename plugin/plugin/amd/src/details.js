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
 * The Canvas prototype's SVG anchored object cards and separate layer overview.
 *
 * @module mod_gns3lab/details
 * @copyright 2026 mod_gns3lab contributors
 * @license https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
/* eslint-disable curly, max-len, jsdoc/require-jsdoc, no-eq-null */
import {svgEl as el} from 'mod_gns3lab/dom';
import {clamp, esc} from 'mod_gns3lab/model';
import {objectData, factsStatus, groupOpen, layerHelp, layerIssues} from 'mod_gns3lab/canvasfacts';

export class Details {
    constructor(element, {stage, strings, host}) {
        this.el = element; this.stage = stage; this.strings = strings; this.host = host;
        this.sel = null; this.layer = 'l1'; this.panel = 'ports'; this.shape = null; this.overviewTab = 'objects';
        this.overview = stage.r('overviewPanel');
        stage.r('overviewClose').onclick = () => { this.hideOverview(); stage.r('overview').focus(); };
        this.overview.addEventListener('keydown', e => {
            if (e.key === 'Escape') { e.stopPropagation(); this.hideOverview(); stage.r('overview').focus(); }
        });
        this.hide();
    }
    get visible() { return !!this.shape || !this.overview.hidden; }
    t(key) { return this.stage.s('ui_canvas_' + key); }
    show(sel) {
        this.hideOverview(); this.sel = sel; this.panel = 'ports';
        this.layer = sel?.kind === 'subnet' ? 'l3' : ['vlan', 'switchvlan', 'segment'].includes(sel?.kind) ? 'l2'
            : this.stage.layer === 'l4' ? 'l1' : this.stage.layer;
        this.render();
    }
    hideOverview() {
        this.overview.hidden = true;
        this.stage.r('overview').setAttribute('aria-expanded', 'false');
        this.stage.canvas.scheduleLabels();
    }
    hideCard() {
        this.el.textContent = ''; this.shape = null; this.sel = null;
        this.stage.canvas.scheduleLabels();
    }
    hide() { this.hideCard(); this.hideOverview(); }
    showSummary() {
        this.hideCard(); this.stage.select(null); this.overview.hidden = false;
        this.stage.r('overview').setAttribute('aria-expanded', 'true');
        this.renderOverview(); this.stage.r('overviewClose').focus();
    }
    toggleSummary() { if (this.overview.hidden) this.showSummary(); else this.hideOverview(); }
    refresh() { if (this.shape) this.render(); if (!this.overview.hidden) this.renderOverview(); }
    table(heads, rows) {
        return `<table><thead><tr>${heads.map(h => `<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row =>
            `<tr${row.go ? ` data-go-kind="${esc(row.go.kind)}" data-go-id="${esc(row.go.id)}" tabindex="0"` : ''}>${row.map(value => {
                const cell = value && typeof value === 'object' ? value : {text: value};
                return `<td${cell.text === 'down' ? ' class="down"' : ''}>${esc(cell.text ?? '—')}${cell.seen ? ` <i class="seenmark" title="${esc(this.strings.ui_seen_mark)}">●</i>` : ''}</td>`;
            }).join('')}</tr>`).join('')}</tbody></table>`;
    }
    banner(n) {
        const s = this.stage, f = n && s.fx?.nodes.get(n.id), status = factsStatus(s);
        let text = status.text;
        if (n?.state === 'silhouette') text = this.strings.ui_fog_unknown_note;
        else if (n?.state === 'known') text = this.strings.ui_fog_found_note;
        else if (f?.err) text = this.strings['ui_facts_err_' + f.err.code] || this.strings.ui_facts_err_other;
        const seen = f?.seen;
        const time = seen?.at && new Date(seen.at).toLocaleTimeString(document.documentElement.lang, {hour: '2-digit', minute: '2-digit'});
        return `<div class="fact-banner${status.state === 'stale' || status.state === 'error' || f?.err ? ' warn' : ''}"><p>${esc(text)}</p>`
            + (time ? `<p class="seenline">${esc(this.strings.ui_seen_at.replace('{$a}', time))} (${esc(seen.command)})</p><p>${esc(this.strings.ui_seen_note)}</p>` : '') + '</div>';
    }
    render() {
        if (!this.sel || !this.stage.model) { this.hideCard(); return; }
        const data = objectData(this.stage, this.sel, this.layer, this.panel);
        if (!data) { this.hideCard(); return; }
        const focusInside = this.el.contains(document.activeElement);
        this.el.textContent = '';
        const lead = el('line', {class: 'card-lead'}, this.el), anchor = el('circle', {r: 5, class: 'card-anchor'}, this.el);
        const card = el('g', {class: 'info-card'}, this.el), foreign = el('foreignObject', {}, card);
        const html = document.createElementNS('http://www.w3.org/1999/xhtml', 'section');
        html.setAttribute('class', 'object-card'); html.setAttribute('aria-label', data.title);
        foreign.appendChild(html);
        const layers = this.sel.kind === 'subnet' ? ['l3'] : this.sel.kind === 'vlan' ? ['l2'] : ['l1', 'l2', 'l3'];
        const mac = this.layer === 'l2' && data.device?.cat === 'switch' && groupOpen(this.stage, data.device, 'l2');
        const inspections = data.device && this.host.inspections ? this.host.inspections(data.device) : [];
        const panelId = this.stage.prefix + '-object-facts';
        html.innerHTML = `<header><div><span class="kicker">${esc(data.kind.toUpperCase())}</span><h2>${esc(data.title)}</h2><p>${esc(data.sub || '')}</p></div><button class="ib" type="button" data-act="close" aria-label="${esc(this.strings.ui_close)}">×</button></header>`
            + `<div class="object-tabs" role="tablist" aria-label="${esc(this.strings.ui_canvas_card_layers)}">${layers.map(layer => `<button type="button" role="tab" id="${panelId}-${layer}" data-card-layer="${layer}" aria-controls="${panelId}" aria-selected="${layer === this.layer}" tabindex="${layer === this.layer ? 0 : -1}">${layer.toUpperCase()}</button>`).join('')}</div>`
            + `<div class="object-content" id="${panelId}" role="tabpanel" aria-labelledby="${panelId}-${this.layer}" tabindex="0">${this.banner(data.device)}`
            + (mac ? `<button class="btn mac-toggle" type="button" aria-pressed="${this.panel === 'mac'}">${esc(this.t(this.panel === 'mac' ? 'mac_back' : 'mac_open'))}</button>` : '')
            + (data.rows?.length ? this.table(data.heads, data.rows) : `<p class="card-empty">${esc(data.empty || this.t('no_layer_data'))}</p>`)
            + (data.directions ? `<h3>${esc(this.t('directions'))}</h3>${this.table(['TX →', '→ RX'], data.directions)}<p class="card-foot">${esc(this.strings.ui_canvas_duplex)}</p>` : '')
            + (data.sections || []).map(x => `<h3>${esc(x.title)}</h3>${this.table(x.heads, x.rows)}`).join('')
            + (data.messages?.length ? `<h3>${esc(this.t('issues'))}</h3>${data.messages.map(message => `<p class="fact-banner warn">${esc(message.text || message)}</p>`).join('')}` : '')
            + (data.related?.length ? `<h3>${esc(this.t('related'))}</h3><div class="object-links">${data.related.map(r => `<button type="button" data-go-kind="${esc(r.kind)}" data-go-id="${esc(r.id)}">${esc(r.label)}</button>`).join('')}</div>` : '')
            + (data.device?.consoleType && data.device.consoleType !== 'none' ? `<h3>${esc(this.strings.ui_console)}</h3><div class="object-links"><button type="button" data-act="console">${esc(this.strings.ui_open_console)}</button>`
                + inspections.map(item => `<button type="button" data-inspect="${esc(item.id)}"${this.host.canInspect(data.device) ? '' : ' disabled'} title="${esc(this.t('inspect_note'))}">${esc(item.cmd)}</button>`).join('') + '</div>'
                + (inspections.length ? `<p>${esc(this.t('inspect_note'))}</p>` : '') : '')
            + `<p class="card-foot">${esc(data.foot || '')}</p></div>`;
        html.querySelector('[data-act="close"]').onclick = () => { this.hideCard(); this.stage.svg.focus(); };
        html.querySelector('[data-act="console"]')?.addEventListener('click', () => this.host.openConsole(data.device));
        html.querySelectorAll('[data-inspect]').forEach(button => button.onclick = () => this.host.inspect(data.device, button.dataset.inspect));
        const changeTab = layer => { this.layer = layer; this.panel = 'ports'; this.render(); this.el.querySelector(`[data-card-layer="${layer}"]`).focus(); };
        html.querySelectorAll('[data-card-layer]').forEach((tab, index) => {
            tab.onclick = () => changeTab(tab.dataset.cardLayer);
            tab.onkeydown = e => {
                const next = e.key === 'ArrowRight' ? (index + 1) % layers.length : e.key === 'ArrowLeft' ? (index + layers.length - 1) % layers.length
                    : e.key === 'Home' ? 0 : e.key === 'End' ? layers.length - 1 : -1;
                if (next >= 0) { e.preventDefault(); e.stopPropagation(); changeTab(layers[next]); }
            };
        });
        html.querySelector('.mac-toggle')?.addEventListener('click', () => { this.panel = this.panel === 'mac' ? 'ports' : 'mac'; this.render(); this.el.querySelector('.mac-toggle').focus(); });
        this.bindNavigation(html);
        html.addEventListener('keydown', e => { e.stopPropagation(); if (e.key === 'Escape') { this.hide(); this.stage.select(null); this.stage.svg.focus(); } });
        this.shape = {data, foreign, card, lead, anchor};
        this.position();
        if (focusInside) this.el.querySelector(`[data-card-layer="${this.layer}"]`).focus();
        this.stage.canvas.scheduleLabels();
    }
    position() {
        const c = this.shape, s = this.stage;
        if (!c) return;
        const ww = s.wrap.clientWidth, wh = s.wrap.clientHeight, compact = ww < 760;
        const w = Math.min(560, Math.max(1, ww - 24));
        const h = Math.max(1, compact ? Math.min(520, wh * .6, wh - 128) : Math.min(520, wh - 24));
        const p = s.toScreen(c.data.focus?.cx || 0, c.data.focus?.cy || 0);
        const rawX = p.x + w + 76 < ww ? p.x + 74 : p.x - w - 74 > 4 ? p.x - w - 74 : ww - w - 12;
        let x = clamp(rawX, 12, Math.max(12, ww - w - 12));
        const minY = compact ? 12 : x < 396 ? 174 : 66, bottom = compact ? 112 : 12;
        const y = clamp(compact ? wh - h - bottom : p.y - 80, Math.min(minY, wh - h - bottom), Math.max(12, wh - h - bottom));
        if (!compact && s.sel) {
            const root = s.wrap.getBoundingClientRect();
            const boxes = [...s.vp.querySelectorAll('.node.on .ico,.l2-virtual.on > rect,.subnet-object.on rect')].map(e => {
                const r = e.getBoundingClientRect(); return {x: r.left - root.left - 12, y: r.top - root.top - 8, w: r.width + 24, h: r.height + 36};
            });
            const overlap = candidate => boxes.reduce((score, b) => {
                const width = Math.max(0, Math.min(candidate + w, b.x + b.w) - Math.max(candidate, b.x));
                const height = Math.max(0, Math.min(y + h, b.y + b.h) - Math.max(y, b.y));
                return score + (width && height ? 1000000 + width * height : 0);
            }, candidate < 396 && y < 174 ? 25000000 : 0);
            x = [x, Math.max(12, ww - w - 12), 12].reduce((best, candidate) => overlap(candidate) < overlap(best) ? candidate : best);
        }
        c.foreign.setAttribute('width', w); c.foreign.setAttribute('height', h);
        c.card.setAttribute('transform', `translate(${x.toFixed(1)} ${y.toFixed(1)})`);
        Object.entries({x1: p.x, y1: p.y, x2: clamp(p.x, x, x + w), y2: clamp(p.y, y, y + h)}).forEach(([k, v]) => c.lead.setAttribute(k, v));
        c.anchor.setAttribute('cx', p.x); c.anchor.setAttribute('cy', p.y);
        s.canvas.scheduleLabels();
    }
    goto(selection) {
        this.stage.select(selection); this.show(selection);
        const focus = this.shape?.data.focus;
        if (focus) {
            const s = this.stage, k = Math.max(s.view.k, 1.25), w = s.wrap.clientWidth, h = s.wrap.clientHeight;
            s.animateTo({k, tx: (s.mobile ? w / 2 : Math.max(100, (w - Math.min(560, w - 24) - 48) / 2)) - k * focus.cx,
                ty: (s.mobile ? Math.max(80, (h - Math.min(520, h * .6)) / 2) : h / 2) - k * focus.cy}, 320);
        }
    }
    bindNavigation(root) {
        root.querySelectorAll('[data-go-kind]').forEach(b => {
            const run = () => this.goto({kind: b.dataset.goKind, id: b.dataset.goId});
            b.onclick = run;
            if (b.tagName === 'TR') b.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); run(); } };
        });
    }
    renderOverview() {
        const s = this.stage, issues = layerIssues(s), objects = [];
        if (s.layer === 'l1') s.model?.links.forEach(l => {
            const state = s.der?.l1.links.get(l.id);
            objects.push({kind: 'link', id: l.id, label: `${s.model.nodes.get(l.a.nodeId).name} ↔ ${s.model.nodes.get(l.b.nodeId).name}`,
                note: !state || state.unknown ? this.t('unknown') : state.down ? this.t('blocked') : this.t('passing')});
        });
        if (s.layer === 'l2') s.der?.l2.domains.filter(d => d.shown).forEach(d => objects.push({kind: 'vlan', id: d.id, label: d.label, note: `${d.where} · ${d.members.length} ${this.t('interfaces')}`}));
        if (s.layer === 'l3') s.canvas.subnets.forEach(x => objects.push({kind: 'subnet', id: x.key, label: x.key, note: `${x.members.length} ${this.t('interfaces')} · ${x.issues.length} ${this.t('issues')}`}));
        s.r('overviewTitle').textContent = this.strings.ui_overview + ' · ' + s.layer.toUpperCase();
        s.r('overviewLead').textContent = layerHelp(s, s.layer);
        const body = s.r('overviewBody');
        const unknown = [...(s.model?.nodes.values() || [])].filter(n => n.state === 'silhouette').length;
        body.innerHTML = `<div class="overview-summary"><span>${objects.length} ${this.t('objects')}</span><span>${issues.length} ${this.t('issues')}</span></div>`
            + (unknown ? `<p>${esc(this.strings.ui_fog_unknown_count)} ${unknown}</p>` : '')
            + `<div class="panel-tabs"><button type="button" data-overview-tab="objects" aria-pressed="${this.overviewTab === 'objects'}">${esc(this.t('objects'))}</button><button type="button" data-overview-tab="issues" aria-pressed="${this.overviewTab === 'issues'}">${esc(this.t('issues'))} · ${issues.length}</button></div>`
            + (this.overviewTab === 'objects' ? objects.map(o => `<button class="overview-row" type="button" data-go-kind="${o.kind}" data-go-id="${esc(o.id)}">${esc(o.label)}<small>${esc(o.note)}</small></button>`).join('') || `<p>${esc(this.t('no_objects'))}</p>`
                : issues.map(i => `<button class="issue-row ${esc(i.sev)}" type="button" data-go-kind="${esc(i.go?.kind || '')}" data-go-id="${esc(i.go?.id || '')}">${esc(i.text)}</button>`).join('') || `<p>${esc(this.t(s.der ? 'no_issues' : 'no_check'))}</p>`);
        body.querySelectorAll('[data-overview-tab]').forEach(b => b.onclick = () => { this.overviewTab = b.dataset.overviewTab; this.renderOverview(); });
        this.bindNavigation(body); s.canvas.scheduleLabels();
    }
}
