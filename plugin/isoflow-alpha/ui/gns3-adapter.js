// Keep GNS3 data separate from Isoflow's presentation model. The source document is never edited.

const CATEGORY = ['router', 'switch', 'pc', 'server', 'cloud', 'firewall', 'hypervisor'];
const PALETTE = {
    router: ['#69d9ed', 'R'], switch: ['#71e6ac', 'SW'], pc: ['#f3bd79', 'PC'],
    server: ['#b5a0fb', 'SRV'], cloud: ['#9ed7fa', 'NET'], firewall: ['#f28c9c', 'FW'],
    hypervisor: ['#d3a2f6', 'PVE'],
};

const iconUrl = (category) => {
    const [color, label] = PALETTE[category];
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
        <path d="M14 35 60 12 106 35 60 59Z" fill="${color}" stroke="#17283c" stroke-width="3"/>
        <path d="M14 35 60 59V105L14 81Z" fill="#263b53" stroke="#17283c" stroke-width="3"/>
        <path d="M60 59 106 35V81L60 105Z" fill="#344e69" stroke="#17283c" stroke-width="3"/>
        <path d="M23 47 51 62M23 58 51 73" stroke="${color}" stroke-width="3" stroke-linecap="round"/>
        <circle cx="28" cy="75" r="3" fill="${color}"/><circle cx="38" cy="80" r="3" fill="${color}"/>
        <text x="80" y="80" text-anchor="middle" fill="white" font-family="Arial,sans-serif" font-size="${label.length > 2 ? 14 : 19}" font-weight="700">${label}</text>
    </svg>`;
    return `data:image/svg+xml,${encodeURIComponent(svg)}`;
};

const categoryOf = node => {
    const symbol = String(node.symbol || '').toLowerCase();
    const type = String(node.node_type || '').toLowerCase();
    const image = String(node.properties?.hda_disk_image || node.properties?.path || '').toLowerCase();
    if (/proxmox|\bpve/.test(`${symbol} ${image}`)) return 'hypervisor';
    if (type === 'nat' || type === 'cloud' || /cloud|nat/.test(symbol)) return 'cloud';
    if (/firewall|asa/.test(symbol)) return 'firewall';
    if (/switch|hub/.test(`${type} ${symbol}`)) return 'switch';
    if (/router/.test(symbol)) return 'router';
    if (type === 'vpcs' || /computer|pc|laptop|workstation/.test(symbol)) return 'pc';
    return 'server';
};

const pointOf = node => ({
    x: (Number(node.x) || 0) + (Number(node.width) || 60) / 2,
    y: (Number(node.y) || 0) + (Number(node.height) || 45) / 2,
});

const tileFor = (wanted, occupied) => {
    for (let radius = 0; radius <= 100; radius++) {
        for (let dy = -radius; dy <= radius; dy++) {
            for (let dx = -radius; dx <= radius; dx++) {
                if (Math.max(Math.abs(dx), Math.abs(dy)) !== radius) continue;
                const tile = {x: wanted.x + dx, y: wanted.y + dy};
                const key = `${tile.x},${tile.y}`;
                if (!occupied.has(key)) {
                    occupied.add(key);
                    return tile;
                }
            }
        }
    }
    throw new Error('Topology has too many overlapping nodes');
};

const endLabel = (node, end) => {
    if (end?.label?.text) return String(end.label.text);
    const port = (node?.ports || []).find(p => p.adapter_number === end?.adapter_number && p.port_number === end?.port_number);
    return port?.short_name || port?.name || `${end?.adapter_number ?? '?'}/${end?.port_number ?? '?'}`;
};

export const convertGns3 = doc => {
    if (!doc || !doc.topology || !Array.isArray(doc.topology.nodes) || !Array.isArray(doc.topology.links)) {
        throw new Error('Ожидается документ .gns3 с topology.nodes и topology.links');
    }
    const sourceNodes = doc.topology.nodes.filter(n => n && n.node_id);
    const nodes = new Map(sourceNodes.map(n => [String(n.node_id), n]));
    const points = sourceNodes.map(pointOf);
    const minX = Math.min(0, ...points.map(p => p.x));
    const minY = Math.min(0, ...points.map(p => p.y));
    const occupied = new Set();
    const icons = CATEGORY.map(category => ({id: category, name: category, url: iconUrl(category), isIsometric: true}));
    const items = sourceNodes.map(n => {
        const p = n.properties || {};
        const summary = [p.cpus && `${p.cpus} vCPU`, p.ram && `${p.ram} MB RAM`].filter(Boolean).join(' · ');
        return {
            id: String(n.node_id), name: String(n.name || n.node_id).slice(0, 100),
            description: summary || undefined, icon: categoryOf(n),
        };
    });
    const viewItems = sourceNodes.map(n => {
        const point = pointOf(n);
        return {
            id: String(n.node_id),
            tile: tileFor({x: Math.round((point.x - minX) / 105) + 2, y: Math.round((point.y - minY) / 80) + 2}, occupied),
        };
    });
    const linkInfo = new Map();
    const connectors = [];
    let skippedLinks = 0;
    doc.topology.links.forEach((link, index) => {
        const ends = link?.nodes;
        if (!Array.isArray(ends) || ends.length < 2 || !nodes.has(String(ends[0].node_id)) || !nodes.has(String(ends[1].node_id))) {
            skippedLinks++;
            return;
        }
        const id = String(link.link_id || `link-${index}`);
        const a = String(ends[0].node_id), b = String(ends[1].node_id);
        const aLabel = endLabel(nodes.get(a), ends[0]), bLabel = endLabel(nodes.get(b), ends[1]);
        connectors.push({
            id, description: `${aLabel} ↔ ${bLabel}`.slice(0, 1000),
            style: link.suspend ? 'DASHED' : 'SOLID',
            anchors: [{id: `${id}:a`, ref: {item: a}}, {id: `${id}:b`, ref: {item: b}}],
        });
        linkInfo.set(id, {id, a, b, aLabel, bLabel, suspended: !!link.suspend, filters: link.filters || {}});
    });
    const drawings = Array.isArray(doc.topology.drawings) ? doc.topology.drawings.length : 0;
    return {
        model: {
            title: String(doc.name || 'GNS3 topology').slice(0, 100),
            items, icons, colors: [{id: '__DEFAULT__', value: '#3b83ab'}],
            views: [{id: 'gns3', name: 'GNS3', items: viewItems, connectors, rectangles: [], textBoxes: []}],
            fitToView: true,
        },
        source: {nodes, links: linkInfo},
        stats: {nodes: items.length, links: connectors.length, skippedLinks, drawings},
    };
};
