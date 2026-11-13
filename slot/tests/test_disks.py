"""Additional blank disks of QEMU nodes (`NodeSpec.disks`, CONTRACTS.md 4.2).

GNS3 is the mock of test_slot: its resize_disk writes a qcow2 header of the new size where GNS3 2.2.61 keeps the
linked clone. The real `qemu-img` and the guest seeing the disks are checked on the stand.
"""

from uuid import uuid4

import pytest
from api import disks, labs
from api.models import DeployTicket, NodeSpec
from pydantic import ValidationError
from test_slot import HEADERS, wait

MIB = 1024 * 1024


def qcow2(size, backing=0):
    return b"QFI\xfb" + bytes(4) + backing.to_bytes(8, "big") + bytes(8) + size.to_bytes(8, "big")


@pytest.fixture
def blank(mock_server):
    path = labs.IMAGES / "QEMU" / "blank.qcow2"
    path.write_bytes(qcow2(MIB))
    return path


def ticket(*sizes, name="D1", **extra):
    return {
        "name": "disks",
        "project_id": str(uuid4()),
        "nodes": [
            {"name": name, "kind": "qemu", "image": "debian12.qcow2", "disks": [{"size_gb": s} for s in sizes], **extra},
            {"name": "D2", "kind": "qemu", "image": "debian12.qcow2"},
        ],
    }


def test_disks_are_cloned_from_the_blank_and_grown_before_the_start(mock_server, blank, projects_dir):
    client, _, nodes = mock_server
    data = ticket(5, 1, disk_interface="sata")
    op = wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))
    assert op["status"] == "SUCCEEDED", op
    first, second = nodes[data["project_id"]]
    props = first["properties"]
    assert props["hda_disk_image"] == "debian12.qcow2"
    assert (props["hdb_disk_image"], props["hdc_disk_image"]) == ("blank.qcow2", "blank.qcow2")
    assert (props["hdb_disk_interface"], props["hdc_disk_interface"]) == ("sata", "sata")
    assert "hdd_disk_image" not in props and "hdb_disk_image" not in second["properties"]
    assert first["resized"] == [["hdb", 5 * 1024 - 1], ["hdc", 1023]] and "resized" not in second
    folder = projects_dir / data["project_id"] / "project-files" / "qemu" / first["node_id"]
    assert disks.virtual_size(folder / "hdb_disk.qcow2") == 5 * 1024 * MIB
    assert all(n["status"] == "started" for n in nodes[data["project_id"]])


def test_a_disk_of_the_size_of_the_blank_is_left_to_gns3(mock_server, blank):
    client, _, nodes = mock_server
    blank.write_bytes(qcow2(1024 * MIB))
    data = ticket(1, 2)
    assert wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))["status"] == "SUCCEEDED"
    assert nodes[data["project_id"]][0]["resized"] == [["hdc", 1024]]


def test_continuing_the_project_leaves_the_disks_alone(mock_server, blank):
    client, _, nodes = mock_server
    data = ticket(2)
    assert wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))["status"] == "SUCCEEDED"
    pid = data["project_id"]
    assert wait(client, client.post(f"/v1/labs/{pid}/stop", headers=HEADERS))["status"] == "SUCCEEDED"
    assert wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))["status"] == "SUCCEEDED"
    assert nodes[pid][0]["resized"] == [["hdb", 2047]]


def test_a_resize_that_did_nothing_fails_the_lab(mock_server, blank):
    client, _, _ = mock_server
    op = wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=ticket(3, name="bad-resize")))
    assert op["status"] == "FAILED" and op["error"]["code"] == "DISK_CREATE_FAILED"
    assert "hdb" in op["error"]["message"]


@pytest.mark.parametrize("content", [None, b"not a qcow2 image at all, just some bytes", qcow2(2048 * MIB),
                                     qcow2(MIB + 1), qcow2(MIB, backing=64)])
def test_without_a_good_blank_the_ticket_is_refused_and_the_feature_is_gone(mock_server, blank, content):
    client, projects, _ = mock_server
    assert "extra-disks" in client.get("/v1/capabilities", headers=HEADERS).json()["features"]
    blank.unlink()
    if content is not None:
        blank.write_bytes(content)
    assert "extra-disks" not in client.get("/v1/capabilities", headers=HEADERS).json()["features"]
    response = client.post("/v1/labs/deploy", headers=HEADERS, json=ticket(1))
    assert response.status_code == 422 and "blank.qcow2" in response.text and not projects
    plain = ticket()
    assert wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=plain))["status"] == "SUCCEEDED"


def test_the_blank_may_have_another_name(mock_server, blank, monkeypatch):
    client, _, nodes = mock_server
    monkeypatch.setenv("SLOT_BLANK_DISK", "empty.qcow2")
    assert not disks.enabled(labs.IMAGES)
    blank.rename(blank.with_name("empty.qcow2"))
    data = ticket(1)
    assert wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))["status"] == "SUCCEEDED"
    assert nodes[data["project_id"]][0]["properties"]["hdb_disk_image"] == "empty.qcow2"


def test_model_bounds_and_the_unchanged_representation_of_other_nodes():
    base = {"name": "D1", "kind": "qemu", "image": "a.qcow2"}
    assert len(NodeSpec(**base, disks=[{"size_gb": 64}] * 3).disks) == 3
    for bad in ([{"size_gb": 0}], [{"size_gb": 65}], [{"size_gb": "5"}], [{"size_gb": 1, "format": "raw"}],
                [{"size_gb": 1}] * 4, [{}]):
        with pytest.raises(ValidationError):
            NodeSpec(**base, disks=bad)
    with pytest.raises(ValidationError):
        NodeSpec(name="R1", kind="iou", image="a.bin", disks=[{"size_gb": 1}])
    dumped = DeployTicket.model_validate(
        {"name": "n", "nodes": [base, {**base, "name": "D2", "disks": []}, {**base, "name": "D3", "disks": [{"size_gb": 2}]}]}
    ).model_dump(mode="json")["nodes"]
    assert "disks" not in dumped[0] and "disks" not in dumped[1] and dumped[2]["disks"] == [{"size_gb": 2}]
