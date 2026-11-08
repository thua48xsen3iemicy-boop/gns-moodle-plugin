// Checks the pure functions of plugin/amd/src/checkreport.js without Moodle or a browser: findings grouped by root cause
// (CONTRACTS §2.9: caused_by points at the root of the chain), the order of the report, the state of the check button
// and the poll interval (docs/autochecks.md).
//
//   node plugin/devtools/check_checkreport.mjs        (Node 22; exit code 1 if a scenario fails)
import assert from 'node:assert/strict';

const R = await import(new URL('../plugin/amd/src/checkreport.js', import.meta.url).href);

let failed = 0;
const check = (name, fn) => {
    try {
        fn();
        console.log('PASS', name);
    } catch (e) {
        failed++;
        console.log('FAIL', name, '\n  ', e.message.split('\n').slice(0, 6).join('\n   '));
    }
};
const f = (id, cls, counted, causedby = '') => ({id, code: 'c' + id, class: cls, counted, causedby, message: id});
const ids = groups => groups.map(g => g.finding.id + (g.consequences.length ? '[' + g.consequences.map(c => c.id) + ']' : ''));

check('roots ordered critical, major, minor, then the uncounted; consequences under their root', () => {
    const list = [f('n1', 'note', false), f('m1', 'minor', true), f('c2', 'critical', false, 'j1'),
        f('j1', 'major', true), f('k1', 'critical', true), f('u1', 'major', false, 'j1')];
    assert.deepEqual(ids(R.groupFindings(list)), ['k1', 'j1[c2,u1]', 'm1', 'n1']);
});

check('a consequence whose root is missing is a root of its own', () => {
    assert.deepEqual(ids(R.groupFindings([f('a', 'major', false, 'zz'), f('b', 'minor', true)])), ['b', 'a']);
});

check('a chain through another consequence lands under the root', () => {
    assert.deepEqual(ids(R.groupFindings([f('r', 'major', true), f('x', 'major', false, 'y'), f('y', 'minor', false, 'r')])),
        ['r[x,y]']);
});

check('a finding caused by itself and a loop do not hang', () => {
    assert.deepEqual(ids(R.groupFindings([f('s', 'minor', true, 's')])), ['s']);
    const loop = R.groupFindings([f('p', 'minor', false, 'q'), f('q', 'minor', false, 'p')]);
    assert.equal(loop.length, 2);
});

check('nothing in, nothing out', () => {
    assert.deepEqual(R.groupFindings(undefined), []);
    assert.deepEqual(R.groupFindings([]), []);
});

check('forgiven means a note', () => {
    assert.ok(R.isForgiven(f('a', 'note', false)));
    assert.ok(!R.isForgiven(f('a', 'minor', false)));
});

const overview = (extra = {}) => ({available: true, canstart: true, attempts: {max: 3, used: 0, left: 3}, runs: [], ...extra});
const running = {status: 'running', features: ['consoles', 'facts', 'checks']};
check('the check button: own running lab with checks', () => {
    assert.deepEqual(R.startState(overview(), running, true), {show: true, enabled: true, reason: ''});
});
check('the check button: hidden for an observer or without checks', () => {
    assert.equal(R.startState(overview(), running, false).show, false);
    assert.equal(R.startState(overview({available: false}), running, true).show, false);
    assert.equal(R.startState(null, running, true).show, false);
});
check('the check button: off, with the reason', () => {
    assert.equal(R.startState(overview(), {status: 'stopped', features: []}, true).reason, 'chk_needsrunning');
    assert.equal(R.startState(overview(), null, true).reason, 'chk_needsrunning');
    assert.equal(R.startState(overview(), {status: 'running', features: ['consoles']}, true).reason, 'chk_nofacts');
    assert.equal(R.startState(overview({attempts: {max: 1, used: 1, left: 0}}), running, true).reason, 'chk_noattempts');
    assert.equal(R.startState(overview({runs: [{status: 'queued'}]}), running, true).reason, 'chk_running');
    assert.equal(R.startState(overview({canstart: false}), running, true).reason, 'chk_cannot');
});
check('unlimited attempts never run out', () => {
    assert.equal(R.startState(overview({attempts: {max: 0, used: 9, left: -1}}), running, true).enabled, true);
});
check('polls quickly at first, then less often', () => {
    assert.equal(R.pollDelay(0), 2000);
    assert.ok(R.pollDelay(20) > R.pollDelay(0) && R.pollDelay(100) > R.pollDelay(20));
});

console.log(failed ? `\n${failed} failed` : '\nall passed');
process.exit(failed ? 1 : 0);
