import copy
import importlib.util
import json
from pathlib import Path

import pytest

from lab_manager.config import SiteLimits
from lab_manager.images import ImageError, load_images
from lab_manager.labdef import LabInvalid, check, deploy_ticket, reference_topology

ROOT = Path(__file__).resolve().parents[1]
IMAGES = load_images(ROOT / "examples" / "images.json")
ALL = SiteLimits()

LAB = {
    "format": "gns3lab.lab/1",
    "title": "Два коммутатора и Debian",
    "nodes": [
        {"name": "SW1", "image": "cisco-iou-l2", "x": -200, "y": 0, "adapters": 2},
        {"name": "SW2", "image": "cisco-iou-l2", "x": 200, "y": 0},
        {"name": "PC1", "image": "debian12", "x": -200, "y": 160, "ram_mb": 1024, "cpus": 2},
        {"name": "PC2", "image": "vpcs", "x": 200, "y": 160},
    ],
    "links": [
        {"a": {"node": "SW1", "adapter": 0, "port": 0}, "b": {"node": "SW2", "adapter": 0, "port": 0}},
        {"a": {"node": "SW1", "adapter": 1, "port": 3}, "b": {"node": "PC1", "adapter": 0, "port": 0}},
        {"a": {"node": "SW2", "port": 1}, "b": {"node": "PC2"}},
    ],
}


def problems(lab, limits=ALL):
    with pytest.raises(LabInvalid) as exc:
        check(lab, IMAGES, limits)
    return [(p["path"], p["code"]) for p in exc.value.problems]


def changed(**patch):
    lab = copy.deepcopy(LAB)
    for path, value in patch.items():
        target = lab
        *parts, last = path.split("__")
        for part in parts:
            target = target[int(part)] if part.isdigit() else target[part]
        if value is ...:
            del target[int(last) if last.isdigit() else last]
        else:
            target[int(last) if last.isdigit() else last] = value
    return lab


def slot_models():
    """slot/api/models.py of this repository: the ticket must pass the slot's own validation."""
    spec = importlib.util.spec_from_file_location("slot_models", ROOT.parent / "slot" / "api" / "models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_valid_lab_hash_resources_and_ticket():
    lab = check(LAB, IMAGES, ALL)
    assert lab.lab_def.startswith("sha256:") and len(lab.lab_def) == 71
    # Memory: 1024 + 1024 + 1024 + 32 = 3104, +10 %, +1024 overhead, rounded up to 256.
    # Cores: 2 QEMU vCPUs + one per two IOL + one per eight VPCS.
    assert lab.resources == {"cores": 4, "memory_mb": 4608}
    assert lab.definition["links"][2] == {"a": {"node": "SW2", "adapter": 0, "port": 1},
                                         "b": {"node": "PC2", "adapter": 0, "port": 0}}
    assert check(changed(title="Другое имя"), IMAGES, ALL).lab_def == lab.lab_def  # Title is not the lab
    assert check(changed(nodes__1__x=210), IMAGES, ALL).lab_def != lab.lab_def
    reordered = json.loads(json.dumps(LAB, sort_keys=True))
    assert check(reordered, IMAGES, ALL).lab_def == lab.lab_def  # Key order does not matter

    ticket = deploy_ticket(lab.definition, IMAGES, project_id="0b6c9d3e-6f1a-4c5e-9a7b-2d8e1f0a3c4b",
                           project_name="lab-1234abcd-w1")
    nodes = {n["name"]: n for n in ticket["nodes"]}
    assert nodes["SW1"] == {"name": "SW1", "kind": "iou", "x": -200, "y": 0, "ram_mb": 1024, "ethernet_adapters": 2,
                            "image": "x86_64_crb_linux_l2-adventerprisek9-ms.bin"}
    assert nodes["PC1"]["image"] == "debian12.qcow2" and nodes["PC1"]["disk_interface"] == "virtio"
    assert nodes["PC1"]["cpus"] == 2 and nodes["PC1"]["adapters"] == 1
    assert nodes["PC2"] == {"name": "PC2", "kind": "vpcs", "x": 200, "y": 160}
    slot_models().DeployTicket.model_validate(ticket)  # Exactly what the slot accepts

    ref = reference_topology(lab.definition, IMAGES)["topology"]
    assert {n["name"]: n["symbol"] for n in ref["nodes"]}["SW1"] == ":/symbols/multilayer_switch.svg"
    assert ref["drawings"] == []


@pytest.mark.parametrize("lab,expected", [
    (changed(format="gns3lab.lab/3"), ("format", "bad_format")),
    (changed(format=...), ("format", "required")),
    (changed(extra=1), ("extra", "unknown_field")),
    (changed(nodes__0__console="telnet"), ("nodes[0].console", "unknown_field")),
    (changed(nodes__0__name="R 1"), ("nodes[0].name", "bad_name")),
    (changed(nodes__1__name="SW1"), ("nodes[1].name", "duplicate_name")),
    (changed(nodes__1__image="cisco-asa"), ("nodes[1].image", "unknown_image")),
    (changed(nodes__2__ram_mb=8192), ("nodes[2].ram_mb", "out_of_range")),
    (changed(nodes__2__ram_mb=True), ("nodes[2].ram_mb", "out_of_range")),
    (changed(nodes__0__cpus=2), ("nodes[0].cpus", "unknown_field")),
    (changed(nodes__3__ram_mb=64), ("nodes[3].ram_mb", "unknown_field")),
    (changed(nodes__0__x=10**6), ("nodes[0].x", "out_of_range")),
    (changed(links__0__b__node="R9"), ("links[0].b.node", "unknown_node")),
    (changed(links__1__b__port=1), ("links[1].b", "bad_port")),
    (changed(links__0__a__adapter=2), ("links[0].a", "bad_port")),
    (changed(links__2__a__port=0), ("links[2].a", "port_in_use")),
    (changed(links__0__b__node="SW1", links__0__b__adapter=1), ("links[0]", "self_link")),
    (changed(nodes=[]), ("nodes", "bad_type")),
])
def test_problems(lab, expected):
    assert expected in problems(lab)


def test_problems_are_all_reported():
    lab = changed(nodes__0__image="nope", nodes__2__ram_mb=1, links__2__b__port=5)
    found = problems(lab)
    assert ("nodes[0].image", "unknown_image") in found and ("nodes[2].ram_mb", "out_of_range") in found
    assert ("links[2].b", "bad_port") in found
    assert not any(code == "unknown_node" for _, code in found)  # SW1 is reported once, at its name


def test_site_limits():
    assert ("nodes", "too_many_nodes") in problems(LAB, SiteLimits(max_nodes=3))
    assert ("nodes[3].image", "image_not_allowed") in problems(LAB, SiteLimits(images=["cisco-iou-l2", "debian12"]))
    assert ("nodes", "limit_exceeded") in problems(LAB, SiteLimits(max_memory_mb=4096))
    assert ("nodes", "limit_exceeded") in problems(LAB, SiteLimits(max_cpus=3))
    check(LAB, IMAGES, SiteLimits(max_nodes=4, max_memory_mb=4608, max_cpus=4, images="*"))


def test_size_and_type():
    assert problems(changed(title="x" * 300 * 1024)) == [("", "too_large")]
    assert problems([]) == [("", "bad_type")]


@pytest.mark.parametrize("image", [
    {"id": "Bad", "title": "x", "kind": "vpcs"},
    {"id": "r", "title": "x", "kind": "iou", "file": "../x.bin", "ram_mb": {"default": 1, "min": 1, "max": 2},
     "adapters": {"default": 1, "min": 1, "max": 4}},
    {"id": "r", "title": "x", "kind": "iou", "file": "x.bin", "ram_mb": {"default": 512, "min": 512, "max": 1024},
     "adapters": {"default": 1, "min": 1, "max": 4}, "cpus": {"default": 1, "min": 1, "max": 1}},
    {"id": "r", "title": "x", "kind": "qemu", "file": "x.qcow2",
     "ram_mb": {"default": 9, "min": 512, "max": 1024}, "adapters": {"default": 1, "min": 1, "max": 4}},
    {"id": "r", "title": "x", "kind": "iou", "file": "x.bin", "ram_mb": {"default": 512, "min": 512, "max": 1024},
     "adapters": {"default": 1, "min": 1, "max": 4}, "slot": {"cli_prompt": ".*"}},
    {"id": "r", "title": "x", "kind": "vpcs", "secret_field": 1},
])
def test_bad_image_catalog_is_refused(tmp_path, image):
    path = tmp_path / "images.json"
    path.write_text(json.dumps({"images": [image]}))
    with pytest.raises(ImageError):
        load_images(path)


def test_public_image_has_no_file_or_slot_options():
    public = IMAGES["debian12"].public()
    assert "file" not in public and "slot" not in public
    assert public["port_name"] == "Ethernet{adapter}" and public["ports_per_adapter"] == 1
    assert IMAGES["cisco-iou-l2"].public()["port_name"] == "Ethernet{adapter}/{port}"
    assert IMAGES["vpcs"].public()["adapters"] is None
