"""The network adapter model of a QEMU node (NodeSpec.adapter_type), CONTRACTS.md 4.2."""

import pytest
from api import labs
from api.models import DeployTicket, NodeSpec


@pytest.fixture
def image(tmp_path, monkeypatch):
    (tmp_path / "QEMU").mkdir()
    (tmp_path / "QEMU" / "a.qcow2").write_bytes(b"")
    monkeypatch.setattr(labs, "IMAGES", tmp_path)


def props(**extra):
    return labs.node_payload(NodeSpec(name="D1", kind="qemu", image="a.qcow2", **extra), "n1")["properties"]


def test_the_model_of_the_ticket_is_set(image):
    assert props(adapter_type="virtio-net-pci", adapters=4)["adapter_type"] == "virtio-net-pci"


def test_e1000_without_the_field(image):
    assert props()["adapter_type"] == "e1000"


@pytest.mark.parametrize("value", ["", "virtio", "E1000", "e1000,mac=x", "virtio-net-pci -device x", "ne2k_pci"])
def test_bad_values(value):
    with pytest.raises(ValueError):
        NodeSpec(name="D1", kind="qemu", image="a.qcow2", adapter_type=value)


def test_qemu_only_and_absent_from_the_dump_when_unset():
    with pytest.raises(ValueError):
        NodeSpec(name="R1", kind="iou", image="i86bi.bin", adapter_type="virtio-net-pci")
    base = {"kind": "qemu", "image": "a.qcow2"}
    dumped = DeployTicket.model_validate(
        {"name": "n", "nodes": [{**base, "name": "D1", "adapter_type": "virtio-net-pci"}, {**base, "name": "D2"}]}
    ).model_dump(mode="json")["nodes"]
    assert dumped[0]["adapter_type"] == "virtio-net-pci" and "adapter_type" not in dumped[1]
