"""IOL switches and routers configured from segments: the startup-config in the ticket (lab constructor §15.2)."""

import copy
import importlib.util
from pathlib import Path

import pytest

from lab_manager import iosconfig
from lab_manager.labdef import check, deploy_ticket
from lab_manager.segments import expected_facts, ios_plans, setup
from test_segments import ALL, IMAGES, found

SLOT_PARSER = Path(__file__).resolve().parents[2] / "slot" / "api" / "ios_config.py"
TAIL = "line con 0\n exec-timeout 0 0\n logging synchronous\nend\n"
HEAD = "no ip domain-lookup\n"
SWITCH_HEAD = HEAD + "vtp mode off\nspanning-tree mode pvst\n"


def node(name, image, **extra):
    return {"name": name, "image": image, **extra}


def port(name, adapter=0, number=0):
    return {"node": name, "adapter": adapter, "port": number}


def cable(a, b):
    return {"a": a, "b": b}


def segment(name, subnet, members, vlan=None, gateway=None):
    seg = {"name": name, "subnet": {"fixed": subnet}, "members": members}
    if vlan is not None:
        seg["vlan"] = {"fixed": vlan}
    if gateway is not None:
        seg["gateway"] = gateway
    return seg


def lab(nodes, links, segments):
    return {"format": "gns3lab.lab/2", "nodes": nodes, "links": links, "segments": segments}


def configs(definition):
    checked = check(definition, IMAGES, ALL).definition
    return {name: preset["nvram_config"] for name, preset in setup(checked, IMAGES).items() if "nvram_config" in preset}


def switch(name, *ports):
    """Expected text of a switch: `ports` are (interface, lines) in the order of the configuration."""
    return f"hostname {name}\n" + SWITCH_HEAD + "".join(ports) + TAIL


def access(name, vlan):
    return f"interface {name}\n switchport mode access\n switchport access vlan {vlan}\n"


def trunk(name, allowed):
    return (f"interface {name}\n switchport trunk encapsulation dot1q\n switchport mode trunk\n"
            f" switchport trunk allowed vlan {allowed}\n")


def vlan(number, name):
    return f"vlan {number}\n name {name}\n"


def test_one_switch_without_vlan_is_vlan_one():
    topology = lab([node("SW1", "cisco-iou-l2"), node("PC1", "debian12"), node("PC2", "vpcs")],
                   [cable(port("PC1"), port("SW1", 0, 0)), cable(port("PC2"), port("SW1", 0, 3))],
                   [segment("LAN", "10.0.0.0/24", [port("PC1"), port("PC2")])])
    assert configs(topology) == {"SW1": (
        "hostname SW1\n" + SWITCH_HEAD + access("Ethernet0/0", 1) + access("Ethernet0/3", 1) + TAIL)}
    assert check(topology, IMAGES, ALL).warnings == ()


def test_one_switch_with_vlan_declares_it_by_the_segment_name():
    topology = lab([node("SW1", "cisco-iou-l2", adapters=2), node("PC1", "debian12"), node("PC2", "vpcs")],
                   [cable(port("PC1"), port("SW1", 1, 2)), cable(port("PC2"), port("SW1", 0, 1))],
                   [segment("USERS", "10.0.0.0/24", [port("PC1"), port("PC2")], vlan=10)])
    assert configs(topology) == {"SW1": switch(
        "SW1", vlan(10, "USERS"), access("Ethernet0/1", 10), access("Ethernet1/2", 10))}
    # Not "set by the student" any more: no warning.
    assert check(topology, IMAGES, ALL).warnings == ()


TWO_SWITCHES = lab(
    [node("SW1", "cisco-iou-l2"), node("SW2", "cisco-iou-l2"), node("PC1", "debian12"), node("PC2", "debian12"),
     node("PC3", "vpcs")],
    [cable(port("PC1"), port("SW1", 0, 0)), cable(port("PC3"), port("SW1", 0, 1)),
     cable(port("SW1", 0, 3), port("SW2", 0, 3)), cable(port("PC2"), port("SW2", 0, 0))],
    [segment("USERS", "10.0.0.0/24", [port("PC1"), port("PC2")], vlan=10),
     segment("LAB", "10.0.1.0/24", [port("PC3")], vlan=30)])


def test_two_switches_with_a_trunk_carry_only_the_vlans_that_go_through_it():
    got = configs(TWO_SWITCHES)
    assert got["SW1"] == switch("SW1", vlan(10, "USERS"), vlan(30, "LAB"), access("Ethernet0/0", 10),
                                access("Ethernet0/1", 30), trunk("Ethernet0/3", "10"))
    assert got["SW2"] == switch("SW2", vlan(10, "USERS"), access("Ethernet0/0", 10), trunk("Ethernet0/3", "10"))
    assert check(TWO_SWITCHES, IMAGES, ALL).warnings == ()


def test_a_ring_of_three_puts_the_vlan_on_every_cable():
    topology = lab(
        [node("SW1", "cisco-iou-l2"), node("SW2", "cisco-iou-l2"), node("SW3", "cisco-iou-l2"),
         node("PC1", "vpcs"), node("PC2", "vpcs")],
        [cable(port("PC1"), port("SW1", 0, 0)), cable(port("PC2"), port("SW2", 0, 0)),
         cable(port("SW1", 0, 1), port("SW2", 0, 1)), cable(port("SW2", 0, 2), port("SW3", 0, 2)),
         cable(port("SW3", 0, 1), port("SW1", 0, 2))],
        [segment("RING", "10.0.0.0/24", [port("PC1"), port("PC2")], vlan=5)])
    got = configs(topology)
    assert got["SW3"] == switch("SW3", vlan(5, "RING"), trunk("Ethernet0/1", "5"), trunk("Ethernet0/2", "5"))
    assert got["SW1"] == switch("SW1", vlan(5, "RING"), access("Ethernet0/0", 5), trunk("Ethernet0/1", "5"),
                                trunk("Ethernet0/2", "5"))


def test_a_switch_off_the_path_is_left_alone():
    topology = copy.deepcopy(TWO_SWITCHES)
    topology["nodes"] += [node("SW3", "cisco-iou-l2"), node("SW4", "linux-switch")]
    topology["links"] += [cable(port("SW2", 0, 2), port("SW3", 0, 0)), cable(port("SW3", 0, 1), port("SW4", 0))]
    got = configs(topology)  # SW3 and SW4 are a spur behind SW2 with no member of any segment
    assert set(got) == {"SW1", "SW2"}
    assert got["SW2"] == configs(TWO_SWITCHES)["SW2"]
    assert check(topology, IMAGES, ALL).warnings == ()


def test_a_linux_switch_on_the_path_stays_the_students_and_is_named():
    topology = lab(
        [node("SW1", "cisco-iou-l2"), node("LSW", "linux-switch"), node("PC1", "vpcs"), node("PC2", "vpcs")],
        [cable(port("PC1"), port("SW1", 0, 0)), cable(port("SW1", 0, 1), port("LSW", 0)),
         cable(port("LSW", 1), port("PC2"))],
        [segment("S", "10.0.0.0/24", [port("PC1"), port("PC2")], vlan=10)])
    assert configs(topology)["SW1"] == switch("SW1", vlan(10, "S"), access("Ethernet0/0", 10), trunk("Ethernet0/1", "10"))
    warnings = check(topology, IMAGES, ALL).warnings
    assert [(w["path"], w["code"], w["message"]) for w in warnings] == [
        ("segments[0].vlan", "switch_not_configured", "set by the student: LSW")]
    plain = copy.deepcopy(topology)
    del plain["segments"][0]["vlan"]  # Without a VLAN a Linux switch just forwards
    assert check(plain, IMAGES, ALL).warnings == ()


def router_lab(vlan_a=None, vlan_b=None, second=True):
    nodes = [node("SW1", "cisco-iou-l2"), node("R1", "cisco-iou-l3"), node("PC1", "debian12"), node("PC2", "vpcs")]
    links = [cable(port("PC1"), port("SW1", 0, 0)), cable(port("PC2"), port("SW1", 0, 1)),
             cable(port("R1", 0, 0), port("SW1", 0, 3))]
    segments = [segment("LANA", "192.168.10.0/24", [port("PC1")], vlan=vlan_a, gateway=port("R1"))]
    if second:
        segments.append(segment("LANB", "192.168.20.0/24", [port("PC2")], vlan=vlan_b, gateway=port("R1")))
    return lab(nodes, links, segments)


def router(*blocks):
    return "hostname R1\n" + HEAD + "".join(blocks) + TAIL


def test_a_router_gateway_on_a_physical_port():
    topology = router_lab(second=False)
    got = configs(topology)
    assert got["R1"] == router("interface Ethernet0/0\n no shutdown\n ip address 192.168.10.254 255.255.255.0\n")
    assert got["SW1"] == switch("SW1", access("Ethernet0/0", 1), access("Ethernet0/3", 1))
    assert check(topology, IMAGES, ALL).warnings == ()


def test_router_on_a_stick_uses_subinterfaces_and_a_trunk():
    topology = router_lab(10, 20)
    got = configs(topology)
    assert got["R1"] == router(
        "interface Ethernet0/0\n no shutdown\n",
        "interface Ethernet0/0.10\n encapsulation dot1Q 10\n ip address 192.168.10.254 255.255.255.0\n",
        "interface Ethernet0/0.20\n encapsulation dot1Q 20\n ip address 192.168.20.254 255.255.255.0\n")
    assert got["SW1"] == switch("SW1", vlan(10, "LANA"), vlan(20, "LANB"), access("Ethernet0/0", 10),
                                access("Ethernet0/1", 20), trunk("Ethernet0/3", "10,20"))
    assert check(topology, IMAGES, ALL).warnings == ()


def test_one_vlan_segment_alone_on_a_router_port_still_gets_a_subinterface():
    got = configs(router_lab(10, second=False))
    assert "interface Ethernet0/0.10\n encapsulation dot1Q 10\n" in got["R1"]
    assert got["SW1"].endswith(trunk("Ethernet0/3", "10") + TAIL)


def test_plain_and_vlan_segments_on_one_router_port_share_a_trunk():
    topology = router_lab(None, 20)  # LANA has no VLAN: the physical port, VLAN 1, native
    got = configs(topology)
    assert got["R1"] == router(
        "interface Ethernet0/0\n no shutdown\n ip address 192.168.10.254 255.255.255.0\n",
        "interface Ethernet0/0.20\n encapsulation dot1Q 20\n ip address 192.168.20.254 255.255.255.0\n")
    assert got["SW1"] == switch("SW1", vlan(20, "LANB"), access("Ethernet0/0", 1), access("Ethernet0/1", 20),
                                trunk("Ethernet0/3", "1,20"))
    assert check(topology, IMAGES, ALL).warnings == ()


def test_the_gateway_host_number_and_the_prefix_come_from_the_segment():
    topology = router_lab(second=False)
    topology["segments"][0]["subnet"] = {"fixed": "10.5.0.0/26"}
    topology["segments"][0]["gateway"]["host"] = 1
    assert "ip address 10.5.0.1 255.255.255.192\n" in configs(topology)["R1"]


def test_a_router_port_is_a_gateway_of_several_segments_only_in_different_vlans():
    assert found(router_lab(None, None)) == [("segments[1].gateway", "gateway_port_conflict")]
    assert found(router_lab(10, 10)) == [("segments[1].gateway", "gateway_port_conflict")]
    check(router_lab(10, 20), IMAGES, ALL)
    check(router_lab(None, 20), IMAGES, ALL)
    check(router_lab(20, None), IMAGES, ALL)
    # Behind the port there is a host, not a switch: nothing tells the VLANs apart.
    topology = router_lab(10, 20)
    topology["nodes"].append(node("PC9", "vpcs"))
    topology["links"][2] = cable(port("R1", 0, 0), port("PC9"))
    topology["segments"][0]["members"] = [port("PC9")]
    assert ("segments[1].gateway", "gateway_port_conflict") in found(topology)


def test_a_router_that_is_not_iol_stays_the_students():
    topology = router_lab(10, 20)
    topology["nodes"][1]["image"] = "vyos"
    assert "R1" not in configs(topology) and "SW1" in configs(topology)
    assert [w["code"] for w in check(topology, IMAGES, ALL).warnings] == ["gateway_not_configured"] * 2


@pytest.mark.parametrize("name,ok", [
    ("R1", True), ("core-sw", True), ("A" * 63, True), ("A" * 64, False), ("1R", False), ("core_sw", False),
    ("r.1", False), ("r-", False), ("-r", False), ("x", True)])
def test_hostname_rules(name, ok):
    assert iosconfig.hostname_ok(name) is ok


def test_a_name_that_is_no_hostname_gives_no_hostname_line_and_a_warning():
    topology = router_lab(10, 20)
    topology["nodes"][0]["name"] = "core_sw"
    topology["links"] = [dict(c, **{side: dict(c[side], node="core_sw") for side in "ab" if c[side]["node"] == "SW1"})
                         for c in topology["links"]]
    got = configs(topology)
    assert not got["core_sw"].startswith("hostname") and got["core_sw"].startswith("no ip domain-lookup\n")
    assert got["R1"].startswith("hostname R1\n")
    warnings = check(topology, IMAGES, ALL).warnings
    assert [(w["path"], w["code"]) for w in warnings] == [("nodes[0].name", "hostname_not_set")]
    topology["segments"] = []  # A node nothing is set on needs no hostname
    assert check(topology, IMAGES, ALL).warnings == ()


def test_vlan_list_compresses_runs_of_three_or_more():
    assert iosconfig.vlan_list({20, 1, 10, 11, 12, 13, 30, 31}) == "1,10-13,20,30,31"
    assert iosconfig.vlan_list({7}) == "7"


def test_ticket_carries_nvram_config_only_for_configured_nodes_and_is_deterministic():
    def tickets(definition):
        return deploy_ticket(check(definition, IMAGES, ALL).definition, IMAGES, project_id="p", project_name="n")

    first = tickets(router_lab(10, 20))
    assert first == tickets(copy.deepcopy(router_lab(10, 20)))
    nodes = {n["name"]: n for n in first["nodes"]}
    assert set(nodes["R1"]) >= {"nvram_config"} and "nvram_config" in nodes["SW1"]
    assert not any("nvram_config" in nodes[name] for name in ("PC1", "PC2"))
    # An IOL on no path: the ticket is what stage 2 made of it.
    lonely = router_lab(10, 20)
    lonely["segments"] = []
    assert not any("nvram_config" in n for n in tickets(lonely)["nodes"])
    linux = lab([node("SW1", "linux-switch"), node("PC1", "vpcs"), node("PC2", "vpcs")],
                [cable(port("PC1"), port("SW1", 0)), cable(port("PC2"), port("SW1", 1))],
                [segment("S", "10.0.0.0/24", [port("PC1"), port("PC2")], vlan=10)])
    assert not any("nvram_config" in n for n in tickets(linux)["nodes"])


def test_lab_definition_hash_does_not_depend_on_the_compiled_text():
    a = check(router_lab(10, 20), IMAGES, ALL)
    assert a.lab_def == check(copy.deepcopy(router_lab(10, 20)), IMAGES, ALL).lab_def


def test_text_is_printable_ascii_within_the_limit():
    for text in configs(router_lab(10, 20)).values():
        assert "\r" not in text and all(c == "\n" or " " <= c <= "~" for c in text) and len(text) < 65536
    with pytest.raises(iosconfig.ConfigTooLarge):
        iosconfig.render_switch("SW1", iosconfig.SwitchPlan(vlans={n: "V" * 32 for n in range(2, 2000)}))


def slot_parser():
    if not SLOT_PARSER.is_file():
        pytest.skip("the slot component is not in this tree")
    spec = importlib.util.spec_from_file_location("slot_ios_config", SLOT_PARSER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("topology", [TWO_SWITCHES, router_lab(10, 20), router_lab(None, 20), router_lab(second=False)],
                         ids=["trunk", "stick", "mixed", "one"])
def test_the_slot_parser_reads_what_the_compiler_wrote(topology):
    """The compiler and the collector of facts (`ios.config/1`) speak one language."""
    parser = slot_parser()
    definition = check(topology, IMAGES, ALL).definition
    expected = expected_facts(definition, IMAGES)["nodes"]
    checked = 0
    for name, text in configs(topology).items():
        ports = {f"Ethernet{a}/{p}": {"adapter": a, "port": p} for a in range(4) for p in range(4)}
        data = parser.parse_ios_config(text, ports)
        assert data["interfaces"] == expected[name]["interfaces"], name
        assert data.get("vlans") == expected[name].get("vlans"), name
        checked += 1
    assert checked >= 2
