"""VNC console of QEMU nodes (NodeSpec.console == "vnc"), CONTRACTS.md 4.2.

The passport tests are the important ones: a ticket without the new field must keep exactly the
representation that older Slot versions stored and fingerprinted, or every project that is
continued after an upgrade fails with 409 "manifest differs".
"""

import asyncio
import hashlib
import json
import socket
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from api import console, labs
from api.lifecycle import Lifecycle
from api.models import DeployTicket, NodeSpec
from main import app
from starlette.websockets import WebSocketDisconnect
from test_console import add_node
from test_slot import HEADERS, wait

PID = "0b6c9d3e-6f1a-4c5e-9a7b-2d8e1f0a3c4b"

# Written by Slot 0.7.0 (before NodeSpec.console): the dumped ticket stored in the lab record.
# Frozen by hand on purpose; it must never be regenerated from the current model.
LEGACY_TICKET = {
    "auto_start": True,
    "links": [],
    "name": "legacy-lab",
    "nodes": [
        {
            "adapters": 1,
            "cli_prompt": "(?m)^[^\\r\\n]+[>#]\\s*$",
            "cpus": 1,
            "disk_interface": "virtio",
            "ethernet_adapters": 1,
            "image": "debian12.qcow2",
            "kind": "qemu",
            "l1_keepalives": True,
            "name": "D1",
            "provisioning": [],
            "qga": True,
            "ram_mb": 1024,
            "startup_script": "",
            "x": 0,
            "y": 0,
        }
    ],
    "project_id": PID,
}

# A larger ticket (three kinds, links, provisioning) and the idempotency fingerprint that
# Slot 0.7.0 computed for it: sha256 over json.dumps([action, pid, dump, node_id], sort_keys=True).
SAMPLE_REQUEST = {
    "name": "legacy-lab",
    "project_id": PID,
    "nodes": [
        {"name": "SW1", "kind": "iou", "image": "cisco.bin", "ram_mb": 1024, "x": -280, "y": -100},
        {
            "name": "D1",
            "kind": "qemu",
            "image": "debian12.qcow2",
            "adapters": 2,
            "provisioning": [{"command": "true", "timeout_seconds": 5}],
        },
        {"name": "PC1", "kind": "vpcs", "startup_script": "ip 10.0.0.2/24"},
    ],
    "links": [{"a": {"node": "SW1", "port": 0}, "b": {"node": "D1"}}],
}
SAMPLE_FINGERPRINT_0_7_0 = "a3e9567e38b7cc839cd2d3a7badba8f7df14f0328e7038d4bf44294ebb1199ac"


def fingerprint(dump):
    return hashlib.sha256(json.dumps(["deploy", None, dump, None], sort_keys=True).encode()).hexdigest()


def vnc_request(console_value="vnc", **extra):
    node = {"name": "D1", "kind": "qemu", "image": "debian12.qcow2"}
    if console_value is not None:
        node["console"] = console_value
    return {"name": "legacy-lab", "project_id": PID, "nodes": [node], **extra}


# ---- model ---------------------------------------------------------------------------------


def test_console_defaults_to_telnet_and_vnc_is_qemu_only():
    assert NodeSpec(name="a", kind="qemu", image="x.qcow2").console == "telnet"
    assert NodeSpec(name="a", kind="qemu", image="x.qcow2", console="vnc").console == "vnc"
    assert NodeSpec(name="a", kind="qemu", image="x.qcow2", console="telnet").console == "telnet"
    for node in (
        {"kind": "iou", "image": "cisco.bin"},
        {"kind": "vpcs"},
    ):
        with pytest.raises(ValueError, match="only supported for qemu"):
            NodeSpec(name="a", console="vnc", **node)
        assert NodeSpec(name="a", console="telnet", **node).console == "telnet"
    for bad in ("spice", "none", "VNC", "", None, 1):
        with pytest.raises(ValueError):
            NodeSpec(name="a", kind="qemu", image="x.qcow2", console=bad)


def test_vnc_node_is_rejected_for_a_non_qemu_node_in_a_ticket():
    with pytest.raises(ValueError, match="only supported for qemu"):
        DeployTicket.model_validate(
            {"name": "t", "nodes": [{"name": "SW1", "kind": "iou", "image": "c.bin", "console": "vnc"}]}
        )


def test_openapi_still_renders_with_the_serializer(mock_server):
    client, _, _ = mock_server
    schema = client.get("/openapi.json").json()
    props = schema["components"]["schemas"]["NodeSpec"]["properties"]
    assert props["console"]["enum"] == ["telnet", "vnc"]
    assert props["console"]["default"] == "telnet"


# ---- passport (what is stored, compared and fingerprinted) -----------------------------------


def test_telnet_nodes_keep_the_representation_older_slots_stored():
    ticket = DeployTicket.model_validate(SAMPLE_REQUEST)
    dump = ticket.model_dump(mode="json")
    assert all("console" not in node for node in dump["nodes"])
    # Byte for byte what Slot 0.7.0 fingerprinted for the same request.
    assert fingerprint(dump) == SAMPLE_FINGERPRINT_0_7_0
    # An explicit default is the same manifest as an absent field.
    explicit = json.loads(json.dumps(SAMPLE_REQUEST))
    for node in explicit["nodes"]:
        if node["kind"] == "qemu":
            node["console"] = "telnet"
    assert DeployTicket.model_validate(explicit).model_dump(mode="json") == dump
    assert DeployTicket.model_validate(explicit).model_dump() == ticket.model_dump()


def test_stored_legacy_passport_matches_a_new_ticket_without_console():
    ticket = DeployTicket.model_validate(vnc_request(None))
    dump = ticket.model_dump(mode="json")
    dump["project_id"] = PID  # submit() does the same before comparing
    assert dump == LEGACY_TICKET
    # The stored record is read back through the model on resume: still a telnet node.
    assert DeployTicket.model_validate(LEGACY_TICKET).nodes[0].console == "telnet"


def test_vnc_node_makes_the_passport_differ():
    legacy = DeployTicket.model_validate(vnc_request(None)).model_dump(mode="json")
    vnc = DeployTicket.model_validate(vnc_request("vnc")).model_dump(mode="json")
    assert vnc != legacy
    assert vnc["nodes"][0]["console"] == "vnc"
    assert fingerprint(vnc) != fingerprint(legacy)
    # ... and it is stable: the same vnc ticket always gives the same passport.
    assert DeployTicket.model_validate(vnc_request("vnc")).model_dump(mode="json") == vnc


def deploy(client, body, key=None):
    headers = dict(HEADERS)
    if key:
        headers["Idempotency-Key"] = key
    return client.post("/v1/labs/deploy", headers=headers, json=body)


def test_project_stored_by_an_older_slot_continues_without_409(mock_server):
    client, _, nodes = mock_server
    assert wait(client, deploy(client, vnc_request(None)))["status"] == "SUCCEEDED"
    lifecycle = app.state.lifecycle
    lab = lifecycle.get("lab", PID)
    assert "console" not in lab["ticket"]["nodes"][0]
    # Replace the record by what the previous release wrote (hand-frozen literal).
    lab["ticket"] = json.loads(json.dumps(LEGACY_TICKET))
    lifecycle.save("lab", PID, lab)
    # Same manifest, with the field absent and with the default spelled out: both continue.
    assert wait(client, deploy(client, vnc_request(None)))["status"] == "SUCCEEDED"
    assert wait(client, deploy(client, vnc_request("telnet")))["status"] == "SUCCEEDED"
    # A vnc node is a different manifest of the same project.
    response = deploy(client, vnc_request("vnc"))
    assert response.status_code == 409 and "manifest differs" in response.text
    assert nodes[PID][0]["console_type"] == "telnet"


def test_explicit_idempotency_key_of_an_older_slot_still_replays(mock_server):
    client, _, _ = mock_server
    # The key was stored with the 0.7.0 fingerprint of the request; a retry after the upgrade
    # must be a replay, not IDEMPOTENCY_KEY_CONFLICT.
    first = deploy(client, vnc_request(None), key="k-legacy")
    assert wait(client, first)["status"] == "SUCCEEDED"
    # SQLite connections belong to the thread of the app, so read the record in that thread.
    op = client.portal.call(app.state.lifecycle.get, "operation", first.json()["operation_id"])
    assert op["fingerprint"] == fingerprint(LEGACY_TICKET)
    retry = deploy(client, vnc_request(None), key="k-legacy")
    assert retry.status_code == 202 and retry.json()["operation_id"] == op["operation_id"]
    changed = deploy(client, vnc_request("vnc"), key="k-legacy")
    assert changed.status_code == 409 and "IDEMPOTENCY_KEY_CONFLICT" in changed.text


def test_stored_vnc_project_continues_only_as_vnc(mock_server):
    client, _, nodes = mock_server
    assert wait(client, deploy(client, vnc_request("vnc")))["status"] == "SUCCEEDED"
    assert wait(client, deploy(client, vnc_request("vnc")))["status"] == "SUCCEEDED"
    assert nodes[PID][0]["console_type"] == "vnc"
    for other in (None, "telnet"):
        response = deploy(client, vnc_request(other))
        assert response.status_code == 409 and "manifest differs" in response.text


# ---- node creation -----------------------------------------------------------------------------


def test_vnc_node_is_created_with_vnc_console_type(mock_server):
    client, _, nodes = mock_server
    body = {
        "name": "lab",
        "project_id": str(uuid4()),
        "nodes": [
            {"name": "D1", "kind": "qemu", "image": "debian12.qcow2", "console": "vnc"},
            {"name": "D2", "kind": "qemu", "image": "debian12.qcow2"},
            {"name": "SW1", "kind": "iou", "image": "cisco.bin"},
        ],
    }
    assert wait(client, deploy(client, body))["status"] == "SUCCEEDED"
    created = {n["name"]: n for n in nodes[body["project_id"]]}
    assert created["D1"]["console_type"] == "vnc"
    assert created["D2"]["console_type"] == "telnet"
    assert "console_type" not in created["SW1"]
    # QGA is independent of the console: the guest agent channel stays on the vnc node.
    assert "guest_agent.0" in created["D1"]["properties"]["options"]


def test_vnc_payload_leaves_the_port_to_gns3(mock_server):
    spec = NodeSpec(name="D1", kind="qemu", image="debian12.qcow2", console="vnc")
    payload = labs.node_payload(spec, str(uuid4()))
    # No explicit port: GNS3 allocates one from 5900 up and derives the -vnc display from it.
    assert payload["console_type"] == "vnc" and "console" not in payload
    telnet = labs.node_payload(NodeSpec(name="D1", kind="qemu", image="debian12.qcow2"), str(uuid4()))
    assert telnet["console_type"] == "telnet" and "console" not in telnet


# ---- readiness -----------------------------------------------------------------------------------


class Qga:
    def __init__(self):
        self.calls = []

    async def call(self, node_id, command, args=None, **options):
        self.calls.append(("call", command))
        return {}

    async def exec(self, node_id, command, **options):
        self.calls.append(("exec", command))
        return {"exit_code": 0}

    async def close(self):
        pass


def run_provision(tmp_path, monkeypatch, spec, node=None, *, initial=True, listen=True):
    """Run Lifecycle.provision against a local TCP listener; returns the QGA calls."""
    monkeypatch.setenv("SLOT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SLOT_READY_TIMEOUT", "0")
    qga = Qga()

    async def scenario():
        async def handler(reader, writer):
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        if not listen:
            server.close()
            await server.wait_closed()
        service = Lifecycle(SimpleNamespace())
        service.qga = qga
        actual = {"node_id": str(uuid4()), "console": port, "console_type": spec_console(spec)}
        actual.update(node or {})
        try:
            await service.provision({}, actual, spec, initial)
        finally:
            await service.close()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())
    return qga.calls


def spec_console(spec):
    return "vnc" if spec.get("console") == "vnc" else "telnet"


def test_vnc_node_is_ready_on_its_tcp_port_alone(tmp_path, monkeypatch):
    spec = {"kind": "qemu", "console": "vnc", "provisioning": []}
    for initial in (True, False):
        # No guest-ping, no serial-getty: neither exists for a node without a serial console.
        assert run_provision(tmp_path, monkeypatch, spec, initial=initial) == []


def test_vnc_node_waits_for_qga_only_to_run_provisioning(tmp_path, monkeypatch):
    spec = {
        "kind": "qemu",
        "console": "vnc",
        "provisioning": [{"command": "echo ok", "timeout_seconds": 5}],
    }
    assert run_provision(tmp_path, monkeypatch, spec, initial=True) == [
        ("call", "guest-ping"),
        ("exec", "echo ok"),
    ]
    # A resume replays nothing and needs no guest at all.
    assert run_provision(tmp_path, monkeypatch, spec, initial=False) == []


@pytest.mark.parametrize("extra", [{}, {"console": "telnet"}])
def test_telnet_node_still_waits_for_serial_getty(tmp_path, monkeypatch, extra):
    # `extra` empty is a ticket stored by an older Slot: no console key at all.
    spec = {"kind": "qemu", "provisioning": [], **extra}
    calls = run_provision(tmp_path, monkeypatch, spec, initial=True)
    assert calls[0] == ("call", "guest-ping")
    assert calls[1][0] == "exec" and "enable --now serial-getty@ttyS0" in calls[1][1]
    calls = run_provision(tmp_path, monkeypatch, spec, initial=False)
    assert calls[1][0] == "exec" and "is-active" in calls[1][1] and "enable" not in calls[1][1]


def test_vnc_readiness_fails_without_a_vnc_console_or_listener(tmp_path, monkeypatch):
    spec = {"kind": "qemu", "console": "vnc", "provisioning": []}
    for node in (
        {"console_type": "telnet"},  # GNS3 did not honour the console type
        {"console_type": None},
        {"console": None},
        {"console": 0},
        {"console": True},
        {"console": 70000},
    ):
        with pytest.raises(RuntimeError, match="Missing local VNC console"):
            run_provision(tmp_path, monkeypatch, spec, node)
    with pytest.raises(OSError):  # nothing listens on the port yet (QEMU not up)
        run_provision(tmp_path, monkeypatch, spec, listen=False)


# ---- /serial/enable ------------------------------------------------------------------------------


class ForbiddenQga:
    async def call(self, *args, **kwargs):
        raise AssertionError("QGA must not be used for a VNC node")

    exec = call

    async def close(self):
        pass


def test_serial_enable_is_refused_for_a_vnc_node_without_touching_the_guest(mock_server, monkeypatch):
    client, projects, nodes = mock_server
    pid, nid = add_node(nodes, projects, 5900)
    nodes[pid][0].update(node_type="qemu", console_type="vnc")
    monkeypatch.setattr(app.state.lifecycle, "qga", ForbiddenQga())
    response = client.post(f"/v1/labs/{pid}/nodes/{nid}/serial/enable", headers=HEADERS)
    assert response.status_code == 409
    assert "VNC console" in response.json()["detail"]
    # Also while the node is stopped: the answer does not depend on the power state.
    nodes[pid][0]["status"] = "stopped"
    assert client.post(f"/v1/labs/{pid}/nodes/{nid}/serial/enable", headers=HEADERS).status_code == 409


def test_serial_enable_of_a_telnet_node_is_unchanged(mock_server, monkeypatch):
    client, projects, nodes = mock_server
    pid, nid = add_node(nodes, projects, 5001)
    nodes[pid][0].update(node_type="qemu", console_type="telnet")

    class Qga:
        calls = []

        async def call(self, node_id, command, args=None, **options):
            return {}

        async def exec(self, node_id, command, **options):
            self.calls.append(command)
            return {"exit_code": 0}

        async def close(self):
            pass

    qga = Qga()
    monkeypatch.setattr(app.state.lifecycle, "qga", qga)
    response = client.post(f"/v1/labs/{pid}/nodes/{nid}/serial/enable", headers=HEADERS)
    assert response.status_code == 200 and response.json()["result"] == "serial_getty_enabled"
    assert qga.calls == ["systemctl enable --now serial-getty@ttyS0.service"]


# ---- console bridge (WebSocket <-> TCP) ------------------------------------------------------------


class FakeVnc:
    """A TCP listener on 127.0.0.1 that plays the part of QEMU's VNC server."""

    def __init__(self, greeting=b"", reply=None, close_after_greeting=False):
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.greeting = greeting
        self.reply = reply
        self.close_after_greeting = close_after_greeting
        self.received = bytearray()
        self.client_closed = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        try:
            conn, _ = self.server.accept()
        except OSError:
            return
        with conn:
            if self.greeting:
                conn.sendall(self.greeting)
            if self.close_after_greeting:
                return
            while True:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    break
                if not chunk:
                    self.client_closed.set()  # the bridge closed its end of the TCP connection
                    break
                self.received.extend(chunk)
                if self.reply:
                    conn.sendall(self.reply(chunk))

    def close(self):
        try:  # wake up accept() when no client ever came
            socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
        except OSError:
            pass
        self.server.close()
        self.thread.join(timeout=2)


@pytest.fixture
def fake_vnc():
    made = []

    def make(**options):
        made.append(FakeVnc(**options))
        return made[-1]

    yield make
    for vnc in made:
        vnc.close()


def vnc_node(mock_server, port, **overrides):
    client, projects, nodes = mock_server
    pid, nid = add_node(nodes, projects, port)
    fields = {"node_type": "qemu", "console_type": "vnc", **overrides}
    nodes[pid][0].update(fields)
    return client, pid, nid, nodes[pid][0]


PROTOCOLS = ["lab-slot", "bearer.console-token"]


def test_vnc_stream_is_raw_in_both_directions(mock_server, monkeypatch, fake_vnc):
    monkeypatch.setenv("SLOT_CONSOLE_TOKEN", "console-token")
    greeting = b"RFB 003.008\n" + b"\xff\xfb\x01\xff\xff\xfd\x1f\x00"  # looks like Telnet on purpose
    server = fake_vnc(greeting=greeting, reply=lambda chunk: b"\xff" + chunk[::-1] + b"\xff\xff")
    client, pid, nid, _ = vnc_node(mock_server, server.port)
    with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=PROTOCOLS) as ws:
        assert ws.accepted_subprotocol == "lab-slot"
        got = b""
        while len(got) < len(greeting):
            got += ws.receive_bytes()
        assert got == greeting  # no IAC stripped, no 0xFF unescaped
        sent = b"RFB 003.008\n" + b"\xff\xfd\x01\xff\xff\x00\xfa"
        ws.send_bytes(sent)
        expected = b"\xff" + sent[::-1] + b"\xff\xff"
        got = b""
        while len(got) < len(expected):
            got += ws.receive_bytes()
        assert got == expected
    # Exactly what the browser sent: 0xFF is not doubled and no Telnet reply was injected.
    assert bytes(server.received) == sent
    assert server.client_closed.wait(3)


def test_vnc_bridge_closes_the_websocket_when_the_screen_goes_away(mock_server, fake_vnc):
    server = fake_vnc(greeting=b"RFB 003.008\n\xff", close_after_greeting=True)
    client, pid, nid, _ = vnc_node(mock_server, server.port)
    with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=["lab-slot", "bearer.test-token"]) as ws:
        assert ws.receive_bytes() == b"RFB 003.008\n\xff"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_bytes()
        assert exc.value.code == 1000


def test_vnc_bridge_closes_the_tcp_side_when_the_browser_goes_away(mock_server, fake_vnc):
    server = fake_vnc(greeting=b"RFB 003.008\n")
    client, pid, nid, _ = vnc_node(mock_server, server.port)
    with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=["lab-slot", "bearer.test-token"]) as ws:
        assert ws.receive_bytes() == b"RFB 003.008\n"
        assert not server.client_closed.is_set()
        ws.close()
    assert server.client_closed.wait(3), "TCP connection to the VNC port was left open"


def test_vnc_text_frame_is_refused_not_reencoded(mock_server, fake_vnc):
    server = fake_vnc(greeting=b"RFB 003.008\n")
    client, pid, nid, _ = vnc_node(mock_server, server.port)
    with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=["lab-slot", "bearer.test-token"]) as ws:
        assert ws.receive_bytes() == b"RFB 003.008\n"
        ws.send_text("ÿ")
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_bytes()
        assert exc.value.code == 1003
    assert server.client_closed.wait(3)
    assert bytes(server.received) == b""


def test_vnc_console_checks_token_and_status(mock_server, monkeypatch, fake_vnc):
    monkeypatch.setenv("SLOT_CONSOLE_TOKEN", "console-token")
    server = fake_vnc(greeting=b"RFB 003.008\n")
    client, pid, nid, node = vnc_node(mock_server, server.port)
    for protocols in (["lab-slot", "bearer.wrong"], ["bearer.console-token"], ["lab-slot"]):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=protocols):
                pass
        assert exc.value.code == 1008
    # A stopped node is refused with 1008, like for telnet.
    node["status"] = "stopped"
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=PROTOCOLS):
            pass
    assert exc.value.code == 1008
    assert bytes(server.received) == b""
    node["status"] = "started"
    with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=PROTOCOLS) as ws:
        assert ws.receive_bytes() == b"RFB 003.008\n"


@pytest.mark.parametrize(
    "overrides",
    [
        {"console_type": "spice"},
        {"console_type": "none"},
        {"console_type": None},
        {"console": None},
        {"console": "5900"},
        {"console": 5001},  # not in the range GNS3 hands out to VNC consoles
    ],
)
def test_other_console_types_and_ports_are_refused(mock_server, monkeypatch, fake_vnc, overrides):
    monkeypatch.setenv("SLOT_CONSOLE_TOKEN", "console-token")
    server = fake_vnc(greeting=b"RFB 003.008\n")
    client, pid, nid, _ = vnc_node(mock_server, server.port, **overrides)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=PROTOCOLS):
            pass
    assert exc.value.code == 1008


def test_vnc_port_that_does_not_answer_is_a_server_error(mock_server, monkeypatch, fake_vnc):
    monkeypatch.setenv("SLOT_CONSOLE_TOKEN", "console-token")
    server = fake_vnc()
    port = server.port
    server.close()  # nobody listens there any more
    client, pid, nid, _ = vnc_node(mock_server, port)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=PROTOCOLS):
            pass
    assert exc.value.code == 1011


def test_telnet_bridge_is_unchanged_next_to_the_vnc_one(mock_server, monkeypatch, fake_vnc):
    """Regression: the same bytes that pass untouched on VNC are Telnet-processed on telnet."""
    monkeypatch.setenv("SLOT_CONSOLE_TOKEN", "console-token")
    greeting = b"\xff\xfb\x01login: \xff\xff"
    server = fake_vnc(greeting=greeting, reply=lambda chunk: b"ok")
    client, projects, nodes = mock_server
    pid, nid = add_node(nodes, projects, server.port)  # console_type "telnet"
    with client.websocket_connect(f"/v1/console/{pid}/{nid}", subprotocols=PROTOCOLS) as ws:
        got = b""
        while not got.endswith(b"\xff"):
            got += ws.receive_bytes()
        assert got == b"login: \xff"  # IAC WILL ECHO stripped, escaped 0xFF unescaped
        ws.send_bytes(b"a\xffb")
        assert ws.receive_bytes() == b"ok"
    # Slot answered the negotiation first and doubled the 0xFF data byte.
    assert bytes(server.received) == b"\xff\xfe\x01" + b"a\xff\xffb"


# ---- bridge termination, without the HTTP stack -----------------------------------------------------


class FakeWebSocket:
    """Just enough of starlette's WebSocket: a second close() raises like the real one."""

    def __init__(self, fail_send=False):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.closed_with = []
        self.fail_send = fail_send

    async def receive(self):
        return await self.incoming.get()

    async def send_bytes(self, data):
        if self.fail_send:
            raise WebSocketDisconnect(1006)
        self.sent.append(data)

    async def close(self, code=1000):
        if self.closed_with:
            raise RuntimeError('Cannot call "send" once a close message has been sent.')
        self.closed_with.append(code)


def bridge_tasks():
    return [t for t in asyncio.all_tasks() if "bridge_vnc" in t.get_coro().__qualname__]


def run_bridge(script, ws, *, server_handler):
    """script(ws, tcp_state) drives the browser side; returns the TCP state after the bridge ended."""
    state = {"received": bytearray(), "eof": asyncio.Event()}

    async def scenario():
        async def handler(reader, writer):
            await server_handler(reader, writer, state)

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        driver = asyncio.create_task(script(ws, state))
        try:
            await asyncio.wait_for(console.bridge_vnc(ws, reader, writer), 5)
            assert writer.is_closing()
            assert bridge_tasks() == []
            await asyncio.wait_for(state["eof"].wait(), 3)  # the peer saw the connection end
        finally:
            driver.cancel()
            server.close()
            await server.wait_closed()
        return state

    return asyncio.run(scenario())


async def drain_until_eof(reader, writer, state):
    while chunk := await reader.read(4096):
        state["received"].extend(chunk)
    state["eof"].set()
    writer.close()


def test_bridge_ends_cleanly_when_the_screen_closes_first():
    ws = FakeWebSocket()

    async def server(reader, writer, state):
        writer.write(b"RFB\xff")
        await writer.drain()
        writer.close()
        state["eof"].set()

    async def script(ws, state):
        await asyncio.sleep(3600)

    run_bridge(script, ws, server_handler=server)
    assert ws.sent == [b"RFB\xff"] and ws.closed_with == [1000]


def test_bridge_ends_cleanly_when_the_browser_closes_first():
    ws = FakeWebSocket()

    async def script(ws, state):
        await ws.incoming.put({"type": "websocket.receive", "bytes": b"\xff\x00", "text": None})
        await asyncio.sleep(0.05)
        await ws.incoming.put({"type": "websocket.disconnect", "code": 1001})

    state = run_bridge(script, ws, server_handler=drain_until_eof)
    assert bytes(state["received"]) == b"\xff\x00"
    assert ws.closed_with == [1000]


def test_bridge_ends_cleanly_on_a_text_frame():
    ws = FakeWebSocket()

    async def script(ws, state):
        await ws.incoming.put({"type": "websocket.receive", "bytes": None, "text": "x"})

    state = run_bridge(script, ws, server_handler=drain_until_eof)
    assert bytes(state["received"]) == b"" and ws.closed_with == [1003]


def test_bridge_ends_cleanly_when_the_browser_is_gone_while_the_screen_sends():
    ws = FakeWebSocket(fail_send=True)

    async def server(reader, writer, state):
        writer.write(b"update")
        await writer.drain()
        await drain_until_eof(reader, writer, state)

    async def script(ws, state):
        await asyncio.sleep(3600)

    run_bridge(script, ws, server_handler=server)
    assert ws.sent == []


def test_cancelling_the_handler_leaves_no_task_and_no_socket():
    async def scenario():
        eof = asyncio.Event()

        async def handler(reader, writer):
            await reader.read()
            eof.set()
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        bridge = asyncio.create_task(console.bridge_vnc(FakeWebSocket(), reader, writer))
        await asyncio.sleep(0.05)
        bridge.cancel()
        with pytest.raises(asyncio.CancelledError):
            await bridge
        assert writer.is_closing()
        assert bridge_tasks() == []
        await asyncio.wait_for(eof.wait(), 3)
        server.close()
        await server.wait_closed()

    asyncio.run(scenario())
