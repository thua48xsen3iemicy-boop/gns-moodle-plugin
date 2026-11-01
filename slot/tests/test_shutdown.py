"""Graceful guest shutdown is a fenced, concurrent node operation, never a hard stop."""
import asyncio
import threading
import time

import pytest
from api.lifecycle import Lifecycle
from api.qga import QgaUnavailable
from main import app
from test_admission import in_thread, NEW_OWNER
from test_slot import HEADERS, payload, wait


def lab_record(client, pid):
    return client.portal.call(lambda: app.state.lifecycle.get('lab', pid))


def eventually(check):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    assert check()


@pytest.fixture
def guests(mock_server):
    client, _, nodes = mock_server
    data = payload()
    pid = data['project_id']
    assert wait(client, client.post('/v1/labs/deploy', json=data, headers=HEADERS))['status'] == 'SUCCEEDED'
    return client, pid, nodes[pid]


class Broker:
    def __init__(self, nodes, outcome='ok'):
        self.nodes, self.outcome, self.calls = nodes, outcome, []

    async def call(self, node_id, command, args=None, **options):
        self.calls.append((node_id, command, args, options))
        if self.outcome == 'unavailable':
            raise QgaUnavailable('No guest agent')
        if self.outcome == 'ok':
            next(n for n in self.nodes if n['node_id'] == node_id)['status'] = 'stopped'
        return {}

    async def forget(self, node_id):
        pass

    async def close(self):
        pass


def shutdown(client, pid, node, **body):
    return client.post(f"/v1/labs/{pid}/nodes/{node['node_id']}/shutdown", json=body,
                       headers={**HEADERS, 'Idempotency-Key': 'shutdown-' + node['node_id']})


def test_stopped_guest_succeeds_without_qga(guests):
    client, pid, nodes = guests
    node = next(n for n in nodes if n['node_type'] == 'qemu')
    node['status'] = 'stopped'
    broker = Broker(nodes, 'unavailable')
    app.state.lifecycle.qga = broker
    result = wait(client, shutdown(client, pid, node))
    assert result['status'] == 'SUCCEEDED' and result['result'] == 'stopped'
    assert not broker.calls
    assert client.get(f'/v1/labs/{pid}/status', headers=HEADERS).json()['state'] == 'READY'


def test_shutdown_powerdown_and_idempotency(guests):
    client, pid, nodes = guests
    node = next(n for n in nodes if n['node_type'] == 'qemu')
    broker = Broker(nodes)
    app.state.lifecycle.qga = broker
    first = shutdown(client, pid, node)
    result = wait(client, first)
    assert result['status'] == 'SUCCEEDED' and result['result'] == 'stopped'
    assert broker.calls[0][1:3] == ('guest-shutdown', {'mode': 'powerdown'})
    replay = shutdown(client, pid, node)
    assert replay.json()['operation_id'] == first.json()['operation_id']
    assert len(broker.calls) == 1
    assert shutdown(client, pid, node, timeout=30).status_code == 409


@pytest.mark.parametrize('outcome,code', [('unavailable', 'GUEST_AGENT_UNAVAILABLE'), ('timeout', 'GUEST_SHUTDOWN_TIMEOUT')])
def test_failure_leaves_guest_and_laboratory_running(guests, monkeypatch, outcome, code):
    client, pid, nodes = guests
    node = next(n for n in nodes if n['node_type'] == 'qemu')
    broker = Broker(nodes, outcome)
    app.state.lifecycle.qga = broker
    original = asyncio.timeout
    monkeypatch.setattr(asyncio, 'timeout', lambda seconds: original(0.05 if seconds == 30 else seconds))
    result = wait(client, shutdown(client, pid, node, timeout=30))
    assert result['status'] == 'FAILED' and result['error']['code'] == code
    assert node['status'] == 'started'
    assert not lab_record(client, pid).get('shutdown_nodes')
    assert client.get(f'/v1/labs/{pid}/status', headers=HEADERS).json()['state'] == 'READY'
    node['status'] = 'stopped'  # A late poweroff after a failed command is not authorized.
    eventually(lambda: lab_record(client, pid)['state'] == 'FAILED')


def test_shutdown_rejects_non_qemu_and_invalid_timeout(guests):
    client, pid, nodes = guests
    node = nodes[0]
    node['node_type'] = 'vpcs'
    assert shutdown(client, pid, node).status_code == 409
    node['node_type'] = 'qemu'
    qemu = node
    for timeout in (True, 29, 301, '120'):
        assert shutdown(client, pid, qemu, timeout=timeout).status_code == 422


def test_manual_poweroff_fails_lab(guests):
    client, pid, nodes = guests
    node = next(n for n in nodes if n['node_type'] == 'qemu')
    node['status'] = 'stopped'
    eventually(lambda: lab_record(client, pid)['state'] == 'FAILED')
    status = client.get(f'/v1/labs/{pid}/status', headers=HEADERS).json()
    assert status['state'] == 'FAILED'
    assert status['error']['message'] == 'Required node stopped or missing'


def test_successful_shutdown_keeps_ready_and_skips_provisioning(guests, monkeypatch):
    client, pid, nodes = guests
    node = next(n for n in nodes if n['node_type'] == 'qemu')
    app.state.lifecycle.qga = Broker(nodes)
    assert wait(client, shutdown(client, pid, node))['status'] == 'SUCCEEDED'
    checked = []

    async def provision(self, lab, current, spec, initial):
        checked.append(current['node_id'])
        assert current['node_id'] != node['node_id']

    monkeypatch.setattr(Lifecycle, 'provision', provision)
    previous = lab_record(client, pid)['updated_at']
    eventually(lambda: lab_record(client, pid)['updated_at'] > previous)
    assert node['node_id'] not in checked
    assert lab_record(client, pid)['state'] == 'READY'
    assert lab_record(client, pid)['shutdown_nodes'] == [node['node_id']]


def test_restarted_node_loses_shutdown_permission(guests):
    client, pid, nodes = guests
    node = next(n for n in nodes if n['node_type'] == 'qemu')
    app.state.lifecycle.qga = Broker(nodes)
    assert wait(client, shutdown(client, pid, node))['status'] == 'SUCCEEDED'
    node['status'] = 'started'
    eventually(lambda: lab_record(client, pid)['shutdown_nodes'] == [])
    node['status'] = 'stopped'
    eventually(lambda: lab_record(client, pid)['state'] == 'FAILED')


@pytest.mark.parametrize('action', ['deploy', 'resume'])
def test_new_deployment_clears_shutdown_permission(guests, action):
    client, pid, nodes = guests
    node = next(n for n in nodes if n['node_type'] == 'qemu')
    app.state.lifecycle.qga = Broker(nodes)
    assert wait(client, shutdown(client, pid, node))['status'] == 'SUCCEEDED'
    lifecycle = app.state.lifecycle
    original = lifecycle.run
    seen = []

    async def run(operation, lab):
        seen.append((lab['state'], lab.get('shutdown_nodes')))
        await original(operation, lab)

    lifecycle.run = run
    response = (client.post('/v1/labs/deploy', json=lab_record(client, pid)['ticket'], headers=HEADERS)
                if action == 'deploy' else client.post(f'/v1/labs/{pid}/resume', headers=HEADERS))
    assert wait(client, response)['status'] == 'SUCCEEDED'
    assert seen == [('DEPLOYING', [])]
    assert lab_record(client, pid)['shutdown_nodes'] == []


def test_stop_clears_shutdown_permission(guests):
    client, pid, nodes = guests
    node = next(n for n in nodes if n['node_type'] == 'qemu')
    app.state.lifecycle.qga = Broker(nodes)
    assert wait(client, shutdown(client, pid, node))['status'] == 'SUCCEEDED'
    assert wait(client, client.post(f'/v1/labs/{pid}/stop', headers=HEADERS))['status'] == 'SUCCEEDED'
    assert lab_record(client, pid)['shutdown_nodes'] == []


def test_shutdown_permission_survives_lifecycle_restart(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('SLOT_STATE_DIR', str(tmp_path / 'slot'))

    async def scenario():
        service = Lifecycle(SimpleNamespace())
        service.save('lab', 'p', {'project_id': 'p', 'state': 'READY', 'shutdown_nodes': ['qemu']})
        await service.close()
        service = Lifecycle(SimpleNamespace())
        try:
            lab = service.get('lab', 'p')
            assert lab['shutdown_nodes'] == ['qemu']
            assert lab['state'] == 'FAILED'  # Existing restart reconciliation still applies.
            service.update(lab, 'DEPLOYING', 'resume')
            assert service.get('lab', 'p')['shutdown_nodes'] == []
        finally:
            await service.close()

    asyncio.run(scenario())


def test_monitor_waits_for_shutdown_outcome(guests):
    client, pid, nodes = guests
    node = next(n for n in nodes if n['node_type'] == 'qemu')
    entered = threading.Event()

    class Held(Broker):
        async def call(self, *args, **kwargs):
            await super().call(*args, **kwargs)
            self.loop = asyncio.get_running_loop()
            self.gate = asyncio.Event()
            entered.set()
            await self.gate.wait()
            return {}

    broker = Held(nodes)
    app.state.lifecycle.qga = broker
    response = shutdown(client, pid, node)
    assert entered.wait(5)
    try:
        time.sleep(0.15)  # Several monitor polls while stopped but not yet SUCCEEDED.
        assert lab_record(client, pid)['state'] == 'READY'
        assert not lab_record(client, pid).get('shutdown_nodes')
    finally:
        broker.loop.call_soon_threadsafe(broker.gate.set)
    assert wait(client, response)['status'] == 'SUCCEEDED'
    assert lab_record(client, pid)['shutdown_nodes'] == [node['node_id']]


def test_fence_waits_for_accepted_async_shutdown(guests):
    client, pid, nodes = guests
    node = next(n for n in nodes if n['node_type'] == 'qemu')
    entered = threading.Event()
    class Held(Broker):
        async def call(self, *args, **kwargs):
            self.loop = asyncio.get_running_loop()
            self.gate = asyncio.Event()
            entered.set()
            await self.gate.wait()
            return await super().call(*args, **kwargs)
    broker = Held(nodes)
    app.state.lifecycle.qga = broker
    accepted = shutdown(client, pid, node)
    assert entered.wait(5)
    claim, result = in_thread(lambda: client.post('/v1/fence', json={'epoch': 1, 'generation': 2},
                                                 headers={'Authorization': 'Bearer test-token'}))
    try:
        claim.join(0.1)
        assert claim.is_alive()
    finally:
        broker.loop.call_soon_threadsafe(broker.gate.set)
    claim.join(5)
    assert result['response'].status_code == 200
    assert wait(client, accepted)['status'] == 'SUCCEEDED'
    assert shutdown(client, pid, node).status_code == 409  # old owner is fenced
