import assert from 'node:assert/strict';
import {createReadStream} from 'node:fs';
import {createServer} from 'node:http';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright-core';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const files = {
    '/preview.html': ['preview.html', 'text/html'],
    '/styles.css': ['styles.css', 'text/css'],
    '/amd/build/app.min.js': ['amd/build/app.min.js', 'application/javascript'],
    '/demo/ospf-multi-area.gns3': ['demo/ospf-multi-area.gns3', 'application/json'],
};
const server = createServer((request, response) => {
    const found = files[new URL(request.url, 'http://localhost').pathname];
    if (!found) {
        response.writeHead(404).end();
        return;
    }
    response.setHeader('Content-Type', found[1]);
    createReadStream(path.join(root, found[0])).pipe(response);
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));

let browser;
try {
    browser = await chromium.launch({
        executablePath: process.env.CHROME_BIN || '/usr/bin/chromium',
        headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'],
    });
    const url = `http://127.0.0.1:${server.address().port}/preview.html`;
    for (const viewport of [{width: 1400, height: 900}, {width: 390, height: 844}]) {
        const page = await browser.newPage({viewport});
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(url);
        await page.locator('.alpha-list button').first().waitFor();
        assert.equal(await page.locator('.alpha-list button').count(), 11);
        assert.equal(await page.locator('.alpha-canvas').evaluate(el => el.querySelectorAll('*').length > 30), true);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
        assert.deepEqual(errors, []);
        console.log(`${viewport.width}px: 11 nodes rendered, no page errors or horizontal overflow`);
        if (viewport.width === 1400) {
            const uploaded = {
                name: 'Uploaded topology',
                topology: {
                    nodes: [
                        {node_id: 'a', name: 'R1', x: 0, y: 0},
                        {node_id: 'b', name: 'R2', x: 150, y: 0},
                    ],
                    links: [{link_id: 'ab', nodes: [{node_id: 'a'}, {node_id: 'b'}]}],
                    drawings: [],
                },
            };
            await page.locator('input[type=file]').setInputFiles({
                name: 'uploaded.gns3', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(uploaded)),
            });
            await page.getByRole('heading', {name: 'Uploaded topology'}).waitFor();
            assert.equal(await page.locator('.alpha-list button').count(), 2);
            assert.deepEqual(errors, []);
            console.log('file upload: 2 nodes and 1 link rendered');
        }
        await page.close();
    }
} finally {
    if (browser) await browser.close();
    server.close();
}
