"""Console WebSocket as used by the console gateway: console-only token, clean output."""

import socket
import threading
from uuid import uuid4

import pytest
from starlette.websockets import WebSocketDisconnect


@pytest.fixture
def telnet_port():
    server = socket.create_server(("127.0.0.1", 0))
    received = []

    def serve():
        conn, _ = server.accept()
        with conn:
            # WILL ECHO + DO NAWS, then a prompt containing an escaped 0xFF data byte.
            conn.sendall(b"\xff\xfb\x01\xff\xfd\x1f\r\nR1> \xff\xff")
            data = b""
            while not data.endswith(b"en\r"):
                chunk = conn.recv(1024)
                if not chunk:
                    break
                data += chunk
            received.append(data)
            conn.sendall(b"echo:en\r")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield server.getsockname()[1], received
    server.close()
    thread.join(timeout=2)


def add_node(nodes, projects, port):
    pid, nid = str(uuid4()), str(uuid4())
    projects[pid] = {"project_id": pid, "name": "console", "status": "opened"}
    nodes[pid] = [
        {
            "node_id": nid,
            "name": "R1",
            "node_type": "iou",
            "status": "started",
            "console": port,
            "console_type": "telnet",
        }
    ]
    return pid, nid


def test_console_token_opens_console_only(mock_server, monkeypatch, telnet_port):
    monkeypatch.setenv("SLOT_CONSOLE_TOKEN", "console-token")
    client, projects, nodes = mock_server
    port, received = telnet_port
    pid, nid = add_node(nodes, projects, port)

    with client.websocket_connect(
        f"/v1/console/{pid}/{nid}", subprotocols=["lab-slot", "bearer.console-token"]
    ) as ws:
        assert ws.accepted_subprotocol == "lab-slot"
        out = b""
        while not out.endswith(b"\xff"):
            out += ws.receive_bytes()
        # Telnet negotiation is stripped, the data byte 0xFF survives unescaped.
        assert out == b"\r\nR1> \xff"
        ws.send_text("en\r")
        out = b""
        while not out.endswith(b"en\r"):
            out += ws.receive_bytes()
        assert out == b"echo:en\r"
    # Slot refuses the options (DONT ECHO, WONT NAWS) before forwarding keyboard input.
    assert received == [b"\xff\xfe\x01\xff\xfc\x1fen\r"]

    # The console token never authorizes the REST API (QGA, lifecycle).
    headers = {"Authorization": "Bearer console-token"}
    assert client.get("/v1/labs", headers=headers).status_code == 401
    assert (
        client.post(
            f"/v1/labs/{pid}/nodes/{nid}/qga/exec", headers=headers, json={"command": "id"}
        ).status_code
        == 401
    )


def test_console_rejects_wrong_token_and_stopped_node(mock_server, monkeypatch):
    monkeypatch.setenv("SLOT_CONSOLE_TOKEN", "console-token")
    client, projects, nodes = mock_server
    pid, nid = add_node(nodes, projects, 1)
    for protocols in (["lab-slot", "bearer.wrong"], ["bearer.console-token"]):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=protocols):
                pass
        assert exc.value.code == 1008
    nodes[pid][0]["status"] = "stopped"
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(
            f"/v1/console/{pid}/{nid}", subprotocols=["lab-slot", "bearer.console-token"]
        ):
            pass
    assert exc.value.code == 1008


def test_console_token_unset_keeps_api_token_only(mock_server, monkeypatch):
    monkeypatch.delenv("SLOT_CONSOLE_TOKEN", raising=False)
    from api.auth import websocket_token_ok

    assert websocket_token_ok("lab-slot, bearer.test-token")
    assert not websocket_token_ok("lab-slot, bearer.")
    assert not websocket_token_ok("lab-slot, bearer.console-token")


def test_history_keeps_the_output_once_and_trims_at_a_line(monkeypatch):
    from api.console import ConsoleHistory

    monkeypatch.setenv("SLOT_CONSOLE_HISTORY", "16")
    history, first, second = ConsoleHistory(), object(), object()
    assert history.opened("p1", "n1", first) == b""
    history.record("n1", first, b"line1\r\n")
    assert history.opened("p1", "n1", second) == b"line1\r\n"
    # GNS3 sends the output to every connection: only the oldest console records it.
    history.record("n1", second, b"twice\r\n")
    history.record("n1", first, b"line2\r\nline3\r\n")
    # Over the limit the history starts at the next line, not in the middle of one.
    assert bytes(history.nodes["n1"].data) == b"line2\r\nline3\r\n"
    history.closed("n1", first)
    history.record("n1", second, b"x\n")
    history.record("n1", second, b"y\n")
    assert bytes(history.nodes["n1"].data) == b"line3\r\nx\ny\n"
    history.closed("n1", second)
    # The history outlives the consoles and goes with another lab.
    assert history.opened("p1", "n1", first) == b"line3\r\nx\ny\n"
    assert history.opened("p2", "n2", second) == b""
    assert "n1" not in history.nodes


def test_history_off(monkeypatch):
    from api.console import ConsoleHistory

    monkeypatch.setenv("SLOT_CONSOLE_HISTORY", "0")
    history, console = ConsoleHistory(), object()
    history.opened("p1", "n1", console)
    history.record("n1", console, b"text\n")
    assert history.opened("p1", "n1", object()) == b""


@pytest.fixture
def chatty_port():
    """A telnet console: the first connection gets the boot log, every connection gets its input echoed."""
    server = socket.create_server(("127.0.0.1", 0))
    server.settimeout(5)
    conns = []

    def serve():
        greeting = b"boot log\r\nR1> "
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            conns.append(conn)
            conn.sendall(greeting)
            greeting = b""  # GNS3 does not repeat old output to a new connection
            threading.Thread(target=echo, args=(conn,), daemon=True).start()

    def echo(conn):
        try:
            while data := conn.recv(1024):
                conn.sendall(data)
        except OSError:
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield server.getsockname()[1]
    server.close()
    for c in conns:
        c.close()
    thread.join(timeout=2)


def test_a_new_console_gets_the_history_only_when_it_asks(mock_server, monkeypatch, chatty_port):
    monkeypatch.setenv("SLOT_CONSOLE_TOKEN", "console-token")
    client, projects, nodes = mock_server
    pid, nid = add_node(nodes, projects, chatty_port)
    protocols = ["lab-slot.2", "lab-slot", "bearer.console-token"]

    with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=protocols) as ws:
        out = b""
        while not out.endswith(b"R1> "):
            out += ws.receive_bytes()
        assert out == b"boot log\r\nR1> "
        with client.websocket_connect(f"/v1/console/{pid}/{nid}?history=1", subprotocols=protocols) as observer:
            assert observer.receive_bytes() == b"boot log\r\nR1> "
    # Without ?history=1 nothing old is sent: the first output is the echo of the input.
    with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=protocols) as ws:
        ws.send_bytes(b"x")
        assert ws.receive_bytes() == b"x"
    # The history outlives the consoles and has the echo too.
    with client.websocket_connect(f"/v1/console/{pid}/{nid}?history=1", subprotocols=protocols) as ws:
        assert ws.receive_bytes() == b"boot log\r\nR1> x"
    assert client.app.state.console_history.nodes[nid].writers == []
