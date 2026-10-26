'use strict';
/**
 * Static server of dist/ plus a fake console gateway on /gw/console/<node_id> (WebSocket).
 *
 * The fake devices answer like the fake slot of the mock manager: a prompt, an echo, a few show commands,
 * `reload` closes the console with code 4000 (the console closed on the slot side).
 *
 * What a device prints depends on its kind: Cisco IOS (IOU, routers) `R1#`, VPCS `PC1> `, any other node a Linux host
 * `user@pc1:~$ `. Their answers are made to differ from the demo facts, so that the page "seen in the console" layer
 * (docs/live-state.md §8) has something to show at once: a switch port that is shut down, a VLAN that is not in the
 * facts, a host with another address.
 *
 * A node with console_type "vnc" has a screen instead: its binary frames (the raw RFB stream) are passed both ways
 * to the fake Lab Slot of the mock manager (devtools/mock-manager, HARNESS_SLOT_URL, default ws://127.0.0.1:8091),
 * like the real gateway does with `console: "vnc"` (CONTRACTS §6). The slot closing is code 4000, a slot that
 * does not take the connection is 4001. GET /gw/stats counts the screens that are open now.
 *
 *   node server.js [port]       → http://127.0.0.1:8123/index.ru.html
 */
const fs = require('fs');
const http = require('http');
const path = require('path');
const {WebSocket, WebSocketServer} = require('ws');

const dist = path.join(__dirname, 'dist');
const port = +process.argv[2] || +process.env.PORT || 8123;
const TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json'};

const demoDir = path.join(__dirname, '../../plugin/demo');
const nodes = fs.readdirSync(demoDir).filter(f => f.endsWith('.gns3'))
    .flatMap(f => JSON.parse(fs.readFileSync(path.join(demoDir, f), 'utf8')).topology.nodes);
const names = new Map(nodes.map(n => [n.node_id, n.name]));
const screens = new Set(nodes.filter(n => n.console_type === 'vnc').map(n => n.node_id));
// The demo facts: a Linux host prints its own interface name and MAC, a VPCS its own interface.
const factNodes = Object.assign({}, ...fs.readdirSync(demoDir).filter(f => f.endsWith('.facts.json'))
    .map(f => JSON.parse(fs.readFileSync(path.join(demoDir, f), 'utf8')).nodes));
const SLOT_URL = process.env.HARNESS_SLOT_URL || 'ws://127.0.0.1:8091';
const SLOT_TOKEN = process.env.HARNESS_SLOT_TOKEN || 'dev-console-token';
const stats = {screens: 0, opened: 0};

const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    if (url.pathname === '/gw/stats') {
        res.writeHead(200, {'Content-Type': 'application/json'}).end(JSON.stringify(stats));
        return;
    }
    const file = path.join(dist, path.normalize(url.pathname === '/' ? '/index.ru.html' : url.pathname));
    if (!file.startsWith(dist) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
        res.writeHead(404).end('not found');
        return;
    }
    res.writeHead(200, {'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store'});
    fs.createReadStream(file).pipe(res);
});

const nodeById = new Map(nodes.map(n => [n.node_id, n]));

/**
 * Kind of the fake device of a node: ios, vpcs or linux.
 *
 * @param {string} id node id
 * @returns {string}
 */
function family(id) {
    const n = nodeById.get(id) || {};
    if (n.node_type === 'vpcs') {
        return 'vpcs';
    }
    return n.node_type === 'iou' || /router|multilayer/.test(n.symbol || '') ? 'ios' : 'linux';
}

const PROMPTS = {
    ios: host => `${host}#`,
    vpcs: host => `${host}> `,
    linux: host => `user@${host.toLowerCase()}:~$ `,
};

const SW_STATUS = [
    'Port      Name               Status       Vlan       Duplex  Speed Type',
    'Et0/0                        connected    trunk      auto    auto  unknown',
    'Et0/1                        connected    10         auto    auto  unknown',
    'Et0/2                        disabled     10         auto    auto  unknown',
    'Et0/3                        disabled     1          auto    auto  unknown',
];

const SW_VLANS = [
    'VLAN Name                             Status    Ports',
    '---- -------------------------------- --------- -------------------------------',
    '1    default                          active    Et0/3',
    '10   USERS                            active    Et0/1, Et0/2',
    '20   LAB                              active',
    '99   MGMT                             active',
    '1002 fddi-default                     act/unsup',
];

/**
 * A host address of the node from the demo facts, moved to .99 (.77 on a VPCS): a change the scheme can show.
 *
 * @param {string} id node id
 * @param {string} last last octet
 * @returns {{name: string, mac: string, ip: string, gateway: string}}
 */
function hostFacts(id, last) {
    const first = ((factNodes[id] || {}).interfaces || []).find(i => i.port) || {};
    const old = (first.ipv4 || ['10.0.10.11/24'])[0].split('/');
    return {
        name: first.name || 'ens3',
        mac: first.mac || '',
        ip: `${old[0].replace(/\d+$/, last)}/${old[1]}`,
        gateway: first.gateway || '',
    };
}

/**
 * The answer of a fake device to a command, or null if it does not know the command.
 *
 * @param {string} kind ios, vpcs or linux
 * @param {string} id node id
 * @param {string} cmd the command line
 * @returns {string[]|null} lines of the output
 */
function answer(kind, id, cmd) {
    if (kind === 'ios') {
        if (/^sh(ow)?\s+ip\s+int(erface)?\s+b(rief)?$/i.test(cmd)) {
            return SHOW_IP;
        }
        if (/^sh(ow)?\s+int(erfaces?)?\s+stat(us)?$/i.test(cmd)) {
            return ['', ...SW_STATUS];
        }
        if (/^sh(ow)?\s+vl(an)?\s+b(rief)?$/i.test(cmd)) {
            return ['', ...SW_VLANS];
        }
        return /^(write memory|wr|write)$/i.test(cmd) ? ['Building configuration...', '[OK]'] : null;
    }
    if (kind === 'vpcs') {
        if (!/^sh(ow)?\s+ip(\s+all)?$/i.test(cmd)) {
            return null;
        }
        const f = hostFacts(id, '77');
        return ['', `NAME        : ${names.get(id)}[1]`, `IP/MASK     : ${f.ip}`, `GATEWAY     : ${f.gateway || '0.0.0.0'}`,
            'DNS         :', 'MAC         : 00:50:79:66:68:00', 'LPORT       : 20000', 'RHOST:PORT  : 127.0.0.1:20001', 'MTU         : 1500'];
    }
    const f = hostFacts(id, '99');
    if (/^ip\s+(-c\s+)?a(ddr(ess)?)?$/.test(cmd)) {
        return [
            '1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN group default qlen 1000',
            '    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00',
            '    inet 127.0.0.1/8 scope host lo',
            `2: ${f.name}: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP group default qlen 1000`,
            ...(f.mac ? [`    link/ether ${f.mac} brd ff:ff:ff:ff:ff:ff`] : []),
            `    inet ${f.ip} brd 255.255.255.255 scope global ${f.name}`,
        ];
    }
    if (/^ip\s+-br\s+a(ddr(ess)?)?$/.test(cmd)) {
        return ['lo               UNKNOWN        127.0.0.1/8', `${f.name.padEnd(16)} UP             ${f.ip}`];
    }
    return /^ip\s+r(oute)?$/.test(cmd) ? [`10.0.10.0/24 dev ${f.name} proto kernel scope link src ${f.ip.split('/')[0]}`] : null;
}

const SHOW_IP = [
    'Interface              IP-Address      OK? Method Status                Protocol',
    'GigabitEthernet0/0     10.0.12.1       YES manual up                    up',
    'GigabitEthernet0/1     10.0.13.1       YES manual up                    up',
    'Loopback0              1.1.1.1         YES manual up                    up',
];

/**
 * Passes the raw RFB stream between a browser and the fake slot, in binary frames.
 *
 * @param {WebSocket} ws the browser
 * @param {string} id node id
 */
function screen(ws, id) {
    const up = new WebSocket(`${SLOT_URL}/v1/console/harness/${encodeURIComponent(id)}`, ['lab-slot', `bearer.${SLOT_TOKEN}`]);
    const waiting = [];
    let ready = false;
    stats.screens++;
    stats.opened++;
    up.on('open', () => {
        ready = true;
        waiting.splice(0).forEach(data => up.send(data, {binary: true}));
    });
    up.on('message', data => {
        if (ws.readyState === WebSocket.OPEN) {
            ws.send(data, {binary: true});
        }
    });
    up.on('error', () => {}); // 'close' follows
    up.on('close', () => ws.close(ready ? 4000 : 4001));
    ws.on('message', (data, isBinary) => {
        if (!isBinary) {
            return; // RFB has no text frames
        }
        if (ready) {
            up.send(data, {binary: true});
        } else {
            waiting.push(data);
        }
    });
    ws.on('close', () => {
        stats.screens--;
        up.close();
    });
}

const wss = new WebSocketServer({noServer: true});
server.on('upgrade', (req, socket, head) => {
    if (!req.url.startsWith('/gw/console/')) {
        socket.destroy();
        return;
    }
    wss.handleUpgrade(req, socket, head, ws => {
        const id = decodeURIComponent(new URL(req.url, 'http://x').pathname.split('/').pop());
        if (screens.has(id)) {
            screen(ws, id);
            return;
        }
        const host = names.get(id) || 'Device';
        const kind = family(id);
        let line = '';
        const out = s => ws.send(Buffer.from(s, 'utf8'));
        const prompt = () => out(PROMPTS[kind](host));
        setTimeout(() => out(`\r\n${host} console\r\n${PROMPTS[kind](host)}`), 50);
        ws.on('message', (data, isBinary) => {
            if (isBinary) {
                return; // window size
            }
            for (const ch of data.toString('utf8')) {
                if (ch === '\r') {
                    const cmd = line.trim();
                    line = '';
                    out('\r\n');
                    if (cmd === 'reload') {
                        ws.close(4000);
                        return;
                    }
                    const lines = answer(kind, id, cmd);
                    if (lines) {
                        out(lines.join('\r\n') + '\r\n');
                    } else if (cmd && cmd !== 'enable') {
                        out(kind === 'ios' ? `% Unknown command: ${cmd}\r\n` : `${cmd}: command not found\r\n`);
                    }
                    prompt();
                } else if (ch === '\x15' || ch === '\x1a') {
                    line = '';
                } else if (ch === '\x7f') {
                    if (line) {
                        line = line.slice(0, -1);
                        out('\b \b');
                    }
                } else if (ch >= ' ') {
                    line += ch;
                    out(ch);
                }
            }
        });
    });
});

server.listen(port, '127.0.0.1', () => console.log(`http://127.0.0.1:${port}/index.ru.html`));
