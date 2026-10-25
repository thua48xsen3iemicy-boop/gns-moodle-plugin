"""QGA broker: serialization, priorities, resync and back-off.

FakeAgent imitates what QEMU and qemu-ga do on the host side of the socket: the chardev
serves one client at a time (a second one waits until the first disconnects) and the
agent answers one request at a time.
"""

import asyncio
import base64
import json
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path

import pytest
from api import qga
from api.qga import (
    Priority,
    QgaBroker,
    QgaBusy,
    QgaCommandError,
    QgaError,
    QgaTimeout,
    QgaUnavailable,
)
from main import app
from test_slot import HEADERS, payload, wait


def line(obj):
    return json.dumps(obj).encode() + b"\n"


class FakeAgent:
    def __init__(self, path):
        self.path = path
        self.connections = 0
        self.disconnects = 0
        self.log = []  # (command, label) in arrival order, resync requests included
        self.hang = False  # read requests but never answer
        self.hold = None  # asyncio.Event: answers wait until it is set
        self.stale_on_sync = False  # leftovers of a dead client precede the sync answer
        self.polls = {}  # shell command -> status polls before it reports "exited"
        self._gate = asyncio.Lock()
        self._pid = 100
        self._execs = {}

    async def start(self):
        self.server = await asyncio.start_unix_server(self._client, path=self.path)

    async def stop(self):
        if self.hold is not None:
            self.hold.set()
        self.server.close()
        await self.server.wait_closed()

    def commands(self):
        return [name for name, _ in self.log if name != "guest-sync-delimited"]

    def execs(self, shell):
        return self.log.count(("guest-exec", shell))

    async def _client(self, reader, writer):
        async with self._gate:  # like the chardev: one client at a time
            self.connections += 1
            try:
                while True:
                    raw = await reader.readline()
                    if not raw:
                        return
                    request = json.loads(raw.lstrip(b"\xff"))
                    name, args = request["execute"], request.get("arguments") or {}
                    self.log.append((name, args["arg"][-1] if "arg" in args else None))
                    if self.hang:
                        await reader.read()  # until the client gives up
                        return
                    if self.hold is not None:
                        await self.hold.wait()
                    writer.write(self._answer(name, args))
                    await writer.drain()
            finally:
                self.disconnects += 1
                writer.close()

    def _answer(self, name, args):
        if name == "guest-sync-delimited":
            reply = b"\xff" + line({"return": args["id"]})
            if self.stale_on_sync:
                reply = line({"return": {"pid": 99}}) + b"not json\n" + reply
            return reply
        if name == "guest-exec":
            shell = args["arg"][-1]
            if shell == "boom":
                return line({"error": {"class": "GenericError", "desc": "boom"}})
            self._pid += 1
            self._execs[self._pid] = [shell, self.polls.get(shell, 1)]
            return line({"return": {"pid": self._pid}})
        if name == "guest-exec-status":
            entry = self._execs[args["pid"]]
            entry[1] -= 1
            if entry[1] > 0:
                return line({"return": {"exited": False}})
            out = base64.b64encode(("ok:" + entry[0]).encode()).decode()
            return line(
                {"return": {"exited": True, "exitcode": 0, "out-data": out}}
            )
        if name == "no-such-command":
            return line({"error": {"class": "CommandNotFound", "desc": name}})
        return line({"return": {}})


@pytest.fixture
def sockdir():
    # AF_UNIX paths are short; keep them out of long pytest temp directories.
    path = Path(tempfile.mkdtemp(prefix="qga-", dir="/tmp"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch):
    monkeypatch.setattr(qga, "POLL_FIRST", 0.01)
    monkeypatch.setattr(qga, "POLL_MAX", 0.02)


def run(scenario, sockdir, **options):
    async def main():
        agent = FakeAgent(str(sockdir / "n1.sock"))
        await agent.start()
        broker = QgaBroker(lambda node_id: sockdir / f"{node_id}.sock", **options)
        try:
            await scenario(agent, broker)
        finally:
            await broker.close()
            await agent.stop()

    asyncio.run(asyncio.wait_for(main(), 20))


async def until(predicate, seconds=3.0):
    end = asyncio.get_running_loop().time() + seconds
    while not predicate():
        assert asyncio.get_running_loop().time() < end, "condition not reached"
        await asyncio.sleep(0.005)


def test_exec_returns_output_and_exit_code(sockdir):
    async def scenario(agent, broker):
        result = await broker.exec("n1", "echo hi")
        assert result == {"exit_code": 0, "stdout": "ok:echo hi", "stderr": ""}

    run(scenario, sockdir)


def test_concurrent_callers_share_one_connection(sockdir):
    async def scenario(agent, broker):
        names = [f"c{i}" for i in range(5)]
        results = await asyncio.gather(*(broker.exec("n1", n) for n in names))
        assert [r["stdout"] for r in results] == ["ok:" + n for n in names]
        assert agent.connections == 1

    run(scenario, sockdir)


def test_long_command_does_not_block_other_callers(sockdir):
    async def scenario(agent, broker):
        agent.polls["slow"] = 25
        slow = asyncio.create_task(broker.exec("n1", "slow", timeout=10))
        await until(lambda: ("guest-exec", "slow") in agent.log)
        fast = await broker.exec("n1", "fast")
        assert fast["stdout"] == "ok:fast"
        assert not slow.done()
        assert (await slow)["stdout"] == "ok:slow"

    run(scenario, sockdir)


def test_priority_decides_who_is_served_next(sockdir):
    async def scenario(agent, broker):
        agent.hold = asyncio.Event()
        first = asyncio.create_task(broker.call("n1", "m-first"))
        await until(lambda: agent.log)  # the first exchange is in flight
        queued = [
            asyncio.create_task(broker.call("n1", name, priority=priority))
            for name, priority in (
                ("m-metrics", Priority.METRICS),
                ("m-command", Priority.COMMAND),
                ("m-lifecycle", Priority.LIFECYCLE),
            )
        ]
        await until(lambda: broker.state("n1")["queue_depth"] == 3)
        agent.hold.set()
        await asyncio.gather(first, *queued)
        assert agent.commands() == ["m-first", "m-lifecycle", "m-command", "m-metrics"]

    run(scenario, sockdir)


def test_caller_that_does_not_wait_gets_busy(sockdir):
    async def scenario(agent, broker):
        agent.hold = asyncio.Event()
        first = asyncio.create_task(broker.call("n1", "m-first"))
        await until(lambda: agent.log)
        with pytest.raises(QgaBusy):
            await broker.call("n1", "m-skipped", wait=False)
        agent.hold.set()
        await first
        assert "m-skipped" not in agent.commands()
        await broker.call("n1", "m-idle", wait=False)  # an idle channel accepts it

    run(scenario, sockdir)


def test_stale_data_before_sync_is_discarded(sockdir):
    async def scenario(agent, broker):
        agent.stale_on_sync = True
        assert (await broker.exec("n1", "x"))["stdout"] == "ok:x"

    run(scenario, sockdir)


def test_timeout_drops_the_connection_and_the_next_call_resyncs(sockdir):
    async def scenario(agent, broker):
        await broker.call("n1", "m-ok1")
        agent.hang = True
        with pytest.raises(QgaTimeout):
            await broker.call("n1", "m-hang", timeout=0.05)
        assert broker.state("n1")["connected"] is False
        agent.hang = False
        await broker.call("n1", "m-ok2")
        assert agent.connections == 2
        assert [n for n, _ in agent.log].count("guest-sync-delimited") == 2

    run(scenario, sockdir)


def test_metrics_are_refused_while_the_channel_backs_off(sockdir):
    async def scenario(agent, broker):
        agent.hang = True
        for _ in range(2):
            with pytest.raises(QgaTimeout):
                await broker.call("n1", "m-x", timeout=0.05)
        state = broker.state("n1")
        assert state["status"] == "unreachable" and state["retry_in"] > 0
        seen = len(agent.log)
        with pytest.raises(QgaUnavailable):
            await broker.call("n1", "m-x", priority=Priority.METRICS)
        assert len(agent.log) == seen  # refused without touching the socket
        agent.hang = False
        await broker.call("n1", "m-y")  # a command still gets through and ends the back-off
        state = broker.state("n1")
        assert state["status"] == "idle" and state["consecutive_failures"] == 0
        await broker.call("n1", "m-z", priority=Priority.METRICS)

    run(scenario, sockdir, breaker_threshold=2, breaker_base=5.0)


def test_queued_metrics_are_refused_once_the_channel_fails_ahead_of_them(sockdir):
    async def scenario(agent, broker):
        agent.hang = True
        failing = asyncio.create_task(broker.call("n1", "m-x", timeout=0.1))
        await until(lambda: agent.log)  # in flight; the channel is not backing off yet
        sampled = asyncio.create_task(
            broker.call("n1", "m-sample", priority=Priority.METRICS)
        )
        await until(lambda: broker.state("n1")["queue_depth"] == 1)
        with pytest.raises(QgaTimeout):
            await failing
        with pytest.raises(QgaUnavailable):
            await sampled
        assert agent.commands() == []  # only the failed resync reached the agent
        assert [n for n, _ in agent.log] == ["guest-sync-delimited"]

    run(scenario, sockdir, breaker_threshold=1, breaker_base=5.0)


def test_unreachable_socket_is_reported_not_raised_raw(sockdir):
    async def scenario(agent, broker):
        with pytest.raises(QgaUnavailable):
            await broker.call("n2", "guest-ping")  # no such socket
        assert broker.state("n2")["last_error"] == "QGA_UNREACHABLE"

    run(scenario, sockdir)


def test_guest_error_does_not_count_against_the_channel(sockdir):
    async def scenario(agent, broker):
        with pytest.raises(QgaCommandError):
            await broker.exec("n1", "boom")
        with pytest.raises(QgaCommandError):
            await broker.call("n1", "no-such-command")
        assert broker.state("n1")["consecutive_failures"] == 0
        assert (await broker.exec("n1", "fine"))["exit_code"] == 0

    run(scenario, sockdir)


def test_exec_timeout_is_a_timeout_but_not_a_channel_failure(sockdir):
    async def scenario(agent, broker):
        agent.polls["never"] = 10**6
        with pytest.raises(QgaTimeout) as caught:
            await broker.exec("n1", "never", timeout=0.2)
        assert isinstance(caught.value, TimeoutError)
        assert isinstance(caught.value, RuntimeError)
        assert broker.state("n1")["consecutive_failures"] == 0

    run(scenario, sockdir)


def test_max_age_shares_one_execution(sockdir):
    async def scenario(agent, broker):
        agent.polls["ro"] = 3
        results = await asyncio.gather(
            *(broker.exec("n1", "ro", max_age=5) for _ in range(4))
        )
        assert {r["stdout"] for r in results} == {"ok:ro"}
        assert agent.execs("ro") == 1
        again = await broker.exec("n1", "ro", max_age=5)
        assert agent.execs("ro") == 1
        again["stdout"] = "mutated"  # callers get copies
        assert (await broker.exec("n1", "ro", max_age=5))["stdout"] == "ok:ro"
        await broker.exec("n1", "ro")  # without max_age it always runs
        assert agent.execs("ro") == 2

    run(scenario, sockdir)


def test_cancelled_caller_is_not_sent_to_the_guest(sockdir):
    async def scenario(agent, broker):
        agent.hold = asyncio.Event()
        first = asyncio.create_task(broker.call("n1", "m-first"))
        await until(lambda: agent.log)
        queued = asyncio.create_task(broker.call("n1", "m-queued"))
        await until(lambda: broker.state("n1")["queue_depth"] == 1)
        queued.cancel()
        with suppress(asyncio.CancelledError):
            await queued
        agent.hold.set()
        await first
        await broker.call("n1", "m-after")
        assert agent.commands() == ["m-first", "m-after"]

    run(scenario, sockdir)


def test_caller_cancelled_mid_exchange_does_not_desync_the_channel(sockdir):
    async def scenario(agent, broker):
        await broker.call("n1", "m-prime")
        agent.hold = asyncio.Event()
        abandoned = asyncio.create_task(broker.call("n1", "m-abandoned"))
        await until(lambda: ("m-abandoned", None) in agent.log)
        abandoned.cancel()
        with suppress(asyncio.CancelledError):
            await abandoned
        agent.hold.set()
        await broker.call("n1", "m-next")
        assert agent.connections == 1  # the late answer was consumed, not left behind
        assert agent.commands() == ["m-prime", "m-abandoned", "m-next"]

    run(scenario, sockdir)


def test_idle_channel_releases_the_socket(sockdir):
    async def scenario(agent, broker):
        await broker.call("n1", "m-a")
        await until(lambda: agent.disconnects == 1)
        assert broker.state("n1")["connected"] is False
        await broker.call("n1", "m-b")
        assert agent.connections == 2

    run(scenario, sockdir, idle_seconds=0.1)


def test_state_reports_busy_and_idle(sockdir):
    async def scenario(agent, broker):
        assert broker.state("n1")["status"] == "idle"
        agent.hold = asyncio.Event()
        first = asyncio.create_task(broker.call("n1", "m-first"))
        await until(lambda: agent.log)
        second = asyncio.create_task(broker.call("n1", "m-second"))
        await until(lambda: broker.state("n1")["queue_depth"] == 1)
        state = broker.state("n1")
        assert state["status"] == "busy" and state["queue_depth"] == 1
        assert state["in_flight"] is not None
        agent.hold.set()
        await asyncio.gather(first, second)
        state = broker.state("n1")
        assert state["status"] == "idle" and state["queue_depth"] == 0
        assert state["last_ok_at"] is not None and state["latency_ms"] is not None
        assert set(broker.states()) == {"n1"}

    run(scenario, sockdir)


def test_close_fails_pending_requests(sockdir):
    async def scenario(agent, broker):
        agent.hold = asyncio.Event()
        first = asyncio.create_task(broker.call("n1", "m-first"))
        await until(lambda: agent.log)
        second = asyncio.create_task(broker.call("n1", "m-second"))
        await until(lambda: broker.state("n1")["queue_depth"] == 1)
        await broker.close()
        for task in (first, second):
            with pytest.raises(QgaError):
                await task
        with pytest.raises(QgaError):
            await broker.call("n1", "m-late")

    run(scenario, sockdir)


def test_forget_drops_the_node(sockdir):
    async def scenario(agent, broker):
        await broker.exec("n1", "ro", max_age=60)
        await broker.forget("n1")
        assert broker.states() == {}
        await broker.exec("n1", "ro", max_age=60)  # cache gone: runs again
        assert agent.execs("ro") == 2

    run(scenario, sockdir)


def test_node_id_cannot_escape_the_socket_directory(sockdir):
    async def scenario(agent, broker):
        for bad in ("", ".", "..", "../n1", "a/b"):
            with pytest.raises(ValueError):
                await broker.call(bad, "guest-ping")

    run(scenario, sockdir)


def test_routes_go_through_the_broker(mock_server):
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

    class Broker:
        seen = []
        failure = None

        async def call(self, node, command, args=None, **options):
            self.seen.append(("call", node, command, options["priority"]))
            return {}

        async def exec(self, node, command, **options):
            self.seen.append(("exec", node, command, options["priority"]))
            if self.failure:
                raise self.failure
            return {"exit_code": 0, "stdout": "up", "stderr": ""}

        async def close(self):
            pass

    broker = Broker()
    app.state.lifecycle.qga = broker
    url = f"/v1/labs/{pid}/nodes/{node_id}"

    reply = client.post(
        url + "/qga/exec",
        json={"command": "uptime", "timeout_seconds": 5},
        headers=HEADERS,
    )
    assert reply.status_code == 200, reply.text
    assert reply.json() == {"exit_code": 0, "stdout": "up", "stderr": ""}
    assert broker.seen[-1] == ("exec", node_id, "uptime", Priority.COMMAND)

    broker.failure = QgaTimeout("late")
    reply = client.post(
        url + "/qga/exec", json={"command": "uptime"}, headers=HEADERS
    )
    assert reply.status_code == 502
    assert reply.json()["detail"] == "QGA error: QgaTimeout: late"

    broker.failure = None
    reply = client.post(url + "/serial/enable", headers=HEADERS)
    assert reply.status_code == 200, reply.text
    assert reply.json()["result"] == "serial_getty_enabled"
    assert [kind for kind, *_ in broker.seen[-2:]] == ["call", "exec"]
