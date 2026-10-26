"""Guest facts: the linux.net, switch.net, switch.fdb and windows.net collectors, background collection and the /facts routes.

The ios.config collector (saved configuration of IOL nodes) is in test_ios_config.py.
"""

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
    SWITCH_FDB_COMMAND,
    SWITCH_NET_COMMAND,
    WINDOWS_NET_COMMAND,
    Facts,
    FactError,
    adapter_macs,
    guest_is_windows,
    parse_linux_net,
    parse_switch_fdb,
    parse_switch_net,
    parse_windows_net,
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


# --- switch.net parser ------------------------------------------------------------------------

SWITCH_FLAGS_UP = ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"]
UNTAGGED_PVID = ["PVID", "Egress Untagged"]


def switch_port(ifindex, name, mac, flags=SWITCH_FLAGS_UP, operstate="UP", traffic=0):
    return {
        "ifindex": ifindex, "ifname": name, "flags": flags, "mtu": 1500, "master": "br0",
        "operstate": operstate, "link_type": "ether", "address": mac,
        "stats64": {
            "rx": {"bytes": traffic, "packets": traffic // 60, "errors": 0, "dropped": 0},
            "tx": {"bytes": traffic * 2, "packets": traffic // 30, "errors": 0, "dropped": 0},
        },
    }


def switch_ports(traffic=0):
    return [
        switch_port(2, "ens3", BASE_MAC, traffic=traffic),
        switch_port(3, "ens4", "00:00:ab:cd:00:01", traffic=traffic),
        switch_port(4, "ens5", "52:54:00:aa:bb:05", ["BROADCAST", "MULTICAST"], "DOWN", traffic),
        switch_port(5, "ens6", "52:54:00:aa:bb:06", traffic=traffic),
        switch_port(6, "ens7", "52:54:00:aa:bb:07", traffic=traffic),
        switch_port(7, "ens8", "52:54:00:aa:bb:08", traffic=traffic),
        switch_port(8, "ens9", "52:54:00:aa:bb:09", ["BROADCAST", "MULTICAST", "UP"], "DOWN", traffic),
    ]


# `bridge -j vlan show`: br0 and a port that `switchctl ports` does not list are not ports of the switch.
SWITCH_VLANS = [
    {"ifname": "br0", "vlans": [{"vlan": 1, "flags": UNTAGGED_PVID}]},
    {"ifname": "ens3", "vlans": [{"vlan": 10, "flags": UNTAGGED_PVID}]},
    {"ifname": "ens4", "vlans": [{"vlan": 10, "flags": []}, {"vlan": 20, "flags": UNTAGGED_PVID}]},
    {"ifname": "ens5", "vlans": [{"vlan": 20, "flags": []}, {"vlan": 10}]},
    {"ifname": "ens7", "vlans": [
        {"vlan": 100, "vlanEnd": 102, "flags": []}, {"vlan": 200, "flags": UNTAGGED_PVID},
    ]},
    {"ifname": "ens8", "vlans": [{"vlan": 10, "flags": []}]},
    {"ifname": "ens9", "vlans": [{"vlan": 30, "flags": UNTAGGED_PVID}]},
    {"ifname": "ghost", "vlans": [{"vlan": 99, "flags": UNTAGGED_PVID}]},
]


def envelope(result):
    return {"version": 1, "id": "0123", "ok": True, "observed_at": 1792800000.0, "result": result}


def refusal(code="COMMAND_FAILED", message="bridge failed"):
    return {"version": 1, "id": "0123", "ok": False, "error": {"code": code, "message": message}}


def switch_document(ports=None, actual=None):
    return {
        "ports": ports if isinstance(ports, dict) else envelope({"ports": switch_ports() if ports is None else ports}),
        "vlans": envelope({
            "desired": None, "last_error": None, "applied": True,
            "actual": SWITCH_VLANS if actual is None else actual,
        }),
    }


def switch_output(**options):
    return json.dumps(switch_document(**options))


def switch_success():
    return {"exit_code": 0, "stdout": switch_output(), "stderr": ""}


EXPECTED_SWITCH_PORTS = [
    {"name": "ens3", "kind": "physical", "admin": "up", "oper": "up", "mac": BASE_MAC,
     "port": {"adapter": 0, "port": 0}, "switchport": {"mode": "access", "vlan": 10}},
    {"name": "ens4", "kind": "physical", "admin": "up", "oper": "up", "mac": "00:00:ab:cd:00:01",
     "port": {"adapter": 1, "port": 0}, "switchport": {"mode": "trunk", "allowed": [10, 20], "native": 20}},
    {"name": "ens5", "kind": "physical", "admin": "down", "oper": "down", "mac": "52:54:00:aa:bb:05",
     "switchport": {"mode": "trunk", "allowed": [10, 20], "native": None}},
    {"name": "ens6", "kind": "physical", "admin": "up", "oper": "up", "mac": "52:54:00:aa:bb:06",
     "switchport": {"mode": "none"}},
    {"name": "ens7", "kind": "physical", "admin": "up", "oper": "up", "mac": "52:54:00:aa:bb:07",
     "switchport": {"mode": "trunk", "allowed": [100, 101, 102, 200], "native": 200}},
    {"name": "ens8", "kind": "physical", "admin": "up", "oper": "up", "mac": "52:54:00:aa:bb:08",
     "switchport": {"mode": "trunk", "allowed": [10], "native": None}},
    {"name": "ens9", "kind": "physical", "admin": "up", "oper": "down", "mac": "52:54:00:aa:bb:09",
     "switchport": {"mode": "access", "vlan": 30}},
]
# The student does not manage the ports of this image: the scheme repeats the other end of the cable
# (live-state.md 3.1, `mirror`). Only switch.net says so; a Cisco switch port never carries it.
EXPECTED_SWITCH_PORTS = [{**port, "mirror": True} for port in EXPECTED_SWITCH_PORTS]


def test_parse_switch_net_describes_the_ports_by_their_actual_membership():
    # Access, trunk with and without native, no membership, a range, a lone tagged VLAN; br0 and ghost are not ports.
    assert parse_switch_net(switch_output(), MACS) == {"interfaces": EXPECTED_SWITCH_PORTS}


def test_switch_ports_come_in_ifindex_order_and_find_their_adapter_by_mac():
    ports = switch_ports()
    # The first two cards trade names: the order and the names of the guest say nothing, the MAC does.
    ports[0], ports[1] = {**ports[0], "ifname": "eth9"}, {**ports[1], "ifname": "eth0"}
    data = parse_switch_net(switch_output(ports=list(reversed(ports))), MACS)
    names = [i["name"] for i in data["interfaces"]]
    assert names == ["eth9", "eth0", "ens5", "ens6", "ens7", "ens8", "ens9"]
    by_name = {i["name"]: i for i in data["interfaces"]}
    assert by_name["eth9"]["port"] == {"adapter": 0, "port": 0}
    assert by_name["eth0"]["port"] == {"adapter": 1, "port": 0}
    assert "port" not in by_name["ens6"]
    assert all("port" not in i for i in parse_switch_net(switch_output(), {})["interfaces"])


def test_switch_data_does_not_move_with_the_counters():
    quiet = parse_switch_net(switch_output(ports=switch_ports(traffic=0)), MACS)
    busy = parse_switch_net(switch_output(ports=switch_ports(traffic=10**9)), MACS)
    assert quiet == busy
    assert "stats64" not in json.dumps(busy) and "bytes" not in json.dumps(busy)


@pytest.mark.parametrize(
    "stdout, fragment",
    [
        ("", "JSONDecodeError"),
        ("not json", "JSONDecodeError"),
        ("[]", "TypeError"),
        ('{"ports": null, "vlans": null}', "no valid response"),
        (switch_output(ports=refusal("CLIENT_ERROR", "no such socket")), "CLIENT_ERROR"),
        (json.dumps({**switch_document(), "vlans": refusal("VLAN_APPLY_FAILED", "ip said no")}), "ip said no"),
        (switch_output(ports=envelope({"nope": 1})), "KeyError"),
        (switch_output(ports=envelope({"ports": {}})), "TypeError"),
        (switch_output(actual=[{"ifname": "ens3", "vlans": [{"vlan": 5000}]}]), "ValueError"),
        (switch_output(actual=[{"ifname": "ens3", "vlans": [{"vlan": 12, "vlanEnd": 10}]}]), "ValueError"),
        (switch_output(actual={"ifname": "ens3"}), "TypeError"),
        (switch_output(ports=[{"ifindex": 2}]), "KeyError"),
    ],
)
def test_garbage_from_the_switch_is_a_failed_collection(stdout, fragment):
    with pytest.raises(FactError) as caught:
        parse_switch_net(stdout, MACS)
    assert caught.value.code == "failed" and fragment in str(caught.value)


def fake_switchctl(tmp_path, ports, vlans, status=0):
    tool = tmp_path / "switchctl"
    tool.write_text(
        f'#!/bin/sh\ncase "$1" in ports) cat "$0.ports";; vlans) cat "$0.vlans";; esac\nexit {status}\n'
    )
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "switchctl.ports").write_text(ports)
    (tmp_path / "switchctl.vlans").write_text(vlans)
    return tool


def run_switch_command(tool):
    command = SWITCH_NET_COMMAND.replace("/usr/local/sbin/switchctl", str(tool))
    assert command != SWITCH_NET_COMMAND
    return subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True)


def test_the_switch_command_glues_the_two_envelopes(tmp_path):
    document = switch_document()
    tool = fake_switchctl(tmp_path, json.dumps(document["ports"]), json.dumps(document["vlans"]))
    done = run_switch_command(tool)
    assert done.returncode == 0
    assert parse_switch_net(done.stdout, MACS) == {"interfaces": EXPECTED_SWITCH_PORTS}


def test_the_switch_command_exits_127_without_switchctl(tmp_path):
    done = run_switch_command(tmp_path / "missing")
    assert done.returncode == 127 and done.stdout == ""


def test_the_switch_command_hands_over_an_error_envelope(tmp_path):
    # switchctl prints the envelope and exits 1 when the daemon says no: the reason must survive.
    tool = fake_switchctl(tmp_path, json.dumps(refusal("BUSY", "daemon busy")), json.dumps(refusal()), status=1)
    done = run_switch_command(tool)
    assert done.returncode == 0
    with pytest.raises(FactError) as caught:
        parse_switch_net(done.stdout, MACS)
    assert caught.value.code == "failed" and "daemon busy" in str(caught.value)
    # A switchctl that prints nothing at all (no Python in the guest, say) is still one document.
    silent = run_switch_command(fake_switchctl(tmp_path, "", "", status=127))
    assert silent.returncode == 0 and json.loads(silent.stdout) == {"ports": None, "vlans": None}
    with pytest.raises(FactError):
        parse_switch_net(silent.stdout, MACS)


# --- switch.fdb parser ------------------------------------------------------------------------

# `bridge -j fdb show br br0` of a live switch image: five ports, two nodes, one of which has spoken.
# The only learned row is the first. The MAC of every port comes twice (with a VLAN and without), the
# `self` rows are group addresses of the devices, and nothing but `br0` itself has a `master` in them.
REAL_FDB = [
    {"mac": "50:5b:3c:02:a2:00", "ifname": "ens3", "vlan": 1, "flags": [], "master": "br0", "state": ""},
    {"mac": "50:df:d5:02:ee:00", "ifname": "ens3", "vlan": 1, "flags": [], "master": "br0", "state": "permanent"},
    {"mac": "50:df:d5:02:ee:00", "ifname": "ens3", "flags": [], "master": "br0", "state": "permanent"},
    {"mac": "33:33:00:00:00:01", "ifname": "ens3", "flags": ["self"], "state": "permanent"},
    {"mac": "01:00:5e:00:00:01", "ifname": "ens3", "flags": ["self"], "state": "permanent"},
    {"mac": "33:33:ff:33:a1:c4", "ifname": "ens3", "flags": ["self"], "state": "permanent"},
    {"mac": "50:df:d5:02:ee:01", "ifname": "ens4", "vlan": 1, "flags": [], "master": "br0", "state": "permanent"},
    {"mac": "50:df:d5:02:ee:01", "ifname": "ens4", "flags": [], "master": "br0", "state": "permanent"},
    {"mac": "33:33:00:00:00:01", "ifname": "ens4", "flags": ["self"], "state": "permanent"},
    {"mac": "01:00:5e:00:00:01", "ifname": "ens4", "flags": ["self"], "state": "permanent"},
    {"mac": "50:df:d5:02:ee:02", "ifname": "ens5", "vlan": 1, "flags": [], "master": "br0", "state": "permanent"},
    {"mac": "50:df:d5:02:ee:02", "ifname": "ens5", "flags": [], "master": "br0", "state": "permanent"},
    {"mac": "33:33:00:00:00:01", "ifname": "ens5", "flags": ["self"], "state": "permanent"},
    {"mac": "01:00:5e:00:00:01", "ifname": "ens5", "flags": ["self"], "state": "permanent"},
    {"mac": "50:df:d5:02:ee:03", "ifname": "ens6", "vlan": 1, "flags": [], "master": "br0", "state": "permanent"},
    {"mac": "50:df:d5:02:ee:03", "ifname": "ens6", "flags": [], "master": "br0", "state": "permanent"},
    {"mac": "33:33:00:00:00:01", "ifname": "ens6", "flags": ["self"], "state": "permanent"},
    {"mac": "01:00:5e:00:00:01", "ifname": "ens6", "flags": ["self"], "state": "permanent"},
    {"mac": "50:df:d5:02:ee:04", "ifname": "ens7", "vlan": 1, "flags": [], "master": "br0", "state": "permanent"},
    {"mac": "50:df:d5:02:ee:04", "ifname": "ens7", "flags": [], "master": "br0", "state": "permanent"},
    {"mac": "33:33:00:00:00:01", "ifname": "ens7", "flags": ["self"], "state": "permanent"},
    {"mac": "01:00:5e:00:00:01", "ifname": "ens7", "flags": ["self"], "state": "permanent"},
    {"mac": "33:33:00:00:00:01", "ifname": "br0", "flags": ["self"], "state": "permanent"},
    {"mac": "01:00:5e:00:00:6a", "ifname": "br0", "flags": ["self"], "state": "permanent"},
    {"mac": "33:33:00:00:00:6a", "ifname": "br0", "flags": ["self"], "state": "permanent"},
    {"mac": "01:00:5e:00:00:01", "ifname": "br0", "flags": ["self"], "state": "permanent"},
    {"mac": "76:61:46:f0:2f:8f", "ifname": "br0", "flags": [], "master": "br0", "state": "permanent"},
]
LEARNED = {"mac_table": {"entries": [{"mac": "50:5b:3c:02:a2:00", "iface": "ens3", "vlan": 1}], "truncated": False}}


def learned_row(mac="52:54:00:aa:00:01", ifname="ens3", vlan=10, **fields):
    return {"mac": mac, "ifname": ifname, "vlan": vlan, "flags": [], "master": "br0", "state": "", **fields}


def fdb_envelope(rows, next_offset=None):
    return envelope({"entries": rows, "total": len(rows), "next_offset": next_offset})


def fdb_output(rows=None, next_offset=None, wrapped=None):
    document = fdb_envelope(REAL_FDB if rows is None else rows, next_offset) if wrapped is None else wrapped
    return json.dumps({"fdb": document})


def fdb_success(**options):
    return {"exit_code": 0, "stdout": fdb_output(**options), "stderr": ""}


def test_parse_switch_fdb_keeps_what_the_switch_has_learned_and_drops_its_own_rows():
    assert parse_switch_fdb(fdb_output()) == LEARNED
    assert len(REAL_FDB) == 27  # the other 26 are the switch's, not its lessons


@pytest.mark.parametrize(
    "state, flags",
    [("permanent", []), ("static", []), ("", ["self"]), ("reachable", ["self", "extern_learn"])],
)
def test_rows_of_the_switch_itself_are_not_lessons(state, flags):
    row = learned_row(state=state, flags=flags)
    assert parse_switch_fdb(fdb_output(rows=[row])) == {"mac_table": {"entries": [], "truncated": False}}


def test_the_mac_table_has_a_fixed_order_and_only_the_three_facts():
    rows = [
        learned_row("52:54:00:AA:00:02", "ens4", 20, updated=3, used=9),
        learned_row("52:54:00:aa:00:01", "ens3", 20, state="reachable"),
        {key: value for key, value in learned_row("52:54:00:aa:00:03", "ens3", 10).items() if key != "state"},
        *REAL_FDB[1:],
    ]
    data = parse_switch_fdb(fdb_output(rows=rows))
    assert data == {"mac_table": {"entries": [
        {"mac": "52:54:00:aa:00:03", "iface": "ens3", "vlan": 10},
        {"mac": "52:54:00:aa:00:01", "iface": "ens3", "vlan": 20},
        {"mac": "52:54:00:aa:00:02", "iface": "ens4", "vlan": 20},
    ], "truncated": False}}
    # Nothing that moves by itself: other timers and another order of the same table change nothing.
    ticking = [dict(row, updated=99, used=77) for row in reversed(rows)]
    assert parse_switch_fdb(fdb_output(rows=ticking)) == data


def test_an_empty_table_is_an_empty_table():
    assert parse_switch_fdb(fdb_output(rows=[])) == {"mac_table": {"entries": [], "truncated": False}}


def test_a_table_longer_than_the_page_is_truncated():
    data = parse_switch_fdb(fdb_output(next_offset=1024))
    assert data == {"mac_table": {**LEARNED["mac_table"], "truncated": True}}


def test_a_table_too_big_for_the_guest_is_a_fact_not_a_failure():
    # The manager carries the `ok: false` of any collector to the `error` of the whole node.
    refused = refusal("RESULT_TOO_LARGE", "Linux command output exceeds limit")
    assert parse_switch_fdb(fdb_output(wrapped=refused)) == {"mac_table": {"entries": [], "truncated": True}}


@pytest.mark.parametrize(
    "stdout, fragment",
    [
        ("", "JSONDecodeError"),
        ("not json", "JSONDecodeError"),
        ("[]", "TypeError"),
        ('{"fdb": null}', "no valid response"),
        (fdb_output(wrapped=refusal("BUSY", "Too many clients")), "BUSY: Too many clients"),
        (fdb_output(wrapped=refusal("COMMAND_FAILED", "bridge failed")), "COMMAND_FAILED"),
        (fdb_output(wrapped=refusal("TIMEOUT", "slow")), "TIMEOUT"),
        (fdb_output(wrapped=envelope({"nope": 1})), "KeyError"),
        (fdb_output(wrapped=envelope({"entries": {}, "next_offset": None})), "TypeError"),
        (fdb_output(wrapped=envelope({"entries": []})), "KeyError"),  # no next_offset: not the contract
        (fdb_output(rows=["not a row"]), "TypeError"),
        (fdb_output(rows=[learned_row(flags="self")]), "TypeError"),
        (fdb_output(rows=[{k: v for k, v in learned_row().items() if k != "vlan"}]), "KeyError"),  # learned: always a VLAN
        (fdb_output(rows=[{k: v for k, v in learned_row().items() if k != "ifname"}]), "KeyError"),
        (fdb_output(rows=[learned_row(vlan=0)]), "ValueError"),
        (fdb_output(rows=[learned_row(vlan=4095)]), "ValueError"),
        (fdb_output(rows=[learned_row(vlan="10")]), "ValueError"),
        (fdb_output(rows=[learned_row(vlan=True)]), "ValueError"),
        (fdb_output(rows=[learned_row(mac="52:54:00:aa:00")]), "ValueError"),
        (fdb_output(rows=[learned_row(mac="not a mac at all")]), "ValueError"),
        (fdb_output(rows=[learned_row(mac=5254)]), "ValueError"),
        (fdb_output(rows=[learned_row(ifname="")]), "ValueError"),
    ],
)
def test_garbage_from_the_mac_table_is_a_failed_collection(stdout, fragment):
    with pytest.raises(FactError) as caught:
        parse_switch_fdb(stdout)
    assert caught.value.code == "failed" and fragment in str(caught.value)


def fake_fdb_switchctl(tmp_path, answer, status=0):
    """A switchctl that notes its arguments and prints `answer`."""
    tool = tmp_path / "switchctl"
    tool.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" > "$0.args"\ncat "$0.out"\nexit {status}\n')
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "switchctl.out").write_text(answer)
    return tool


def run_fdb_command(tool):
    command = SWITCH_FDB_COMMAND.replace("/usr/local/sbin/switchctl", str(tool))
    assert command != SWITCH_FDB_COMMAND
    return subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True)


def test_the_fdb_command_asks_for_one_page_and_wraps_the_envelope(tmp_path):
    tool = fake_fdb_switchctl(tmp_path, json.dumps(fdb_envelope(REAL_FDB)))
    done = run_fdb_command(tool)
    assert done.returncode == 0
    assert (tmp_path / "switchctl.args").read_text().strip() == 'fdb --params {"limit":1024}'
    assert parse_switch_fdb(done.stdout) == LEARNED


def test_the_fdb_command_exits_127_without_switchctl(tmp_path):
    done = run_fdb_command(tmp_path / "missing")
    assert done.returncode == 127 and done.stdout == ""


def test_the_fdb_command_hands_over_an_error_envelope(tmp_path):
    # switchctl prints the envelope and exits 1 when the daemon says no: the reason must survive.
    done = run_fdb_command(fake_fdb_switchctl(tmp_path, json.dumps(refusal("BUSY", "daemon busy")), status=1))
    assert done.returncode == 0
    with pytest.raises(FactError) as caught:
        parse_switch_fdb(done.stdout)
    assert caught.value.code == "failed" and "daemon busy" in str(caught.value)
    # A switchctl that prints nothing at all is still one document.
    silent = run_fdb_command(fake_fdb_switchctl(tmp_path, "", status=127))
    assert silent.returncode == 0 and json.loads(silent.stdout) == {"fdb": None}
    with pytest.raises(FactError):
        parse_switch_fdb(silent.stdout)


# --- background collection --------------------------------------------------------------------


NOT_A_SWITCH = {"exit_code": 127, "stdout": "", "stderr": ""}  # no switchctl in the guest


DEBIAN = {"id": "debian", "name": "Debian GNU/Linux", "kernel-release": "6.1.0"}
WINDOWS = {"id": "mswindows", "name": "Microsoft Windows", "version": "Microsoft Windows 10 Pro"}


class FakeQga:
    """`reply` answers the linux.net command, `switch_reply` the switch.net one (an ordinary host by default),
    `fdb_reply` the switch.fdb one (an ordinary host by default as well).

    `osinfo` is what `guest-get-osinfo` returns (or raises): a Debian guest unless a test says otherwise.
    """

    def __init__(self, reply, switch_reply=NOT_A_SWITCH, osinfo=DEBIAN, fdb_reply=NOT_A_SWITCH):
        self.reply = reply
        self.switch_reply = switch_reply
        self.fdb_reply = fdb_reply
        self.osinfo = osinfo
        self.windows_reply = None
        self.switch_by_node = {}  # node_id -> reply: a lab with a switch next to ordinary hosts
        self.fdb_by_node = {}  # the same for the MAC table
        self.calls = []  # exec only
        self.osinfo_calls = []

    def asked(self, command):
        return [call for call in self.calls if call[1] == command]

    async def call(self, node_id, command, args=None, **options):
        assert command == "guest-get-osinfo"
        self.osinfo_calls.append((node_id, options))
        if isinstance(self.osinfo, BaseException):
            raise self.osinfo
        return self.osinfo

    async def exec(self, node_id, command, **options):
        self.calls.append((node_id, command, options))
        reply = self.reply
        if command == SWITCH_NET_COMMAND:
            reply = self.switch_by_node.get(node_id, self.switch_reply)
        if command == SWITCH_FDB_COMMAND:
            reply = self.fdb_by_node.get(node_id, self.fdb_reply)
        if command == WINDOWS_NET_COMMAND:
            reply = self.windows_reply
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
        assert list(snapshot["nodes"]) == ["n1"]  # the IOU node has no saved configuration yet: no record
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
        assert len(lifecycle.qga.asked(LINUX_NET_COMMAND)) == 1
        labs["p1"]["state"] = "STOPPING"  # results disappear when the lab stops being READY
        await facts.cycle()
        assert facts.snapshot("p1")["nodes"] == {} and facts.results == {}

    scenario(monkeypatch, body)


def test_a_stopped_node_is_reported_without_touching_the_guest(monkeypatch):
    async def body(facts, lifecycle, labs):
        await facts.cycle()  # while it runs the guest tells what it is
        lifecycle.qga.calls.clear()
        lifecycle.qga.osinfo_calls.clear()
        labs["p1"]["nodes"][0]["status"] = "stopped"
        await facts.cycle()
        assert lifecycle.qga.calls == [] and lifecycle.qga.osinfo_calls == []
        assert collector_of(facts)["error"]["code"] == "stopped"
        assert list(facts.snapshot("p1")["nodes"]["n1"]["collectors"]) == ["linux.net"]

    scenario(monkeypatch, body)


def test_a_node_that_was_never_asked_says_nothing_while_stopped(monkeypatch):
    async def body(facts, lifecycle, labs):
        labs["p1"]["nodes"][0]["status"] = "stopped"
        await facts.cycle()
        # Linux or Windows is unknown: a `stopped` under either name would stay as a stranger's record.
        assert lifecycle.qga.calls == [] and lifecycle.qga.osinfo_calls == []
        assert facts.snapshot("p1")["nodes"] == {}

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
        assert len(lifecycle.qga.asked(LINUX_NET_COMMAND)) == 1  # not due yet
        facts.request_refresh()
        assert facts.wake.is_set()
        await facts.cycle()
        assert len(lifecycle.qga.asked(LINUX_NET_COMMAND)) == 2
        facts.min_refresh = 3600
        facts.wake.clear()
        facts.request_refresh()
        assert not facts.wake.is_set()  # asked again too soon: coalesced

    scenario(monkeypatch, body)


# --- switch.net collection --------------------------------------------------------------------

SWITCH_KEY = ("p1", "n3", "switch.net")
FDB_KEY = ("p1", "n3", "switch.fdb")
SWITCH_ROLE = {SWITCH_KEY, FDB_KEY}  # what a working switch has confirmed
HOST_ABSENT = {("p1", "n1", "switch.net"), ("p1", "n1", "switch.fdb")}  # what the ordinary host said


def add_switch(lifecycle, labs, reply=None, fdb_reply=None):
    """A QEMU node n3 next to the ordinary host n1; its guest answers the switch.net and switch.fdb commands."""
    labs["p1"]["ticket"]["nodes"].append({"name": "SW2", "kind": "qemu", "qga": True})
    labs["p1"]["nodes"].append(
        {"node_id": "n3", "name": "SW2", "node_type": "qemu", "status": "started",
         "properties": {"mac_address": BASE_MAC, "adapters": 2}}
    )
    lifecycle.qga.switch_by_node["n3"] = reply or switch_success()
    lifecycle.qga.fdb_by_node["n3"] = fdb_reply or fdb_success()


def switch_of(facts, node_id="n3"):
    """The switch.net record of a node; None when the slot says nothing about it."""
    node = facts.snapshot("p1")["nodes"].get(node_id, {})
    return node.get("collectors", {}).get("switch.net")


FAILURES = [
    (QgaTimeout("slow"), "timeout"),
    (QgaUnavailable("channel is backing off"), "unreachable"),
    (FileNotFoundError("no socket"), "unreachable"),
    (QgaCommandError("agent said no"), "failed"),
    ({"exit_code": 1, "stdout": "", "stderr": "boom"}, "failed"),
    ({"exit_code": 0, "stdout": "garbage", "stderr": ""}, "failed"),
    ({"exit_code": 0, "stdout": json.dumps({"ports": refusal(), "vlans": refusal()}), "stderr": ""}, "failed"),
]


def test_a_switch_is_published_next_to_linux_net_with_the_broker_priority_that_yields(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs)
        await facts.cycle()
        collectors = facts.snapshot("p1")["nodes"]["n3"]["collectors"]
        assert list(collectors) == ["linux.net", "switch.net", "switch.fdb"]
        record = collectors["switch.net"]
        assert record["schema"] == "switch.net/1" and record["ok"] is True and record["error"] is None
        assert record["attempted_at"] == record["observed_at"] and record["attempted_at"].endswith("Z")
        assert record["data"] == {"interfaces": EXPECTED_SWITCH_PORTS}  # no routes: linux.net owns them
        assert collectors["linux.net"]["data"] == collector_of(facts)["data"]  # as on any other host
        (call,) = [c for c in lifecycle.qga.asked(SWITCH_NET_COMMAND) if c[0] == "n3"]
        assert call[2]["priority"] is Priority.METRICS and call[2]["wait"] is False and call[2]["max_age"] == 2
        assert facts.confirmed == SWITCH_ROLE and facts.absent == HOST_ABSENT

    scenario(monkeypatch, body)


def test_an_ordinary_host_is_asked_once_and_never_gets_a_record(monkeypatch):
    async def body(facts, lifecycle, labs):
        for _ in range(3):
            await facts.cycle()
        assert len(lifecycle.qga.asked(LINUX_NET_COMMAND)) == 3  # linux.net goes on as before
        assert len(lifecycle.qga.asked(SWITCH_NET_COMMAND)) == 1  # the guest said it is no switch
        assert facts.absent == HOST_ABSENT and facts.confirmed == set()
        assert list(facts.snapshot("p1")["nodes"]["n1"]["collectors"]) == ["linux.net"]

    scenario(monkeypatch, body)


@pytest.mark.parametrize("failure, code", FAILURES + [(None, "stopped")])
def test_failures_before_the_first_success_are_silent_unless_the_guest_answered(monkeypatch, failure, code):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs, failure)
        if failure is None:  # a node that has not started says nothing about its role either
            labs["p1"]["nodes"][2]["status"] = "stopped"
        await facts.cycle()
        record = switch_of(facts)
        # Whatever the broker raised (any exchange failure, a refusal of the agent included) and a node
        # that is not started say nothing about the role. The manager never deletes a collector's row,
        # so a record published now would stay as a stranger's error on an ordinary host.
        if isinstance(failure, BaseException) or failure is None:
            assert record is None and facts.absent == HOST_ABSENT
            if failure is None:  # never asked: it is not known what it is, so nothing is published
                assert "n3" not in facts.snapshot("p1")["nodes"]
            else:
                assert facts.snapshot("p1")["nodes"]["n3"]["collectors"]["linux.net"]  # the host is still reported
        else:  # switchctl is there but does not answer properly: the owner has to see it
            assert record["ok"] is False and record["error"]["code"] == "failed" and record["error"]["message"]
            assert "data" not in record and "observed_at" not in record
        lifecycle.qga.switch_by_node["n3"] = switch_success()
        labs["p1"]["nodes"][2]["status"] = "started"
        await facts.cycle()
        assert switch_of(facts)["ok"] is True and facts.confirmed == SWITCH_ROLE

    scenario(monkeypatch, body)


@pytest.mark.parametrize("failure, code", FAILURES + [(None, "stopped")])
def test_failures_after_the_first_success_are_published_with_the_last_data(monkeypatch, failure, code):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs)
        await facts.cycle()
        first = switch_of(facts)
        if failure is None:
            labs["p1"]["nodes"][2]["status"] = "stopped"
        else:
            lifecycle.qga.switch_by_node["n3"] = failure
        await facts.cycle()
        record = switch_of(facts)
        assert record["ok"] is False and record["error"]["code"] == code and record["error"]["message"]
        assert record["data"] == first["data"] and record["observed_at"] == first["observed_at"]
        assert record["attempted_at"] >= first["attempted_at"]
        labs["p1"]["nodes"][2]["status"] = "started"
        lifecycle.qga.switch_by_node["n3"] = switch_success()
        await facts.cycle()
        assert switch_of(facts)["ok"] is True and switch_of(facts)["error"] is None

    scenario(monkeypatch, body)


def test_a_guest_that_loses_switchctl_is_no_switch_any_more(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs)
        await facts.cycle()
        assert switch_of(facts)["ok"] is True
        lifecycle.qga.switch_by_node["n3"] = NOT_A_SWITCH
        await facts.cycle()
        assert switch_of(facts) is None and SWITCH_KEY in facts.absent and SWITCH_KEY not in facts.confirmed
        assert facts.snapshot("p1")["nodes"]["n3"]["collectors"]["linux.net"]["ok"] is True
        lifecycle.qga.switch_by_node["n3"] = switch_success()
        await facts.cycle()  # not asked again: the role is remembered
        assert switch_of(facts) is None and len(lifecycle.qga.asked(SWITCH_NET_COMMAND)) == 3

    scenario(monkeypatch, body)


def test_the_role_is_remembered_until_the_owner_or_the_lab_goes(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs)
        await facts.cycle()
        assert len(lifecycle.qga.asked(SWITCH_NET_COMMAND)) == 2
        facts.reset()  # another owner: everything is learned again
        assert facts.absent == set() and facts.confirmed == set()
        await facts.cycle()
        assert len(lifecycle.qga.asked(SWITCH_NET_COMMAND)) == 4 and facts.confirmed == SWITCH_ROLE
        labs["p1"]["state"] = "STOPPING"  # the lab leaves READY: its memory goes with its results
        await facts.cycle()
        assert facts.absent == set() and facts.confirmed == set() and facts.results == {}
        labs["p1"]["state"] = "READY"
        await facts.cycle()
        assert len(lifecycle.qga.asked(SWITCH_NET_COMMAND)) == 6
        assert facts.absent == HOST_ABSENT and facts.confirmed == SWITCH_ROLE
        # Forgotten with the memory: a timeout of a node that has not answered yet is silent again.
        facts.reset()
        lifecycle.qga.switch_by_node["n3"] = QgaTimeout("slow")
        lifecycle.qga.fdb_by_node["n3"] = QgaTimeout("slow")
        await facts.cycle()
        assert switch_of(facts) is None and facts.confirmed == set()

    scenario(monkeypatch, body)


def test_an_attempt_of_the_previous_owner_leaves_no_memory(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs)
        release, entered = asyncio.Event(), asyncio.Event()
        replies = {"n1": NOT_A_SWITCH, "n3": switch_success()}
        asked = []

        async def slow(node_id, command, **options):
            if command == LINUX_NET_COMMAND:
                return success()
            asked.append(node_id)
            if len(asked) == 2:
                entered.set()
            await release.wait()
            return replies[node_id]

        lifecycle.qga.exec = slow
        running = asyncio.create_task(facts.cycle())
        await asyncio.wait_for(entered.wait(), 5)
        facts.reset()  # the owner changes while the guests are being asked
        release.set()
        await running
        assert facts.absent == set() and facts.confirmed == set() and facts.snapshot("p1")["nodes"] == {}

    scenario(monkeypatch, body)


def test_the_collectors_of_a_node_share_its_channel_and_run_one_after_another(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs)
        running, peak = {}, {}
        answer = lifecycle.qga.exec

        async def watched(node_id, command, **options):
            running[node_id] = running.get(node_id, 0) + 1
            peak[node_id] = max(peak.get(node_id, 0), running[node_id])
            try:
                await asyncio.sleep(0.01)
                return await answer(node_id, command, **options)
            finally:
                running[node_id] -= 1

        lifecycle.qga.exec = watched
        await facts.cycle()
        assert peak == {"n1": 1, "n3": 1}  # a second exec at once would be refused as busy
        assert len(lifecycle.qga.calls) == 6 and switch_of(facts)["ok"] is True

    scenario(monkeypatch, body)


def test_the_collectors_fail_independently(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs)
        await facts.cycle()
        lifecycle.qga.reply = {"exit_code": 1, "stdout": "", "stderr": "ip broke"}
        await facts.cycle()
        assert facts.snapshot("p1")["nodes"]["n3"]["collectors"]["linux.net"]["error"]["code"] == "failed"
        assert switch_of(facts)["ok"] is True
        lifecycle.qga.reply = success()
        lifecycle.qga.switch_by_node["n3"] = {"exit_code": 1, "stdout": "", "stderr": "daemon down"}
        await facts.cycle()
        assert collector_of(facts, "n3")["ok"] is True
        assert switch_of(facts)["ok"] is False and "daemon down" in switch_of(facts)["error"]["message"]

    scenario(monkeypatch, body)


# --- switch.fdb collection ---------------------------------------------------------------------


def fdb_of(facts, node_id="n3"):
    """The switch.fdb record of a node; None when the slot says nothing about it."""
    node = facts.snapshot("p1")["nodes"].get(node_id, {})
    return node.get("collectors", {}).get("switch.fdb")


# The same exchange failures as for switch.net; the last one is a refusal in the shape of this collector.
FDB_FAILURES = FAILURES[:-1] + [
    ({"exit_code": 0, "stdout": json.dumps({"fdb": refusal("BUSY", "Too many clients")}), "stderr": ""}, "failed"),
]


def test_the_mac_table_is_a_collector_of_its_own_next_to_switch_net(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs)
        await facts.cycle()
        collectors = facts.snapshot("p1")["nodes"]["n3"]["collectors"]
        assert list(collectors) == ["linux.net", "switch.net", "switch.fdb"]
        record = collectors["switch.fdb"]
        assert record["schema"] == "switch.fdb/1" and record["ok"] is True and record["error"] is None
        assert record["attempted_at"] == record["observed_at"] and record["attempted_at"].endswith("Z")
        assert record["data"] == LEARNED  # the table only: ports and VLANs stay with switch.net
        assert collectors["switch.net"]["data"] == {"interfaces": EXPECTED_SWITCH_PORTS}
        (call,) = [c for c in lifecycle.qga.asked(SWITCH_FDB_COMMAND) if c[0] == "n3"]
        assert call[2]["priority"] is Priority.METRICS and call[2]["wait"] is False and call[2]["max_age"] == 2
        assert FDB_KEY in facts.confirmed and ("p1", "n1", "switch.fdb") in facts.absent

    scenario(monkeypatch, body)


def test_an_ordinary_host_is_asked_for_the_mac_table_once_and_never_gets_a_record(monkeypatch):
    async def body(facts, lifecycle, labs):
        for _ in range(3):
            await facts.cycle()
        assert len(lifecycle.qga.asked(SWITCH_FDB_COMMAND)) == 1  # the guest said it has no switchctl
        assert ("p1", "n1", "switch.fdb") in facts.absent
        assert list(facts.snapshot("p1")["nodes"]["n1"]["collectors"]) == ["linux.net"]

    scenario(monkeypatch, body)


@pytest.mark.parametrize("failure, code", FDB_FAILURES)
def test_mac_table_failures_before_the_first_success_are_silent_unless_the_guest_answered(monkeypatch, failure, code):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs, fdb_reply=failure)
        await facts.cycle()
        record = fdb_of(facts)
        if isinstance(failure, BaseException):  # the exchange failed: the role is not known, nothing is published
            assert record is None and FDB_KEY not in facts.absent and FDB_KEY not in facts.confirmed
        else:  # switchctl is there but does not answer properly: the owner has to see it
            assert record["ok"] is False and record["error"]["code"] == "failed" and record["error"]["message"]
            assert "data" not in record and "observed_at" not in record
        assert switch_of(facts)["ok"] is True  # the ports do not depend on the table
        lifecycle.qga.fdb_by_node["n3"] = fdb_success()
        await facts.cycle()
        assert fdb_of(facts)["ok"] is True and FDB_KEY in facts.confirmed

    scenario(monkeypatch, body)


@pytest.mark.parametrize("failure, code", FDB_FAILURES + [(None, "stopped")])
def test_mac_table_failures_after_the_first_success_are_published_with_the_last_table(monkeypatch, failure, code):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs)
        await facts.cycle()
        first = fdb_of(facts)
        if failure is None:
            labs["p1"]["nodes"][2]["status"] = "stopped"
        else:
            lifecycle.qga.fdb_by_node["n3"] = failure
        await facts.cycle()
        record = fdb_of(facts)
        assert record["ok"] is False and record["error"]["code"] == code and record["error"]["message"]
        assert record["data"] == first["data"] and record["observed_at"] == first["observed_at"]
        labs["p1"]["nodes"][2]["status"] = "started"
        lifecycle.qga.fdb_by_node["n3"] = fdb_success()
        await facts.cycle()
        assert fdb_of(facts)["ok"] is True and fdb_of(facts)["error"] is None

    scenario(monkeypatch, body)


def test_a_table_too_big_for_the_guest_is_published_as_truncated_and_the_node_stays_healthy(monkeypatch):
    async def body(facts, lifecycle, labs):
        refused = refusal("RESULT_TOO_LARGE", "Linux command output exceeds limit")
        add_switch(lifecycle, labs, fdb_reply=fdb_success(wrapped=refused))
        await facts.cycle()
        collectors = facts.snapshot("p1")["nodes"]["n3"]["collectors"]
        assert collectors["switch.fdb"]["ok"] is True and collectors["switch.fdb"]["error"] is None
        assert collectors["switch.fdb"]["data"] == {"mac_table": {"entries": [], "truncated": True}}
        assert all(record["ok"] for record in collectors.values())  # nothing for the manager to call an error

    scenario(monkeypatch, body)


def test_the_mac_table_and_the_ports_fail_independently(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs)
        await facts.cycle()
        lifecycle.qga.fdb_by_node["n3"] = {"exit_code": 1, "stdout": "", "stderr": "bridge down"}
        await facts.cycle()
        assert fdb_of(facts)["ok"] is False and "bridge down" in fdb_of(facts)["error"]["message"]
        assert switch_of(facts)["ok"] is True
        lifecycle.qga.fdb_by_node["n3"] = fdb_success()
        lifecycle.qga.switch_by_node["n3"] = {"exit_code": 1, "stdout": "", "stderr": "daemon down"}
        await facts.cycle()
        assert fdb_of(facts)["ok"] is True and switch_of(facts)["ok"] is False

    scenario(monkeypatch, body)


def test_the_table_follows_the_switch_and_a_quiet_table_is_stable(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs)
        await facts.cycle()
        before = fdb_of(facts)["data"]
        ticking = [dict(row, updated=5, used=1) for row in reversed(REAL_FDB)]
        lifecycle.qga.fdb_by_node["n3"] = fdb_success(rows=ticking)
        await facts.cycle()
        assert fdb_of(facts)["data"] == before  # the manager would journal any difference
        lifecycle.qga.fdb_by_node["n3"] = fdb_success(rows=[*REAL_FDB, learned_row("50:5b:3c:02:b3:00", "ens4", 1)])
        await facts.cycle()
        assert [e["iface"] for e in fdb_of(facts)["data"]["mac_table"]["entries"]] == ["ens3", "ens4"]

    scenario(monkeypatch, body)


def test_the_role_of_the_mac_table_is_forgotten_with_the_owner_and_with_the_lab(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_switch(lifecycle, labs)
        await facts.cycle()
        await facts.cycle()
        assert len(lifecycle.qga.asked(SWITCH_FDB_COMMAND)) == 3  # the host once, the switch every time
        facts.reset()
        assert facts.absent == set() and facts.confirmed == set()
        await facts.cycle()
        assert len(lifecycle.qga.asked(SWITCH_FDB_COMMAND)) == 5 and FDB_KEY in facts.confirmed
        labs["p1"]["state"] = "STOPPING"
        await facts.cycle()
        assert facts.absent == set() and facts.confirmed == set() and facts.results == {}

    scenario(monkeypatch, body)


# --- windows.net -------------------------------------------------------------------------------


def windows_document(**parts):
    document = {
        "adapters": [
            {"Name": "Ethernet", "Index": 12, "Mac": "00-00-AB-CD-00-00", "Status": "Up"},
            {"Name": "Ethernet 2", "Index": 8, "Mac": "00-00-AB-CD-00-01", "Status": "Disconnected"},
            {"Name": "Ethernet 3", "Index": 15, "Mac": "52-54-00-12-34-56", "Status": "Disabled"},
        ],
        "addresses": [
            {"Index": 12, "Ip": "10.0.12.20", "Prefix": 24, "State": "Preferred"},
            {"Index": 12, "Ip": "10.0.12.2", "Prefix": 24, "State": "Preferred"},
            {"Index": 8, "Ip": "169.254.10.1", "Prefix": 16, "State": "Tentative"},
            {"Index": 15, "Ip": "10.9.9.9", "Prefix": 24, "State": "Duplicate"},
            {"Index": 1, "Ip": "127.0.0.1", "Prefix": 8, "State": "Preferred"},  # the loopback is no adapter
        ],
        "routes": [
            {"Index": 12, "Dst": "0.0.0.0/0", "Next": "10.0.12.254", "Metric": 281, "Proto": "NetMgmt"},
            {"Index": 12, "Dst": "10.0.12.0/24", "Next": "0.0.0.0", "Metric": 281, "Proto": "Local"},
            {"Index": 12, "Dst": "10.0.12.20/32", "Next": "0.0.0.0", "Metric": 281, "Proto": "Local"},
            {"Index": 12, "Dst": "10.0.12.255/32", "Next": "0.0.0.0", "Metric": 281, "Proto": "Local"},
            {"Index": 12, "Dst": "224.0.0.0/4", "Next": "0.0.0.0", "Metric": 281, "Proto": "Local"},
            {"Index": 12, "Dst": "10.1.0.0/16", "Next": "10.0.12.3", "Metric": 291, "Proto": "NetMgmt"},
            {"Index": 1, "Dst": "127.0.0.0/8", "Next": "0.0.0.0", "Metric": 331, "Proto": "Local"},
        ],
    }
    document.update(parts)
    return json.dumps(document)


def windows_success():
    return {"exit_code": 0, "stdout": windows_document(), "stderr": ""}


def test_parse_windows_net_describes_interfaces_and_routes_like_linux_net():
    data = parse_windows_net(windows_document(), MACS)
    assert data["interfaces"] == [
        {"name": "Ethernet 2", "kind": "physical", "admin": "up", "oper": "down", "mac": "00:00:ab:cd:00:01",
         "port": {"adapter": 1, "port": 0}, "ipv4": ["169.254.10.1/16"]},
        {"name": "Ethernet", "kind": "physical", "admin": "up", "oper": "up", "mac": "00:00:ab:cd:00:00",
         "port": {"adapter": 0, "port": 0}, "ipv4": ["10.0.12.2/24", "10.0.12.20/24"], "gateway": "10.0.12.254"},
        {"name": "Ethernet 3", "kind": "physical", "admin": "down", "oper": "down", "mac": "52:54:00:12:34:56",
         "ipv4": []},  # no port: the MAC is not one of the adapters of the node; a duplicate address is not listed
    ]
    assert data["routes"] == [
        {"dst": "10.0.12.0/24", "dev": "Ethernet", "proto": "local", "metric": 281},
        {"dst": "10.1.0.0/16", "via": "10.0.12.3", "dev": "Ethernet", "proto": "netmgmt", "metric": 291},
        {"dst": "default", "via": "10.0.12.254", "dev": "Ethernet", "proto": "netmgmt", "metric": 281},
    ]


@pytest.mark.parametrize(
    "status, states",
    [
        ("Up", ("up", "up")),
        ("Disconnected", ("up", "down")),  # no cable: the adapter is enabled, there is no carrier
        ("Disabled", ("down", "down")),
        ("Not Present", ("down", "down")),
        ("Unknown", ("up", "down")),
    ],
)
def test_windows_adapter_status_becomes_admin_and_oper(status, states):
    adapter = {"Name": "Ethernet", "Index": 5, "Mac": "00-00-AB-CD-00-00", "Status": status}
    (entry,) = parse_windows_net(windows_document(adapters=[adapter], addresses=[], routes=[]), MACS)["interfaces"]
    assert (entry["admin"], entry["oper"]) == states


def test_windows_lists_are_not_confused_with_single_objects_or_missing_data():
    adapter = {"Name": "Ethernet", "Index": 5, "Mac": "00-00-AB-CD-00-00", "Status": "Up"}
    single = windows_document(adapters=adapter, addresses=[], routes=[])  # PowerShell unrolled a list of one
    assert [i["name"] for i in parse_windows_net(single, MACS)["interfaces"]] == ["Ethernet"]
    empty = windows_document(adapters=[], addresses=[], routes=[])
    assert parse_windows_net(empty, MACS) == {"interfaces": [], "routes": []}  # empty is known, not unknown


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "Get-NetAdapter : boom",
        "[]",
        json.dumps({"adapters": []}),
        json.dumps({"adapters": "x", "addresses": [], "routes": []}),
        json.dumps({"adapters": [{"Name": "E", "Index": "5", "Mac": "", "Status": "Up"}], "addresses": [], "routes": []}),
        json.dumps({"adapters": [{"Name": "E", "Index": 5, "Mac": "", "Status": 2}], "addresses": [], "routes": []}),
    ],
)
def test_garbage_from_windows_is_a_failed_collection(stdout):
    with pytest.raises(FactError) as error:
        parse_windows_net(stdout, MACS)
    assert error.value.code == "failed"


def test_the_windows_script_is_ascii_and_prints_one_json_document():
    assert WINDOWS_NET_COMMAND.isascii()  # the output is escaped the same way: no code page matters
    for cmdlet in ("Get-NetAdapter", "Get-NetIPAddress", "Get-NetRoute", "ConvertTo-Json"):
        assert cmdlet in WINDOWS_NET_COMMAND


@pytest.mark.parametrize(
    "info, windows",
    [
        ({"id": "mswindows", "name": "Microsoft Windows"}, True),
        ({"name": "Microsoft Windows"}, True),
        ({"id": "debian", "name": "Debian GNU/Linux"}, False),
        ({"id": "alpine"}, False),
        ({}, False),
        (None, False),
    ],
)
def test_guest_is_windows_by_what_the_agent_says(info, windows):
    assert guest_is_windows(info) is windows


def add_windows(lifecycle, osinfo=WINDOWS, reply=None):
    lifecycle.qga.osinfo = osinfo
    lifecycle.qga.windows_reply = windows_success() if reply is None else reply


def windows_of(facts, node_id="n1"):
    return facts.snapshot("p1")["nodes"][node_id]["collectors"]["windows.net"]


def test_a_windows_guest_gets_windows_net_through_powershell_and_only_that(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_windows(lifecycle)
        for _ in range(3):
            await facts.cycle()
        assert list(facts.snapshot("p1")["nodes"]["n1"]["collectors"]) == ["windows.net"]
        record = windows_of(facts)
        assert record["schema"] == "windows.net/1" and record["ok"] is True
        assert record["data"]["interfaces"][1]["port"] == {"adapter": 0, "port": 0}
        assert set(record["data"]) == {"interfaces", "routes"}
        assert lifecycle.qga.asked(LINUX_NET_COMMAND) == [] and lifecycle.qga.asked(SWITCH_NET_COMMAND) == []
        assert lifecycle.qga.asked(SWITCH_FDB_COMMAND) == []
        asked = lifecycle.qga.asked(WINDOWS_NET_COMMAND)
        assert len(asked) == 3
        assert asked[0][2]["shell"] == "powershell" and asked[0][2]["priority"] is Priority.METRICS
        assert asked[0][2]["wait"] is False
        assert asked[0][2]["timeout"] == 3 * facts.timeout  # a cold PowerShell is slow
        assert len(lifecycle.qga.osinfo_calls) == 1  # asked once, remembered
        (node_id, options), = lifecycle.qga.osinfo_calls
        assert node_id == "n1" and options["priority"] is Priority.METRICS and options["wait"] is False

    scenario(monkeypatch, body)


def test_a_linux_guest_keeps_its_collectors_and_is_never_asked_for_windows(monkeypatch):
    async def body(facts, lifecycle, labs):
        await facts.cycle()
        assert lifecycle.qga.asked(WINDOWS_NET_COMMAND) == []
        assert lifecycle.qga.asked(LINUX_NET_COMMAND)[0][2]["shell"] == "sh"
        assert list(facts.snapshot("p1")["nodes"]["n1"]["collectors"]) == ["linux.net"]

    scenario(monkeypatch, body)


def test_an_agent_that_does_not_know_osinfo_is_an_old_linux_agent(monkeypatch):
    async def body(facts, lifecycle, labs):
        lifecycle.qga.osinfo = QgaCommandError("The command guest-get-osinfo has not been found")
        await facts.cycle()
        assert collector_of(facts)["ok"] is True and facts.families == {("p1", "n1"): "posix"}

    scenario(monkeypatch, body)


@pytest.mark.parametrize("failure", [QgaTimeout("slow"), QgaUnavailable("backing off"), FileNotFoundError("no socket")])
def test_a_guest_that_cannot_be_asked_what_it_is_says_nothing_and_is_asked_again(monkeypatch, failure):
    async def body(facts, lifecycle, labs):
        add_windows(lifecycle, osinfo=failure)
        await facts.cycle()
        # Not even `unreachable`: it would be published as linux.net of what may be a Windows guest.
        assert facts.snapshot("p1")["nodes"] == {} and lifecycle.qga.calls == [] and facts.families == {}
        lifecycle.qga.osinfo = WINDOWS
        await facts.cycle()
        assert windows_of(facts)["ok"] is True and facts.families == {("p1", "n1"): "windows"}

    scenario(monkeypatch, body)


def test_a_busy_channel_postpones_the_question_without_a_failure(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_windows(lifecycle, osinfo=QgaBusy("student command in flight"))
        await facts.cycle()
        assert facts.snapshot("p1")["nodes"] == {} and lifecycle.qga.calls == []
        assert facts.next_due[("p1", "n1", "windows.net")] <= time.monotonic() + facts.tick
        lifecycle.qga.osinfo = WINDOWS
        await facts.cycle()
        assert facts.snapshot("p1")["nodes"] == {}  # looked again only after a tick, not at once
        await asyncio.sleep(facts.tick + 0.01)
        await facts.cycle()
        assert windows_of(facts)["ok"] is True

    scenario(monkeypatch, body)


def test_windows_failures_are_published_after_the_family_is_known(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_windows(lifecycle)
        await facts.cycle()
        first = windows_of(facts)
        lifecycle.qga.windows_reply = QgaTimeout("slow")
        await facts.cycle()
        record = windows_of(facts)
        assert record["ok"] is False and record["error"]["code"] == "timeout"
        assert record["data"] == first["data"] and "linux.net" not in facts.snapshot("p1")["nodes"]["n1"]["collectors"]
        lifecycle.qga.windows_reply = {
            "exit_code": 1, "stdout": "", "stderr": "The term 'Get-NetAdapter' is not recognized as the name of a cmdlet",
        }
        await facts.cycle()
        assert windows_of(facts)["error"]["code"] == "unsupported"
        lifecycle.qga.windows_reply = {"exit_code": 1, "stdout": "", "stderr": "Access is denied"}
        await facts.cycle()
        assert windows_of(facts)["error"]["code"] == "failed"
        labs["p1"]["nodes"][0]["status"] = "stopped"
        await facts.cycle()
        assert windows_of(facts)["error"]["code"] == "stopped"

    scenario(monkeypatch, body)


def test_the_family_is_forgotten_with_the_owner_and_with_the_ready_lab(monkeypatch):
    async def body(facts, lifecycle, labs):
        add_windows(lifecycle)
        await facts.cycle()
        assert facts.families == {("p1", "n1"): "windows"}
        facts.reset()
        assert facts.families == {}
        await facts.cycle()
        assert facts.families == {("p1", "n1"): "windows"}
        labs["p1"]["state"] = "WIPING"
        await facts.cycle()
        assert facts.families == {}
        # The wiped guest may be another system: it is asked again.
        add_windows(lifecycle, osinfo=DEBIAN)
        labs["p1"]["state"] = "READY"
        await facts.cycle()
        assert facts.families == {("p1", "n1"): "posix"} and collector_of(facts)["ok"] is True

    scenario(monkeypatch, body)


def test_an_owner_change_during_the_question_leaves_no_family(monkeypatch):
    async def body(facts, lifecycle, labs):
        release, entered = asyncio.Event(), asyncio.Event()

        async def slow(node_id, command, args=None, **options):
            entered.set()
            await release.wait()
            return WINDOWS

        lifecycle.qga.call = slow
        task = asyncio.create_task(facts.cycle())
        await entered.wait()
        facts.reset()
        release.set()
        await task
        assert facts.families == {} and facts.snapshot("p1")["nodes"] == {}

    scenario(monkeypatch, body)


def test_the_registry_matches_the_capabilities():
    ids = ["linux.net/1", "switch.net/1", "switch.fdb/1", "windows.net/1", "ios.config/1"]
    assert facts_module.COLLECTOR_IDS == [c.schema for c in COLLECTORS] == ids


# --- routes -----------------------------------------------------------------------------------


@pytest.fixture
def fast_facts(monkeypatch):
    monkeypatch.setenv("SLOT_FACTS_INTERVAL", "0.05")
    monkeypatch.setenv("SLOT_FACTS_MIN_REFRESH", "0")


def deployed(client, monkeypatch, reply, switch_reply=NOT_A_SWITCH, fdb_reply=NOT_A_SWITCH):
    from api.qga import QgaBroker

    calls = []

    async def exec_(self, node_id, command, **options):
        calls.append(node_id)
        if command == SWITCH_FDB_COMMAND:
            return fdb_reply
        return switch_reply if command == SWITCH_NET_COMMAND else reply

    async def call_(self, node_id, command, args=None, **options):
        assert command == "guest-get-osinfo"
        return DEBIAN

    monkeypatch.setattr(QgaBroker, "exec", exec_)
    monkeypatch.setattr(QgaBroker, "call", call_)
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
    assert list(node["collectors"]) == ["linux.net"]  # an ordinary host has no switch.net record
    assert "raw" not in record and "raw_sha256" not in record  # no raw output leaves the slot
    # Stopping the lab removes its facts: the Manager must not store a dead lab as current.
    stopped = client.post(f"/v1/labs/{pid}/stop", headers=HEADERS)
    assert stopped.status_code in (200, 202), stopped.text
    wait_for_facts(client, pid, lambda b: b["nodes"] == {})


def test_facts_route_serves_the_switch_ports(fast_facts, mock_server, monkeypatch):
    client, _projects, _nodes = mock_server
    pid, _calls = deployed(client, monkeypatch, success(), switch_reply=switch_success(), fdb_reply=fdb_success())
    body = wait_for_facts(
        client, pid, lambda b: any("switch.fdb" in n["collectors"] for n in b["nodes"].values())
    )
    (node,) = body["nodes"].values()
    assert list(node["collectors"]) == ["linux.net", "switch.net", "switch.fdb"]
    table = node["collectors"]["switch.fdb"]
    assert table["schema"] == "switch.fdb/1" and table["ok"] is True and table["data"] == LEARNED
    record = node["collectors"]["switch.net"]
    assert record["schema"] == "switch.net/1" and record["ok"] is True
    assert [i["switchport"] for i in record["data"]["interfaces"]][:2] == [
        {"mode": "access", "vlan": 10}, {"mode": "trunk", "allowed": [10, 20], "native": 20},
    ]
    assert set(record["data"]) == {"interfaces"} and "stats64" not in json.dumps(record)


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
    assert "collectors" in body["features"]
    assert body["collectors"] == ["linux.net/1", "switch.net/1", "switch.fdb/1", "windows.net/1", "ios.config/1"]
    assert os.getenv("SLOT_API_TOKEN") == "test-token"
