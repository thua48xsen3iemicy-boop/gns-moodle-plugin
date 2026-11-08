"""Auto-checks: the gns3lab.checks/1 document, the rules over facts, root causes and the grade (CONTRACTS 2.9)."""

import copy

import pytest

from lab_manager import checks as checks_mod
from lab_manager.checks import check_definition, normalize_grading, run_checks
from lab_manager.labdef import LabInvalid, check
from lab_manager.segments import setup
from test_labdef import ALL
from test_segments import IMAGES

# SW1 is a Linux switch; R1 (VyOS) routes between LAN1 (VLAN 10) and LAN2 (VLAN 20).
NODES = [("SW1", "linux-switch", 8), ("R1", "vyos", 2), ("PC1", "debian12", 1), ("PC2", "debian12", 1),
         ("PC3", "debian12", 1)]
LINKS = [(("R1", 0), ("SW1", 0)), (("R1", 1), ("SW1", 1)), (("PC1", 0), ("SW1", 2)), (("PC2", 0), ("SW1", 3)),
         (("PC3", 0), ("SW1", 4))]
DEFINITION = {
    "format": "gns3lab.lab/2", "title": "VLAN",
    "nodes": [{"name": n, "image": i, "adapters": a} for n, i, a in NODES],
    "links": [{"a": {"node": a[0], "adapter": a[1], "port": 0}, "b": {"node": b[0], "adapter": b[1], "port": 0}}
              for a, b in LINKS],
    "segments": [
        {"name": "LAN1", "configure": "student", "vlan": {"fixed": 10}, "subnet": {"fixed": "192.168.10.0/24"},
         "gateway": {"node": "R1", "adapter": 0, "port": 0}, "members": [{"node": "PC1"}, {"node": "PC2"}]},
        {"name": "LAN2", "configure": "student", "vlan": {"fixed": 20}, "subnet": {"fixed": "192.168.20.0/24"},
         "gateway": {"node": "R1", "adapter": 1, "port": 0}, "members": [{"node": "PC3"}]},
    ],
    "checks": {"format": "gns3lab.checks/1", "reachability": "segments"},
}
GRADING = normalize_grading(None)


def nid(name):
    return "n-" + name


def topology(nodes=NODES, links=LINKS, suspend=()):
    return {"name": "lab", "topology": {
        "nodes": [{"node_id": nid(n), "name": n, "node_type": "iou" if IMAGES[i].kind == "iou" else IMAGES[i].kind,
                   "console_type": IMAGES[i].console_type,
                   "ports": [{"name": f"Ethernet{k}", "short_name": f"e{k}", "adapter_number": k, "port_number": 0}
                             for k in range(a)]} for n, i, a in nodes],
        "links": [{"link_id": f"l{k}", "suspend": k in suspend,
                   "nodes": [{"node_id": nid(a[0]), "adapter_number": a[1], "port_number": 0},
                             {"node_id": nid(b[0]), "adapter_number": b[1], "port_number": 0}]}
                  for k, (a, b) in enumerate(links)]}}


def host(addr=None, gw=None, iface="ens3", adapter=0, extra=()):
    ifaces = [{"name": iface, "kind": "physical", "port": {"adapter": adapter, "port": 0}, "admin": "up", "oper": "up",
               "ipv4": [addr] if addr else []}, *extra]
    routes = []
    if addr:
        routes.append({"dst": str(__import__("ipaddress").IPv4Interface(addr).network), "dev": iface, "proto": "kernel"})
    if gw:
        routes.append({"dst": "default", "via": gw, "dev": iface, "proto": "static"})
    return {"interfaces": ifaces, "routes": routes}


def switch(vlans):
    return {"interfaces": [{"name": f"eth{k}", "kind": "physical", "port": {"adapter": k, "port": 0}, "admin": "up",
                            "oper": "up", "mirror": True, "switchport": {"mode": "access", "vlan": v}}
                           for k, v in enumerate(vlans)]}


def router(a0="192.168.10.254/24", a1="192.168.20.254/24"):
    ifaces = [{"name": f"eth{k}", "kind": "physical", "port": {"adapter": k, "port": 0}, "admin": "up", "oper": "up",
               "ipv4": [a] if a else []} for k, a in enumerate((a0, a1))]
    routes = [{"dst": str(__import__("ipaddress").IPv4Interface(a).network), "dev": f"eth{k}", "proto": "kernel"}
              for k, a in enumerate((a0, a1)) if a]
    return {"interfaces": ifaces, "routes": routes}


def good_facts():
    return {
        "SW1": switch([10, 20, 10, 10, 20]),
        "R1": router(),
        "PC1": host("192.168.10.1/24", "192.168.10.254"),
        "PC2": host("192.168.10.2/24", "192.168.10.254"),
        "PC3": host("192.168.20.1/24", "192.168.20.254"),
    }


def run(facts, definition=DEFINITION, grading=GRADING, stopped=(), stale=(), topo=None, checks="default"):
    state = {"nodes": {nid(n): {"name": n, "status": "stopped" if n in stopped else "started", **f}
                       for n, f in facts.items()}}
    statuses = {nid(n): ("stopped" if n in stopped else "started") for n, _, _ in NODES}
    return run_checks(topology=topo or topology(), state=state, statuses=statuses, definition=definition,
                      images=IMAGES, checks=definition.get("checks") if checks == "default" else checks,
                      grading=grading, stale={nid(n) for n in stale})


def codes(result, counted=None):
    return sorted(f["code"] for f in result["findings"] if counted is None or f["counted"] == counted)


def rule(result, code):
    return next(r for r in result["rules"] if r["code"] == code)


# ---------------------------------------------------------------------- definition


def test_a_correct_lab_gets_the_top_mark():
    result = run(good_facts())
    assert result["findings"] == []
    assert result["outcome"] == "graded" and result["mark"] == 5
    assert result["counts"] == {"critical": 0, "major": 0, "minor": 0, "note": 0}
    for code in ("segment_split", "segments_merged", "address_missing", "gateway_missing", "unreachable", "vlan_wrong"):
        assert rule(result, code)["status"] == "pass"
    assert rule(result, "hostname_wrong") == {"code": "hostname_wrong", "group": "host", "status": "skipped",
                                              "reason": "no_collector"}


def test_a_wrong_vlan_is_one_mistake_not_five():
    facts = good_facts()
    facts["SW1"] = switch([10, 20, 10, 20, 20])  # PC2 ended up in VLAN 20
    result = run(facts)
    assert codes(result, counted=True) == ["vlan_wrong"]
    roots = {f["id"]: f for f in result["findings"]}
    consequences = [f for f in result["findings"] if f["caused_by"]]
    assert {f["code"] for f in consequences} >= {"segment_split", "segments_merged", "subnet_mixed", "unreachable"} \
        or {f["code"] for f in consequences} >= {"segment_split", "segments_merged", "subnet_mixed"}
    for f in consequences:
        assert roots[f["caused_by"]]["code"] == "vlan_wrong" and not f["counted"]
    wrong = next(f for f in result["findings"] if f["code"] == "vlan_wrong")
    assert wrong["class"] == "major" and "VLAN 20, должен быть 10" in wrong["message"]
    assert result["mark"] == 4 and result["counts"]["major"] == 1


def test_an_address_on_the_wrong_interface_is_misplaced_and_extra():
    facts = good_facts()
    facts["PC1"] = host(None, "192.168.10.254", extra=[
        {"name": "ens4", "kind": "physical", "port": {"adapter": 1, "port": 0}, "admin": "up", "oper": "up",
         "ipv4": ["192.168.10.1/24"]}])
    result = run(facts)
    assert {"address_misplaced", "address_extra"} <= set(codes(result, counted=True))
    misplaced = next(f for f in result["findings"] if f["code"] == "address_misplaced")
    assert misplaced["ifaces"] == [{"node": "PC1", "iface": "ens4"}] and "должен быть на ens3" in misplaced["message"]
    assert result["mark"] < 5  # Never a credit for an address in the wrong place


@pytest.mark.parametrize("pc1, expected", [
    (host("192.168.10.7/24", "192.168.10.254"), "address_wrong"),
    (host("192.168.10.1/16", "192.168.10.254"), "mask_wrong"),
    (host(None, None), "address_missing"),
    (host("192.168.10.1/24", None), "gateway_missing"),
    (host("192.168.10.1/24", "192.168.10.253"), "gateway_wrong"),
])
def test_address_and_gateway_mistakes(pc1, expected):
    facts = good_facts()
    facts["PC1"] = pc1
    result = run(facts)
    assert expected in codes(result, counted=True)


def test_match_subnet_takes_any_address_of_the_segment():
    definition = copy.deepcopy(DEFINITION)
    definition["checks"]["addresses"] = {"match": "subnet"}
    facts = good_facts()
    facts["PC1"] = host("192.168.10.7/24", "192.168.10.254")
    assert "address_wrong" not in codes(run(facts, definition))
    assert "address_wrong" in codes(run(facts))  # plan by default


def test_public_addresses_apipa_and_duplicates():
    facts = good_facts()
    facts["PC2"] = host("192.168.10.1/24", "192.168.10.254")
    facts["PC3"] = host("169.254.3.4/16", "192.168.20.254")
    found = codes(run(facts, definition={**DEFINITION, "segments": [], "checks": None}, checks=None))
    assert {"address_duplicate", "address_apipa"} <= set(found)
    facts = good_facts()
    facts["R1"] = router("8.8.8.1/24", "192.168.20.254/24")
    facts["PC1"] = host("8.8.8.2/24", "8.8.8.1")
    facts["PC2"] = host("8.8.8.3/24", "8.8.8.1")
    result = run(facts, definition={**DEFINITION, "segments": [], "checks": None}, checks=None)
    assert codes(result).count("address_not_private") == 3


def test_a_stopped_node_explains_what_follows():
    result = run(good_facts(), stopped=("PC3",))
    stopped = next(f for f in result["findings"] if f["code"] == "node_stopped")
    assert stopped["counted"] and stopped["class"] == "critical"
    assert all(f["caused_by"] == stopped["id"] for f in result["findings"] if f["code"] == "unreachable")
    assert result["mark"] == 2


def test_stale_facts_make_the_run_incomplete():
    result = run(good_facts(), stale=("PC1",))
    assert result["outcome"] == "incomplete" and result["mark"] is None
    assert rule(result, "address_missing") == {"code": "address_missing", "group": "addressing",
                                               "status": "skipped", "reason": "stale_facts"}


def test_unreachable_without_a_route():
    facts = good_facts()
    facts["PC3"] = host("192.168.20.1/24", None)
    result = run(facts)
    assert "gateway_missing" in codes(result, counted=True)
    unreachable = next(f for f in result["findings"] if f["code"] == "unreachable")
    assert "нет маршрута" in unreachable["message"] and unreachable["caused_by"]


# ---------------------------------------------------------------------- groups by name

GNODES = [("SW1", "linux-switch", 8), ("R1", "vyos", 2), ("BUH-1", "debian12", 1), ("BUH-2", "debian12", 1),
          ("ADM-1", "debian12", 1)]
GLINKS = [(("R1", 0), ("SW1", 0)), (("R1", 1), ("SW1", 1)), (("BUH-1", 0), ("SW1", 2)), (("BUH-2", 0), ("SW1", 3)),
          (("ADM-1", 0), ("SW1", 4))]


def group_run(vlans, adm_gw="192.168.20.254", checks=None):
    facts = {"SW1": switch(vlans), "R1": router(),
             "BUH-1": host("192.168.10.1/24", "192.168.10.254"), "BUH-2": host("192.168.10.2/24", "192.168.10.254"),
             "ADM-1": host("192.168.20.1/24", adm_gw)}
    state = {"nodes": {nid(n): {"name": n, "status": "started", **f} for n, f in facts.items()}}
    checks = checks or {"format": "gns3lab.checks/1", "groups": {"by_name": True}}
    return run_checks(topology=topology(GNODES, GLINKS), state=state, statuses={nid(n): "started" for n, _, _ in GNODES},
                      definition=None, images={}, checks=checks, grading=GRADING, stale=set())


def test_groups_by_name_buh_and_adm():
    assert group_run([10, 20, 10, 10, 20])["findings"] == []
    result = group_run([10, 20, 10, 10, 10])  # ADM-1 in the VLAN of BUH
    assert {"segments_merged", "gateway_wrong"} <= set(codes(result))
    merged = next(f for f in result["findings"] if f["code"] == "segments_merged")
    assert "ADM и BUH в одном L2-домене" in merged["message"]
    assert "gateway_wrong" in codes(group_run([10, 20, 10, 10, 20], adm_gw="192.168.20.9"), counted=True)


def test_explicit_group_with_a_vlan_and_its_name():
    checks = {"format": "gns3lab.checks/1", "groups": {"explicit": [
        {"name": "BUH", "members": ["BUH-1", "BUH-2"], "vlan": 10}, {"name": "ADM", "members": ["ADM-1"], "vlan": 30}]}}
    result = group_run([10, 20, 10, 10, 20], checks=checks)
    wrong = [f for f in result["findings"] if f["code"] == "vlan_wrong"]
    assert len(wrong) == 1 and "должен быть 30 (ADM)" in wrong[0]["message"]


# ---------------------------------------------------------------------- grade


def test_profiles_overrides_focus_and_thresholds():
    facts = good_facts()
    facts["PC1"] = host("192.168.10.1/16", "192.168.10.254")  # mask_wrong: minor / major / critical
    assert run(facts, grading=normalize_grading({"profile": "lenient"}))["mark"] == 5
    assert run(facts)["mark"] == 4
    assert run(facts, grading=normalize_grading({"profile": "strict"}))["mark"] == 2
    assert run(facts, grading=normalize_grading({"overrides": {"mask_wrong": "note"}}))["mark"] == 5
    focused = copy.deepcopy(DEFINITION)
    focused["checks"]["focus"] = ["addressing"]
    result = run(facts, focused, grading=normalize_grading({"profile": "lenient"}))
    mask = next(f for f in result["findings"] if f["code"] == "mask_wrong")
    assert (mask["base_class"], mask["class"], result["mark"]) == ("minor", "major", 4)
    custom = normalize_grading({"thresholds": [{"mark": 100, "critical": 0, "major": 0, "minor": 0}], "fail_mark": 0})
    assert run(facts, grading=custom)["mark"] == 0


@pytest.mark.parametrize("raw, message", [
    ({"profile": "exam"}, "profile"),
    ({"overrides": {"nope": "minor"}}, "unknown rule"),
    ({"overrides": {"mask_wrong": "fatal"}}, "overrides"),
    ({"thresholds": [{"mark": 3}, {"mark": 4}]}, "decreasing"),
    ({"fail_mark": 3}, "fail_mark"),
])
def test_bad_grading(raw, message):
    with pytest.raises(ValueError, match=message):
        normalize_grading(raw)


def test_rules_can_be_turned_off():
    facts = good_facts()
    facts["PC1"] = host("192.168.10.1/16", "192.168.10.254")
    definition = copy.deepcopy(DEFINITION)
    definition["checks"]["rules"] = {"mask_wrong": "off"}
    result = run(facts, definition)
    assert "mask_wrong" not in codes(result) and rule(result, "mask_wrong")["status"] == "off"


# ---------------------------------------------------------------------- the document and the definition


def test_check_definition_problems():
    raw = {"format": "gns3lab.checks/1", "focus": ["l2", "dns"], "extra": 1,
           "groups": {"explicit": [{"name": "A", "members": ["PC1"]}, {"name": "B", "members": ["PC1", "PCX"]}]},
           "addresses": {"allowed": ["rfc1918", "10.0.0.1/8"]}, "rules": {"nope": "off"},
           "reachability": [["PC1", "PC1"]]}
    norm, problems = check_definition(raw, {"PC1", "PC2"})
    assert norm is None
    assert {(p["path"], p["code"]) for p in problems} == {
        ("checks.focus", "out_of_range"), ("checks.extra", "unknown_field"),
        ("checks.groups.explicit[1].members[0]", "group_overlap"), ("checks.groups.explicit[1].members[1]", "unknown_node"),
        ("checks.addresses.allowed[1]", "bad_range"), ("checks.rules.nope", "unknown_rule"),
        ("checks.reachability[0]", "bad_type")}
    norm, problems = check_definition({"format": "gns3lab.checks/1", "focus": ["l2", "l1", "l2"],
                                       "switches": {"trunk_prune": False, "mgmt_vlan": 333}}, set())
    assert problems == [] and norm == {"format": "gns3lab.checks/1", "focus": ["l1", "l2"],
                                       "switches": {"mgmt_vlan": 333}}


def test_the_definition_carries_checks_and_student_segments():
    lab = check(copy.deepcopy(DEFINITION), IMAGES, ALL)
    assert lab.definition["checks"] == {"format": "gns3lab.checks/1", "reachability": "segments"}
    assert [s["configure"] for s in lab.definition["segments"]] == ["student", "student"]
    assert setup(lab.definition, IMAGES) == {}  # The student sets it up: nothing is preconfigured
    assert not [w for w in lab.warnings if w["code"] in ("gateway_not_configured", "switch_not_configured")]
    managed = copy.deepcopy(DEFINITION)
    for seg in managed["segments"]:
        del seg["configure"]
    managed_lab = check(managed, IMAGES, ALL)
    assert "configure" not in managed_lab.definition["segments"][0] and setup(managed_lab.definition, IMAGES)
    assert managed_lab.lab_def != lab.lab_def


def test_bad_checks_in_the_definition():
    lab = copy.deepcopy(DEFINITION)
    lab["checks"] = {"format": "gns3lab.checks/1", "nodes": {"PCX": {"persistence": "runtime"}}}
    lab["segments"][0]["configure"] = "teacher"
    with pytest.raises(LabInvalid) as exc:
        check(lab, IMAGES, ALL)
    assert {(p["path"], p["code"]) for p in exc.value.problems} == {
        ("checks.nodes.PCX", "unknown_node"), ("segments[0].configure", "out_of_range")}
    lab1 = {k: v for k, v in DEFINITION.items() if k != "segments"}
    lab1["format"] = "gns3lab.lab/1"
    with pytest.raises(LabInvalid) as exc:
        check(lab1, IMAGES, ALL)
    assert [(p["path"], p["code"]) for p in exc.value.problems] == [("checks", "unknown_field")]


def test_the_catalog_of_rules_is_complete():
    for code, (group, classes, _) in checks_mod.RULES.items():
        assert group in checks_mod.GROUPS and len(classes) == 3 and set(classes) <= set(checks_mod.CLASSES)
    for code, causes in checks_mod.CAUSES.items():
        assert code in checks_mod.RULES and causes <= set(checks_mod.RULES)


# ---------------------------------------------------------------------- Cisco: router on a stick, switch settings


def test_cisco_switch_and_router_on_a_stick():
    nodes = [("SW1", "cisco-iou-l2"), ("R1", "cisco-iou-l3"), ("PC1", "debian12"), ("PC2", "debian12")]
    links = [(("R1", 0, 0), ("SW1", 0, 0)), (("PC1", 0, 0), ("SW1", 0, 1)), (("PC2", 0, 0), ("SW1", 0, 2))]
    topo = {"topology": {
        "nodes": [{"node_id": nid(n), "name": n, "node_type": IMAGES[i].kind, "console_type": "telnet"} for n, i in nodes],
        "links": [{"link_id": f"l{k}", "nodes": [{"node_id": nid(a[0]), "adapter_number": a[1], "port_number": a[2]},
                                                 {"node_id": nid(b[0]), "adapter_number": b[1], "port_number": b[2]}]}
                  for k, (a, b) in enumerate(links)]}}

    def port(n, mode):
        return {"name": f"Ethernet0/{n}", "kind": "physical", "port": {"adapter": 0, "port": n}, "admin": "up",
                "switchport": mode}

    sw = {"vlans": [{"id": 10, "name": "LAN1"}, {"id": 20, "name": "lan2"}, {"id": 99, "name": "junk"},
                    {"id": 333, "name": "mgmt"}],
          "interfaces": [port(0, {"mode": "trunk", "allowed": [10, 20, 30]}), port(1, {"mode": "access", "vlan": 10}),
                         port(2, {"mode": "access", "vlan": 20}), port(3, {"mode": "access", "vlan": 1}),
                         {"name": "Vlan333", "kind": "svi", "vlan": 333, "admin": "up", "ipv4": ["10.33.3.2/24"]}]}
    r1 = {"interfaces": [
        {"name": "Ethernet0/0", "kind": "physical", "port": {"adapter": 0, "port": 0}, "admin": "up", "ipv4": []},
        {"name": "Ethernet0/0.10", "kind": "subif", "parent": "Ethernet0/0", "dot1q": 10, "admin": "up",
         "ipv4": ["192.168.10.254/24"]},
        {"name": "Ethernet0/0.20", "kind": "subif", "parent": "Ethernet0/0", "dot1q": 20, "admin": "up",
         "ipv4": ["192.168.20.254/24"]}]}
    facts = {"SW1": sw, "R1": r1, "PC1": host("192.168.10.1/24", "192.168.10.254"),
             "PC2": host("192.168.20.1/24", "192.168.20.254")}
    definition = {
        "nodes": [{"name": n, "image": i} for n, i in nodes],
        "segments": [{"name": "LAN1", "vlan": {"fixed": 10}, "subnet": {"fixed": "192.168.10.0/24"},
                      "gateway": {"node": "R1", "adapter": 0, "port": 0}, "members": [{"node": "PC1"}]},
                     {"name": "LAN2", "vlan": {"fixed": 20}, "subnet": {"fixed": "192.168.20.0/24"},
                      "gateway": {"node": "R1", "adapter": 0, "port": 0}, "members": [{"node": "PC2"}]}]}
    checks = {"format": "gns3lab.checks/1", "reachability": "segments",
              "switches": {"unused_ports_shutdown": True, "trunk_prune": True, "vlan_names": "group", "mgmt_vlan": 333}}
    state = {"nodes": {nid(n): {"name": n, "status": "started", **f} for n, f in facts.items()}}
    result = run_checks(topology=topo, state=state, statuses={nid(n): "started" for n, _ in nodes},
                        definition=definition, images=IMAGES, checks=checks, grading=GRADING, stale=set())
    found = {f["code"]: f["message"] for f in result["findings"]}
    # VLAN 30 on the trunk to the router is not a broken link; pruning is checked between switches only.
    assert set(found) == {"unused_port_up", "vlan_extra"}, found
    assert "Ethernet0/3" in found["unused_port_up"] and "99" in found["vlan_extra"]
    assert rule(result, "unreachable")["status"] == "skipped"  # IOL keeps no routes in the saved configuration
    assert rule(result, "vlan_name_wrong")["status"] == "pass"  # lan2 = LAN2: the case does not matter
    assert rule(result, "segment_split")["status"] == "pass" and rule(result, "address_missing")["status"] == "pass"


def test_a_vlan_missing_on_a_trunk_between_switches():
    nodes = [("SW1", "cisco-iou-l2"), ("SW2", "cisco-iou-l2"), ("PC1", "debian12"), ("PC2", "debian12")]
    links = [(("SW1", 0, 0), ("SW2", 0, 0)), (("PC1", 0, 0), ("SW1", 0, 1)), (("PC2", 0, 0), ("SW2", 0, 1))]
    topo = {"topology": {
        "nodes": [{"node_id": nid(n), "name": n, "node_type": IMAGES[i].kind, "console_type": "telnet"} for n, i in nodes],
        "links": [{"link_id": f"l{k}", "nodes": [{"node_id": nid(a[0]), "adapter_number": a[1], "port_number": a[2]},
                                                 {"node_id": nid(b[0]), "adapter_number": b[1], "port_number": b[2]}]}
                  for k, (a, b) in enumerate(links)]}}

    def sw(allowed):
        return {"vlans": [{"id": 10, "name": "LAN1"}, {"id": 20, "name": "X"}], "interfaces": [
            {"name": "Ethernet0/0", "kind": "physical", "port": {"adapter": 0, "port": 0}, "admin": "up",
             "switchport": {"mode": "trunk", "allowed": allowed}},
            {"name": "Ethernet0/1", "kind": "physical", "port": {"adapter": 0, "port": 1}, "admin": "up",
             "switchport": {"mode": "access", "vlan": 10}}]}

    definition = {"nodes": [{"name": n, "image": i} for n, i in nodes],
                  "segments": [{"name": "LAN1", "vlan": {"fixed": 10}, "subnet": {"fixed": "192.168.10.0/24"},
                                "members": [{"node": "PC1"}, {"node": "PC2"}]}]}
    checks = {"format": "gns3lab.checks/1", "switches": {"trunk_prune": True}, "rules": {"vlan_extra": "off"}}

    def result(a, b):
        facts = {"SW1": sw(a), "SW2": sw(b), "PC1": host("192.168.10.1/24"), "PC2": host("192.168.10.2/24")}
        state = {"nodes": {nid(n): {"name": n, "status": "started", **f} for n, f in facts.items()}}
        return run_checks(topology=topo, state=state, statuses={nid(n): "started" for n, _ in nodes},
                          definition=definition, images=IMAGES, checks=checks, grading=GRADING, stale=set())

    assert result([10], [10])["findings"] == []
    broken = result([10, 20], [20])
    by_code = {f["code"]: f for f in broken["findings"]}
    assert by_code["tag_not_allowed"]["counted"] and "VLAN 10 не проходит" in by_code["tag_not_allowed"]["message"]
    assert by_code["segment_split"]["caused_by"] == by_code["tag_not_allowed"]["id"]
    assert [f["message"] for f in broken["findings"] if f["code"] == "trunk_not_pruned"] == [
        "SW1 Ethernet0/0: на транке разрешены лишние VLAN (20).", "SW2 Ethernet0/0: на транке разрешены лишние VLAN (20)."]


def test_an_unsaved_cisco_switch_is_checked_as_it_would_boot():
    nodes = [("SW1", "cisco-iou-l2"), ("PC1", "debian12"), ("PC2", "debian12")]
    links = [(("PC1", 0, 0), ("SW1", 0, 1)), (("PC2", 0, 0), ("SW1", 0, 2))]
    topo = {"topology": {
        "nodes": [{"node_id": nid(n), "name": n, "node_type": IMAGES[i].kind, "console_type": "telnet",
                   "ports": [{"name": f"Ethernet0/{p}", "adapter_number": 0, "port_number": p} for p in range(4)]}
                  for n, i in nodes],
        "links": [{"link_id": f"l{k}", "nodes": [{"node_id": nid(a[0]), "adapter_number": a[1], "port_number": a[2]},
                                                 {"node_id": nid(b[0]), "adapter_number": b[1], "port_number": b[2]}]}
                  for k, (a, b) in enumerate(links)]}}
    definition = {"nodes": [{"name": n, "image": i} for n, i in nodes],
                  "segments": [{"name": "A", "vlan": {"fixed": 10}, "subnet": {"fixed": "192.168.10.0/24"},
                                "members": [{"node": "PC1"}]},
                               {"name": "B", "vlan": {"fixed": 20}, "subnet": {"fixed": "192.168.20.0/24"},
                                "members": [{"node": "PC2"}]}]}
    facts = {"PC1": host("192.168.10.1/24"), "PC2": host("192.168.20.1/24")}  # No ios.config record for SW1
    state = {"nodes": {nid(n): {"name": n, "status": "started", **f} for n, f in facts.items()}}
    result = run_checks(topology=topo, state=state, statuses={nid(n): "started" for n, _ in nodes},
                        definition=definition, images=IMAGES, checks=None, grading=GRADING, stale=set())
    by_code = {f["code"]: f for f in result["findings"]}
    assert by_code["config_not_saved"]["counted"] and "write memory" in by_code["config_not_saved"]["message"]
    assert {f["code"] for f in result["findings"] if f["caused_by"] == by_code["config_not_saved"]["id"]} >= {
        "vlan_wrong", "segments_merged"}
    assert codes(result, counted=True) == ["config_not_saved"]
