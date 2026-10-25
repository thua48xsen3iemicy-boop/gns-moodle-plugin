"""Guest facts: the linux.net collector, background collection and the /facts routes."""

import asyncio
import json
import os
import stat
import subprocess
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from api import facts as facts_module
from api.facts import (
    COLLECTORS,
    LINUX_NET_COMMAND,
    Facts,
    FactError,
    adapter_macs,
    parse_linux_net,
)
from api.qga import Priority, QgaBusy, QgaCommandError, QgaTimeout, QgaUnavailable

HEADERS = {"Authorization": "Bearer test-token"}
BASE_MAC = "00:00:ab:cd:00:00"
MACS = {BASE_MAC: 0, "00:00:ab:cd:00:01": 1}


def lifetimes(valid):
    return {"valid_life_time": valid, "preferred_life_time": valid}


def addr_document(valid=86000):
    return [
        {
            "ifindex": 1, "ifname": "lo", "flags": ["LOOPBACK", "UP", "LOWER_UP"], "operstate": "UNKNOWN",
            "link_type": "loopback", "address": "00:00:00:00:00:00",
            "addr_info": [
                {"family": "inet", "local": "127.0.0.1", "prefixlen": 8, **lifetimes(4294967295)},
                {"family": "inet6", "local": "::1", "prefixlen": 128, **lifetimes(4294967295)},
            ],
        },
        {
            "ifindex": 2, "ifname": "ens3", "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"],
            "operstate": "UP", "link_type": "ether", "address": BASE_MAC,
            "addr_info": [
                {"family": "inet", "local": "10.0.12.1", "prefixlen": 30, "dynamic": True, **lifetimes(valid)},
                {"family": "inet6", "local": "fe80::1", "prefixlen": 64, **lifetimes(valid)},
            ],
        },
        {
            "ifindex": 3, "ifname": "ens4", "flags": ["BROADCAST", "MULTICAST"], "operstate": "DOWN",
            "link_type": "ether", "address": "00:00:ab:cd:00:01", "addr_info": [],
        },
        {
            "ifindex": 4, "ifname": "ens5", "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"],
            "operstate": "UP", "link_type": "ether", "address": "52:54:00:aa:bb:cc",
            "addr_info": [{"family": "inet", "local": "192.168.5.7", "prefixlen": 24, **lifetimes(valid)}],
        },
        {
            "ifindex": 5, "ifname": "ens3.20", "link": "ens3", "flags": ["BROADCAST", "UP", "LOWER_UP"],
            "operstate": "UP", "link_type": "ether", "address": BASE_MAC,
            "linkinfo": {"info_kind": "vlan", "info_data": {"protocol": "802.1Q", "id": 20}},
            "addr_info": [{"family": "inet", "local": "10.2.20.5", "prefixlen": 24, **lifetimes(valid)}],
        },
        {
            "ifindex": 6, "ifname": "br0", "flags": ["BROADCAST", "UP", "LOWER_UP"], "operstate": "UP",
            "link_type": "ether", "address": "02:00:00:00:00:09", "linkinfo": {"info_kind": "bridge"},
            "addr_info": [{"family": "inet", "local": "10.9.0.1", "prefixlen": 24, **lifetimes(valid)}],
        },
    ]


ROUTE_DOCUMENT = [
    {"dst": "default", "gateway": "10.0.12.2", "dev": "ens3", "protocol": "dhcp", "metric": 100, "flags": []},
    {"dst": "10.0.12.0/30", "dev": "ens3", "protocol": "kernel", "scope": "link", "metric": 100, "flags": []},
    {"dst": "10.9.9.9", "gateway": "10.0.12.2", "dev": "ens3", "protocol": "static", "flags": []},
    {"dst": "fe80::/64", "dev": "ens3", "protocol": "kernel", "metric": 256, "flags": []},
]


def output(addr=None, route=None):
    return json.dumps({"addr": addr if addr is not None else addr_document(), "route": route or ROUTE_DOCUMENT})


# --- parser -----------------------------------------------------------------------------------


def test_parse_linux_net_describes_interfaces_and_routes():
    data = parse_linux_net(output(), MACS)
    by_name = {i["name"]: i for i in data["interfaces"]}
    assert list(by_name) == ["lo", "ens3", "ens4", "ens5", "ens3.20"]  # br0 is not described
    assert by_name["lo"] == {
        "name": "lo", "kind": "loopback", "admin": "up", "oper": "up", "ipv4": ["127.0.0.1/8"],
    }
    assert by_name["ens3"] == {
        "name": "ens3", "kind": "physical", "admin": "up", "oper": "up", "mac": BASE_MAC,
        "port": {"adapter": 0, "port": 0}, "ipv4": ["10.0.12.1/30"], "gateway": "10.0.12.2",
    }
    assert by_name["ens4"] == {
        "name": "ens4", "kind": "physical", "admin": "down", "oper": "down", "mac": "00:00:ab:cd:00:01",
        "port": {"adapter": 1, "port": 0}, "ipv4": [],
    }
    assert "port" not in by_name["ens5"]  # MAC matches no adapter: unknown, not guessed
    assert by_name["ens3.20"] == {
        "name": "ens3.20", "kind": "subif", "parent": "ens3", "dot1q": 20, "admin": "up", "oper": "up",
        "mac": BASE_MAC, "ipv4": ["10.2.20.5/24"],
    }
    assert data["routes"] == [
        {"dst": "10.0.12.0/30", "dev": "ens3", "proto": "kernel", "metric": 100},
        {"dst": "10.9.9.9/32", "via": "10.0.12.2", "dev": "ens3", "proto": "static"},
        {"dst": "default", "via": "10.0.12.2", "dev": "ens3", "proto": "dhcp", "metric": 100},
    ]


def test_parse_ignores_what_changes_by_itself():
    assert parse_linux_net(output(addr_document(86000)), MACS) == parse_linux_net(
        output(addr_document(85970)), MACS
    )


def test_ports_are_not_guessed_without_a_base_mac():
    data = parse_linux_net(output(), {})
    assert all("port" not in i for i in data["interfaces"])
    assert {i["name"] for i in data["interfaces"]} >= {"ens3", "ens4"}


@pytest.mark.parametrize("stdout", ["", "not json", "[]", '{"addr": 1, "route": []}', '{"addr": [{}], "route": []}'])
def test_garbage_is_a_failed_collection(stdout):
    with pytest.raises(FactError) as caught:
        parse_linux_net(stdout, MACS)
    assert caught.value.code == "failed"


def test_adapter_macs_count_up_from_the_base():
    node = {"properties": {"mac_address": "00:00:ab:cd:00:fe", "adapters": 3}}
    assert adapter_macs(node) == {"00:00:ab:cd:00:fe": 0, "00:00:ab:cd:00:ff": 1, "00:00:ab:cd:01:00": 2}
    for properties in ({}, {"adapters": 2}, {"mac_address": "zz", "adapters": 2}, {"mac_address": "", "adapters": 2}):
        assert adapter_macs({"properties": properties}) == {}
    assert adapter_macs({}) == {}


def test_the_guest_command_prints_one_json_document_or_fails(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "addr.json").write_text(json.dumps(addr_document()))
    (tmp_path / "route.json").write_text(json.dumps(ROUTE_DOCUMENT))
    fake = bin_dir / "ip"
    fake.write_text(
        '#!/bin/sh\ncase "$*" in\n"-j -d addr") cat "$FAKE_DIR/addr.json";;\n'
        '"-j route") cat "$FAKE_DIR/route.json";;\n*) exit 2;;\nesac\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE_DIR": str(tmp_path)}
    done = subprocess.run(["/bin/sh", "-c", LINUX_NET_COMMAND], capture_output=True, text=True, env=env)
    assert done.returncode == 0
    assert parse_linux_net(done.stdout, MACS)["interfaces"][1]["name"] == "ens3"
    (bin_dir / "ip").write_text("#!/bin/sh\nexit 3\n")
    broken = subprocess.run(["/bin/sh", "-c", LINUX_NET_COMMAND], capture_output=True, text=True, env=env)
    assert broken.returncode != 0 and broken.stdout == ""


# --- background collection --------------------------------------------------------------------


class FakeQga:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    async def exec(self, node_id, command, **options):
        self.calls.append((node_id, command, options))
        reply = self.reply
        if isinstance(reply, BaseException):
            raise reply
        return reply


def lab(state="READY", status="started"):
    return {
        "project_id": "p1",
        "state": state,
        "ticket": {"nodes": [{"name": "PC1", "kind": "qemu", "qga": True}, {"name": "SW1", "kind": "iou"}]},
        "nodes": [
            {
                "node_id": "n1", "name": "PC1", "node_type": "qemu", "status": status,
                "properties": {"mac_address": BASE_MAC, "adapters": 2},
            },
            {"node_id": "n2", "name": "SW1", "node_type": "iou", "status": status},
        ],
    }


def success():
    return {"exit_code": 0, "stdout": output(), "stderr": ""}


def scenario(monkeypatch, body):
    monkeypatch.setenv("SLOT_FACTS_INTERVAL", "0")  # every cycle collects again
    monkeypatch.setenv("SLOT_FACTS_MIN_REFRESH", "0")

    async def run():
        labs = {"p1": lab()}
        lifecycle = SimpleNamespace(
            all=lambda kind: list(labs.values()),
            get=lambda kind, pid: labs.get(pid),
            claimed=True,
            qga=FakeQga(success()),
        )
        facts = Facts(lifecycle)
        await facts.stop()  # the test drives cycle() itself
        await body(facts, lifecycle, labs)

    asyncio.run(run())


def collector_of(facts, node_id="n1"):
    return facts.snapshot("p1")["nodes"][node_id]["collectors"]["linux.net"]


def test_success_is_recorded_with_the_broker_priority_that_yields(monkeypatch):
    async def body(facts, lifecycle, labs):
        await facts.cycle()
        snapshot = facts.snapshot("p1")
        assert snapshot["schema"] == "gns3lab.slot-facts/1" and snapshot["project_id"] == "p1"
        assert list(snapshot["nodes"]) == ["n1"]  # the IOU node has no applicable collector
        result = snapshot["nodes"]["n1"]
        assert result["name"] == "PC1"
        record = result["collectors"]["linux.net"]
        assert record["schema"] == "linux.net/1" and record["ok"] is True and record["error"] is None
        assert record["attempted_at"] == record["observed_at"] and record["attempted_at"].endswith("Z")
        assert record["data"]["interfaces"][1]["port"] == {"adapter": 0, "port": 0}
        node_id, command, options = lifecycle.qga.calls[0]
        assert (node_id, command) == ("n1", LINUX_NET_COMMAND)
        assert options["priority"] is Priority.METRICS and options["wait"] is False

    scenario(monkeypatch, body)


@pytest.mark.parametrize(
    "failure, code",
    [
        (QgaTimeout("slow"), "timeout"),
        (QgaUnavailable("channel is backing off"), "unreachable"),
        (FileNotFoundError("no socket"), "unreachable"),
        (QgaCommandError("agent said no"), "failed"),
        ({"exit_code": 127, "stdout": "", "stderr": "sh: ip: not found"}, "unsupported"),
        ({"exit_code": 255, "stdout": "", "stderr": 'Option "-j" is unknown, try "ip -help".'}, "unsupported"),
        ({"exit_code": 1, "stdout": "", "stderr": "boom"}, "failed"),
        ({"exit_code": 0, "stdout": "garbage", "stderr": ""}, "failed"),
    ],
)
def test_failure_keeps_the_last_known_data(monkeypatch, failure, code):
    async def body(facts, lifecycle, labs):
        await facts.cycle()
        first = collector_of(facts)
        lifecycle.qga.reply = failure
        await facts.cycle()
        record = collector_of(facts)
        assert record["ok"] is False and record["error"]["code"] == code
        assert record["error"]["message"]
        assert record["data"] == first["data"] and record["observed_at"] == first["observed_at"]
        assert record["attempted_at"] >= first["attempted_at"]
        lifecycle.qga.reply = success()
        await facts.cycle()
        assert collector_of(facts)["ok"] is True and collector_of(facts)["error"] is None

    scenario(monkeypatch, body)


def test_failure_before_any_success_has_no_data(monkeypatch):
    async def body(facts, lifecycle, labs):
        lifecycle.qga.reply = QgaTimeout("slow")
        await facts.cycle()
        record = collector_of(facts)
        assert record["ok"] is False and "data" not in record and "observed_at" not in record

    scenario(monkeypatch, body)


def test_a_busy_channel_is_not_a_failure(monkeypatch):
    async def body(facts, lifecycle, labs):
        await facts.cycle()
        before = collector_of(facts)
        lifecycle.qga.reply = QgaBusy("student command in flight")
        await facts.cycle()
        assert collector_of(facts) == before  # nothing changed, not even attempted_at

    scenario(monkeypatch, body)


def test_nothing_is_collected_until_ready(monkeypatch):
    async def body(facts, lifecycle, labs):
        for state in ("DEPLOYING", "STARTING", "PROVISIONING", "FAILED", "STOPPED"):
            labs["p1"]["state"] = state
            await facts.cycle()
            assert lifecycle.qga.calls == [] and facts.snapshot("p1")["nodes"] == {}
        labs["p1"]["state"] = "READY"
        await facts.cycle()
        assert len(lifecycle.qga.calls) == 1
        labs["p1"]["state"] = "STOPPING"  # results disappear when the lab stops being READY
        await facts.cycle()
        assert facts.snapshot("p1")["nodes"] == {} and facts.results == {}

    scenario(monkeypatch, body)


def test_a_stopped_node_is_reported_without_touching_the_guest(monkeypatch):
    async def body(facts, lifecycle, labs):
        labs["p1"]["nodes"][0]["status"] = "stopped"
        await facts.cycle()
        assert lifecycle.qga.calls == []
        assert collector_of(facts)["error"]["code"] == "stopped"

    scenario(monkeypatch, body)


def test_no_collection_while_no_one_owns_the_slot(monkeypatch):
    async def body(facts, lifecycle, labs):
        lifecycle.claimed = False
        await facts.cycle()
        assert lifecycle.qga.calls == []

    scenario(monkeypatch, body)


def test_owner_change_drops_results_even_of_a_running_attempt(monkeypatch):
    async def body(facts, lifecycle, labs):
        await facts.cycle()
        assert facts.snapshot("p1")["nodes"]
        facts.reset()
        assert facts.snapshot("p1")["nodes"] == {}

        release, entered = asyncio.Event(), asyncio.Event()

        async def slow(node_id, command, **options):
            entered.set()
            await release.wait()
            return success()

        lifecycle.qga.exec = slow
        running = asyncio.create_task(facts.cycle())
        await asyncio.wait_for(entered.wait(), 5)
        facts.reset()  # the owner changes while the guest command is in flight
        release.set()
        await running
        assert facts.snapshot("p1")["nodes"] == {}

    scenario(monkeypatch, body)


def test_collection_is_paced_and_refresh_forces_it(monkeypatch):
    async def body(facts, lifecycle, labs):
        facts.interval = 3600
        await facts.cycle()
        await facts.cycle()
        assert len(lifecycle.qga.calls) == 1  # not due yet
        facts.request_refresh()
        assert facts.wake.is_set()
        await facts.cycle()
        assert len(lifecycle.qga.calls) == 2
        facts.min_refresh = 3600
        facts.wake.clear()
        facts.request_refresh()
        assert not facts.wake.is_set()  # asked again too soon: coalesced

    scenario(monkeypatch, body)


def test_the_registry_matches_the_capabilities():
    assert facts_module.COLLECTOR_IDS == [c.schema for c in COLLECTORS] == ["linux.net/1"]


# --- routes -----------------------------------------------------------------------------------


@pytest.fixture
def fast_facts(monkeypatch):
    monkeypatch.setenv("SLOT_FACTS_INTERVAL", "0.05")
    monkeypatch.setenv("SLOT_FACTS_MIN_REFRESH", "0")


def deployed(client, monkeypatch, reply):
    from api.qga import QgaBroker

    calls = []

    async def exec_(self, node_id, command, **options):
        calls.append(node_id)
        return reply

    monkeypatch.setattr(QgaBroker, "exec", exec_)
    pid = str(uuid4())
    response = client.post(
        "/v1/labs/deploy",
        headers=HEADERS,
        json={"name": "facts", "project_id": pid, "nodes": [{"name": "D1", "kind": "qemu", "image": "debian12.qcow2"}],
              "links": []},
    )
    assert response.status_code == 202, response.text
    deadline = time.time() + 5
    while time.time() < deadline:
        if client.get(f"/v1/labs/{pid}/status", headers=HEADERS).json().get("state") == "READY":
            return pid, calls
        time.sleep(0.02)
    raise AssertionError("the lab did not become READY")


def wait_for_facts(client, pid, predicate):
    deadline = time.time() + 5
    while time.time() < deadline:
        body = client.get(f"/v1/labs/{pid}/facts", headers=HEADERS).json()
        if predicate(body):
            return body
        time.sleep(0.02)
    raise AssertionError(f"facts never matched: {body}")


def test_facts_route_serves_the_latest_results(fast_facts, mock_server, monkeypatch):
    client, _projects, _nodes = mock_server
    assert client.get("/v1/labs/%s/facts" % uuid4()).status_code == 401
    unknown = client.get("/v1/labs/%s/facts" % uuid4(), headers=HEADERS)
    assert unknown.status_code == 200 and unknown.json()["nodes"] == {}  # not running: empty, not 404
    pid, calls = deployed(client, monkeypatch, success())
    body = wait_for_facts(client, pid, lambda b: b["nodes"])
    assert body["schema"] == "gns3lab.slot-facts/1" and body["project_id"] == pid
    (node,) = body["nodes"].values()
    assert node["name"] == "D1"
    record = node["collectors"]["linux.net"]
    assert record["ok"] is True and record["data"]["routes"][-1]["dst"] == "default"
    assert "raw" not in record and "raw_sha256" not in record  # no raw output leaves the slot
    # Stopping the lab removes its facts: the Manager must not store a dead lab as current.
    stopped = client.post(f"/v1/labs/{pid}/stop", headers=HEADERS)
    assert stopped.status_code in (200, 202), stopped.text
    wait_for_facts(client, pid, lambda b: b["nodes"] == {})


def test_refresh_needs_the_owner_and_a_known_lab(fast_facts, mock_server, monkeypatch):
    client, _projects, _nodes = mock_server
    pid, _calls = deployed(client, monkeypatch, success())
    ok = client.post(f"/v1/labs/{pid}/facts/refresh", headers=HEADERS)
    assert ok.status_code == 202 and ok.json() == {"accepted": True}
    stale = client.post(
        f"/v1/labs/{pid}/facts/refresh",
        headers={**HEADERS, "X-Lab-Epoch": "9", "X-Lab-Generation": "9"},
    )
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "STALE_OWNER"
    assert client.post(f"/v1/labs/{uuid4()}/facts/refresh", headers=HEADERS).status_code == 404
    assert client.post(f"/v1/labs/{pid}/facts/refresh").status_code == 401


def test_capabilities_announce_the_collectors(mock_server):
    client, _projects, _nodes = mock_server
    body = client.get("/v1/capabilities", headers=HEADERS).json()
    assert "collectors" in body["features"] and body["collectors"] == ["linux.net/1"]
    assert os.getenv("SLOT_API_TOKEN") == "test-token"
