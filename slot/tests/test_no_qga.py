"""QEMU images without a guest agent (NodeSpec.qga false), CONTRACTS.md 4.2.

Such a node is never asked anything: no QGA channel, readiness by the console port, no collectors,
and shutdown or guest commands are refused at once instead of waiting for an agent that is not there.
"""

import asyncio

import pytest
from api import labs
from api.facts import Facts
from api.models import NodeSpec
from main import app
from test_facts import FakeQga, lab, scenario, success
from test_shutdown import Broker, shutdown
from test_slot import HEADERS, payload, wait
from test_vnc import ForbiddenQga, Qga, run_provision


def test_spec_keeps_qga_true_in_the_dump_and_takes_false_for_qemu():
    # Tickets stored before the flag carry "qga": true; the dump must stay the same for them.
    assert NodeSpec(name="D1", kind="qemu", image="a.qcow2").model_dump()["qga"] is True
    assert NodeSpec(name="D1", kind="qemu", image="a.qcow2", qga=False).model_dump()["qga"] is False


@pytest.mark.parametrize("extra", [
    {"kind": "iou", "image": "i86bi.bin"},
    {"kind": "vpcs"},
    {"kind": "qemu", "image": "a.qcow2", "provisioning": [{"command": "true"}]},
    {"kind": "qemu", "image": "a.qcow2", "role": "switch"},
    {"kind": "qemu", "image": "a.qcow2", "adapters": 2, "uplink_port": 0},
])
def test_spec_refuses_qga_false_with_what_needs_an_agent(extra):
    with pytest.raises(ValueError):
        NodeSpec(name="D1", qga=False, **extra)


def test_no_qga_channel_in_the_qemu_options(tmp_path, monkeypatch):
    (tmp_path / "QEMU").mkdir()
    (tmp_path / "QEMU" / "a.qcow2").write_bytes(b"")
    monkeypatch.setattr(labs, "IMAGES", tmp_path)
    for qga, present in ((True, True), (False, False)):
        spec = NodeSpec(name="D1", kind="qemu", image="a.qcow2", console="vnc", qga=qga)
        options = labs.node_payload(spec, "n1")["properties"]["options"]
        assert ("guest_agent" in options) is present
        assert "-vga virtio" in options


@pytest.mark.parametrize("console", ["telnet", "vnc"])
def test_readiness_is_the_console_port_only(tmp_path, monkeypatch, console):
    spec = {"name": "D1", "kind": "qemu", "qga": False, "console": console}
    assert run_provision(tmp_path, monkeypatch, spec, qga=Qga()) == []


def test_telnet_node_with_an_agent_is_unchanged(tmp_path, monkeypatch):
    calls = run_provision(tmp_path, monkeypatch, {"name": "D1", "kind": "qemu"}, qga=Qga())
    assert ("call", "guest-ping") in calls
    assert any(kind == "exec" and "serial-getty" in command for kind, command in calls)


def test_collectors_never_ask_a_node_without_an_agent(monkeypatch):
    async def body(facts, lifecycle, labs_):
        labs_["p1"]["ticket"]["nodes"][0]["qga"] = False
        await facts.cycle()
        assert lifecycle.qga.calls == [] and lifecycle.qga.osinfo_calls == []
        assert "n1" not in facts.snapshot("p1")["nodes"]

    scenario(monkeypatch, body)


def test_shutdown_is_refused_at_once(mock_server):
    client, _, nodes = mock_server
    data = payload()
    data["nodes"][0].update(console="vnc", qga=False)
    pid = data["project_id"]
    assert wait(client, client.post("/v1/labs/deploy", json=data, headers=HEADERS))["status"] == "SUCCEEDED"
    node = next(n for n in nodes[pid] if n["node_type"] == "qemu")
    broker = Broker(nodes[pid])
    app.state.lifecycle.qga = broker
    result = wait(client, shutdown(client, pid, node))
    assert result["status"] == "FAILED" and result["error"]["code"] == "GUEST_AGENT_UNAVAILABLE"
    assert broker.calls == [] and node["status"] == "started"
    assert client.get(f"/v1/labs/{pid}/status", headers=HEADERS).json()["state"] == "READY"
    # The guest commands are refused before the broker is touched.
    app.state.lifecycle.qga = ForbiddenQga()
    exec_ = client.post(f"/v1/labs/{pid}/nodes/{node['node_id']}/qga/exec",
                        json={"command": "true"}, headers=HEADERS)
    assert exec_.status_code == 409 and "no guest agent" in exec_.json()["detail"]
    node["console_type"] = "telnet"  # past the VNC refusal: the agent check answers as well
    serial = client.post(f"/v1/labs/{pid}/nodes/{node['node_id']}/serial/enable", headers=HEADERS)
    assert serial.status_code == 409 and "no guest agent" in serial.json()["detail"]
