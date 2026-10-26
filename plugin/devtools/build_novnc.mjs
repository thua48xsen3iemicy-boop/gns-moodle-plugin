// Builds amd/src/novnc.js: the RFB client of @novnc/novnc as ONE ES module for the Moodle AMD pipeline.
//
//   node build_novnc.mjs <dir with the unpacked npm package> <dir where esbuild is installed> <output file>
//
// Example (see plugin/plugin/thirdpartylibs.xml for the versions that were used):
//   npm pack @novnc/novnc@1.7.0 && mkdir pkg && tar -xzf novnc-novnc-1.7.0.tgz -C pkg
//   mkdir tool && (cd tool && npm init -y && npm install esbuild@0.28.2 --ignore-scripts)
//   node build_novnc.mjs pkg/package tool out/novnc.js
//
// The noVNC sources are not edited. One line is replaced while bundling (see PATCHES): core/util/browser.js has a
// top-level await, and the Babel step of `grunt amd` cannot turn a module with top-level await into AMD.
import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
import path from 'node:path';

const [pkgDir, esbuildDir, outFile] = process.argv.slice(2).map(p => p && path.resolve(p));
if (!pkgDir || !esbuildDir || !outFile) {
    console.error('usage: node build_novnc.mjs <noVNC package dir> <esbuild install dir> <output file>');
    process.exit(2);
}
const esbuild = createRequire(path.join(esbuildDir, 'noop.js'))('esbuild');

/** File (suffix of the path) → [exact text in noVNC, replacement]. Every text must occur exactly once. */
const PATCHES = {
    'core/util/browser.js': [
        'supportsWebCodecsH264Decode = await _checkWebCodecsH264DecodeSupport();',
        // Same value, set when the check ends instead of before the module is ready. RFB reads it when it sends
        // its encodings, long after the check ended.
        '_checkWebCodecsH264DecodeSupport().then(ok => { supportsWebCodecsH264Decode = ok; }, () => {});',
    ],
};

const pkg = JSON.parse(await fs.readFile(path.join(pkgDir, 'package.json'), 'utf8'));

/**
 * Text as the lines of a block comment.
 *
 * @param {string} text text
 * @returns {string}
 */
const quote = text => text.trim().split('\n').map(line => (' * ' + line).trimEnd()).join('\n');

/**
 * The notices that have to travel with the bundle: esbuild drops ordinary comments, so they are written here.
 * The texts of the parts with their own terms are copied from the package, not retyped.
 *
 * @returns {Promise<string>} block comment
 */
async function makeBanner() {
    const pako = await fs.readFile(path.join(pkgDir, 'vendor/pako/LICENSE'), 'utf8');
    const des = (await fs.readFile(path.join(pkgDir, 'core/crypto/des.js'), 'utf8')).match(/^\/\*([\s\S]*?)\*\//)[1]
        .split('\n').map(line => line.replace(/^ ?\* ?/, '')).join('\n');
    return `/*
 * noVNC ${pkg.version}: the RFB client (@novnc/novnc, https://github.com/novnc/noVNC), bundled into one ES module
 * for the Moodle AMD pipeline of mod_gns3lab. Exports: RFB.
 *
 * Built by build_novnc.mjs (plugin/devtools) with esbuild ${esbuild.version} from the npm package. The package is not
 * edited, but one line is replaced while bundling: core/util/browser.js had "supportsWebCodecsH264Decode = await ...",
 * a top-level await that the Babel step of "grunt amd" cannot handle. It is now a promise that sets the same variable.
 *
 * noVNC is Copyright (C) 2022 The noVNC authors (https://github.com/novnc/noVNC/blob/master/AUTHORS).
 * This Source Code Form is subject to the terms of the Mozilla Public License, v. 2.0. If a copy of the MPL was not
 * distributed with this file, You can obtain one at http://mozilla.org/MPL/2.0/ . The source code of the package:
 * https://www.npmjs.com/package/@novnc/novnc/v/${pkg.version}
 *
 * Parts with their own terms, all MPL 2.0 compatible (noVNC LICENSE.txt):
 *
 * 1. vendor/pako: pako 1.0.3 as ES modules, the zlib inflate and deflate behind the ZRLE, Tight and Zlib decoders.
 *
${quote(pako)}
 *
 * 2. core/crypto/des.js: DES for VNC password authentication, ported from the Flashlight VNC ActionScript
 *    implementation. Its attribution follows in full.
 *
${quote(des)}
 *
 * 3. core/input/keysymdef.js and core/input/xtscancodes.js are tables generated from the X11 keysymdef.h and from
 *    the keycodemapdb of libvirt (see the headers of the files in the package).
 */`;
}

const noTopLevelAwait = {
    name: 'no-top-level-await',
    setup(build) {
        build.onLoad({filter: /\.js$/}, async args => {
            const rel = path.relative(pkgDir, args.path).split(path.sep).join('/');
            if (!(rel in PATCHES)) {
                return null;
            }
            const [needle, replacement] = PATCHES[rel];
            const text = await fs.readFile(args.path, 'utf8');
            if (text.split(needle).length !== 2) {
                throw new Error(`${rel}: the line to replace is not there exactly once; check the patch for this noVNC version`);
            }
            return {contents: text.replace(needle, replacement), loader: 'js'};
        });
    },
};

const result = await esbuild.build({
    stdin: {contents: "export {default as RFB} from './core/rfb.js';", resolveDir: pkgDir, loader: 'js'},
    bundle: true,
    format: 'esm',
    target: 'es2020',
    minify: false,
    legalComments: 'none',
    banner: {js: await makeBanner()},
    plugins: [noTopLevelAwait],
    write: false,
    logLevel: 'warning',
});
const code = result.outputFiles[0].text;

// Proof that no top-level await is left: esbuild refuses it in any format that is not ESM.
esbuild.transformSync(code, {format: 'cjs', logLevel: 'error'});

await fs.mkdir(path.dirname(outFile), {recursive: true});
await fs.writeFile(outFile, code);
console.log(`${outFile}: ${code.length} bytes, noVNC ${pkg.version}, esbuild ${esbuild.version}`);
