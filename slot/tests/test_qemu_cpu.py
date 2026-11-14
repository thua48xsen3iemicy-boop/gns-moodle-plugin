"""The QEMU CPU model of a node (NodeSpec.cpu, SLOT_QEMU_CPU), CONTRACTS.md 4.2."""

import shlex

import pytest
from api import labs
from api.models import NodeSpec
from fastapi import HTTPException


@pytest.fixture
def image(tmp_path, monkeypatch):
    (tmp_path / "QEMU").mkdir()
    (tmp_path / "QEMU" / "a.qcow2").write_bytes(b"")
    monkeypatch.setattr(labs, "IMAGES", tmp_path)
    monkeypatch.delenv("SLOT_QEMU_CPU", raising=False)


def options(**extra):
    spec = NodeSpec(name="D1", kind="qemu", image="a.qcow2", **extra)
    return shlex.split(labs.node_payload(spec, "n1")["properties"]["options"])


def cpu_of(words):
    assert words.count("-cpu") <= 1
    return words[words.index("-cpu") + 1] if "-cpu" in words else None


def test_host_by_default(image):
    assert cpu_of(options()) == "host"
    assert cpu_of(options(console="vnc", qga=False)) == "host"


def test_the_ticket_wins(image, monkeypatch):
    monkeypatch.setenv("SLOT_QEMU_CPU", "qemu64")
    assert cpu_of(options(cpu="SandyBridge,+rdrand,+avx2")) == "SandyBridge,+rdrand,+avx2"
    assert cpu_of(options()) == "qemu64"


def test_an_empty_setting_leaves_qemu_its_own_model(image, monkeypatch):
    monkeypatch.setenv("SLOT_QEMU_CPU", "")
    assert cpu_of(options()) is None


def test_a_bad_setting_fails_the_node_not_the_options(image, monkeypatch):
    monkeypatch.setenv("SLOT_QEMU_CPU", "host -device foo")
    with pytest.raises(HTTPException):
        options()


@pytest.mark.parametrize("value", [
    "host", "max", "SandyBridge,+rdrand,+avx2", "host,-avx512f,kvm=off", "Skylake-Server-v4,hv-vendor-id=abc",
])
def test_good_values(value):
    assert NodeSpec(name="D1", kind="qemu", image="a.qcow2", cpu=value).cpu == value


@pytest.mark.parametrize("value", [
    "", "host -device x", "host,+avx2 -enable-kvm", "-host", "host,,avx", "host,%vm-name%", "host;id", "host,+",
    "a" * 65,
])
def test_bad_values(value):
    with pytest.raises(ValueError):
        NodeSpec(name="D1", kind="qemu", image="a.qcow2", cpu=value)


def test_qemu_only_and_absent_from_the_dump_when_unset():
    with pytest.raises(ValueError):
        NodeSpec(name="R1", kind="iou", image="i86bi.bin", cpu="host")
    assert "cpu" not in NodeSpec(name="D1", kind="qemu", image="a.qcow2").model_dump()
    assert NodeSpec(name="D1", kind="qemu", image="a.qcow2", cpu="host").model_dump()["cpu"] == "host"
