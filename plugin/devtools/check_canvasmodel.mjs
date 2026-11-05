// Checks the L2 chassis layout of canvasmodel.js without a browser: neighbouring switches must not cover each other.
// Run: node devtools/check_canvasmodel.mjs
/* eslint-disable */
import assert from 'node:assert/strict';
import {register} from 'node:module';

// amd/src imports its siblings as 'mod_gns3lab/<name>': point that prefix at the source directory.
const srcUrl = new URL('../plugin/amd/src/', import.meta.url).href;
register('data:text/javascript,' + encodeURIComponent(`
const src = ${JSON.stringify(srcUrl)};
export async function resolve(specifier, context, next) {
    if (specifier.startsWith('mod_gns3lab/')) {
        return {url: new URL(specifier.slice('mod_gns3lab/'.length) + '.js', src).href, shortCircuit: true, format: 'module'};
    }
    return next(specifier, context);
}
export async function load(url, context, next) {
    return next(url, url.startsWith(src) ? {...context, format: 'module'} : context);
}`));
const {layoutVlans, pillWidth, PILL_H} = await import(srcUrl + 'canvasmodel.js');

const node = (id, cx, cy) => [id, {id, cx, cy, name: id}];
const boxes = (model, vlans) => layoutVlans(model, vlans).map(({orig, items}) => {
    const xs = items.map(i => [orig.cx + i.x - i.w / 2, orig.cx + i.x + i.w / 2]), ys = items.map(i => orig.cy + i.y);
    return {id: orig.id, x0: Math.min(...xs.map(x => x[0])) - 12, x1: Math.max(...xs.map(x => x[1])) + 12,
        y0: Math.min(...ys) - PILL_H / 2 - 12, y1: Math.max(...ys) + PILL_H / 2 + 12};
});
const apart = (a, b) => a.x1 <= b.x0 || b.x1 <= a.x0 || a.y1 <= b.y0 || b.y1 <= a.y0;
let n = 0;
const ok = (name, fn) => { fn(); n++; console.log('PASS  ' + name); };

ok('a lone switch keeps its VLANs in a row', () => {
    const model = {nodes: new Map([node('SW1', 0, 0)])};
    const [{items}] = layoutVlans(model, new Map([['SW1', new Set([10, 20])]]));
    assert.equal(new Set(items.map(i => i.y)).size, 1);
    assert.ok(items[0].x < items[1].x);
});

ok('two switches on one line, 2 + 2 VLANs, do not cover each other', () => {
    const model = {nodes: new Map([node('SW1', 0, 0), node('SW2', 200, 0)])};
    const [a, b] = boxes(model, new Map([['SW1', new Set([10, 20])], ['SW2', new Set([30, 40])]]));
    assert.ok(apart(a, b), JSON.stringify([a, b]));
});

ok('a switch next to a host does not cover the host', () => {
    const model = {nodes: new Map([node('SW1', 0, 0), node('PC1', 110, 0)])};
    const [a] = boxes(model, new Map([['SW1', new Set([10, 20, 30])]]));
    assert.ok(a.x1 <= 110 - 40 || a.y1 <= -42 || a.y0 >= 42, JSON.stringify(a));
});

ok('pill width grows with the VLAN number', () => assert.ok(pillWidth(1000) > pillWidth(1)));
console.log(`${n} checks passed`);
