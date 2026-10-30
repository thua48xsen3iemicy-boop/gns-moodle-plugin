"""The "Internet" node: NodeSpec.uplink_port, the cloud node on eth1 and its link (CONTRACTS.md 4.2, 7)."""

import json
from uuid import uuid4

import pytest
from api import uplink
from api.models import DeployTicket
from main import app
from test_slot import HEADERS, wait

MAC = "02:6c:61:00:07:d1"
PID = "5f1c2f4e-1d3b-4c55-8a5a-0c3a6e0c9b11"


@pytest.fixture
def eth1(tmp_path, monkeypatch):
    (tmp_path / "net" / "eth1").mkdir(parents=True)
    monkeypatch.setattr(uplink, "NET_DIR", tmp_path / "net")
    monkeypatch.setenv("SLOT_UPLINK_MAC", MAC)


def inet_lab(adapters=4, port=0, **extra):
    return {
        "name": "inet",
        "project_id": PID,
        "nodes": [
            {"name": "R1", "kind": "iou", "image": "cisco.bin"},
            {"name": "NET", "kind": "qemu", "image": "debian12.qcow2", "adapters": adapters, "uplink_port": port, **extra},
        ],
        "links": [{"a": {"node": "R1", "port": 0}, "b": {"node": "NET", "adapter": 1}}],
    }


def mac_int(text):
    return int(text.replace(":", ""), 16)


# ---- model

def test_uplink_port_is_absent_from_the_passport_when_not_set():
    plain = DeployTicket.model_validate({"name": "x", "nodes": [{"name": "a", "kind": "vpcs"}]})
    assert "uplink_port" not in json.dumps(plain.model_dump(mode="json"))
    marked = DeployTicket.model_validate(inet_lab())
    assert [n["uplink_port"] for n in marked.model_dump(mode="json")["nodes"] if "uplink_port" in n] == [0]


@pytest.mark.parametrize("change,message", [
    (lambda d: d["nodes"][0].update(uplink_port=0), "only supported for qemu"),
    (lambda d: d["nodes"][1].update(uplink_port=4), "less than adapters"),
    (lambda d: d["nodes"][1].update(uplink_port=-1), "greater than or equal to 0"),
    (lambda d: d["nodes"].append({"name": "N2", "kind": "qemu", "image": "debian12.qcow2", "uplink_port": 0}), "At most one"),
    (lambda d: d["links"].append({"a": {"node": "NET", "adapter": 0}, "b": {"node": "R1", "port": 1}}), "used more than once"),
])
def test_invalid_uplink_tickets(change, message):
    data = inet_lab()
    change(data)
    with pytest.raises(ValueError, match=message):
        DeployTicket.model_validate(data)


# ---- the MAC of the outer port

@pytest.mark.parametrize("port,adapters", [(0, 1), (0, 4), (3, 4), (15, 16)])
def test_base_mac_gives_the_port_the_slot_mac(eth1, port, adapters):
    base = mac_int(uplink.base_mac(port, adapters))
    assert base + port == mac_int(MAC)
    assert uplink.base_mac(port, adapters) == uplink.base_mac(port, adapters).lower()


def test_base_mac_borrows_across_bytes(eth1, monkeypatch):
    monkeypatch.setenv("SLOT_UPLINK_MAC", "02:6c:61:00:00:01")  # VMID 1, uplink on adapter 3
    assert uplink.base_mac(3, 4) == "02:6c:60:ff:ff:fe"


def test_base_mac_refuses_a_range_that_leaves_the_address_class(eth1, monkeypatch):
    monkeypatch.setenv("SLOT_UPLINK_MAC", "02:00:00:00:00:01")
    with pytest.raises(ValueError):
        uplink.base_mac(3, 4)  # would borrow into the first byte
    monkeypatch.setenv("SLOT_UPLINK_MAC", "02:ff:ff:ff:ff:ff")
    with pytest.raises(ValueError):
        uplink.base_mac(0, 2)  # would carry into the first byte


@pytest.mark.parametrize("value", ["", "02:6c:61:00:07", "zz:6c:61:00:07:d1", "03:6c:61:00:07:d1", "00:6c:61:00:07:d1"])
def test_unusable_slot_mac_disables_the_feature(eth1, monkeypatch, value):
    monkeypatch.setenv("SLOT_UPLINK_MAC", value)
    assert not uplink.enabled()


def test_feature_needs_eth1(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOT_UPLINK_MAC", MAC)
    monkeypatch.setattr(uplink, "NET_DIR", tmp_path / "no-net")
    assert not uplink.enabled()


# ---- capabilities and refusal

def test_capabilities_announce_uplink_only_when_usable(mock_server, eth1, monkeypatch):
    client, _, _ = mock_server
    assert "uplink" in client.get("/v1/capabilities", headers=HEADERS).json()["features"]
    monkeypatch.delenv("SLOT_UPLINK_MAC")
    assert "uplink" not in client.get("/v1/capabilities", headers=HEADERS).json()["features"]


def test_ticket_with_uplink_is_refused_without_the_feature(mock_server, monkeypatch):
    client, projects, nodes = mock_server
    monkeypatch.delenv("SLOT_UPLINK_MAC", raising=False)
    response = client.post("/v1/labs/deploy", headers=HEADERS, json=inet_lab())
    assert response.status_code == 422
    assert "SLOT_UPLINK_MAC" in response.text
    assert projects == {} and nodes == {}


# ---- the cloud node and its link

def deploy(client, data):
    return wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))


def test_cloud_node_and_link_are_created(mock_server, eth1):
    client, _, nodes = mock_server
    assert deploy(client, inet_lab(adapters=4, port=2))["status"] == "SUCCEEDED"
    by_name = {n["name"]: n for n in nodes[PID]}
    assert set(by_name) == {"R1", "NET", "_uplink"}
    qemu, cloud = by_name["NET"], by_name["_uplink"]
    assert mac_int(qemu["properties"]["mac_address"]) + 2 == mac_int(MAC)
    assert cloud["node_type"] == "cloud"
    assert cloud["properties"]["ports_mapping"] == [
        {"interface": "eth1", "name": "eth1", "port_number": 0, "type": "ethernet"}
    ]
    ends = [sorted((e["node_id"], e["adapter_number"], e["port_number"]) for e in l["nodes"])
            for l in app.state.mock_links[PID]]
    assert sorted([(qemu["node_id"], 2, 0), (cloud["node_id"], 0, 0)]) in ends
    assert len(ends) == 2  # the lab's own link and the uplink


def test_the_lab_does_not_see_the_cloud_node(mock_server, eth1):
    client, _, nodes = mock_server
    op = deploy(client, inet_lab())
    assert op["status"] == "SUCCEEDED"
    assert {n["name"] for n in op["nodes"]} == {"R1", "NET"}
    status = client.get(f"/v1/labs/{PID}/status", headers=HEADERS).json()
    assert {n["name"] for n in status["nodes"]} == {"R1", "NET"}
    topo = client.get(f"/v1/labs/{PID}/topology", headers=HEADERS).json()
    assert {n["name"] for n in topo["nodes"]} == {"R1", "NET"}
    assert len(topo["links"]) == 1
    assert client.get(f"/v1/labs/{PID}", headers=HEADERS).json()["nodes"] and all(
        n["name"] != "_uplink" for n in client.get(f"/v1/labs/{PID}", headers=HEADERS).json()["nodes"]
    )
    assert all(n["status"] == "started" for n in nodes[PID])  # the cloud node starts with the project


def test_repeated_deploy_changes_nothing(mock_server, eth1):
    client, _, nodes = mock_server
    assert deploy(client, inet_lab())["status"] == "SUCCEEDED"
    before = json.dumps([nodes[PID], app.state.mock_links[PID]], sort_keys=True)
    passport = app.state.lifecycle.get("lab", PID)["ticket"]
    assert deploy(client, inet_lab())["status"] == "SUCCEEDED"
    assert json.dumps([nodes[PID], app.state.mock_links[PID]], sort_keys=True) == before
    assert app.state.lifecycle.get("lab", PID)["ticket"] == passport
    assert len([n for n in nodes[PID] if n["name"] == "_uplink"]) == 1


def test_a_missing_cloud_node_is_an_inventory_error(mock_server, eth1):
    client, _, nodes = mock_server
    assert deploy(client, inet_lab())["status"] == "SUCCEEDED"
    cloud_id = next(n["node_id"] for n in nodes[PID] if n["name"] == "_uplink")
    nodes[PID][:] = [n for n in nodes[PID] if n["name"] != "_uplink"]
    app.state.mock_links[PID][:] = [  # GNS3 drops the links of a deleted node
        l for l in app.state.mock_links[PID] if all(e["node_id"] != cloud_id for e in l["nodes"])
    ]
    stop = wait(client, client.post(f"/v1/labs/{PID}/stop", headers=HEADERS))
    assert stop["status"] == "SUCCEEDED"
    op = wait(client, client.post(f"/v1/labs/{PID}/resume", headers=HEADERS))
    assert op["status"] == "FAILED"
    assert "Uplink cloud node" in op["error"]["message"]


def test_a_missing_uplink_link_is_a_link_error(mock_server, eth1):
    client, _, _ = mock_server
    assert deploy(client, inet_lab())["status"] == "SUCCEEDED"
    app.state.mock_links[PID][:] = app.state.mock_links[PID][:1]  # only the lab's own link
    stop = wait(client, client.post(f"/v1/labs/{PID}/stop", headers=HEADERS))
    assert stop["status"] == "SUCCEEDED"
    op = wait(client, client.post(f"/v1/labs/{PID}/resume", headers=HEADERS))
    assert op["status"] == "FAILED"
    assert "Uplink link" in op["error"]["message"]


def test_a_cloud_node_in_a_lab_without_uplink_is_foreign(mock_server, eth1):
    client, _, nodes = mock_server
    data = {"name": "plain", "project_id": PID, "nodes": [{"name": "D1", "kind": "qemu", "image": "debian12.qcow2"}]}
    assert deploy(client, data)["status"] == "SUCCEEDED"
    nodes[PID].append({"node_id": str(uuid4()), "name": "_uplink", "node_type": "cloud", "status": "started"})
    wait(client, client.post(f"/v1/labs/{PID}/stop", headers=HEADERS))
    op = wait(client, client.post(f"/v1/labs/{PID}/resume", headers=HEADERS))
    assert op["status"] == "FAILED"
    assert "inventory" in op["error"]["message"]


def test_lab_without_uplink_makes_the_same_requests_as_before(mock_server, eth1):
    client, _, nodes = mock_server
    data = {"name": "plain", "project_id": PID, "nodes": [{"name": "D1", "kind": "qemu", "image": "debian12.qcow2"}]}
    assert deploy(client, data)["status"] == "SUCCEEDED"
    assert [n["name"] for n in nodes[PID]] == ["D1"]
    assert "mac_address" not in nodes[PID][0]["properties"]
    assert app.state.mock_links[PID] == []
