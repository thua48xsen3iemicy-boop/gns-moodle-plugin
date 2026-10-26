import pytest

from test_slot import mock_server  # noqa: F401 - expose existing fixture across regression modules


@pytest.fixture(autouse=True)
def lab_state_dir(tmp_path, monkeypatch):
    """The student's directory (/opt/gns3/projects/.lab-slot) for every test."""
    monkeypatch.setenv("SLOT_LAB_STATE_DIR", str(tmp_path / "student" / ".lab-slot"))


@pytest.fixture(autouse=True)
def projects_dir(tmp_path, monkeypatch):
    """GNS3 projects (/opt/gns3/projects) for every test: the saved configurations of IOL nodes live there."""
    path = tmp_path / "projects"
    monkeypatch.setenv("SLOT_PROJECTS_DIR", str(path))
    return path


@pytest.fixture(autouse=True)
def no_iou_export(tmp_path, monkeypatch):
    """No iou_export.py of the GNS3 venv (/opt/gns3-server) unless a test names one."""
    from api import ios_config

    monkeypatch.delenv("SLOT_IOU_EXPORT", raising=False)
    monkeypatch.setattr(ios_config, "DEFAULT_EXPORT", str(tmp_path / "no-gns3" / "iou_export.py"))
