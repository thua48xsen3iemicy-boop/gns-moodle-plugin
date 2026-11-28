"""Additional serial console lines of a QEMU node (NodeSpec.extra_consoles), CONTRACTS.md 4.2 and 6.4."""

import asyncio
import shlex
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
from starlette.websockets import WebSocketDisconnect

from api import labs, lines, winsize
from api.models import NodeSpec
from api.qga import Priority
from api.winsize import WindowSizes, command
from test_slot import HEADERS, wait
from test_winsize import Guest

CONSOLE = ["lab-slot", "bearer.console-token"]


@pytest.fixture
def serial_dir(monkeypatch):
    # A Unix socket path is limited to 108 bytes: pytest's tmp_path is too long for "<uuid>.<n>.sock".
    path = Path(tempfile.mkdtemp(prefix="sl"))
    (path / "serial").mkdir()  # made by the slot template
    monkeypatch.setattr(lines, "SERIAL_DIR", path / "serial")
    yield path / "serial"
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def image(tmp_path, monkeypatch):
    (tmp_path / "QEMU").mkdir()
    (tmp_path / "QEMU" / "a.qcow2").write_bytes(b"")
    monkeypatch.setattr(labs, "IMAGES", tmp_path)
    monkeypatch.delenv("SLOT_QEMU_CPU", raising=False)


# --- the ticket ---------------------------------------------------------------------------------------------------


def test_spec_takes_one_to_three_lines_and_dumps_them_only_when_set():
    assert NodeSpec(name="W1", kind="qemu", image="a.qcow2", extra_consoles=3).model_dump()["extra_consoles"] == 3
    assert "extra_consoles" not in NodeSpec(name="W1", kind="qemu", image="a.qcow2").model_dump()
    for bad in (0, 4, True, "2", 1.0):
        with pytest.raises(ValueError):
            NodeSpec(name="W1", kind="qemu", image="a.qcow2", extra_consoles=bad)


@pytest.mark.parametrize("extra", [
    {"kind": "iou", "image": "i86bi.bin"},
    {"kind": "vpcs"},
    {"kind": "qemu", "image": "a.qcow2", "console": "vnc"},
    {"kind": "qemu", "image": "a.qcow2", "qga": False},
])
def test_spec_refuses_lines_without_a_telnet_qemu_node_with_an_agent(extra):
    with pytest.raises(ValueError):
        NodeSpec(name="W1", extra_consoles=1, **extra)


def test_every_line_is_a_socket_and_an_isa_serial_port_with_its_index(image):
    spec = NodeSpec(name="W1", kind="qemu", image="a.qcow2", extra_consoles=2)
    words = shlex.split(labs.node_payload(spec, "n1")["properties"]["options"])
    assert "guest_agent" in " ".join(words)  # the agent channel stays
    chardevs = [words[i + 1] for i, w in enumerate(words) if w == "-chardev" and words[i + 1].startswith("socket,id=line")]
    devices = [words[i + 1] for i, w in enumerate(words) if w == "-device" and words[i + 1].startswith("isa-serial")]
    assert chardevs == [f"socket,id=line{n},path={lines.SERIAL_DIR}/n1.{n}.sock,server=on,wait=off" for n in (1, 2)]
    assert devices == ["isa-serial,index=1,chardev=line1", "isa-serial,index=2,chardev=line2"]


def test_no_line_without_the_field(image):
    spec = NodeSpec(name="W1", kind="qemu", image="a.qcow2")
    assert "isa-serial" not in labs.node_payload(spec, "n1")["properties"]["options"]


def ssh_ticket():
    return {"name": "ssh", "project_id": str(uuid4()),
            "nodes": [{"name": "W1", "kind": "qemu", "image": "debian12.qcow2", "extra_consoles": 3}]}


def test_the_feature_follows_the_socket_directory(mock_server, serial_dir):
    client, _, _ = mock_server
    assert "extra-consoles" in client.get("/v1/capabilities", headers=HEADERS).json()["features"]
    serial_dir.rmdir()
    assert "extra-consoles" not in client.get("/v1/capabilities", headers=HEADERS).json()["features"]


def test_a_ticket_with_lines_is_refused_without_the_directory(mock_server, serial_dir):
    client, projects, _ = mock_server
    serial_dir.rmdir()
    response = client.post("/v1/labs/deploy", headers=HEADERS, json=ssh_ticket())
    assert response.status_code == 422 and "extra-consoles" in response.text
    assert projects == {}  # nothing is created


def test_deploy_puts_the_line_sockets_into_the_directory(mock_server, serial_dir):
    client, _, nodes = mock_server
    data = ssh_ticket()
    assert wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))["status"] == "SUCCEEDED"
    node = nodes[data["project_id"]][0]
    assert f"{serial_dir}/{node['node_id']}.3.sock" in node["properties"]["options"]


def test_line_parameter():
    assert [lines.parse_line(v) for v in (None, "0", "1", "2", "3")] == [0, 0, 1, 2, 3]
    for bad in ("4", "-1", "01", "1.0", "", "one", " 1"):
        assert lines.parse_line(bad) is None, bad


# --- window size and login ----------------------------------------------------------------------------------------


def test_window_size_of_a_line_goes_to_its_own_port():
    assert winsize.tty(0) == "/dev/ttyS0" and winsize.tty(2) == "/dev/ttyS2"
    assert command(100, 30, serial="/dev/ttyS2") == "stty -F /dev/ttyS2 rows 30 cols 100"

    async def run():
        guest, sizes = Guest(), WindowSizes()
        sizes.opened("p1", "n1")
        sizes.opened("p1", "n1", 2)
        sizes.resize(guest, "n1", 120, 40)
        sizes.resize(guest, "n1", 90, 20, line=2)
        for _ in range(200):
            if len(guest.calls) == 2 and not any(e.worker for e in sizes.nodes.values()):
                break
            await asyncio.sleep(0.01)
        return sorted(cmd for _, cmd, _ in guest.calls), {n for n, _, _ in guest.calls}, set(sizes.nodes)

    commands, node_ids, keys = asyncio.run(run())
    # Each line redraws once on its first size; line 0 keeps the key it always had.
    assert commands == [
        "stty -F /dev/ttyS0 rows 39 cols 120 && stty -F /dev/ttyS0 rows 40 cols 120",
        "stty -F /dev/ttyS2 rows 19 cols 90 && stty -F /dev/ttyS2 rows 20 cols 90",
    ]
    assert node_ids == {"n1"} and keys == {"n1", "n1/2"}


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setenv("SLOT_WINSIZE_REFRESH", "0.2")


async def settled(gettys):
    for _ in range(200):
        if not any(e.task for e in gettys.lines.values()):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the getty task did not finish")


def test_getty_starts_when_a_line_opens_and_again_on_input_after_the_refresh(quick):
    async def run():
        guest, gettys = Guest(), lines.Gettys()
        gettys.opened(guest, "p1", "n1", 2)
        await settled(gettys)
        gettys.touch(guest, "p1", "n1", 2)  # too soon
        await settled(gettys)
        first = len(guest.calls)
        await asyncio.sleep(0.25)
        gettys.touch(guest, "p1", "n1", 2)
        await settled(gettys)
        return first, guest.calls

    first, calls = asyncio.run(run())
    assert first == 1
    assert [(n, c) for n, c, _ in calls] == [("n1", "systemctl start serial-getty@ttyS2.service")] * 2
    assert calls[0][2] == {"timeout": lines.TIMEOUT, "priority": Priority.COMMAND}


@pytest.mark.parametrize("guest", [Guest(family="windows"), Guest(agent=False)])
def test_getty_is_not_asked_of_a_guest_that_cannot_have_it(guest):
    assert asyncio.run(lines.Gettys.apply(guest, "p1", "n1", 1)) is False
    assert guest.calls == []


def test_a_failed_getty_command_leaves_the_line_alone():
    guest = Guest()
    guest.fail = RuntimeError("agent busy")
    assert asyncio.run(lines.Gettys.apply(guest, "p1", "n1", 1)) is False


# --- the console --------------------------------------------------------------------------------------------------


class LineServer:
    """The QEMU end of a line: one client at a time, a login prompt, echo of what comes in."""

    def __init__(self, path):
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen(4)
        self.connections = 0
        self.active = False  # a client is connected
        self.received = b""
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            self.connections += 1
            self.active = True
            with conn:
                conn.sendall(b"login: ")
                while True:
                    data = conn.recv(1024)
                    if not data:
                        break
                    self.received += data
                    conn.sendall(b"got:" + data)
            self.active = False

    def close(self):
        self.server.close()


@pytest.fixture
def line_node(mock_server, monkeypatch, serial_dir):
    monkeypatch.setenv("SLOT_CONSOLE_TOKEN", "console-token")
    client, projects, nodes = mock_server
    pid, nid = str(uuid4()), str(uuid4())
    projects[pid] = {"project_id": pid, "name": "ssh", "status": "opened"}
    nodes[pid] = [{"node_id": nid, "name": "W1", "node_type": "qemu", "status": "started",
                   "console": 5001, "console_type": "telnet"}]
    server = LineServer(lines.socket_path(nid, 1))
    yield client, pid, nid, nodes, server
    server.close()


def leave(ws, done):
    """Closes a console and waits until the slot has let it go.

    Leaving the `with` block alone would cancel the handler right after the disconnect (Starlette's TestClient), while it
    may still be closing: an occasional CancelledError that is the test client's, not the slot's.
    """
    ws.close()
    for _ in range(300):
        if done():
            return
        time.sleep(0.01)
    raise AssertionError("the console was not let go")


def writers(nid, line=1):
    from main import app

    entry = app.state.console_history.nodes.get(f"{nid}/{line}")
    return len(entry.writers) if entry else 0


def receive_until(ws, end):
    out = b""
    while not out.endswith(end):
        out += ws.receive_bytes()
    return out


def test_consoles_of_a_line_share_one_connection_and_see_the_same_output(line_node):
    client, pid, nid, _, server = line_node
    with client.websocket_connect(f"/v1/console/{pid}/{nid}?line=1", subprotocols=CONSOLE) as first:
        assert first.accepted_subprotocol == "lab-slot"
        assert receive_until(first, b"login: ") == b"login: "
        with client.websocket_connect(f"/v1/console/{pid}/{nid}?line=1&history=1", subprotocols=CONSOLE) as second:
            assert second.receive_bytes() == b"login: "  # the history of the line
            first.send_text("root\r")
            assert receive_until(first, b"got:root\r") == b"got:root\r"
            assert receive_until(second, b"got:root\r") == b"got:root\r"
            second.send_bytes(b"\xff\r")  # no Telnet on a line: 0xFF is not doubled
            assert receive_until(first, b"got:\xff\r") == b"got:\xff\r"
            leave(second, lambda: writers(nid) == 1)
        assert server.active  # the first console keeps the line
        leave(first, lambda: not server.active)
    assert server.connections == 1
    assert server.received == b"root\r\xff\r"


def test_lab_slot_2_on_a_line_takes_binary_input_and_control_text(line_node):
    client, pid, nid, _, server = line_node
    with client.websocket_connect(f"/v1/console/{pid}/{nid}?line=1",
                                  subprotocols=["lab-slot.2", "lab-slot", "bearer.console-token"]) as ws:
        assert ws.accepted_subprotocol == "lab-slot.2"
        receive_until(ws, b"login: ")
        ws.send_text('{"resize": {"cols": 120, "rows": 40}}')  # control: never reaches the line
        ws.send_bytes(b"ls\r")
        assert receive_until(ws, b"got:ls\r") == b"got:ls\r"
        leave(ws, lambda: not server.active)
    assert server.received == b"ls\r"


@pytest.mark.parametrize("query", ["?line=2", "?line=4", "?line=x"])
def test_a_line_the_node_does_not_have_is_refused(line_node, query):
    client, pid, nid, _, _ = line_node
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/v1/console/{pid}/{nid}{query}", subprotocols=CONSOLE):
            pass
    assert exc.value.code == 1008


@pytest.mark.parametrize("change", [{"status": "stopped"}, {"console_type": "vnc"}, {"node_type": "iou"}])
def test_a_line_needs_a_running_qemu_node_with_a_text_console(line_node, change):
    client, pid, nid, nodes, _ = line_node
    nodes[pid][0].update(change)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/v1/console/{pid}/{nid}?line=1", subprotocols=CONSOLE):
            pass
    assert exc.value.code == 1008


def test_a_line_still_needs_the_console_token(line_node):
    client, pid, nid, _, server = line_node
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/v1/console/{pid}/{nid}?line=1", subprotocols=["lab-slot", "bearer.wrong"]):
            pass
    assert exc.value.code == 1008
    assert server.connections == 0
