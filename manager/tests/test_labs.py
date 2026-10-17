import json
import shutil

import pytest
from conftest import ROOT

from lab_manager.labs import Catalog, LabError, LabNotFound, deploy_ticket, validate


@pytest.fixture
def catalog(tmp_path):
    shutil.copytree(ROOT / "examples" / "labs", tmp_path / "labs")
    return Catalog(tmp_path / "labs")


def test_example_lab_is_consistent(catalog):
    labs = catalog.list()
    assert labs == [{"lab_ref": "cisco-3x2", "title": "Три Cisco в кольце и шесть Debian",
                     "description": "Пример лабы из slot/examples/cisco-3x2.json"}]
    assert catalog.check_all() == {"cisco-3x2": "ok"}


@pytest.mark.parametrize("ref", ["../etc", "CISCO", "a/b", "", ".hidden", "x" * 80])
def test_bad_lab_ref(catalog, ref):
    with pytest.raises(LabNotFound):
        catalog.get(ref)


def test_name_mismatch_is_reported(catalog, tmp_path):
    path = tmp_path / "labs" / "cisco-3x2" / "ticket.json"
    ticket = json.loads(path.read_text())
    ticket["nodes"][0]["name"] = "RENAMED"
    path.write_text(json.dumps(ticket))
    with pytest.raises(LabError, match="RENAMED"):
        catalog.get("cisco-3x2")
    assert catalog.list() == []  # Broken labs are not offered to Moodle


def test_symlinked_lab_dir_is_refused(catalog, tmp_path):
    (tmp_path / "labs" / "evil").symlink_to(tmp_path / "labs" / "cisco-3x2")
    with pytest.raises(LabNotFound):
        catalog.get("evil")


def test_validate_requires_title():
    topo = {"topology": {"nodes": [{"name": "R1"}]}}
    assert validate("x", topo, {"nodes": [{"name": "R1"}]}, {}) == ["meta.json needs a title"]


def test_deploy_ticket_sets_identity(catalog):
    lab = catalog.get("cisco-3x2")
    ticket = deploy_ticket(lab, project_id="11111111-1111-4111-8111-111111111111", project_name="cisco-3x2-w7")
    assert ticket["project_id"] == "11111111-1111-4111-8111-111111111111"
    assert ticket["name"] == "cisco-3x2-w7"
    assert lab.ticket.get("project_id") is None  # Catalog copy untouched
