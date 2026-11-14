"""The network adapter model of an image (CONTRACTS 2.6, 4.2): `slot.adapter_type`, NodeSpec.adapter_type in the ticket."""

import typing

import pytest
from pydantic import ValidationError

from lab_manager.config import SiteLimits
from lab_manager.images import AdapterType, Image
from lab_manager.labdef import check, deploy_ticket
from test_labdef import slot_models

PID = "6d0f1c1e-3c58-4c0b-9a6e-0d6f6f3c1a11"
BASE = {"title": "X", "kind": "qemu", "file": "x.qcow2",
        "ram_mb": {"default": 2048, "min": 1024, "max": 4096}, "adapters": {"default": 2, "min": 1, "max": 4}}


def images():
    return {"vio": Image(id="vio", **BASE, slot={"adapter_type": "virtio-net-pci"}), "plain": Image(id="plain", **BASE)}


def test_only_nodes_of_an_image_with_a_model_carry_it():
    lab = {"format": "gns3lab.lab/1", "links": [],
           "nodes": [{"name": "V", "image": "vio"}, {"name": "PC", "image": "plain"}]}
    checked = check(lab, images(), SiteLimits())
    ticket = deploy_ticket(checked.definition, images(), project_id=PID, project_name="p")
    vio, plain = ticket["nodes"]
    assert vio["adapter_type"] == "virtio-net-pci" and "adapter_type" not in plain
    parsed = slot_models().DeployTicket.model_validate(ticket)  # exactly what the slot accepts
    assert [n.adapter_type for n in parsed.nodes] == ["virtio-net-pci", None]


def test_the_models_are_the_slots():
    assert typing.get_args(AdapterType) == typing.get_args(slot_models().AdapterType)


@pytest.mark.parametrize("value", ["", "virtio", "E1000", "e1000,mac=x"])
def test_bad_models(value):
    with pytest.raises(ValidationError):
        Image(id="x", **BASE, slot={"adapter_type": value})


def test_qemu_only():
    with pytest.raises(ValidationError):
        Image(id="r", **{**BASE, "kind": "iou", "file": "x.bin"}, slot={"adapter_type": "virtio-net-pci"})
