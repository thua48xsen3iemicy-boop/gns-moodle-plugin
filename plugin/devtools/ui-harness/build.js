'use strict';
/**
 * Builds the harness: compiles amd/src to AMD (a stand-in for `grunt amd`, without minifying), renders the real
 * Mustache template with the demo data and the language strings, and copies styles.css and RequireJS.
 *
 * With --amd-build[ --only=a,b] it also writes minified modules with source maps to plugin/amd/build (see README.md).
 */
const fs = require('fs');
const path = require('path');
const babel = require('@babel/core');
const Mustache = require('mustache');

const plugin = path.resolve(__dirname, '../../plugin');
const src = path.join(plugin, 'amd/src');
const dist = path.join(__dirname, 'dist');
const THIRD_PARTY = new Set(['xterm', 'xterm-addon-fit']);

/**
 * Compiles an ES module of the plugin to an AMD module with its id.
 *
 * @param {string} name module name without the component
 * @param {string} code source
 * @returns {{code: string, map: object}}
 */
function compile(name, code) {
    return babel.transformSync(code, {
        babelrc: false,
        configFile: false,
        sourceType: 'module',
        filename: name + '.js',
        sourceMaps: true,
        comments: false,
        plugins: [require('@babel/plugin-transform-dynamic-import'), [require('@babel/plugin-transform-modules-amd'), {}]],
        moduleIds: true,
        moduleId: 'mod_gns3lab/' + name,
    });
}

/**
 * Reads the strings of a language file of the plugin.
 *
 * @param {string} lang en or ru
 * @returns {object} key → text
 */
function strings(lang) {
    const text = fs.readFileSync(path.join(plugin, 'lang', lang, 'gns3lab.php'), 'utf8');
    const out = {};
    for (const m of text.matchAll(/^\$string\['([^']+)'\] = '((?:[^'\\]|\\.)*)';$/gm)) {
        out[m[1]] = m[2].replace(/\\(['\\])/g, '$1');
    }
    return out;
}

fs.rmSync(dist, {recursive: true, force: true});
fs.mkdirSync(path.join(dist, 'amd'), {recursive: true});

const modules = fs.readdirSync(src).filter(f => f.endsWith('.js')).map(f => f.slice(0, -3));
for (const name of modules) {
    const code = fs.readFileSync(path.join(src, name + '.js'), 'utf8');
    if (THIRD_PARTY.has(name)) {
        fs.writeFileSync(path.join(dist, 'amd', name + '.js'), code);
        continue;
    }
    fs.writeFileSync(path.join(dist, 'amd', name + '.js'), compile(name, code).code);
}

fs.cpSync(path.join(__dirname, 'stubs'), path.join(dist, 'stubs'), {recursive: true});
fs.copyFileSync(path.join(plugin, 'styles.css'), path.join(dist, 'styles.css'));
fs.copyFileSync(require.resolve('requirejs/require.js'), path.join(dist, 'require.js'));
for (const lang of ['en', 'ru']) {
    fs.writeFileSync(path.join(dist, `strings.${lang}.json`), JSON.stringify(strings(lang)));
}

// The demo data is what the activity page gets when the lab manager is not set up (classes/local/demo.php).
const read = f => JSON.parse(fs.readFileSync(path.join(plugin, 'demo', f), 'utf8'));
const data = {
    topology: read('ospf-multi-area.gns3'),
    live: read('ospf-multi-area.live.json'),
    facts: {ok: read('ospf-multi-area.facts.json'), broken: read('ospf-multi-area.facts-broken.json')},
    source: 'demo',
    labname: 'Lab 4',
};
fs.writeFileSync(path.join(dist, 'demo.json'), JSON.stringify(data));

/**
 * Renders the real template of the lab page.
 *
 * @param {string} lang language
 * @param {object} extra overrides of the template context
 * @returns {string} HTML
 */
function page(lang, extra = {}) {
    const str = strings(lang);
    const tpl = fs.readFileSync(path.join(plugin, 'templates/viewer.mustache'), 'utf8');
    const icons = fs.readFileSync(path.join(plugin, 'templates/icons.mustache'), 'utf8');
    const view = {
        uniqid: 'u1',
        labname: lang === 'ru' ? 'Лаба 4 · OSPF multi-area' : 'Lab 4 · OSPF multi-area',
        backurl: '#back',
        isdemo: true,
        previewerror: false,
        cancontrol: true,
        cmid: 1,
        datajson: JSON.stringify(data),
        str: () => (text, render) => {
            const key = render(text).split(',')[0].trim();
            if (!(key in str)) {
                throw new Error('Missing string ' + key);
            }
            return str[key];
        },
        js: () => () => '',
        ...extra,
    };
    return Mustache.render(tpl, view, {'mod_gns3lab/icons': icons});
}
for (const lang of ['en', 'ru']) {
    const body = page(lang);
    const html = fs.readFileSync(path.join(__dirname, 'index.template.html'), 'utf8')
        .replaceAll('{{lang}}', lang).replace('{{body}}', () => body);
    fs.writeFileSync(path.join(dist, `index.${lang}.html`), html);
}
// A user who may only look: no session bar.
fs.writeFileSync(path.join(dist, 'index.viewer.html'), fs.readFileSync(path.join(__dirname, 'index.template.html'), 'utf8')
    .replaceAll('{{lang}}', 'ru').replace('{{body}}', () => page('ru', {cancontrol: false})));

if (process.argv.includes('--amd-build')) {
    const {minify} = require('terser');
    const out = path.join(plugin, 'amd/build');
    const only = (process.argv.find(a => a.startsWith('--only=')) || '').slice(7).split(',').filter(Boolean);
    (async() => {
        for (const name of modules.filter(n => !THIRD_PARTY.has(n) && (!only.length || only.includes(n)))) {
            const code = fs.readFileSync(path.join(src, name + '.js'), 'utf8');
            const c = compile(name, code);
            const res = await minify(c.code, {
                ecma: 2020,
                sourceMap: {content: c.map, filename: name + '.min.js', url: name + '.min.js.map'},
                format: {comments: false},
            });
            fs.writeFileSync(path.join(out, name + '.min.js'), res.code + '\n');
            fs.writeFileSync(path.join(out, name + '.min.js.map'), res.map);
        }
        copyMin();
    })();
} else {
    copyMin();
}

/**
 * The minified modules of amd/build, as Moodle serves them: index.*.html?min runs them.
 */
function copyMin() {
    fs.mkdirSync(path.join(dist, 'amd-min'), {recursive: true});
    for (const f of fs.readdirSync(path.join(plugin, 'amd/build')).filter(n => n.endsWith('.min.js'))) {
        fs.copyFileSync(path.join(plugin, 'amd/build', f), path.join(dist, 'amd-min', f.replace('.min.js', '.js')));
    }
}
console.log('built', modules.length, 'modules');
