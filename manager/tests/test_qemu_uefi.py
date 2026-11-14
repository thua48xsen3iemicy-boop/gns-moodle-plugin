"""UEFI firmware of an image (CONTRACTS 2.6, 4.2): `slot.firmware` in the catalog, NodeSpec.firmware in the ticket."""

import pytest
from pydantic import ValidationError

from lab_manager.config import SiteLimits
from lab_manager.images import Image
from lab_manager.labdef import check, deploy_ticket
from test_labdef import slot_models

PID = "6d0f1c1e-3c58-4c0b-9a6e-0d6f6f3c1a11"
BASE = {"title": "X", "kind": "qemu", "file": "x.qcow2",
        "ram_mb": {"default": 2048, "min": 1024, "max": 4096}, "adapters": {"default": 1, "min": 1, "max": 4}}


def images():
    return {"efi": Image(id="efi", **BASE, slot={"firmware": "uefi", "machine": "q35"}),
            "plain": Image(id="plain", **BASE)}


def test_only_nodes_of_a_uefi_image_carry_it():
    lab = {"format": "gns3lab.lab/1", "links": [],
           "nodes": [{"name": "E", "image": "efi"}, {"name": "PC", "image": "plain"}]}
    checked = check(lab, images(), SiteLimits())
    ticket = deploy_ticket(checked.definition, images(), project_id=PID, project_name="p")
    efi, plain = ticket["nodes"]
    assert efi["firmware"] == "uefi" and efi["machine"] == "q35" and "firmware" not in plain
    parsed = slot_models().DeployTicket.model_validate(ticket)  # exactly what the slot accepts
    assert [n.firmware for n in parsed.nodes] == ["uefi", None]


@pytest.mark.parametrize("value", ["", "bios", "UEFI", "efi", True])
def test_bad_values(value):
    with pytest.raises(ValidationError):
        Image(id="x", **BASE, slot={"firmware": value})


def test_qemu_only():
    with pytest.raises(ValidationError):
        Image(id="r", **{**BASE, "kind": "iou", "file": "x.bin"}, slot={"firmware": "uefi"})
