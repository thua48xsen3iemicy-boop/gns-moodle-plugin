"""The QEMU machine type of a node (NodeSpec.machine), CONTRACTS.md 4.2."""

import shlex

import pytest
from api import labs
from api.models import NodeSpec


@pytest.fixture
def image(tmp_path, monkeypatch):
    (tmp_path / "QEMU").mkdir()
    (tmp_path / "QEMU" / "a.qcow2").write_bytes(b"")
    monkeypatch.setattr(labs, "IMAGES", tmp_path)
    monkeypatch.delenv("SLOT_QEMU_CPU", raising=False)


def options(**extra):
    spec = NodeSpec(name="D1", kind="qemu", image="a.qcow2", **extra)
    return shlex.split(labs.node_payload(spec, "n1")["properties"]["options"])


def test_the_type_of_the_ticket_is_set(image):
    words = options(machine="pc-q35-2.11", console="vnc", disk_interface="sata")
    assert words[words.index("-machine") + 1] == "type=pc-q35-2.11"
    assert words[words.index("-cpu") + 1] == "host"  # the CPU model stays


def test_no_type_without_the_field(image):
    assert "-machine" not in options()


@pytest.mark.parametrize("value", ["pc-q35-2.11", "q35", "pc-i440fx-8.2", "pc-q35-noble"])
def test_good_values(value):
    assert NodeSpec(name="D1", kind="qemu", image="a.qcow2", machine=value).machine == value


@pytest.mark.parametrize("value", [
    "", "q35,accel=tcg", "q35 -device x", "-q35", "Q35", "q35%vm-id%", "a" * 65,
])
def test_bad_values(value):
    with pytest.raises(ValueError):
        NodeSpec(name="D1", kind="qemu", image="a.qcow2", machine=value)


def test_qemu_only_and_absent_from_the_dump_when_unset():
    with pytest.raises(ValueError):
        NodeSpec(name="R1", kind="iou", image="i86bi.bin", machine="q35")
    assert "machine" not in NodeSpec(name="D1", kind="qemu", image="a.qcow2").model_dump()
