"""Window size of the browser terminal on the serial console of a Linux guest (CONTRACTS.md §6.1)."""

import asyncio
import socket
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from api import winsize
from api.qga import Priority, QgaTimeout
from api.winsize import WindowSizes, command, parse_control


class Guest:
    """A lifecycle with a guest agent: records the size commands, can be slow, fail or be another family."""

    def __init__(self, family="posix", agent=True):
        self.family = family
        self.agent = agent
        self.calls = []
        self.fail = None
        self.hold = None  # an asyncio.Event the command waits for
        self.qga = self

    def has_guest_agent(self, pid, node_id):
        return self.agent

    async def guest_family(self, node_id, priority):
        assert priority == Priority.COMMAND
        return self.family

    async def exec(self, node_id, cmd, **options):
        self.calls.append((node_id, cmd, options))
        if self.hold is not None:
            await self.hold.wait()
        if self.fail is not None:
            raise self.fail
        return {"exit_code": 0, "stdout": "", "stderr": ""}


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setenv("SLOT_WINSIZE_DELAY", "0.05")
    monkeypatch.setenv("SLOT_WINSIZE_REFRESH", "0.3")


async def settle(sizes, node_id="n1"):
    """Until the node's worker is done."""
    for _ in range(200):
        entry = sizes.nodes.get(node_id)
        if entry is None or entry.worker is None:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the size worker did not finish")


def test_parse_control():
    assert parse_control('{"resize": {"cols": 120, "rows": 40}}') == (120, 40)
    assert parse_control('{"resize": {"cols": 1000, "rows": 1}, "other": 1}') == (1000, 1)
    for bad in ('{"resize": {"cols": 0, "rows": 40}}', '{"resize": {"cols": 120, "rows": 1001}}',
                '{"resize": {"cols": true, "rows": 40}}', '{"resize": {"cols": "120", "rows": 40}}',
                '{"resize": {"cols": 80.0, "rows": 24}}', '{"resize": [120, 40]}', '{"cols": 120, "rows": 40}',
                '[1, 2]', 'not json', '', 'null'):
        assert parse_control(bad) is None, bad


def test_command_names_the_serial_console():
    assert command(120, 40) == "stty -F /dev/ttyS0 rows 40 cols 120"


def test_a_redraw_changes_the_size_twice():
    assert command(120, 40, redraw=True) == \
        "stty -F /dev/ttyS0 rows 39 cols 120 && stty -F /dev/ttyS0 rows 40 cols 120"
    assert command(80, 1, redraw=True) == "stty -F /dev/ttyS0 rows 2 cols 80 && stty -F /dev/ttyS0 rows 1 cols 80"


def test_a_new_console_redraws_the_screen_once():
    async def run():
        guest, sizes = Guest(), WindowSizes()
        sizes.opened("p1", "n1")
        sizes.resize(guest, "n1", 120, 40)
        await settle(sizes)
        sizes.resize(guest, "n1", 100, 30)
        await settle(sizes)
        # A reloaded page: the same size from a new console still makes nano redraw.
        sizes.closed("n1")
        sizes.opened("p1", "n1")
        sizes.resize(guest, "n1", 100, 30)
        await settle(sizes)
        assert [c[1] for c in guest.calls] == [command(120, 40, True), command(100, 30), command(100, 30, True)]

    asyncio.run(run())


def test_quick_sizes_are_one_command_with_the_last_size():
    async def run():
        guest, sizes = Guest(), WindowSizes()
        sizes.opened("p1", "n1")
        for cols in (100, 110, 120):
            sizes.resize(guest, "n1", cols, 40)
            await asyncio.sleep(0.01)
        assert guest.calls == []  # the quiet pause is still running
        await settle(sizes)
        assert guest.calls == [("n1", command(120, 40, redraw=True),
                                {"timeout": winsize.TIMEOUT, "priority": Priority.COMMAND})]

    asyncio.run(run())


def test_a_size_during_a_command_waits_for_it():
    async def run():
        guest, sizes = Guest(), WindowSizes()
        guest.hold = asyncio.Event()
        sizes.opened("p1", "n1")
        sizes.resize(guest, "n1", 100, 30)
        for _ in range(100):
            if guest.calls:
                break
            await asyncio.sleep(0.01)
        sizes.resize(guest, "n1", 90, 20)
        sizes.resize(guest, "n1", 80, 20)
        await asyncio.sleep(0.1)
        assert len(guest.calls) == 1  # one command of the node in the queue at a time
        guest.hold.set()
        await settle(sizes)
        assert [c[1] for c in guest.calls] == [command(100, 30, redraw=True), command(80, 20)]

    asyncio.run(run())


def test_input_reapplies_the_size_once_per_refresh():
    async def run():
        guest, sizes = Guest(), WindowSizes()
        sizes.opened("p1", "n1")
        sizes.touch(guest, "n1")  # no size yet: nothing to repeat
        await asyncio.sleep(0.1)
        assert guest.calls == []
        sizes.resize(guest, "n1", 120, 40)
        await settle(sizes)
        sizes.touch(guest, "n1")  # just applied
        await settle(sizes)
        assert len(guest.calls) == 1
        await asyncio.sleep(0.35)
        sizes.touch(guest, "n1")  # a logout may have reset the size: repeated at once, without the pause
        sizes.touch(guest, "n1")
        await asyncio.sleep(0.02)
        await settle(sizes)
        assert [c[1] for c in guest.calls] == [command(120, 40, redraw=True), command(120, 40)]

    asyncio.run(run())


@pytest.mark.parametrize("guest", [Guest(family="windows"), Guest(family="routeros"), Guest(agent=False)],
                         ids=["windows", "routeros", "no-agent"])
def test_only_a_linux_guest_with_an_agent_gets_the_size(guest):
    async def run():
        sizes = WindowSizes()
        sizes.opened("p1", "n1")
        sizes.resize(guest, "n1", 120, 40)
        await settle(sizes)
        assert guest.calls == []

    asyncio.run(run())


@pytest.mark.parametrize("error", [QgaTimeout("busy"), OSError("gone"), RuntimeError("closed")])
def test_a_failure_is_swallowed_and_the_next_size_is_tried(error):
    async def run():
        guest, sizes = Guest(), WindowSizes()
        guest.fail = error
        sizes.opened("p1", "n1")
        sizes.resize(guest, "n1", 120, 40)
        await settle(sizes)
        guest.fail = None
        sizes.resize(guest, "n1", 100, 30)
        await settle(sizes)
        # The redraw failed with the first command, so the next one still makes it.
        assert [c[1] for c in guest.calls] == [command(120, 40, redraw=True), command(100, 30, redraw=True)]

    asyncio.run(run())


def test_the_node_is_forgotten_when_its_last_console_closes():
    async def run():
        guest, sizes = Guest(), WindowSizes()
        sizes.opened("p1", "n1")
        sizes.opened("p1", "n1")
        sizes.resize(guest, "n1", 120, 40)
        sizes.closed("n1")
        assert "n1" in sizes.nodes
        sizes.closed("n1")
        assert "n1" in sizes.nodes  # the size still being applied keeps it
        await settle(sizes)
        assert "n1" not in sizes.nodes and len(guest.calls) == 1
        sizes.resize(guest, "n1", 80, 24)  # no console: nothing to apply
        sizes.touch(guest, "n1")
        await asyncio.sleep(0.1)
        assert len(guest.calls) == 1 and "n1" not in sizes.nodes

    asyncio.run(run())


# ---------------------------------------------------------------- the console WebSocket

@pytest.fixture
def telnet_port():
    server = socket.create_server(("127.0.0.1", 0))
    received = []

    def serve():
        conn, _ = server.accept()
        with conn:
            conn.sendall(b"\r\nlogin: ")
            data = b""
            while not data.endswith(b"root\r"):
                chunk = conn.recv(1024)
                if not chunk:
                    break
                data += chunk
            received.append(data)
            conn.sendall(b"ok\r")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield server.getsockname()[1], received
    server.close()
    thread.join(timeout=2)


def add_node(nodes, projects, port, node_type, console_type="telnet"):
    pid, nid = str(uuid4()), str(uuid4())
    projects[pid] = {"project_id": pid, "name": "console", "status": "opened"}
    nodes[pid] = [{"node_id": nid, "name": "PC1", "node_type": node_type, "status": "started",
                   "console": port, "console_type": console_type}]
    return pid, nid


def login(ws):
    out = b""
    while not out.endswith(b"login: "):
        out += ws.receive_bytes()
    ws.send_text('{"resize": {"cols": 120, "rows": 40}}')
    ws.send_text("not json")
    ws.send_text('{"resize": {"cols": 0, "rows": 40}}')
    ws.send_bytes(b"ro")
    ws.send_bytes(b"ot\r")
    out = b""
    while not out.endswith(b"ok\r"):
        out += ws.receive_bytes()


@pytest.mark.parametrize("node_type", ["qemu", "iou"])
def test_lab_slot_2_takes_binary_input_and_control_text(mock_server, monkeypatch, telnet_port, node_type):
    monkeypatch.setenv("SLOT_CONSOLE_TOKEN", "console-token")
    applied = []

    async def apply(lifecycle, pid, node_id, cols, rows, redraw=False, serial=winsize.SERIAL):
        applied.append((pid, node_id, cols, rows, redraw))
        return True

    monkeypatch.setattr(WindowSizes, "apply", staticmethod(apply))
    client, projects, nodes = mock_server
    port, received = telnet_port
    pid, nid = add_node(nodes, projects, port, node_type)
    with client.websocket_connect(f"/v1/console/{pid}/{nid}",
                                  subprotocols=["lab-slot.2", "lab-slot", "bearer.console-token"]) as ws:
        assert ws.accepted_subprotocol == "lab-slot.2"
        login(ws)
        for _ in range(100):
            if applied or node_type != "qemu":
                break
            threading.Event().wait(0.01)
    # Control frames never reach the console, input does.
    assert received == [b"root\r"]
    # Only a QEMU node is asked; the guest agent and the family are checked in `apply`.
    assert applied == ([(pid, nid, 120, 40, True)] if node_type == "qemu" else [])


@pytest.mark.parametrize("console_type,offered", [("telnet", ["lab-slot"]), ("vnc", ["lab-slot.2", "lab-slot"])],
                         ids=["old-gateway", "vnc"])
def test_lab_slot_is_kept_for_an_old_gateway_and_vnc(mock_server, monkeypatch, console_type, offered):
    monkeypatch.setenv("SLOT_CONSOLE_TOKEN", "console-token")
    client, projects, nodes = mock_server
    server = socket.create_server(("127.0.0.1", 0))  # an ephemeral port is also a VNC port (5900 and up)
    pid, nid = add_node(nodes, projects, server.getsockname()[1], "qemu", console_type)
    try:
        with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=[*offered, "bearer.console-token"]) as ws:
            assert ws.accepted_subprotocol == "lab-slot"
    finally:
        server.close()


def test_lab_slot_2_alone_is_a_console_protocol(monkeypatch):
    monkeypatch.setenv("SLOT_API_TOKEN", "api-token")
    from api.auth import websocket_token_ok

    assert websocket_token_ok("lab-slot.2, bearer.api-token")
    assert not websocket_token_ok("lab-slot.3, bearer.api-token")
