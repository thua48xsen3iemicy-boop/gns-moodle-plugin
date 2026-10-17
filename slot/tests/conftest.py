import pytest

from test_slot import mock_server  # noqa: F401 - expose existing fixture across regression modules


@pytest.fixture(autouse=True)
def lab_state_dir(tmp_path, monkeypatch):
    """The student's directory (/opt/gns3/projects/.lab-slot) for every test."""
    monkeypatch.setenv("SLOT_LAB_STATE_DIR", str(tmp_path / "student" / ".lab-slot"))
