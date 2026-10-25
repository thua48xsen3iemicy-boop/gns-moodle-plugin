'use strict';
/**
 * Static server of dist/ plus a fake console gateway on /gw/console/<node_id> (WebSocket).
 *
 * The fake devices answer like the fake slot of the mock manager: a prompt, an echo, a few show commands,
 * `reload` closes the console with code 4000 (the console closed on the slot side).
 *
 *   node server.js [port]       → http://127.0.0.1:8123/index.ru.html
 */
const fs = require('fs');
const http = require('http');
const path = require('path');
const {WebSocketServer} = require('ws');

const dist = path.join(__dirname, 'dist');
const port = +process.argv[2] || +process.env.PORT || 8123;
const TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json'};

const demoDir = path.join(__dirname, '../../plugin/demo');
const names = new Map(fs.readdirSync(demoDir).filter(f => f.endsWith('.gns3'))
    .flatMap(f => JSON.parse(fs.readFileSync(path.join(demoDir, f), 'utf8')).topology.nodes.map(n => [n.node_id, n.name])));

const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    const file = path.join(dist, path.normalize(url.pathname === '/' ? '/index.ru.html' : url.pathname));
    if (!file.startsWith(dist) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
        res.writeHead(404).end('not found');
        return;
    }
    res.writeHead(200, {'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store'});
    fs.createReadStream(file).pipe(res);
});

const SHOW_IP = [
    'Interface              IP-Address      OK? Method Status                Protocol',
    'GigabitEthernet0/0     10.0.12.1       YES manual up                    up',
    'GigabitEthernet0/1     10.0.13.1       YES manual up                    up',
    'Loopback0              1.1.1.1         YES manual up                    up',
];

const wss = new WebSocketServer({noServer: true});
server.on('upgrade', (req, socket, head) => {
    if (!req.url.startsWith('/gw/console/')) {
        socket.destroy();
        return;
    }
    wss.handleUpgrade(req, socket, head, ws => {
        const id = decodeURIComponent(new URL(req.url, 'http://x').pathname.split('/').pop());
        const host = names.get(id) || 'Device';
        let line = '';
        const out = s => ws.send(Buffer.from(s, 'utf8'));
        const prompt = () => out(`${host}#`);
        setTimeout(() => out(`\r\n${host} console\r\n${host}#`), 50);
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
                    if (cmd === 'show ip interface brief') {
                        out(SHOW_IP.join('\r\n') + '\r\n');
                    } else if (cmd === 'write memory') {
                        out('Building configuration...\r\n[OK]\r\n');
                    } else if (cmd && cmd !== 'enable') {
                        out(`% Unknown command: ${cmd}\r\n`);
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
