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
const str = (lang, key) => strings(lang)[key];

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
// ospf-multi-area is the default lab; the other labs of plugin/demo open as index.<lang>.<lab>.html.
const read = f => JSON.parse(fs.readFileSync(path.join(plugin, 'demo', f), 'utf8'));
const demoOf = (lab, labname) => ({
    topology: read(`${lab}.gns3`),
    live: read(`${lab}.live.json`),
    facts: {ok: read(`${lab}.facts.json`), broken: read(`${lab}.facts-broken.json`)},
    source: 'demo',
    labname,
});
const EXTRA_LABS = {'linux-switch-vlan': {ru: 'Лаба 5 · Linux-коммутатор', en: 'Lab 5 · Linux switch'}};
const data = demoOf('ospf-multi-area', 'Lab 4');
fs.writeFileSync(path.join(dist, 'demo.json'), JSON.stringify(data));
for (const lab of Object.keys(EXTRA_LABS)) {
    fs.writeFileSync(path.join(dist, `demo.${lab}.json`), JSON.stringify(demoOf(lab, 'Lab 5')));
}

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
const LAB_INIT = "require(['mod_gns3lab/lab'], function(lab) { lab.init('u1', 1, q.has('observer') ? 7 : 0); });";
const EDITOR_INIT = "require(['mod_gns3lab/editor'], function(editor) { editor.init('u1'); });";
const shell = (lang, lab, body, mode = 'live', start = '', init = LAB_INIT) => fs.readFileSync(path.join(__dirname, 'index.template.html'), 'utf8')
    .replaceAll('{{lang}}', lang).replaceAll('{{lab}}', lab).replaceAll('{{mode}}', mode).replaceAll('{{start}}', start)
    .replace('{{init}}', () => init).replace('{{body}}', () => body);
// The modes of the scheme (docs/lab-constructor/README.md §2): index.<lang>.seen.html, .fog.html, and .fog-start.html
// where the fog starts from PC1 (the activity setting `fogstart`).
const MODES = {seen: {mode: 'seen', start: []}, fog: {mode: 'fog', start: []}, 'fog-start': {mode: 'fog', start: ['PC1']}};
for (const lang of ['en', 'ru']) {
    fs.writeFileSync(path.join(dist, `index.${lang}.html`), shell(lang, '', page(lang)));
    for (const [lab, names] of Object.entries(EXTRA_LABS)) {
        const body = page(lang, {labname: names[lang], datajson: JSON.stringify(demoOf(lab, 'Lab 5'))});
        fs.writeFileSync(path.join(dist, `index.${lang}.${lab}.html`), shell(lang, lab, body));
    }
}
for (const lang of ['en', 'ru']) {
    for (const [name, m] of Object.entries(MODES)) {
        const body = page(lang, {
            datajson: JSON.stringify({...data, factsview: m.mode, fogstart: m.start}),
            modetag: str(lang, `ui_mode_${m.mode}_tag`),
            modehelp: str(lang, `ui_mode_${m.mode}_help`),
        });
        fs.writeFileSync(path.join(dist, `index.${lang}.${name}.html`), shell(lang, '', body, m.mode, m.start.join(',')));
    }
}

// The lab editor of the library (classes/local/lab_definition.php, editor.php): a catalog of the lab manager with the
// lab constructor, a lab of a switch, two Debian, a VPCS and a router, no segments yet. editor.<lang>.html is a manager
// with the lab constructor; editor.<lang>.plain.html is one without it (a lab/1 manager); editor.<lang>.segments.html
// opens a lab that already has a segment; editor.<lang>.norouter.html is one without a router.
const image = (id, title, kind, category, extra = {}) => ({
    id, title, kind, category, symbol: ':/symbols/x.svg', console_type: 'telnet', ram_mb: null, cpus: null,
    adapters: {default: 1, min: 1, max: 8}, port_name: kind === 'iou' ? 'Ethernet{adapter}/{port}' : 'Ethernet{adapter}',
    ports_per_adapter: kind === 'iou' ? 4 : 1, preconfig: false, ...extra,
});
const EDITOR_IMAGES = [
    image('debian12', 'Debian 12', 'qemu', 'host', {preconfig: true}),
    image('vpcs', 'VPCS', 'vpcs', 'host', {preconfig: true, adapters: null, port_name: 'Ethernet0'}),
    image('linux-switch', 'Linux Switch', 'qemu', 'switch', {adapters: {default: 8, min: 1, max: 16}, console_type: 'none'}),
    image('cisco-iou-l3', 'Cisco IOU L3', 'iou', 'router', {adapters: {default: 2, min: 1, max: 4}}),
];
const end = (node, adapter, port = 0) => ({node, adapter, port});
const EDITOR_LAB = {
    format: 'gns3lab.lab/1',
    nodes: [
        {name: 'SW1', image: 'linux-switch', x: 0, y: 0, adapters: 8},
        {name: 'PC1', image: 'debian12', x: -200, y: 160, adapters: 1},
        {name: 'PC2', image: 'debian12', x: 0, y: 160, adapters: 2},
        {name: 'PC3', image: 'vpcs', x: 200, y: 160},
        {name: 'R1', image: 'cisco-iou-l3', x: 0, y: -160, adapters: 2},
    ],
    links: [
        {a: end('SW1', 0), b: end('PC1', 0)}, {a: end('SW1', 1), b: end('PC2', 0)}, {a: end('SW1', 2), b: end('PC3', 0)},
        {a: end('SW1', 3), b: end('R1', 0, 0)},
    ],
};
const EDITOR_SEGMENTED = {
    ...EDITOR_LAB, format: 'gns3lab.lab/2',
    segments: [{name: 'LAN1', subnet: {fixed: '192.168.10.0/24'}, vlan: {fixed: 10}, gateway: end('R1', 0, 0),
        members: [end('PC1', 0), {...end('PC3', 0), host: 20}]}],
};
// A lab without a router: the Gateway field of the segment has nothing to offer and says why.
const EDITOR_NOROUTER = {
    format: 'gns3lab.lab/2',
    nodes: EDITOR_LAB.nodes.filter(n => n.name !== 'R1'),
    links: EDITOR_LAB.links.filter(l => l.b.node !== 'R1'),
    segments: [{name: 'LAN1', subnet: {fixed: '192.168.10.0/24'}, members: [end('PC1', 0)]}],
};
/**
 * Renders the real template of the lab editor with the strings editor.php sends.
 *
 * @param {string} lang language
 * @param {object} def definition the editor opens
 * @param {boolean} constructor the manager has the lab constructor
 * @returns {string} HTML
 */
function editorPage(lang, def, constructor) {
    const all = strings(lang);
    const texts = {};
    for (const [key, text] of Object.entries(all)) {
        if (key.startsWith('ed_problem_') && key !== 'ed_problem') {
            texts['problem_' + key.slice(11)] = text;
        } else if (key.startsWith('ed_warning_')) {
            texts['warning_' + key.slice(11)] = text;
        } else if (key.startsWith('ed_')) {
            texts[key] = text;
        }
    }
    const data = {labid: 1, definition: def, version: 1, status: 'draft', images: EDITOR_IMAGES,
        limits: {max_nodes: 30}, canpublish: true, labconstructor: constructor, catalogerror: '', strings: texts};
    const view = {
        uniqid: 'u1', labname: 'Lab', libraryurl: '#library', canpublish: true, labconstructor: constructor,
        datajson: JSON.stringify(data),
        str: () => (text, render) => {
            const key = render(text).split(',')[0].trim();
            if (!(key in all)) {
                throw new Error('Missing string ' + key);
            }
            return all[key];
        },
        js: () => () => '',
    };
    const icons = fs.readFileSync(path.join(plugin, 'templates/icons.mustache'), 'utf8');
    return Mustache.render(fs.readFileSync(path.join(plugin, 'templates/editor.mustache'), 'utf8'), view, {'mod_gns3lab/icons': icons});
}
for (const lang of ['en', 'ru']) {
    fs.writeFileSync(path.join(dist, `editor.${lang}.html`), shell(lang, '', editorPage(lang, EDITOR_LAB, true), 'live', '', EDITOR_INIT));
    fs.writeFileSync(path.join(dist, `editor.${lang}.plain.html`), shell(lang, '', editorPage(lang, EDITOR_LAB, false), 'live', '', EDITOR_INIT));
    fs.writeFileSync(path.join(dist, `editor.${lang}.segments.html`),
        shell(lang, '', editorPage(lang, EDITOR_SEGMENTED, true), 'live', '', EDITOR_INIT));
    fs.writeFileSync(path.join(dist, `editor.${lang}.norouter.html`),
        shell(lang, '', editorPage(lang, EDITOR_NOROUTER, true), 'live', '', EDITOR_INIT));
}

// A user who may only look: no session bar.
fs.writeFileSync(path.join(dist, 'index.viewer.html'), shell('ru', '', page('ru', {cancontrol: false})));

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
