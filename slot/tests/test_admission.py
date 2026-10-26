"""Long guest commands do not hold command_lock, yet ownership never moves under them."""

import asyncio
import threading
from contextlib import suppress
from types import SimpleNamespace

import pytest
from api.lifecycle import Admission
from main import LONG_COMMAND, app
from test_slot import HEADERS, payload, wait

NEW_OWNER = dict(HEADERS, **{"X-Lab-Epoch": "1", "X-Lab-Generation": "2"})


class SlowBroker:
    """exec() blocks inside the app's event loop until the test releases it."""

    def __init__(self):
        self.started = threading.Event()
        self.finished = threading.Event()
        self.commands = []
        self.forgotten = []
        self.hold = True
        self.loop = self.gate = None

    async def exec(self, node_id, command, **options):
        self.commands.append(command)
        if self.hold:
            self.loop, self.gate = asyncio.get_running_loop(), asyncio.Event()
            self.started.set()
            await self.gate.wait()
        self.finished.set()
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    async def call(self, node_id, command, args=None, **options):
        return {}

    async def forget(self, node_id):
        self.forgotten.append(node_id)

    async def close(self):
        pass

    def release(self):
        self.loop.call_soon_threadsafe(self.gate.set)


def in_thread(call):
    box = {}
    thread = threading.Thread(target=lambda: box.update(response=call()))
    thread.start()
    return thread, box


@pytest.fixture
def running_exec(mock_server):
    """A deployed lab with one guest command that is running and held."""
    client, _projects, nodes = mock_server
    data = payload()
    pid = data["project_id"]
    assert (
        wait(client, client.post("/v1/labs/deploy", json=data, headers=HEADERS))[
            "status"
        ]
        == "SUCCEEDED"
    )
    node_id = nodes[pid][0]["node_id"]
    broker = SlowBroker()
    app.state.lifecycle.qga = broker
    url = f"/v1/labs/{pid}/nodes/{node_id}/qga/exec"
    thread, box = in_thread(
        lambda: client.post(
            url, json={"command": "long", "timeout_seconds": 120}, headers=HEADERS
        )
    )
    assert broker.started.wait(5), "the command never reached the broker"
    yield client, pid, node_id, url, broker, thread, box, nodes
    if broker.gate is not None:
        broker.release()
    thread.join(10)


def test_stop_passes_while_a_long_exec_runs_and_forgets_its_channel(running_exec):
    client, pid, node_id, _url, broker, thread, box, _nodes = running_exec
    stop = client.post(f"/v1/labs/{pid}/stop", headers=HEADERS)
    assert stop.status_code == 202, stop.text
    assert thread.is_alive() and not broker.finished.is_set()
    assert wait(client, stop)["status"] == "SUCCEEDED"
    assert broker.forgotten == [node_id]
    broker.release()
    thread.join(5)
    assert box["response"].status_code == 200


def test_fence_waits_for_a_command_started_before_it(running_exec):
    client, _pid, _node, url, broker, thread, box, _nodes = running_exec
    claim, claimed = in_thread(
        lambda: client.post(
            "/v1/fence",
            json={"epoch": 1, "generation": 2},
            headers={"Authorization": "Bearer test-token"},
        )
    )
    claim.join(0.5)
    assert claim.is_alive(), "ownership moved while the command was still running"
    assert not broker.finished.is_set()
    broker.release()
    claim.join(5)
    thread.join(5)
    assert claimed["response"].status_code == 200
    assert box["response"].status_code == 200

    # The previous owner is refused after the fence; the new one is served.
    stale = client.post(url, json={"command": "late"}, headers=HEADERS)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STALE_OWNER"
    assert broker.commands == ["long"]
    broker.hold = False
    fresh = client.post(url, json={"command": "fresh"}, headers=NEW_OWNER)
    assert fresh.status_code == 200, fresh.text
    assert broker.commands == ["long", "fresh"]


def test_rejected_command_leaves_nothing_for_a_claim_to_wait_on(running_exec):
    client, pid, node_id, url, broker, thread, box, nodes = running_exec
    broker.release()
    thread.join(5)
    nodes[pid][0]["status"] = "stopped"
    refused = client.post(url, json={"command": "x"}, headers=HEADERS)
    assert refused.status_code == 409
    assert app.state.lifecycle.commands == set()
    claimed = client.post(
        "/v1/fence",
        json={"epoch": 1, "generation": 2},
        headers={"Authorization": "Bearer test-token"},
    )
    assert claimed.status_code == 200


def test_admitted_work_survives_the_caller_and_is_released():
    async def scenario():
        owner = SimpleNamespace(commands=set())
        admission = Admission(owner)
        gate, ran = asyncio.Event(), []

        async def work():
            await gate.wait()
            ran.append(1)
            return "done"

        caller = asyncio.create_task(admission.run(work()))
        await asyncio.sleep(0.01)
        caller.cancel()  # the HTTP client went away
        with suppress(asyncio.CancelledError):
            await caller
        assert owner.commands == {admission.future}  # still counted as running
        gate.set()
        await asyncio.wait_for(admission.future, 1)
        await asyncio.sleep(0)
        assert ran == [1] and owner.commands == set()

        failing = Admission(owner)

        async def broken():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await failing.run(broken())
        await asyncio.sleep(0)
        assert failing.future.done() and owner.commands == set()

        unused = Admission(owner)  # the route gave up before starting any work
        unused.release()
        await asyncio.sleep(0)
        assert owner.commands == set()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "path, long",
    [
        ("/v1/labs/p/nodes/n/qga/exec", True),
        ("/v1/labs/p/nodes/n/serial/enable", True),
        ("/v1/labs/p/nodes/n/wipe", False),
        ("/v1/labs/p/stop", False),
        ("/v1/labs/p/resume", False),
        ("/v1/labs/deploy", False),
        ("/v1/fence", False),
        ("/v1/labs/p/nodes/n/qga/exec/extra", False),
        ("/x/v1/labs/p/nodes/n/qga/exec", False),
    ],
)
def test_only_guest_commands_are_admitted_outside_the_lock(path, long):
    assert bool(LONG_COMMAND.fullmatch(path)) is long
