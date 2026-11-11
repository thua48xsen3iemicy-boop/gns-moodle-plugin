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
 * The object card in a drawer on the right of the scheme (a sheet at the bottom on a narrow scheme) and the layer overview.
 *
 * @module mod_gns3lab/details
 * @copyright 2026 mod_gns3lab contributors
 * @license https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
/* eslint-disable curly, max-len, jsdoc/require-jsdoc, no-eq-null */
import {esc} from 'mod_gns3lab/model';
import {objectData, factsStatus, groupOpen, layerHelp, layerIssues} from 'mod_gns3lab/canvasfacts';

/** A selector that finds the same control in a redrawn card, or null. */
const focusSelector = e => {
    for (const name of ['data-act', 'data-card-layer', 'data-inspect', 'data-go-id']) {
        if (e.hasAttribute(name)) return `[${name}="${CSS.escape(e.getAttribute(name))}"]`;
    }
    return e.classList.contains('mac-toggle') ? '.mac-toggle' : e.classList.contains('object-content') ? '.object-content' : null;
};

export class Details {
    constructor(element, {stage, strings, host}) {
        this.el = element; this.stage = stage; this.strings = strings; this.host = host;
        this.clearTimer = null;
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
        this.keepVisible();
    }
    hideOverview() {
        this.overview.hidden = true;
        this.stage.r('overview').setAttribute('aria-expanded', 'false');
        this.stage.canvas.scheduleLabels();
    }
    hideCard() {
        this.shape = null; this.sel = null;
        this.el.classList.remove('open'); this.el.setAttribute('inert', '');
        // The content stays while the drawer slides out.
        clearTimeout(this.clearTimer);
        this.clearTimer = setTimeout(() => { if (!this.shape) this.el.textContent = ''; }, 300);
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
        this.stage.r('chip').hidden = true;
        const layers = this.sel.kind === 'subnet' ? ['l3'] : this.sel.kind === 'vlan' ? ['l2'] : ['l1', 'l2', 'l3'];
        const mac = this.layer === 'l2' && data.device?.cat === 'switch' && groupOpen(this.stage, data.device, 'l2');
        const inspections = data.device && this.host.inspections ? this.host.inspections(data.device) : [];
        const canInspect = !!data.device && inspections.length > 0 && this.host.canInspect(data.device);
        const panelId = this.stage.prefix + '-object-facts';
        const markup = `<header><div><span class="kicker">${esc(data.kind.toUpperCase())}</span><h2>${esc(data.title)}</h2><p>${esc(data.sub || '')}</p></div><button class="ib" type="button" data-act="close" aria-label="${esc(this.strings.ui_close)}">×</button></header>`
            + `<div class="object-tabs" role="tablist" aria-label="${esc(this.strings.ui_canvas_card_layers)}">${layers.map(layer => `<button type="button" role="tab" id="${panelId}-${layer}" data-card-layer="${layer}" aria-controls="${panelId}" aria-selected="${layer === this.layer}" tabindex="${layer === this.layer ? 0 : -1}">${layer.toUpperCase()}</button>`).join('')}</div>`
            + `<div class="object-content" id="${panelId}" role="tabpanel" aria-labelledby="${panelId}-${this.layer}" tabindex="0">${this.banner(data.device)}`
            + (mac ? `<button class="btn mac-toggle" type="button" aria-pressed="${this.panel === 'mac'}">${esc(this.t(this.panel === 'mac' ? 'mac_back' : 'mac_open'))}</button>` : '')
            + (data.rows?.length ? this.table(data.heads, data.rows) : `<p class="card-empty">${esc(data.empty || this.t('no_layer_data'))}</p>`)
            + (data.directions ? `<h3>${esc(this.t('directions'))}</h3>${this.table(['TX →', '→ RX'], data.directions)}<p class="card-foot">${esc(this.strings.ui_canvas_duplex)}</p>` : '')
            + (data.sections || []).map(x => `<h3>${esc(x.title)}</h3>${this.table(x.heads, x.rows)}`).join('')
            + (data.messages?.length ? `<h3>${esc(this.t('issues'))}</h3>${data.messages.map(message => `<p class="fact-banner warn">${esc(message.text || message)}</p>`).join('')}` : '')
            + (data.related?.length ? `<h3>${esc(this.t('related'))}</h3><div class="object-links">${data.related.map(r => `<button type="button" data-go-kind="${esc(r.kind)}" data-go-id="${esc(r.id)}">${esc(r.label)}</button>`).join('')}</div>` : '')
            + (data.device?.consoleType && data.device.consoleType !== 'none' ? `<h3>${esc(this.strings.ui_console)}</h3><div class="object-links"><button type="button" data-act="console">${esc(this.strings.ui_open_console)}</button>`
                + inspections.map(item => `<button type="button" data-inspect="${esc(item.id)}"${canInspect ? '' : ' disabled'} title="${esc(this.t('inspect_note'))}">${esc(item.cmd)}</button>`).join('') + '</div>'
                + (inspections.length ? `<p>${esc(this.t('inspect_note'))}</p>` : '') : '')
            + `<p class="card-foot">${esc(data.foot || '')}</p></div>`;
        const key = `${this.sel.kind}:${this.sel.id}:${this.layer}:${this.panel}`, same = this.shape?.key === key;
        // A poll of the facts redraws the same card: nothing changed, nothing to do; otherwise keep the scroll and the focus.
        if (same && this.shape.markup === markup) { this.shape.data = data; this.open(); return; }
        const oldContent = this.el.querySelector('.object-content'), scroll = same && oldContent ? oldContent.scrollTop : 0;
        const focusInside = this.el.contains(document.activeElement), focusKey = same && focusInside ? focusSelector(document.activeElement) : null;
        clearTimeout(this.clearTimer);
        this.el.textContent = '';
        const html = document.createElement('section');
        html.setAttribute('class', 'object-card'); html.setAttribute('aria-label', data.title);
        html.innerHTML = markup;
        this.el.appendChild(html);
        html.querySelector('[data-act="close"]').onclick = () => { this.hideCard(); this.stage.svg.focus(); };
        html.querySelector('[data-act="console"]')?.addEventListener('click', () => this.host.openConsole(data.device));
        html.querySelectorAll('[data-inspect]').forEach(button => {
            button.onclick = () => this.host.inspect(data.device, button.dataset.inspect);
        });
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
        this.shape = {data, key, markup};
        html.querySelector('.object-content').scrollTop = scroll;
        this.open();
        if (focusInside) (focusKey && html.querySelector(focusKey) || html.querySelector(`[data-card-layer="${this.layer}"]`)).focus();
        this.stage.canvas.scheduleLabels();
    }
    open() {
        this.position();
        this.el.removeAttribute('inert');
        this.el.classList.add('open');
    }
    /** The part of the scheme the drawer leaves free, in screen pixels of the wrap (the slide is not counted). */
    freeArea() {
        const s = this.stage, w = s.wrap.clientWidth, h = s.wrap.clientHeight;
        if (!this.el.classList.contains('open')) return {w, h};
        return this.el.classList.contains('sheet') ? {w, h: Math.max(1, this.el.offsetTop)} : {w: Math.max(1, this.el.offsetLeft), h};
    }
    /** Moves the scheme so that the object of the card is not under the drawer. */
    keepVisible() {
        const s = this.stage, f = this.shape?.data.focus;
        if (!f) return;
        const area = this.freeArea(), p = s.toScreen(f.cx, f.cy), pad = 48;
        const dx = p.x > area.w - pad ? area.w / 2 - p.x : 0, dy = p.y > area.h - pad ? area.h / 2 - p.y : 0;
        if (dx || dy) s.animateTo({k: s.view.k, tx: s.view.tx + dx, ty: s.view.ty + dy}, 260);
    }
    position() {
        // A narrow scheme (a phone, a narrow Moodle column) gets a sheet at the bottom instead of the drawer on the right.
        this.el.classList.toggle('sheet', this.stage.wrap.clientWidth < 760);
    }
    goto(selection) {
        this.stage.select(selection); this.show(selection);
        const focus = this.shape?.data.focus;
        if (focus) {
            const s = this.stage, k = Math.max(s.view.k, 1.25), area = this.freeArea();
            s.animateTo({k, tx: area.w / 2 - k * focus.cx, ty: area.h / 2 - k * focus.cy}, 320);
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
        body.querySelectorAll('[data-overview-tab]').forEach(b => {
            b.onclick = () => {
                this.overviewTab = b.dataset.overviewTab;
                this.renderOverview();
            };
        });
        this.bindNavigation(body); s.canvas.scheduleLabels();
    }
}
