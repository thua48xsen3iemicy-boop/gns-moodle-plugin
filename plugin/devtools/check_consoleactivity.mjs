// Checks plugin/amd/src/consoleactivity.js without Moodle or a browser: the frames of the gateway (manager CONTRACTS §6.3),
// the marks of the nodes, the pause before reconnecting and what a stopped follower ignores. The page in a browser:
// devtools/ui-harness/check_activity.js.
//
//   node plugin/devtools/check_consoleactivity.mjs        (Node 22; exit code 1 if a scenario fails)
import assert from 'node:assert/strict';

const {ConsoleActivity} = await import(new URL('../plugin/amd/src/consoleactivity.js', import.meta.url).href);

let failed = 0;
const check = async(name, fn) => {
    try {
        await fn();
        console.log('PASS', name);
    } catch (e) {
        failed++;
        console.log('FAIL', name, '\n  ', e.message.split('\n').slice(0, 6).join('\n   '));
    }
};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/** A follower with a fake gateway: sockets lists the WebSockets it opened, changes the node ids it reported. */
const make = ({fail = false} = {}) => {
    const sockets = [], changes = [], calls = [];
    class FakeSocket {
        constructor(url) {
            this.url = url;
            this.closed = false;
            sockets.push(this);
        }
        close() {
            this.closed = true;
        }
        frame(f) {
            this.onmessage({data: typeof f === 'string' ? f : JSON.stringify(f)});
        }
        drop() {
            this.onclose({code: 1006});
        }
    }
    const api = requests => {
        calls.push(requests[0]);
        return [fail ? Promise.reject(new Error('no')) : Promise.resolve({ticket: 't/' + calls.length, gatewayurl: 'wss://gw', expiresin: 30})];
    };
    const a = new ConsoleActivity({cmid: 3, userid: 7, api, onChange: ids => changes.push(...ids), WebSocketImpl: FakeSocket,
        typingMs: 120});
    return {a, sockets, changes, calls};
};

await check('the ticket names the lab of the student, the socket goes to /console/activity', async() => {
    const {a, sockets, calls} = make();
    a.start('s1');
    await sleep(0);
    assert.deepEqual(calls, [{methodname: 'mod_gns3lab_get_activity_ticket', args: {cmid: 3, userid: 7}}]);
    assert.equal(sockets[0].url, 'wss://gw/console/activity?ticket=t%2F1');
    a.start('s1');
    await sleep(0);
    assert.equal(sockets.length, 1, 'the same session again opens nothing');
    a.stop();
});

await check('snapshot, open, input, close: open, typing for a moment, the console of the last input', async() => {
    const {a, sockets, changes} = make();
    a.start('s1');
    await sleep(0);
    const ws = sockets[0];
    ws.frame({type: 'snapshot', open: ['n1', 'n2'], input: {n1: 30, n2: 2.5}});
    assert.deepEqual(a.state('n1'), {open: true, typing: false, last: false});
    assert.deepEqual(a.state('n2'), {open: true, typing: false, last: true});
    assert.deepEqual(new Set(changes), new Set(['n1', 'n2']));
    changes.length = 0;
    ws.frame({type: 'input', node: 'n1'});
    assert.deepEqual(a.state('n1'), {open: true, typing: true, last: true});
    assert.equal(a.state('n2').last, false);
    assert.deepEqual(changes, ['n1', 'n2']);
    await sleep(200);
    assert.deepEqual(a.state('n1'), {open: true, typing: false, last: true}, 'the blink ends by itself');
    assert.ok(changes.filter(id => id === 'n1').length >= 2, 'the end of the blink is reported');
    ws.frame({type: 'close', node: 'n1'});
    assert.deepEqual(a.state('n1'), {open: false, typing: false, last: false});
    assert.equal(a.state('n2').last, true, 'the last input moves to the open console with the latest input');
    ws.frame({type: 'open', node: 'n3'});
    assert.deepEqual(a.state('n3'), {open: true, typing: false, last: false});
    a.stop();
    assert.ok(ws.closed);
    assert.deepEqual([a.state('n2').open, a.state('n3').open], [false, false], 'stop takes the marks away');
});

await check('unknown and broken frames are skipped', async() => {
    const {a, sockets} = make();
    a.start('s1');
    await sleep(0);
    const ws = sockets[0];
    ws.frame('not json');
    ws.frame({type: 'later', node: 'n1'});
    ws.frame({type: 'open'});
    ws.frame({type: 'snapshot', open: 'n1', input: {n2: 'x', n3: -1}});
    assert.deepEqual(a.state('n1'), {open: false, typing: false, last: false});
    a.stop();
});

await check('a closed socket drops the marks and reconnects after a pause that grows, reset by a frame', async() => {
    const {a, sockets} = make();
    a.start('s1');
    await sleep(0);
    sockets[0].frame({type: 'snapshot', open: ['n1'], input: {}});
    sockets[0].drop();
    assert.equal(a.state('n1').open, false);
    assert.equal(a.delay, 2000, 'the next pause doubles');
    assert.equal(sockets.length, 1, 'not at once');
    await sleep(1100);
    assert.equal(sockets.length, 2, 'reconnected after a second');
    sockets[1].drop();
    assert.equal(a.delay, 4000);
    a.timer && clearTimeout(a.timer);
    a.connect();
    await sleep(0);
    sockets[2].frame({type: 'snapshot', open: [], input: {}});
    assert.equal(a.delay, 1000, 'a frame resets the pause');
    a.stop();
});

await check('a refused ticket is retried; a stopped follower opens nothing', async() => {
    const {a, sockets, calls} = make({fail: true});
    a.start('s1');
    await sleep(0);
    assert.equal(calls.length, 1);
    assert.ok(a.timer, 'a retry is planned');
    a.stop();
    await sleep(1100);
    assert.equal(calls.length, 1, 'stop cancels the retry');
    assert.equal(sockets.length, 0);
});

await check('a ticket that comes after stop or for an old session opens no socket', async() => {
    const {a, sockets} = make();
    a.start('s1');
    a.stop();
    await sleep(0);
    assert.equal(sockets.length, 0);
    a.start('s1');
    a.start('s2');
    await sleep(0);
    assert.equal(sockets.length, 1, 'only the ticket of the current session opens a socket');
    a.stop();
});

console.log(failed ? `\n${failed} failed` : '\nall checks passed');
process.exit(failed ? 1 : 0);
