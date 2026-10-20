"""Regressions from the nine-device integration run; no external infrastructure."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
import release
from api.lifecycle import Lifecycle
from fastapi import HTTPException
from fastapi.testclient import TestClient
from main import app
from test_slot import HEADERS, payload, wait


def test_readiness_and_compact_operations(mock_server, tmp_path):
    client, _, _nodes = mock_server
    (tmp_path / "locks").mkdir(exist_ok=True)
    ready = client.get("/v1/readyz").json()
    assert ready["status"] == "ready"
    assert ready["version"] == release.VERSION
    assert ready["build_id"]
    assert ready["gns3_version"] == "2.2.61"
    assert all(c["ok"] for c in ready["checks"].values())
    data = payload()
    response = client.post("/v1/labs/deploy", json=data, headers=HEADERS)
    assert "nodes" not in response.json() and "fingerprint" not in response.json()
    assert response.json()["replayed"] is False
    op = wait(client, response)
    assert op["status"] == "SUCCEEDED"
    assert set(op["nodes"][0]) == {"node_id", "name", "node_type", "status"}
    replay = client.post("/v1/labs/deploy", json=data, headers=HEADERS)
    assert (
        replay.json()["replayed"] is False
    )  # no explicit key, new intentional invocation
    wait(client, replay)


def test_missing_lifecycle_and_uninitialized_service(mock_server, monkeypatch):
    client, _, _ = mock_server
    lifecycle = app.state.lifecycle
    monkeypatch.setattr(app.state, "lifecycle", None)
    try:
        assert client.get("/healthz").status_code == 200
        ready = client.get("/v1/readyz")
        assert ready.status_code == 503
        assert ready.json()["checks"]["lifecycle"]["ok"] is False
        response = client.post("/v1/labs/deploy", json=payload(), headers=HEADERS)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "SERVICE_NOT_INITIALIZED"
    finally:
        app.state.lifecycle = lifecycle


def test_stale_resume_and_replay_current_state(mock_server):
    client, _, nodes = mock_server
    data = payload()
    pid = data["project_id"]
    wait(client, client.post("/v1/labs/deploy", json=data, headers=HEADERS))
    headers = {**HEADERS, "Idempotency-Key": "resume-1"}
    response = client.post(f"/v1/labs/{pid}/resume", headers=headers)
    wait(client, response)
    replay = client.post(f"/v1/labs/{pid}/resume", headers=headers)
    assert replay.status_code == 202
    assert replay.json()["replayed"] is True
    assert replay.json()["laboratory"]["state"] == "READY"
    assert replay.json()["operation_id"] == response.json()["operation_id"]
    wait(client, client.post(f"/v1/labs/{pid}/stop", headers=HEADERS))
    stale = client.post(f"/v1/labs/{pid}/resume", headers=headers)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "STALE_IDEMPOTENCY_KEY"
    assert stale.json()["detail"]["laboratory"]["state"] == "STOPPED"
    assert all(n["status"] == "stopped" for n in nodes[pid])
    assert client.post(f"/v1/labs/{pid}/stop", headers=headers).status_code == 409


def test_restart_preserves_idempotency_and_rejects_stale(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOT_STATE_DIR", str(tmp_path))

    async def scenario():
        service = Lifecycle(SimpleNamespace())
        service.save(
            "lab", "p", {"project_id": "p", "state": "STOPPED", "stage": "stopped"}
        )
        service.save(
            "operation",
            "o",
            {
                "operation_id": "o",
                "project_id": "p",
                "status": "SUCCEEDED",
                "key": "key",
                "action": "resume",
                "fingerprint": __import__("hashlib")
                .sha256(
                    json.dumps(["resume", "p", None, None], sort_keys=True).encode()
                )
                .hexdigest(),
            },
        )
        await service.close()
        service = Lifecycle(SimpleNamespace())
        with pytest.raises(HTTPException) as error:
            service.submit("resume", "p", key="key")
        assert error.value.detail["code"] == "STALE_IDEMPOTENCY_KEY"
        assert service.operation("o")["laboratory"]["state"] == "STOPPED"
        await service.close()

    asyncio.run(scenario())


def test_gns3_failure_is_structured(mock_server):
    client, _, _ = mock_server
    original = app.state.gns3_http

    def offline(request):
        raise httpx.ConnectError("offline")

    app.state.gns3_http = httpx.AsyncClient(
        transport=httpx.MockTransport(offline), base_url="http://127.0.0.1:3080/v2/"
    )
    try:
        ready = client.get("/v1/readyz")
        assert ready.status_code == 503
        assert ready.json()["checks"]["gns3"]["code"] == "GNS3_UNAVAILABLE"
        op = wait(
            client, client.post("/v1/labs/deploy", json=payload(), headers=HEADERS)
        )
        assert op["status"] == "FAILED" and op["error"]["code"] == "HTTPException"
    finally:
        app.state.gns3_http = original


def test_release_missing_corrupt_and_startup_rejection(tmp_path, monkeypatch):
    assert release.verify(tmp_path)["ok"] is False
    (tmp_path / "release.json").write_text(
        '{"version":"0.3.0","files":{},"build_id":"test"}'
    )
    assert release.verify(tmp_path)["ok"] is False
    import shutil

    shutil.copytree(
        release.ROOT,
        tmp_path / "copy",
        ignore=shutil.ignore_patterns("venv", ".venv", "__pycache__"),
    )
    assert release.verify(tmp_path / "copy")["ok"] is True
    (tmp_path / "copy" / "main.py").write_text("# mixed release")
    assert release.verify(tmp_path / "copy")["ok"] is False
    (tmp_path / "main.py").write_text("# mixed release")
    assert release.verify(tmp_path)["ok"] is False
    monkeypatch.setattr("main.verify", lambda: {"ok": False})
    with pytest.raises(RuntimeError, match="RELEASE_INTEGRITY_ERROR"), TestClient(app):
        pass


def test_release_changed_blocks_mutations_and_readiness(mock_server, monkeypatch):
    client, _, _ = mock_server
    monkeypatch.setattr("main.verify", lambda: {"ok": False})
    monkeypatch.setattr("api.health.verify", lambda: {"ok": False})
    assert (
        client.post("/v1/labs/deploy", headers=HEADERS, json=payload()).status_code
        == 503
    )
    assert client.get("/v1/readyz").status_code == 503


def test_restart_between_stopped_snapshot_and_operation_commit(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOT_STATE_DIR", str(tmp_path))

    async def scenario():
        service = Lifecycle(SimpleNamespace())
        service.save(
            "lab", "p", {"project_id": "p", "state": "STOPPED", "stage": "stopped"}
        )
        service.save(
            "operation",
            "o",
            {"operation_id": "o", "project_id": "p", "status": "RUNNING"},
        )
        await service.close()
        service = Lifecycle(SimpleNamespace())
        assert service.status("p")["state"] == "FAILED"
        assert service.operation("o")["error"]["code"] == "SERVICE_RESTART"
        await service.close()

    asyncio.run(scenario())


def test_sqlite_failure_releases_new_project_lock(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setenv("SLOT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SLOT_PROJECT_LOCK_DIR", str(tmp_path / "locks"))

    async def scenario():
        service = Lifecycle(SimpleNamespace())
        service.db.execute("PRAGMA query_only=ON")
        with pytest.raises(sqlite3.OperationalError):
            service.submit(
                "deploy",
                ticket=__import__(
                    "api.models", fromlist=["DeployTicket"]
                ).DeployTicket.model_validate(payload()),
            )
        assert service.project_locks == {}
        assert service.all("lab") == [] and service.all("operation") == []
        await service.close()

    asyncio.run(scenario())


def test_explicit_running_replay_and_conflicting_transition(mock_server, monkeypatch):
    async def slow(*args):
        await asyncio.sleep(0.1)

    monkeypatch.setattr(Lifecycle, "provision", slow)
    client, _, _ = mock_server
    data = payload()
    headers = {**HEADERS, "Idempotency-Key": "running-key"}
    first = client.post("/v1/labs/deploy", json=data, headers=headers)
    replay = client.post("/v1/labs/deploy", json=data, headers=headers)
    assert replay.json()["operation_id"] == first.json()["operation_id"]
    assert replay.json()["replayed"] is True
    assert replay.json()["status"] == "RUNNING"
    conflict = client.post(f"/v1/labs/{data['project_id']}/stop", headers=HEADERS)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "OPERATION_IN_PROGRESS"
    changed = {**data, "name": "different"}
    conflict = client.post("/v1/labs/deploy", json=changed, headers=headers)
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert wait(client, first)["status"] == "SUCCEEDED"


def test_readiness_unsupported_gns3_and_read_only_sqlite(mock_server, tmp_path):
    client, _, _ = mock_server
    (tmp_path / "locks").mkdir(exist_ok=True)
    original = app.state.gns3_http
    app.state.gns3_http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"version": "2.2.60"})
        ),
        base_url="http://127.0.0.1:3080/v2/",
    )
    try:
        response = client.get("/v1/readyz")
        assert response.status_code == 503
        assert response.json()["checks"]["gns3"]["code"] == "GNS3_VERSION_UNSUPPORTED"
    finally:
        app.state.gns3_http = original

    async def readonly():
        app.state.lifecycle.db.execute("PRAGMA query_only=ON")

    client.portal.call(readonly)
    response = client.get("/v1/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["sqlite"]["code"] == "SQLITE_UNAVAILABLE"


def test_diagnostic_cli_validates_before_http_and_is_readonly(tmp_path, monkeypatch):
    from tools.check_slot import main

    monkeypatch.setenv("SLOT_API_TOKEN", "diagnostic-test-token")
    monkeypatch.setattr(
        "sys.argv", ["check_slot.py", "--input", str(tmp_path / "missing.json")]
    )
    assert main() == 1
    requests = []

    def respond(request):
        requests.append(request.method)
        return httpx.Response(200, json={"status": "ready", "version": release.VERSION})

    original = httpx.Client
    monkeypatch.setattr(
        "tools.check_slot.httpx.Client",
        lambda **kw: original(transport=httpx.MockTransport(respond), **kw),
    )
    monkeypatch.setattr("sys.argv", ["check_slot.py"])
    assert main() == 0
    assert requests == ["GET", "GET"]


def test_new_routes_on_old_main_fail_with_diagnostic():
    from api.labs import router
    from fastapi import FastAPI

    old_app = FastAPI()
    old_app.include_router(router)
    with TestClient(old_app) as client:
        response = client.post("/v1/labs/deploy", json=payload())
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "SERVICE_NOT_INITIALIZED"


def test_failed_lab_of_earlier_run_does_not_own_slot(mock_server):
    """The manager reuses a slot for another student after the operator restarted its CT."""
    client, projects, _ = mock_server
    first = payload()
    response = client.post("/v1/labs/deploy", json=first, headers=HEADERS)
    assert wait(client, response)["status"] == "SUCCEEDED"
    lifecycle = app.state.lifecycle
    pid = first["project_id"]

    def fail():  # SQLite belongs to the event loop thread of the TestClient
        lab = lifecycle.get("lab", pid)
        lifecycle.update(lab, "FAILED", "monitor", error={"code": "X", "message": "x"})

    client.portal.call(fail)
    # While this process holds the project lock, a FAILED lab keeps the slot.
    busy = client.post("/v1/labs/deploy", json=payload(), headers=HEADERS)
    assert busy.status_code == 409
    assert busy.json()["detail"]["code"] == "SLOT_OCCUPIED"
    assert busy.json()["detail"]["project_id"] == pid
    # Restart: no lock is held, and another student's directory is mounted.
    client.portal.call(lambda: lifecycle.project_locks.pop(pid).close())
    projects.clear()
    second = client.post("/v1/labs/deploy", json=payload(), headers=HEADERS)
    assert wait(client, second)["status"] == "SUCCEEDED"
    status = client.get(f"/v1/labs/{pid}/status", headers=HEADERS)
    assert status.json()["state"] == "FAILED"


def test_failed_lab_of_earlier_run_still_blocks_running_project(mock_server):
    """Without the lock, the running-project check of run() still protects the slot."""
    client, _, _ = mock_server
    first = payload()
    response = client.post("/v1/labs/deploy", json=first, headers=HEADERS)
    assert wait(client, response)["status"] == "SUCCEEDED"
    lifecycle = app.state.lifecycle
    pid = first["project_id"]

    def restart():  # what Lifecycle() does to a READY lab after a service restart
        lifecycle.update(lifecycle.get("lab", pid), "FAILED", "restart_requires_resume")
        lifecycle.project_locks.pop(pid).close()

    client.portal.call(restart)
    other = dict(payload(), name="other")
    op = wait(client, client.post("/v1/labs/deploy", json=other, headers=HEADERS))
    assert op["status"] == "FAILED"
    assert "Another GNS3 project is running" in op["error"]["message"]


def test_capabilities(mock_server):
    client, _, _ = mock_server
    assert client.get("/v1/capabilities").status_code == 401
    body = client.get("/v1/capabilities", headers=HEADERS).json()
    assert body["schema"] == "gns3lab.capabilities/1"
    assert body["slot_version"] == release.VERSION
    assert body["deploy_schemas"] == ["gns3lab.deploy/1"]
    assert set(body["features"]) == {"qemu", "iou", "vpcs", "qga", "iol-cli", "student-state", "command-fencing-v1"}
