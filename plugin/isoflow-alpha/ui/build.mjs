import {build} from 'esbuild';
import {mkdir, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outdir = path.join(root, 'amd', 'build');
const result = await build({
    entryPoints: [path.join(root, 'ui', 'app.jsx')],
    bundle: true, write: false, outdir, entryNames: 'app.min',
    format: 'iife', globalName: 'Gns3IsoflowAlpha', platform: 'browser',
    target: ['es2020'], minify: true,
    define: {'process.env.NODE_ENV': '"production"'},
    loader: {'.svg': 'dataurl', '.png': 'dataurl'},
    logLevel: 'warning',
});
await mkdir(outdir, {recursive: true});
for (const file of result.outputFiles) {
    if (file.path.endsWith('.js')) {
        const amd = `define([], function() {\n${file.text}\nreturn Gns3IsoflowAlpha;\n});\n`;
        await writeFile(file.path, amd);
    } else if (file.path.endsWith('.css')) {
        await writeFile(path.join(root, 'styles.css'), file.text);
    }
}
