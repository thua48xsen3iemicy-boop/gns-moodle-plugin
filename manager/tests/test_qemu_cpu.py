"""The QEMU CPU model of an image (CONTRACTS 2.6, 4.2): `slot.cpu` in the catalog, NodeSpec.cpu in the ticket."""

import pytest
from pydantic import ValidationError

from lab_manager.config import SiteLimits
from lab_manager.images import CPU_PATTERN, Image
from lab_manager.labdef import check, deploy_ticket
from test_labdef import slot_models

PID = "6d0f1c1e-3c58-4c0b-9a6e-0d6f6f3c1a11"
BASE = {"title": "X", "kind": "qemu", "file": "x.qcow2",
        "ram_mb": {"default": 2048, "min": 1024, "max": 4096}, "adapters": {"default": 1, "min": 1, "max": 4}}


def images():
    return {"pve": Image(id="pve", **BASE, slot={"cpu": "host", "disk_interface": "sata"}),
            "plain": Image(id="plain", **BASE)}


def ticket():
    lab = {"format": "gns3lab.lab/1", "links": [],
           "nodes": [{"name": "PVE", "image": "pve"}, {"name": "PC", "image": "plain"}]}
    checked = check(lab, images(), SiteLimits())
    return deploy_ticket(checked.definition, images(), project_id=PID, project_name="p")


def test_only_nodes_of_an_image_with_a_model_carry_it():
    pve, plain = ticket()["nodes"]
    assert pve["cpu"] == "host" and pve["disk_interface"] == "sata" and "cpu" not in plain
    parsed = slot_models().DeployTicket.model_validate(ticket())  # exactly what the slot accepts
    assert [n.cpu for n in parsed.nodes] == ["host", None]


def test_the_pattern_is_the_slots():
    assert CPU_PATTERN == slot_models().CPU_PATTERN


@pytest.mark.parametrize("value", ["SandyBridge,+rdrand,+avx2", "host,-avx512f", "max"])
def test_good_models(value):
    assert Image(id="x", **BASE, slot={"cpu": value}).slot.cpu == value


@pytest.mark.parametrize("value", ["", "host -device x", "host,%vm-name%", "-host"])
def test_bad_models(value):
    with pytest.raises(ValidationError):
        Image(id="x", **BASE, slot={"cpu": value})


def test_qemu_only():
    with pytest.raises(ValidationError):
        Image(id="r", **{**BASE, "kind": "iou", "file": "x.bin"}, slot={"cpu": "host"})


def test_not_public_and_kept_by_a_derived_image():
    assert "host" not in str(images()["pve"].public()) and "slot" not in images()["pve"].public()
    assert images()["pve"].model_copy(update={"id": "d-1"}).slot.cpu == "host"
