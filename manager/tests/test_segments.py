"""Segments of gns3lab.lab/2: checks, addresses, the compiled ticket and the expected facts (lab constructor §13.1)."""

import copy
import subprocess
from pathlib import Path

import pytest

from lab_manager.config import SiteLimits
from lab_manager.images import Image, ImageError, load_images
from lab_manager.labdef import LabInvalid, check, deploy_ticket
from lab_manager.segments import expected_facts
from test_labdef import ALL, LAB, ROOT, slot_models

EXTRA = [
    {"id": "cisco-iou-l3", "title": "R", "kind": "iou", "category": "router", "file": "l3.bin",
     "ram_mb": {"default": 512, "min": 512, "max": 1024}, "adapters": {"default": 2, "min": 1, "max": 4}},
    {"id": "vyos", "title": "VyOS", "kind": "qemu", "category": "router", "file": "vyos.qcow2",
     "ram_mb": {"default": 512, "min": 256, "max": 1024}, "adapters": {"default": 2, "min": 1, "max": 4}},
    {"id": "ubuntu", "title": "U", "kind": "qemu", "category": "host", "file": "u.qcow2", "net_config": "netplan",
     "ram_mb": {"default": 512, "min": 256, "max": 1024}, "adapters": {"default": 2, "min": 1, "max": 4},
     "slot": {"provisioning": [{"command": "true"}]}},
    {"id": "fedora", "title": "F", "kind": "qemu", "category": "host", "file": "f.qcow2", "net_config": "networkmanager",
     "ram_mb": {"default": 512, "min": 256, "max": 1024}, "adapters": {"default": 1, "min": 1, "max": 4}},
    {"id": "alpine", "title": "A", "kind": "qemu", "category": "host", "file": "a.qcow2", "net_config": "networkd",
     "ram_mb": {"default": 512, "min": 256, "max": 1024}, "adapters": {"default": 1, "min": 1, "max": 4}},
    {"id": "vpcs-boot", "title": "V", "kind": "vpcs", "category": "host", "slot": {"startup_script": "set pcname X\n"}},
]
IMAGES = {**load_images(ROOT / "examples" / "images.json"),
          **{i["id"]: Image.model_validate(i) for i in EXTRA}}
LAB1_HASH = "sha256:fb1e2b03714b6fe154b4e69a58621691031d702cf26f5c11f8fa4a51b2e227c9"  # Computed by manager 0.9.0

# Two Debian and a VPCS behind a Linux switch, a router on the fourth port.
LAB2 = {
    "format": "gns3lab.lab/2",
    "title": "LAN",
    "nodes": [
        {"name": "SW1", "image": "linux-switch", "adapters": 8},
        {"name": "PC1", "image": "debian12"},
        {"name": "PC2", "image": "debian12"},
        {"name": "PC3", "image": "vpcs"},
        {"name": "R1", "image": "cisco-iou-l3"},
    ],
    "links": [
        {"a": {"node": "SW1", "adapter": 0}, "b": {"node": "PC1"}},
        {"a": {"node": "SW1", "adapter": 1}, "b": {"node": "PC2"}},
        {"a": {"node": "SW1", "adapter": 2}, "b": {"node": "PC3"}},
        {"a": {"node": "SW1", "adapter": 3}, "b": {"node": "R1", "adapter": 0, "port": 0}},
    ],
    "segments": [{
        "name": "LAN1",
        "subnet": {"fixed": "192.168.10.0/24"},
        "gateway": {"node": "R1", "adapter": 0, "port": 0},
        "members": [{"node": "PC1"}, {"node": "PC2", "host": 1}, {"node": "PC3"}],
    }],
}


def mk(**patch):
    lab = copy.deepcopy(LAB2)
    for path, value in patch.items():
        target = lab
        *parts, last = path.split("__")
        for part in parts:
            target = target[int(part)] if part.isdigit() else target[part]
        key = int(last) if last.isdigit() else last
        if value is ...:
            del target[key]
        else:
            target[key] = value
    return lab


def found(lab, limits=ALL):
    with pytest.raises(LabInvalid) as exc:
        check(lab, IMAGES, limits)
    return [(p["path"], p["code"]) for p in exc.value.problems]


def ticket(lab):
    return deploy_ticket(check(lab, IMAGES, ALL).definition, IMAGES, project_id="0b6c9d3e-6f1a-4c5e-9a7b-2d8e1f0a3c4b",
                         project_name="n")


def test_lab1_is_unchanged_to_the_byte():
    from test_labdef import IMAGES as CATALOG
    lab = check(LAB, CATALOG, ALL)
    assert lab.lab_def == LAB1_HASH and "segments" not in lab.definition and lab.warnings == ()
    assert "segments" in LAB2 and ("segments", "unknown_field") in found({**copy.deepcopy(LAB2), "format": "gns3lab.lab/1"})
    ticket_1 = deploy_ticket(lab.definition, CATALOG, project_id="p", project_name="n")
    assert "provisioning" not in {k for n in ticket_1["nodes"] for k in n}


def test_valid_lab_addresses_and_warnings():
    assert check(LAB2, IMAGES, ALL).warnings == ()  # The gateway is an IOL router: the manager sets it (stage 3)
    lab = check(mk(nodes__4__image="vyos"), IMAGES, ALL)  # ... and a VyOS router is the student's job
    assert lab.definition["format"] == "gns3lab.lab/2"
    assert [w["code"] for w in lab.warnings] == ["gateway_not_configured"]
    assert lab.warnings[0]["path"] == "segments[0].gateway"
    facts = expected_facts(lab.definition, IMAGES)["nodes"]
    # The gateway is the last working address, PC2 asked for .1, the others take the free numbers in order.
    assert facts["PC1"]["interfaces"][0]["ipv4"] == ["192.168.10.2/24"]
    assert facts["PC2"]["interfaces"][0]["ipv4"] == ["192.168.10.1/24"]
    assert facts["PC3"]["interfaces"][0]["ipv4"] == ["192.168.10.3/24"]
    assert {n["interfaces"][0]["gateway"] for n in facts.values()} == {"192.168.10.254"}
    assert "R1" not in facts and "SW1" not in facts
    assert expected_facts(check(LAB2, IMAGES, ALL).definition, IMAGES)["nodes"]["R1"]["interfaces"][0]["ipv4"] == \
        ["192.168.10.254/24"]


def test_lab_def_covers_segments_and_not_the_title():
    base = check(LAB2, IMAGES, ALL).lab_def
    assert check(mk(title="x"), IMAGES, ALL).lab_def == base
    assert check(mk(segments__0__members__0__host=7), IMAGES, ALL).lab_def != base
    assert check(mk(segments=[]), IMAGES, ALL).lab_def != base


def test_ticket_is_deterministic_and_complete():
    first = ticket(LAB2)
    assert first == ticket(copy.deepcopy(LAB2))
    nodes = {n["name"]: n for n in first["nodes"]}
    command = nodes["PC1"]["provisioning"]
    assert len(command) == 1 and command[0]["timeout_seconds"] == 120
    assert command[0]["command"].endswith("cfg 0 192.168.10.2/24 255.255.255.0 192.168.10.254\nfinish\n")
    assert nodes["PC3"]["startup_script"] == "ip 192.168.10.3/24 192.168.10.254\n"
    assert "provisioning" not in nodes["SW1"] and "provisioning" not in nodes["R1"]
    slot_models().DeployTicket.model_validate(first)
    for n in ("PC1", "PC2"):
        assert subprocess.run(["/bin/sh", "-n", "-c", nodes[n]["provisioning"][0]["command"]]).returncode == 0


def test_commands_of_the_image_come_first_and_each_mechanism_is_used():
    lab = {"format": "gns3lab.lab/2", "nodes": [{"name": n, "image": i} for n, i in (
        ("U", "ubuntu"), ("F", "fedora"), ("A", "alpine"), ("V", "vpcs-boot"))], "links": [],
        "segments": [{"name": "S", "subnet": {"fixed": "10.1.0.0/29"}, "members": [
            {"node": "U", "adapter": 1}, {"node": "F"}, {"node": "A"}, {"node": "V"}]}]}
    # No cables: four members on no common path.
    assert found(lab).count(("segments[0].members[1]", "segment_not_connected")) == 1
    lab["links"] = [{"a": {"node": "U", "adapter": 1}, "b": {"node": "F"}}]
    assert ("segments[0].members[2]", "segment_not_connected") in found(lab)
    lab["nodes"].append({"name": "S1", "image": "linux-switch"})
    lab["links"] = [{"a": {"node": "S1", "adapter": n}, "b": {"node": name}} for n, name in enumerate("FAV")]
    lab["links"].append({"a": {"node": "S1", "adapter": 3}, "b": {"node": "U", "adapter": 1}})
    nodes = {n["name"]: n for n in ticket(lab)["nodes"]}
    assert [c["command"][:3] for c in nodes["U"]["provisioning"]] == ["tru", "set"]
    assert "netplan apply" in nodes["U"]["provisioning"][1]["command"]
    assert "address1=10.1.0.2/29" in nodes["F"]["provisioning"][0]["command"] or "cfg 0 10.1.0.2/29" in \
        nodes["F"]["provisioning"][0]["command"]
    assert nodes["V"]["startup_script"] == "set pcname X\nip 10.1.0.4/29\n"  # No gateway: no second address


@pytest.mark.parametrize("lab,expected", [
    (mk(segments__0__name="1x"), ("segments[0].name", "bad_name")),
    (mk(segments__0__name=...), ("segments[0].name", "required")),
    (mk(segments__0__extra=1), ("segments[0].extra", "unknown_field")),
    (mk(segments__0__vlan={"random": [10, 20]}), ("segments[0].vlan.random", "unknown_field")),
    (mk(segments__0__vlan={"random": [10, 20]}), ("segments[0].vlan.fixed", "required")),
    (mk(segments__0__subnet={"random": {"pool": "rfc1918", "prefix": 24}}), ("segments[0].subnet.random", "unknown_field")),
    (mk(values="shared"), ("values", "unknown_field")),
    (mk(faults=[]), ("faults", "unknown_field")),
    (mk(segments__0__vlan={"fixed": 1}), ("segments[0].vlan.fixed", "vlan_out_of_range")),
    (mk(segments__0__vlan={"fixed": 1003}), ("segments[0].vlan.fixed", "vlan_out_of_range")),
    (mk(segments__0__vlan={"fixed": 4095}), ("segments[0].vlan.fixed", "vlan_out_of_range")),
    (mk(segments__0__vlan={"fixed": "10"}), ("segments[0].vlan.fixed", "bad_type")),
    (mk(segments__0__subnet={"fixed": "192.168.10.5/24"}), ("segments[0].subnet.fixed", "bad_subnet")),
    (mk(segments__0__subnet={"fixed": "fe80::/64"}), ("segments[0].subnet.fixed", "bad_subnet")),
    (mk(segments__0__subnet={"fixed": "192.168.10.0"}), ("segments[0].subnet.fixed", "bad_subnet")),
    (mk(segments__0__subnet={"fixed": "127.0.0.0/24"}), ("segments[0].subnet.fixed", "bad_subnet")),
    (mk(segments__0__subnet={"fixed": "192.168.10.0/255.255.255.0"}), ("segments[0].subnet.fixed", "bad_subnet")),
    (mk(segments__0__subnet=...), ("segments[0].subnet", "required")),
    (mk(segments__0__subnet={"fixed": "192.168.10.0/31"}), ("segments[0].subnet.fixed", "subnet_too_small")),
    (mk(segments__0__subnet={"fixed": "192.168.10.0/30"}), ("segments[0].members", "subnet_too_small")),
    (mk(segments__0__members__0__host=0), ("segments[0].members[0]", "host_out_of_range")),
    (mk(segments__0__members__0__host=254), ("segments[0].members[0]", "host_in_use")),  # The gateway's address
    (mk(segments__0__members__0__host=255), ("segments[0].members[0]", "host_out_of_range")),
    (mk(segments__0__members__0__host=1), ("segments[0].members[1]", "host_in_use")),  # PC2 asked for it first in order
    (mk(segments__0__gateway__host=300), ("segments[0].gateway", "host_out_of_range")),
    (mk(segments__0__members__0__host="1"), ("segments[0].members[0].host", "bad_type")),
    (mk(segments__0__gateway={"node": "PC1"}), ("segments[0].gateway", "gateway_not_router")),
    (mk(segments__0__gateway={"node": "R1", "adapter": 5}), ("segments[0].gateway", "bad_port")),
    (mk(segments__0__members__1__node="R9"), ("segments[0].members[1].node", "unknown_node")),
    (mk(segments__0__members__1__port=1), ("segments[0].members[1]", "bad_port")),
    (mk(segments__0__members__1={"node": "PC1"}), ("segments[0].members[1]", "member_in_two_segments")),
    (mk(segments__0__members__1={"node": "SW1"}), ("segments[0].members[1]", "unsupported_image")),
    (mk(segments__0__members__1={"node": "R1", "adapter": 1}), ("segments[0].members[1]", "unsupported_image")),
    (mk(segments__0__members=[]), ("segments[0].members", "bad_type")),
    (mk(segments__0__members=...), ("segments[0].members", "required")),
    (mk(segments=[mk()["segments"][0]] * 2), ("segments[1].name", "duplicate_segment")),
    (mk(segments="x"), ("segments", "bad_type")),
])
def test_problems(lab, expected):
    assert expected in found(lab)


def test_a_port_in_two_segments_is_reported_once_at_the_second():
    second = {"name": "LAN2", "subnet": {"fixed": "192.168.20.0/24"}, "members": [{"node": "PC1"}]}
    assert found(mk(segments=[mk()["segments"][0], second])) == [("segments[1].members[0]", "member_in_two_segments")]
    second["gateway"] = {"node": "R1", "adapter": 0, "port": 0}
    second["members"] = [{"node": "PC3"}]
    # Two segments without VLAN cannot share the router port (stage 3: with different VLANs they can)
    assert ("segments[1].gateway", "gateway_port_conflict") in found(mk(segments=[mk()["segments"][0], second]))


def test_segment_not_connected_names_the_member_that_is_lost():
    lab = mk()
    del lab["links"][2]  # PC3 is unplugged
    assert found(lab) == [("segments[0].members[2]", "segment_not_connected")]
    del lab["links"][2]  # ... and the router too: the gateway is the reference, so all members are lost
    assert found(lab) == [(f"segments[0].members[{j}]", "segment_not_connected") for j in range(3)]
    # Direct cable: two members need no switch, the traffic ends at a host.
    direct = {"format": "gns3lab.lab/2", "nodes": [{"name": "A", "image": "vpcs"}, {"name": "B", "image": "vpcs"}],
              "links": [{"a": {"node": "A"}, "b": {"node": "B"}}],
              "segments": [{"name": "S", "subnet": {"fixed": "10.0.0.0/30"}, "members": [{"node": "A"}, {"node": "B"}]}]}
    assert check(direct, IMAGES, ALL).definition["segments"][0]["members"][1]["node"] == "B"


def test_segment_overlap_and_switch_warnings():
    other = {"name": "LAN2", "subnet": {"fixed": "192.168.20.0/24"}, "members": [{"node": "PC9"}, {"node": "PC8"}]}
    lab = mk()
    lab["nodes"] += [{"name": "PC9", "image": "vpcs"}, {"name": "PC8", "image": "vpcs"}]
    lab["links"] += [{"a": {"node": "SW1", "adapter": 4}, "b": {"node": "PC9"}},
                     {"a": {"node": "SW1", "adapter": 5}, "b": {"node": "PC8"}}]
    lab["segments"].append(other)
    assert found(lab) == [("segments[1]", "segment_overlap")]  # Two segments without VLAN on one switch
    lab["segments"][0]["vlan"] = {"fixed": 10}
    lab["segments"][1]["vlan"] = {"fixed": 10}
    assert found(lab) == [("segments[1]", "segment_overlap")]  # The same VLAN
    lab["segments"][1]["vlan"] = {"fixed": 20}
    done = check(lab, IMAGES, ALL)
    assert [(w["path"], w["code"]) for w in done.warnings] == [
        ("segments[0].vlan", "switch_not_configured"), ("segments[1].vlan", "switch_not_configured")]
    del lab["segments"][1]["vlan"]  # A VLAN and a plain segment on one switch: the student sets the switch
    assert check(lab, IMAGES, ALL).definition["segments"][1]["name"] == "LAN2"


def test_multiple_gateways():
    lab = mk()
    lab["nodes"].append({"name": "R2", "image": "vyos"})
    lab["links"].append({"a": {"node": "SW1", "adapter": 4}, "b": {"node": "R2", "adapter": 0}})
    lab["nodes"][1]["adapters"] = 2
    lab["links"].append({"a": {"node": "SW1", "adapter": 5}, "b": {"node": "PC1", "adapter": 1}})
    lab["segments"].append({"name": "LAN2", "subnet": {"fixed": "10.0.0.0/24"},
                            "gateway": {"node": "R2", "adapter": 0}, "members": [{"node": "PC1", "adapter": 1}]})
    assert found(lab) == [("segments[1].members[0]", "multiple_gateways")]
    lab["segments"][0]["vlan"], lab["segments"][1]["vlan"] = {"fixed": 10}, {"fixed": 20}
    del lab["segments"][1]["gateway"]  # Without a gateway the second address is fine
    assert [a for a in check(lab, IMAGES, ALL).definition["segments"][1]["members"]][0]["adapter"] == 1


def test_all_problems_come_at_once_and_segments_see_bad_nodes_quietly():
    lab = mk(segments__0__vlan={"fixed": 1}, segments__0__subnet={"fixed": "x"}, nodes__2__image="nope")
    got = found(lab)
    assert {("nodes[2].image", "unknown_image"), ("segments[0].vlan.fixed", "vlan_out_of_range"),
            ("segments[0].subnet.fixed", "bad_subnet")} <= set(got)
    assert not any(code in ("unknown_node", "segment_not_connected") for _, code in got)  # PC2 has its own problem


def test_net_config_in_the_catalog(tmp_path):
    base = {"id": "d", "title": "D", "kind": "qemu", "file": "d.qcow2",
            "ram_mb": {"default": 512, "min": 256, "max": 1024}, "adapters": {"default": 1, "min": 1, "max": 2}}
    for bad in ({**base, "net_config": "template"}, {**base, "net_config": "x"},
                {"id": "v", "title": "V", "kind": "vpcs", "net_config": "ifupdown"}):
        with pytest.raises(Exception):
            Image.model_validate(bad)
    assert Image.model_validate({**base, "net_config": "networkd"}).public()["preconfig"] is True
    assert Image.model_validate(base).public()["preconfig"] is False
    assert "net_config" not in Image.model_validate({**base, "net_config": "networkd"}).public()
    assert IMAGES["vpcs"].public()["preconfig"] and IMAGES["debian12"].public()["preconfig"]
    assert not IMAGES["linux-switch"].public()["preconfig"] and not IMAGES["cisco-iou-l2"].public()["preconfig"]
