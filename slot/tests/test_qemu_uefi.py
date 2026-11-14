"""UEFI firmware of a QEMU node (NodeSpec.firmware), CONTRACTS.md 4.2.

The slot only sets GNS3's "uefi" property; GNS3 2.2.61 itself boots OVMF from pflash and keeps a copy of the
variables store in the node directory. That the guest boots is checked on the stand.
"""

from uuid import uuid4

import pytest
from api import labs
from api.models import DeployTicket, NodeSpec
from test_slot import HEADERS, wait


@pytest.fixture
def ovmf(mock_server):
    labs.OVMF_DIR.mkdir()
    for name in ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd"):  # Ubuntu 24.04 spells them so; GNS3 ignores the case
        (labs.OVMF_DIR / name).write_bytes(b"")
    return labs.OVMF_DIR


def ticket(**extra):
    return {
        "name": "uefi",
        "project_id": str(uuid4()),
        "nodes": [
            {"name": "D1", "kind": "qemu", "image": "debian12.qcow2", **extra},
            {"name": "D2", "kind": "qemu", "image": "debian12.qcow2"},
        ],
    }


def features(client):
    return client.get("/v1/capabilities", headers=HEADERS).json()["features"]


def test_only_the_uefi_node_gets_the_property(mock_server, ovmf):
    client, _, nodes = mock_server
    data = ticket(firmware="uefi")
    assert wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))["status"] == "SUCCEEDED"
    uefi, plain = (props for props in (n["properties"] for n in nodes[data["project_id"]]))
    assert uefi["uefi"] is True and "uefi" not in plain
    assert "-bios" not in uefi["options"] and "bios_image" not in uefi  # GNS3 refuses both at once


@pytest.mark.parametrize("missing", [None, "OVMF_VARS_4M.fd", "OVMF_CODE_4M.fd"])
def test_without_ovmf_the_ticket_is_refused_and_the_feature_is_gone(mock_server, ovmf, missing):
    client, projects, _ = mock_server
    assert "qemu-uefi" in features(client)
    for f in ovmf.iterdir():
        if missing is None or f.name == missing:
            f.unlink()
    assert "qemu-uefi" not in features(client)
    response = client.post("/v1/labs/deploy", headers=HEADERS, json=ticket(firmware="uefi"))
    assert response.status_code == 422 and "ovmf" in response.text and not projects
    assert wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=ticket()))["status"] == "SUCCEEDED"


def test_lower_case_names_count_too(mock_server):
    labs.OVMF_DIR.mkdir()
    for name in ("ovmf_code_4m.fd", "ovmf_vars_4m.fd"):
        (labs.OVMF_DIR / name).write_bytes(b"")
    assert labs.uefi_enabled()


@pytest.mark.parametrize("value", ["bios", "UEFI", "efi", "", "uefi -bios x"])
def test_bad_values(value):
    with pytest.raises(ValueError):
        NodeSpec(name="D1", kind="qemu", image="a.qcow2", firmware=value)


def test_qemu_only_and_absent_from_the_dump_when_unset():
    with pytest.raises(ValueError):
        NodeSpec(name="R1", kind="iou", image="i86bi.bin", firmware="uefi")
    dumped = DeployTicket.model_validate(ticket(firmware="uefi")).model_dump(mode="json")["nodes"]
    assert dumped[0]["firmware"] == "uefi" and "firmware" not in dumped[1]
