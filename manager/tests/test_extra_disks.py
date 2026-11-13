"""Additional blank disks of a node (CONTRACTS 2.6, 4.2): the catalog, the lab definition and the ticket."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from lab_manager.config import SiteLimits
from lab_manager.images import Image, load_images
from lab_manager.labdef import LabInvalid, check, deploy_ticket

IMAGES = load_images(Path(__file__).resolve().parents[1] / "examples" / "images.json")
PID = "6d0f1c1e-3c58-4c0b-9a6e-0d6f6f3c1a11"


def lab(*disks, image="debian12", **extra):
    node = {"name": "PC1", "image": image, **({"disks": [*disks]} if disks or extra.pop("empty", False) else {})}
    return {"format": "gns3lab.lab/1", "nodes": [node, {"name": "PC2", "image": "debian12", **extra}], "links": []}


def problems(definition, limits=SiteLimits()):
    with pytest.raises(LabInvalid) as caught:
        check(definition, IMAGES, limits)
    return [(p["path"], p["code"]) for p in caught.value.problems]


def test_catalog_flag_is_public_and_qemu_only():
    assert IMAGES["debian12"].public()["extra_disks"] == {"max": 3, "size_gb": {"default": 1, "min": 1, "max": 20}}
    assert "extra_disks" not in IMAGES["cisco-iou-l2"].public()
    base = {"id": "x", "title": "X", "ram_mb": {"default": 256, "min": 128, "max": 512},
            "adapters": {"default": 2, "min": 2, "max": 4}, "extra_disks": True}
    assert Image(**base, kind="qemu", file="x.qcow2").extra_disks
    with pytest.raises(ValidationError):
        Image(**base, kind="iou", file="x.bin")
    with pytest.raises(ValidationError):
        Image(**base, kind="qemu", file="x.qcow2", uplink_port=0, console_type="none")


def test_disks_reach_the_ticket_in_order():
    checked = check(lab({"size_gb": 5}, {"size_gb": 1}), IMAGES, SiteLimits())
    assert checked.definition["nodes"][0]["disks"] == [{"size_gb": 5}, {"size_gb": 1}]
    first, second = deploy_ticket(checked.definition, IMAGES, project_id=PID, project_name="p")["nodes"]
    assert first["disks"] == [{"size_gb": 5}, {"size_gb": 1}] and "disks" not in second


def test_an_empty_list_is_no_field():
    plain = check(lab(), IMAGES, SiteLimits())
    empty = check(lab(empty=True), IMAGES, SiteLimits())
    assert empty.lab_def == plain.lab_def and "disks" not in empty.definition["nodes"][0]
    assert check(lab({"size_gb": 1}), IMAGES, SiteLimits()).lab_def != plain.lab_def


@pytest.mark.parametrize("disks, expected", [
    ([{"size_gb": 0}], ("nodes[0].disks[0].size_gb", "out_of_range")),
    ([{"size_gb": 21}], ("nodes[0].disks[0].size_gb", "out_of_range")),
    ([{"size_gb": True}], ("nodes[0].disks[0].size_gb", "out_of_range")),
    ([{}], ("nodes[0].disks[0].size_gb", "required")),
    ([{"size_gb": 1, "format": "raw"}], ("nodes[0].disks[0].format", "unknown_field")),
    ([1], ("nodes[0].disks[0]", "bad_type")),
    ([{"size_gb": 1}] * 4, ("nodes[0].disks", "out_of_range")),
])
def test_bad_disks(disks, expected):
    assert problems(lab(*disks)) == [expected]


def test_disks_must_be_a_list_of_an_image_that_takes_them():
    definition = lab()
    definition["nodes"][0]["disks"] = {"size_gb": 1}
    assert problems(definition) == [("nodes[0].disks", "bad_type")]
    assert problems(lab({"size_gb": 1}, image="cisco-iou-l2")) == [("nodes[0].disks", "unsupported_image")]


def test_site_limit_counts_all_nodes():
    limits = SiteLimits(max_disk_gb=10)
    assert limits.public()["max_disk_gb"] == 10 and SiteLimits().public()["max_disk_gb"] is None
    definition = lab({"size_gb": 5}, {"size_gb": 5})
    assert check(definition, IMAGES, limits)
    definition["nodes"][1]["disks"] = [{"size_gb": 1}]
    assert problems(definition, limits) == [("nodes", "limit_exceeded")]
