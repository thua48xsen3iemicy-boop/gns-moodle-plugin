"""The QEMU machine type of an image (CONTRACTS 2.6, 4.2): `slot.machine` in the catalog, NodeSpec.machine in the ticket."""

import pytest
from pydantic import ValidationError

from lab_manager.config import SiteLimits
from lab_manager.images import MACHINE_PATTERN, Image
from lab_manager.labdef import check, deploy_ticket
from test_labdef import slot_models

PID = "6d0f1c1e-3c58-4c0b-9a6e-0d6f6f3c1a11"
BASE = {"title": "X", "kind": "qemu", "file": "x.qcow2",
        "ram_mb": {"default": 2048, "min": 1024, "max": 4096}, "adapters": {"default": 1, "min": 1, "max": 4}}


def images():
    return {"q35": Image(id="q35", **BASE, slot={"machine": "pc-q35-2.11", "disk_interface": "sata"}),
            "plain": Image(id="plain", **BASE)}


def test_only_nodes_of_an_image_with_a_type_carry_it():
    lab = {"format": "gns3lab.lab/1", "links": [],
           "nodes": [{"name": "Q", "image": "q35"}, {"name": "PC", "image": "plain"}]}
    checked = check(lab, images(), SiteLimits())
    ticket = deploy_ticket(checked.definition, images(), project_id=PID, project_name="p")
    q35, plain = ticket["nodes"]
    assert q35["machine"] == "pc-q35-2.11" and "machine" not in plain
    parsed = slot_models().DeployTicket.model_validate(ticket)  # exactly what the slot accepts
    assert [n.machine for n in parsed.nodes] == ["pc-q35-2.11", None]


def test_the_pattern_is_the_slots():
    assert MACHINE_PATTERN == slot_models().MACHINE_PATTERN


@pytest.mark.parametrize("value", ["", "q35,accel=tcg", "q35 -x", "Q35"])
def test_bad_types(value):
    with pytest.raises(ValidationError):
        Image(id="x", **BASE, slot={"machine": value})


def test_qemu_only():
    with pytest.raises(ValidationError):
        Image(id="r", **{**BASE, "kind": "iou", "file": "x.bin"}, slot={"machine": "q35"})
