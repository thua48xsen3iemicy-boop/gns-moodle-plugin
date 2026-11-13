"""Images without a QEMU guest agent (CONTRACTS 2.6, 4.2): `qga: false` in the catalog and in the ticket."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from lab_manager.config import SiteLimits
from lab_manager.images import Image
from lab_manager.labdef import check, deploy_ticket
from test_labdef import slot_models

PID = "6d0f1c1e-3c58-4c0b-9a6e-0d6f6f3c1a11"
BASE = {"title": "X", "kind": "qemu", "file": "x.qcow2", "console_type": "vnc",
        "ram_mb": {"default": 2048, "min": 1024, "max": 4096}, "adapters": {"default": 2, "min": 1, "max": 4}}


def images():
    return {"blackbox": Image(id="blackbox", qga=False, **BASE), "plain": Image(id="plain", **BASE)}


def ticket():
    lab = {"format": "gns3lab.lab/1", "links": [],
           "nodes": [{"name": "BB", "image": "blackbox"}, {"name": "PC", "image": "plain"}]}
    checked = check(lab, images(), SiteLimits())
    return deploy_ticket(checked.definition, images(), project_id=PID, project_name="p")


def test_only_a_node_of_an_image_without_agent_carries_the_field():
    blackbox, plain = ticket()["nodes"]
    assert blackbox["qga"] is False and "qga" not in plain
    parsed = slot_models().DeployTicket.model_validate(ticket())  # exactly what the slot accepts
    assert [n.qga for n in parsed.nodes] == [False, True]


def test_the_flag_is_not_public_and_defaults_to_true():
    assert Image(id="plain", **BASE).qga is True
    assert "qga" not in images()["blackbox"].public()


@pytest.mark.parametrize("extra", [
    {"kind": "iou", "file": "x.bin", "console_type": "telnet"},
    {"net_config": "ifupdown"},
    {"net_config": "ifupdown", "random_ifnames": True},
    {"role": "switch"},
    {"uplink_port": 0, "console_type": "none"},
    {"slot": {"provisioning": [{"command": "true"}]}},
    {"qga": "false"},  # a string is not a boolean
])
def test_what_needs_an_agent_is_refused(extra):
    with pytest.raises(ValidationError):
        Image.model_validate({**BASE, "id": "bad", "qga": False, **extra})


def test_a_derived_image_keeps_the_flag_of_its_base():
    copy = images()["blackbox"].model_copy(update={"id": "d-1", "file": "d-1.qcow2"})
    assert copy.qga is False
