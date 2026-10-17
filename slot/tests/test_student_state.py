"""student-state: the lab record follows the student's directory to any slot."""

import json

from api.lifecycle import Lifecycle
from main import app
from test_slot import HEADERS, payload, wait


def switch_slot(client, tmp_path, monkeypatch, name):
    """Another slot CT: own SQLite, the same student's directory and GNS3 projects."""
    monkeypatch.setenv("SLOT_STATE_DIR", str(tmp_path / name))

    async def swap():
        await app.state.lifecycle.close()
        app.state.lifecycle = Lifecycle(app)

    client.portal.call(swap)


def record_provisioning(monkeypatch):
    calls = []

    async def provision(self, lab, node, spec, initial):
        calls.append(initial)
        if spec["name"] == "bad-provision":
            raise RuntimeError("mandatory provisioning failed")

    monkeypatch.setattr(Lifecycle, "provision", provision)
    return calls


def deploy(client, data, key):
    headers = {**HEADERS, "Idempotency-Key": key}
    return wait(client, client.post("/v1/labs/deploy", json=data, headers=headers))


def test_lab_record_lives_in_student_directory(mock_server, tmp_path):
    client, _, _ = mock_server
    data = payload()
    assert deploy(client, data, "k1")["status"] == "SUCCEEDED"
    path = tmp_path / "student" / ".lab-slot" / "labs" / (data["project_id"] + ".json")
    record = json.loads(path.read_text())
    assert record["state"] == "READY" and record["provisioned"] is True
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".")]
    rows = app.state.lifecycle.db.execute
    count = client.portal.call(
        lambda: rows("SELECT count(*) FROM records WHERE kind='lab'").fetchone()[0]
    )
    assert count == 0
    assert "student-state" in client.get("/v1/capabilities", headers=HEADERS).json()[
        "features"
    ]


def test_stopped_project_continues_in_another_slot(mock_server, tmp_path, monkeypatch):
    client, _, nodes = mock_server
    calls = record_provisioning(monkeypatch)
    data = payload()
    pid = data["project_id"]
    assert deploy(client, data, "s1-deploy")["status"] == "SUCCEEDED"
    wait(client, client.post(f"/v1/labs/{pid}/stop", headers=HEADERS))
    original = json.loads(json.dumps(nodes[pid]))
    assert calls[0] is True
    switch_slot(client, tmp_path, monkeypatch, "slot-2")
    calls.clear()
    assert deploy(client, data, "s2-deploy")["status"] == "SUCCEEDED"
    # Readiness checks only (deploy and monitor); initial provisioning is never replayed.
    assert calls and True not in calls
    assert [n["node_id"] for n in nodes[pid]] == [n["node_id"] for n in original]
    status = client.get(f"/v1/labs/{pid}/status", headers=HEADERS).json()
    assert status["state"] == "READY"


def test_interrupted_lab_continues_in_another_slot(mock_server, tmp_path, monkeypatch):
    """The CT was powered off while READY (the manager may do this when stop fails)."""
    client, _, nodes = mock_server
    calls = record_provisioning(monkeypatch)
    data = payload()
    pid = data["project_id"]
    assert deploy(client, data, "s1-deploy")["status"] == "SUCCEEDED"
    for node in nodes[pid]:
        node["status"] = "stopped"
    switch_slot(client, tmp_path, monkeypatch, "slot-2")
    status = client.get(f"/v1/labs/{pid}/status", headers=HEADERS).json()
    assert status["state"] == "FAILED"
    assert status["error"]["code"] == "SERVICE_RESTART"
    calls.clear()
    assert deploy(client, data, "s2-deploy")["status"] == "SUCCEEDED"
    assert calls and True not in calls


def test_unfinished_provisioning_is_refused_in_another_slot(
    mock_server, tmp_path, monkeypatch
):
    client, projects, _ = mock_server
    record_provisioning(monkeypatch)
    data = payload("bad-provision")
    pid = data["project_id"]
    assert deploy(client, data, "s1-deploy")["status"] == "FAILED"
    switch_slot(client, tmp_path, monkeypatch, "slot-2")
    op = deploy(client, data, "s2-deploy")
    assert op["status"] == "FAILED"
    assert "Provisioning completion is unknown" in op["error"]["message"]
    assert pid in projects  # nothing recreated or deleted


def test_other_students_labs_are_invisible(mock_server, tmp_path, monkeypatch):
    client, projects, _ = mock_server
    first = payload()
    assert deploy(client, first, "a-deploy")["status"] == "SUCCEEDED"
    # The next session mounts another student's directory into a restarted slot.
    monkeypatch.setenv("SLOT_LAB_STATE_DIR", str(tmp_path / "student-b" / ".lab-slot"))
    switch_slot(client, tmp_path, monkeypatch, "state-after-restart")
    projects.clear()
    response = client.get(f"/v1/labs/{first['project_id']}/status", headers=HEADERS)
    assert response.status_code == 404
    assert deploy(client, payload(), "b-deploy")["status"] == "SUCCEEDED"


def test_failed_operation_insert_restores_previous_record(tmp_path, monkeypatch):
    import asyncio
    import sqlite3
    from types import SimpleNamespace

    import pytest

    monkeypatch.setenv("SLOT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SLOT_PROJECT_LOCK_DIR", str(tmp_path / "locks"))

    async def scenario():
        service = Lifecycle(SimpleNamespace())
        service.save("lab", "p", {"project_id": "p", "state": "STOPPED", "stage": "x"})
        before = service.get("lab", "p")
        service.db.execute("PRAGMA query_only=ON")
        with pytest.raises(sqlite3.OperationalError):
            service.submit("resume", "p")
        assert service.get("lab", "p") == before
        assert service.project_locks == {}
        await service.close()

    asyncio.run(scenario())
