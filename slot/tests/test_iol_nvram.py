"""Preset NVRAM of IOL nodes (`NodeSpec.nvram_config`, CONTRACTS.md 4.2, lab constructor §15.3).

`iou_import.py` of gns3server is replaced by a file of the same signature, `nvram_import(nvram, startup, private,
size)`: its real format is checked on the stand (and by test_real_gns3server_roundtrip where gns3server is there).
"""

import json
import os
from uuid import uuid4

import pytest
from api import iol_nvram, ios_config
from api.models import DeployTicket, NodeSpec
from test_slot import HEADERS, wait

TEXT = "hostname SW1\nvtp mode off\ninterface Ethernet0/0\n switchport mode access\nend\n"
FAKE_IMPORT = '''
import json, os

def nvram_import(nvram, startup, private, size):
    with open(os.environ["FAKE_IMPORT_LOG"], "a") as log:
        log.write(json.dumps([nvram is None, startup.decode(), private is None, size]) + "\\n")
    if b"explode" in startup:
        raise ValueError("invalid size")
    return bytearray(b"NVRAM:" + startup + bytes(size * 1024 - 6 - len(startup)))
'''


@pytest.fixture
def fake_import(tmp_path, monkeypatch):
    path = tmp_path / "bin" / "iou_import.py"
    path.parent.mkdir()
    path.write_text(FAKE_IMPORT)
    log = tmp_path / "import.log"
    monkeypatch.setenv("SLOT_IOU_IMPORT", str(path))
    monkeypatch.setenv("FAKE_IMPORT_LOG", str(log))
    monkeypatch.setattr(iol_nvram, "_importers", {})

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return calls


def ticket(**extra):
    return {
        "name": "iol",
        "project_id": str(uuid4()),
        "nodes": [
            {"name": "SW1", "kind": "iou", "image": "cisco.bin", **extra},
            {"name": "SW2", "kind": "iou", "image": "cisco.bin"},
            {"name": "D1", "kind": "qemu", "image": "debian12.qcow2"},
        ],
    }


def nvram_files(projects_dir, pid):
    root = projects_dir / pid / "project-files" / "iou"
    return sorted(root.glob("*/nvram_*")) if root.exists() else []


def test_nvram_is_written_once_before_the_first_start(mock_server, fake_import, projects_dir, monkeypatch):
    client, _, nodes = mock_server
    seen = []
    real = iol_nvram.preset

    def spy(project_id, node, text):
        seen.append((node["name"], node["status"]))  # GNS3's answer to the creation: not started yet
        return real(project_id, node, text)

    monkeypatch.setattr(iol_nvram, "preset", spy)
    data = ticket(nvram_config=TEXT)
    op = wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))
    assert op["status"] == "SUCCEEDED", op
    assert seen == [("SW1", "stopped")]  # Only the node that has the field
    assert fake_import() == [[True, TEXT, True, 256]]
    (path,) = nvram_files(projects_dir, data["project_id"])
    assert path.name == "nvram_00001" and path.stat().st_size == 256 * 1024
    assert path.read_bytes().startswith(b"NVRAM:hostname SW1\n")
    assert oct(path.stat().st_mode & 0o777) == "0o644"
    assert path.parent.name == nodes[data["project_id"]][0]["node_id"]
    assert all(n["status"] == "started" for n in nodes[data["project_id"]])


def test_continuing_the_project_leaves_the_nvram_alone(mock_server, fake_import, projects_dir):
    client, _, _ = mock_server
    data = ticket(nvram_config=TEXT)
    assert wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))["status"] == "SUCCEEDED"
    pid = data["project_id"]
    (path,) = nvram_files(projects_dir, pid)
    path.write_bytes(b"the student saved something")
    assert wait(client, client.post(f"/v1/labs/{pid}/stop", headers=HEADERS))["status"] == "SUCCEEDED"
    assert wait(client, client.post(f"/v1/labs/{pid}/resume", headers=HEADERS))["status"] == "SUCCEEDED"
    assert wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))["status"] == "SUCCEEDED"
    assert path.read_bytes() == b"the student saved something"
    assert len(fake_import()) == 1


def test_nodes_without_the_field_do_not_touch_nvram(mock_server, fake_import, projects_dir):
    client, _, _ = mock_server
    data = ticket()
    assert wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))["status"] == "SUCCEEDED"
    assert fake_import() == [] and nvram_files(projects_dir, data["project_id"]) == []


def test_import_failure_fails_the_lab_without_the_text(mock_server, fake_import, projects_dir):
    client, _, _ = mock_server
    data = ticket(nvram_config="explode\nend\n")
    op = wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))
    assert op["status"] == "FAILED" and op["error"]["code"] == "NVRAM_PRESET_FAILED"
    assert "SW1" in op["error"]["message"] and "explode" not in op["error"]["message"]
    assert nvram_files(projects_dir, data["project_id"]) == []
    status = client.get(f"/v1/labs/{data['project_id']}/status", headers=HEADERS).json()
    assert status["state"] == "FAILED"


def test_missing_module_fails_the_lab(mock_server, projects_dir):
    client, _, _ = mock_server  # No SLOT_IOU_IMPORT, no gns3server: conftest points the default nowhere
    op = wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=ticket(nvram_config=TEXT)))
    assert op["status"] == "FAILED" and op["error"]["code"] == "NVRAM_PRESET_FAILED"
    assert "SLOT_IOU_IMPORT" in op["error"]["message"]


def test_a_broken_module_fails_the_lab(mock_server, tmp_path, monkeypatch):
    broken = tmp_path / "iou_import.py"
    broken.write_text("def nvram_import(:\n")
    monkeypatch.setenv("SLOT_IOU_IMPORT", str(broken))
    client, _, _ = mock_server
    op = wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=ticket(nvram_config=TEXT)))
    assert op["status"] == "FAILED" and "Cannot load iou_import.py" in op["error"]["message"]


def test_a_write_failure_fails_the_lab(mock_server, fake_import, projects_dir, monkeypatch):
    client, _, _ = mock_server
    data = ticket(nvram_config=TEXT)
    real_open = os.open

    def refuse(path, *args, **kwargs):
        if "nvram_" in str(path):
            raise PermissionError("no")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", refuse)
    op = wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))
    monkeypatch.undo()
    assert op["status"] == "FAILED" and op["error"]["code"] == "NVRAM_PRESET_FAILED"
    assert "cannot write the NVRAM file: PermissionError" in op["error"]["message"]


def test_an_existing_file_is_not_overwritten(mock_server, fake_import, projects_dir, monkeypatch):
    client, _, _ = mock_server
    data = ticket(nvram_config=TEXT)
    original = iol_nvram.preset

    def occupied(project_id, node, text):
        directory = ios_config.node_directory(project_id, node)
        directory.mkdir(parents=True)
        (directory / "nvram_00001").write_bytes(b"theirs")
        return original(project_id, node, text)

    monkeypatch.setattr(iol_nvram, "preset", occupied)
    op = wait(client, client.post("/v1/labs/deploy", headers=HEADERS, json=data))
    assert op["status"] == "FAILED" and op["error"]["code"] == "NVRAM_PRESET_FAILED"
    assert nvram_files(projects_dir, data["project_id"])[0].read_bytes() == b"theirs"


def test_no_application_id_is_an_error(fake_import):
    node = {"name": "SW1", "node_id": str(uuid4()), "properties": {}}
    with pytest.raises(iol_nvram.NvramError, match="application id"):
        iol_nvram.preset(str(uuid4()), node, TEXT)


def test_the_nvram_file_is_the_one_the_collector_reads(fake_import, projects_dir):
    """The directory, the name and the application id are those of ios_config.find_nvram."""
    pid, node = str(uuid4()), {"name": "SW1", "node_id": str(uuid4()), "properties": {"application_id": 7}}
    path = iol_nvram.preset(pid, node, TEXT)
    directory = ios_config.node_directory(pid, node)
    assert path.parent == directory and ios_config.find_nvram(directory, node)[0] == path


def test_field_rules_and_the_passport():
    base = {"name": "SW1", "kind": "iou", "image": "cisco.bin"}
    assert NodeSpec(**base, nvram_config=TEXT).nvram_config == TEXT
    assert NodeSpec(**base, nvram_config="x" * 65536)
    for bad in ("x" * 65537, "a\r\nb", "café", "tab\there", "nul\x00"):
        with pytest.raises(ValueError):
            NodeSpec(**base, nvram_config=bad)
    for kind, extra in (("qemu", {}), ("vpcs", {})):
        with pytest.raises(ValueError, match="only supported for iou"):
            NodeSpec(name="N", kind=kind, image="a.qcow2" if kind == "qemu" else None, nvram_config=TEXT, **extra)
    # The dumped ticket is the project passport: a node without the field has no such key.
    dumped = DeployTicket.model_validate({"name": "n", "nodes": [base, {**base, "name": "SW2", "nvram_config": TEXT}]})
    nodes = dumped.model_dump(mode="json")["nodes"]
    assert "nvram_config" not in nodes[0] and nodes[1]["nvram_config"] == TEXT


def test_capabilities_announce_the_feature(mock_server):
    client, _, _ = mock_server
    assert "iol-nvram" in client.get("/v1/capabilities", headers=HEADERS).json()["features"]


def test_real_gns3server_roundtrip(tmp_path, monkeypatch):
    """With the gns3server of the stand: what nvram_import writes, nvram_export reads back."""
    if not (os.path.isfile(iol_nvram.DEFAULT_IMPORT) or os.getenv("REAL_IOU_IMPORT")):
        pytest.skip("gns3server is not installed here")
    monkeypatch.setenv("SLOT_IOU_IMPORT", os.getenv("REAL_IOU_IMPORT", iol_nvram.DEFAULT_IMPORT))
    monkeypatch.setenv("SLOT_IOU_EXPORT", os.getenv("REAL_IOU_EXPORT", ios_config.DEFAULT_EXPORT))
    monkeypatch.setattr(iol_nvram, "_importers", {})
    pid, node = str(uuid4()), {"name": "SW1", "node_id": str(uuid4()), "properties": {"application_id": 3}}
    path = iol_nvram.preset(pid, node, TEXT)
    assert path.stat().st_size == 256 * 1024
    assert ios_config.read_nvram(path) == TEXT
