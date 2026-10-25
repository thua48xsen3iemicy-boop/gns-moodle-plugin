import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
import test from 'node:test';
import assert from 'node:assert/strict';
import {convertGns3} from './gns3-adapter.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(path.join(here, '..', 'demo', 'ospf-multi-area.gns3'), 'utf8'));

test('GNS3 IDs and topology survive the Isoflow projection', () => {
    const result = convertGns3(fixture);
    const sourceIds = fixture.topology.nodes.map(n => n.node_id);
    assert.deepEqual(result.model.items.map(i => i.id), sourceIds);
    assert.equal(result.model.views[0].connectors.length, fixture.topology.links.length);
    assert.equal(new Set(result.model.views[0].items.map(i => `${i.tile.x},${i.tile.y}`)).size, sourceIds.length);
    for (const link of result.model.views[0].connectors) {
        assert.equal(link.anchors.length, 2);
        assert.ok(sourceIds.includes(link.anchors[0].ref.item));
        assert.ok(sourceIds.includes(link.anchors[1].ref.item));
    }
    assert.equal(result.stats.drawings, fixture.topology.drawings.length);
});

test('broken links are skipped and the source document stays untouched', () => {
    const doc = structuredClone(fixture);
    const before = structuredClone(doc);
    doc.topology.links.push({link_id: 'broken', nodes: [{node_id: 'missing'}, {node_id: doc.topology.nodes[0].node_id}]});
    const unchanged = structuredClone(doc);
    const result = convertGns3(doc);
    assert.equal(result.stats.skippedLinks, 1);
    assert.deepEqual(doc, unchanged);
    assert.equal(before.topology.links.length + 1, doc.topology.links.length);
});
