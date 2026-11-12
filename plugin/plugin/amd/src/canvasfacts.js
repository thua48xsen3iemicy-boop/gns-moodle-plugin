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
 * The prototype's structured object cards backed by the student's filtered, authoritative facts.
 *
 * @module mod_gns3lab/canvasfacts
 * @copyright 2026 mod_gns3lab contributors
 * @license https://www.gnu.org/copyleft/gpl.html GNU GPL v3 or later
 */
/* eslint-disable curly, max-len, jsdoc/require-jsdoc, no-eq-null */
import {endIf, endState, macTable, stpText} from 'mod_gns3lab/facts';
import {fmtFilters} from 'mod_gns3lab/model';
import {linkDirections} from 'mod_gns3lab/canvasmodel';

export const groupOpen = (s, n, layer) => !s.reveal || !!s.reveal.groups.get(n.id)?.has(layer);
export const layerHelp = (s, layer) => s.s(layer === 'l4' ? 'ui_canvas_l4' : `ui_canvas_${layer}_help`);
export const layerIssues = s => {
    const issues = [...(s.der?.[s.layer]?.issues || [])];
    s.fx?.nodes.forEach(f => {
        if (f.err) issues.unshift({sev: 'warn', text: (s.model.nodes.get(f.id)?.name || '?') + ': ' + s.s('ui_canvas_collection_error'), go: {kind: 'node', id: f.id}});
    });
    return issues;
};
export const factsStatus = s => {
    const failed = [...(s.fx?.nodes.values() || [])].some(f => f.err);
    const state = !s.der ? 'none' : failed ? 'error' : s.factsMeta.stale ? 'stale' : 'fresh';
    const at = s.factsMeta.at || s.fx?.at;
    const text = state === 'none' ? s.s('ui_facts_nodata') : state === 'error' ? s.s('ui_canvas_collection_error')
        : state === 'stale' ? s.s('ui_facts_stale_mark') : at
            ? s.s('ui_facts_at').replace('{$a}', new Date(at).toLocaleTimeString(document.documentElement.lang, {hour: '2-digit', minute: '2-digit'}))
            : s.s('ui_facts_ago_now');
    return {state, text};
};

export const objectData = (s, selected, layer, panel = 'ports') => {
    const t = key => s.s('ui_canvas_' + key), name = id => s.model.nodes.get(id)?.name || '?';
    const memberRows = members => members.map(m => [name(m.nodeId), m.iface.name, (m.iface.ipv4 || []).join(', ') || '—']);
    const relatedMembers = members => [...new Set(members.map(m => m.nodeId))].map(id => ({kind: 'node', id, label: name(id)}));
    if (selected.kind === 'subnet') {
        const x = s.canvas.subnets.find(x => x.key === selected.id);
        return x && {kind: 'L3 / ' + t('subnet'), title: x.key, sub: x.members.length + ' · ' + t('interfaces'),
            heads: [t('node'), t('interface'), 'IPV4'], rows: x.members.map(m => [name(m.nodeId), m.iface.name, `${m.c.ip}/${m.c.pre}`]),
            related: relatedMembers(x.members), messages: x.issues, foot: t('subnet_foot'), focus: x};
    }
    if (selected.kind === 'vlan') {
        const d = s.der?.l2.domains.find(d => d.id === selected.id);
        if (!d) return null;
        const points = [...d.nodes].map(id => s.model.nodes.get(id)).filter(Boolean);
        return {kind: 'L2 / ' + t('domain'), title: d.label, sub: d.where,
            heads: [t('node'), t('interface'), 'IPV4'], rows: memberRows(d.members), related: relatedMembers(d.members), foot: t('subnet_foot'),
            focus: {cx: points.reduce((sum, n) => sum + n.cx, 0) / Math.max(1, points.length), cy: points.reduce((sum, n) => sum + n.cy, 0) / Math.max(1, points.length)}};
    }
    const segment = selected.kind === 'segment' && s.canvas.l2?.segments.find(p => p.id === selected.id);
    if (selected.kind === 'segment' && !segment) return null;
    if (segment && layer === 'l2') {
        const l = segment.link;
        return {kind: 'L2 / ' + t('logical_link'), title: name(l.a.nodeId) + ' ↔ ' + name(l.b.nodeId),
            sub: segment.active ? t('passing') : segment.unknown ? t('unknown') : t('blocked'),
            heads: [t('node'), t('port'), t('tag')], rows: [[name(l.a.nodeId), s.endShown(l.a) ? segment.aname : '?', segment.tag === 'u' ? t('untagged') : segment.tag],
                [name(l.b.nodeId), s.endShown(l.b) ? segment.bname : '?', segment.tag === 'u' ? t('untagged') : segment.tag]],
            related: [{kind: 'link', id: l.id, label: t('physical')}], messages: s.der.l2.links.get(l.id)?.issues || [],
            foot: t('link_foot'), focus: {cx: (s.model.nodes.get(l.a.nodeId).cx + s.model.nodes.get(l.b.nodeId).cx) / 2,
                cy: (s.model.nodes.get(l.a.nodeId).cy + s.model.nodes.get(l.b.nodeId).cy) / 2}};
    }
    if (selected.kind === 'link' || segment) {
        const l = segment ? segment.link : s.model.links.find(l => l.id === selected.id);
        if (!l) return null;
        const state = s.der?.l1.links.get(l.id), port = e => s.endShown(e) ? e.text : '?';
        const data = {kind: layer.toUpperCase() + ' / ' + t('connection'), title: name(l.a.nodeId) + ' ↔ ' + name(l.b.nodeId),
            sub: !state || state.unknown ? t('unknown') : state.down || l.suspend ? t('blocked') : t('passing'),
            heads: [t('node'), t('port'), t(layer === 'l1' ? 'state' : layer === 'l2' ? 'config' : 'ipv4')],
            rows: [l.a, l.b].map(e => {
                const i = endIf(s.fx, e), f = s.fx?.nodes.get(e.nodeId), end = endState(s.der, e);
                return [name(e.nodeId), port(e), layer === 'l1' ? end?.unknown ? '?' : end?.down ? 'down' : i?.oper || '?'
                    : layer === 'l2' ? switchportText(s, i) : groupOpen(s, s.model.nodes.get(e.nodeId), 'l3')
                        ? [i, ...(f && i ? f.subs(i.name) : [])].filter(Boolean).flatMap(i => i.ipv4 || []).join(', ') || '—' : s.s('ui_fog_unexplored')];
            }), related: [l.a, l.b].map(e => ({kind: 'node', id: e.nodeId, label: name(e.nodeId)})), messages: state?.why || [],
            foot: t('link_foot'), focus: {cx: (s.model.nodes.get(l.a.nodeId).cx + s.model.nodes.get(l.b.nodeId).cx) / 2,
                cy: (s.model.nodes.get(l.a.nodeId).cy + s.model.nodes.get(l.b.nodeId).cy) / 2}};
        if (layer === 'l1') data.directions = linkDirections(l).map(({from, to}) => [name(from.nodeId) + ' ' + port(from), name(to.nodeId) + ' ' + port(to)]);
        if (layer === 'l2') data.messages = s.der?.l2.links.get(l.id)?.issues || [];
        const filters = fmtFilters(l.filters);
        if (filters.length) data.sections = [{title: t('filters'), heads: [t('name'), t('value')], rows: filters.map(f => [f.k, f.v])}];
        return data;
    }
    const v = selected.kind === 'switchvlan' && s.canvas.l2?.virtuals.get(selected.id);
    const n = v ? v.orig : s.model.nodes.get(selected.id);
    if (!n) return null;
    const f = s.fx?.nodes.get(n.id), ints = f?.ifs || [], sil = n.state === 'silhouette';
    const status = s.nodeStatus(n);
    const data = {kind: layer.toUpperCase() + ' / ' + (sil ? t('exploration') : s.s('ui_cat_' + n.cat)),
        title: sil ? s.s('ui_fog_unknown') : n.name,
        sub: sil ? s.s('ui_fog_unknown_note') : (status ? s.s('ui_st_' + status) : s.s('ui_status_nodata')) + ' · ' + ints.length + ' ' + t('interfaces'),
        device: n, focus: v || n, related: [], foot: t('node_foot'), sections: []};
    const guests = s.statusKnown && n.cat === 'hypervisor' && (n.state === 'open' || !s.reveal) && s.guests?.[n.id];
    if (guests) data.sections.push({title: t('guests'), heads: ['ID', t('name'), t('type'), t('state')], rows: guests.items.map(g => [g.vmid, g.name, g.type, g.status])});
    if (sil || !groupOpen(s, n, layer)) return {...data, rows: [], heads: [], empty: s.s('ui_fog_unexplored')};
    const tree = v && layer === 'l2' && stpSection(s, f, v.vlan);
    if (tree) data.sections.push(tree);
    if (v && layer === 'l2' && panel !== 'mac') return {...data, kind: 'L2 / ' + t('vlan_object'), title: n.name + ' · VLAN ' + v.vlan,
        heads: [t('node'), t('interface'), 'IPV4'], rows: memberRows(v.domain?.members || []),
        empty: t('disconnected'), related: [{kind: 'node', id: n.id, label: t('physical')}], foot: v.domain?.vlans.length > 1 ? t('vlan_mixed') : t('vlan_foot')};
    if (layer === 'l2' && panel === 'mac') {
        const table = macTable(s.model, s.fx, n.id);
        return {...data, kind: 'L2 / ' + s.s('ui_mac_table'), heads: [s.s('ui_mac_col_mac'), s.s('ui_mac_col_port'), s.s('ui_mac_col_vlan')],
            rows: (table?.rows || []).map(r => Object.assign([r.mac + (r.owner ? ' · ' + r.owner.node : ''), r.iface, r.vlan], {go: r.link ? {kind: 'link', id: r.link} : null})),
            empty: table ? s.s('ui_mac_empty') : t('mac_missing'), foot: table?.truncated ? s.s('ui_mac_truncated') : s.s('ui_mac_note')};
    }
    if (!ints.length) return {...data, heads: [], rows: [], empty: s.s('ui_facts_never')};
    const cell = (i, key, value = i[key] || '?') => ({text: value, seen: Array.isArray(i.seen)
        && (i.seen.includes(key) || key === 'switchport' && i.seen.some(k => ['admin', 'oper'].includes(k)))});
    if (layer === 'l1') {
        data.heads = [t('port'), s.s('ui_canvas_admin'), s.s('ui_canvas_oper')];
        data.rows = ints.filter(i => i.port).map(i => {
            const l = s.model.links.find(l => [l.a, l.b].some(e => e.nodeId === n.id && e.adapter === i.port.adapter && e.port === i.port.port));
            if (l) data.related.push({kind: 'link', id: l.id, label: i.name});
            const end = endState(s.der, {nodeId: n.id, adapter: i.port.adapter, port: i.port.port});
            const mirrored = !end || end.unknown ? '?' : end.down ? 'down' : 'up';
            return [i.name, cell(i, 'admin', i.mirror ? mirrored : i.admin || '?'),
                cell(i, 'oper', i.mirror ? mirrored : end?.unknown ? '?' : end?.down ? 'down' : i.oper || '?')];
        });
    } else if (layer === 'l2') {
        data.heads = [t('port'), t('config'), 'STP'];
        data.rows = ints.filter(i => i.kind !== 'loopback').map(i => [i.name, cell(i, 'switchport', switchportText(s, i)), cell(i, 'stp', stpText(i))]);
        const trees = stpSection(s, f);
        if (trees) data.sections.push(trees);
        ints.forEach(i => {
            const d = s.der?.l2.domFor(n.id, i);
            if (d) data.related.push({kind: 'vlan', id: d.id, label: i.name + ' · ' + d.label});
        });
    } else {
        data.heads = [t('interface'), 'IPV4', t('state')];
        data.rows = ints.filter(i => i.ipv4?.length || i.kind === 'physical').map(i => [i.name, cell(i, 'ipv4', (i.ipv4 || []).join(', ') || '—'), cell(i, 'oper', i.admin === 'down' ? 'shutdown' : i.oper || '?')]);
        s.canvas.subnets.filter(x => x.members.some(m => m.nodeId === n.id)).forEach(x => data.related.push({kind: 'subnet', id: x.key, label: x.key}));
        const gateways = [...new Set(ints.map(i => i.gateway).filter(Boolean))];
        if (gateways.length) data.sections.push({title: t('gateways'), heads: [t('address')], rows: gateways.map(g => [g])});
    }
    return data;
};

/**
 * The spanning trees of a bridge as the console showed them: per VLAN the root and the cost to it.
 *
 * @param {object} s stage
 * @param {object|undefined} f facts of the node (facts.js index)
 * @param {number} [vlan] only this VLAN
 * @returns {object|null} a section of the card
 */
function stpSection(s, f, vlan) {
    const trees = f?.stp ? Object.entries(f.stp.vlans).filter(([v]) => vlan === undefined || Number(v) === Number(vlan))
        .sort((a, b) => a[0] - b[0]) : [];
    if (!trees.length) return null;
    const id = x => x && (x.priority !== undefined || x.mac) ? [x.priority, x.mac].filter(v => v !== undefined).join(' ') : '?';
    return {title: 'STP' + (f.stp.mode ? ' · ' + f.stp.mode : ''), heads: ['VLAN', 'Root ID', 'Cost'],
        rows: trees.map(([v, x]) => [v, x.is_root === true ? s.s('ui_canvas_stp_self') + (x.root || x.bridge ? ' · ' + id(x.root || x.bridge) : '')
            : id(x.root), x.root_cost ?? '?'])};
}

function switchportText(s, i) {
    if (!i) return s.s('ui_canvas_unknown');
    if (!i.mirror && i.admin === 'down') return 'shutdown';
    const sp = i.switchport;
    if (sp?.mode === 'access') return 'access · VLAN ' + sp.vlan;
    if (sp?.mode === 'trunk') return 'trunk · ' + (Array.isArray(sp.allowed) ? sp.allowed.join(', ') : s.s('ui_canvas_all_vlans'))
        + ' · native ' + (sp.native === null ? '—' : sp.native ?? 1);
    if (sp?.mode === 'none') return s.s('ui_canvas_disabled');
    if (i.dot1q != null) return '802.1Q · VLAN ' + i.dot1q;
    if (i.kind === 'svi') return 'SVI · VLAN ' + i.vlan;
    return s.s('ui_canvas_no_l2');
}
