import json
import time
from uuid import uuid4

import httpx
import pytest
from api import labs
from api.console import TelnetStripper
from api.lifecycle import Lifecycle
from api.models import DeployTicket
from fastapi.testclient import TestClient
from main import app


@pytest.fixture
def mock_server(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOT_API_TOKEN", "test-token")
    monkeypatch.setenv("SLOT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SLOT_PROJECT_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("SLOT_READY_TIMEOUT", "0.05")
    monkeypatch.setenv("SLOT_MONITOR_INTERVAL", "0.02")

    async def provision(self, lab, node, spec, initial):
        if spec["name"] == "bad-provision":
            raise RuntimeError("mandatory provisioning failed")

    monkeypatch.setattr(Lifecycle, "provision", provision)
    labs.IMAGES = tmp_path / "images"
    labs.QGA_DIR = tmp_path / "qga"
    for group, filename in (("QEMU", "debian12.qcow2"), ("IOU", "cisco.bin")):
        folder = labs.IMAGES / group
        folder.mkdir(parents=True)
        (folder / filename).write_bytes(b"image-test")
    projects = {}
    nodes = {}
    links = {}

    def respond(req: httpx.Request):
        path = req.url.path.removeprefix("/v2/").split("/")

        def reply(code, content):
            return httpx.Response(code, json=content)

        if path == ["version"]:
            return reply(200, {"version": "2.2.61", "local": False})
        if path == ["projects"] and req.method == "GET":
            return reply(200, list(projects.values()))
        if path == ["projects"] and req.method == "POST":
            obj = json.loads(req.content)
            p = {
                "project_id": obj["project_id"],
                "name": obj["name"],
                "status": "opened",
            }
            projects[obj["project_id"]] = p
            nodes[obj["project_id"]] = []
            links[obj["project_id"]] = []
            return reply(201, p)
        if len(path) >= 2 and path[0] == "projects":
            pid = path[1]
            if pid not in projects:
                return reply(404, {"message": "No project"})
            if len(path) == 2:
                return reply(200, projects[pid])
            if path[2] == "nodes":
                if len(path) == 3 and req.method == "POST":
                    node = json.loads(req.content)
                    node["status"] = "stopped"
                    node["console"] = 5001
                    if node["node_type"] == "iou":  # GNS3 hands out application ids
                        node["properties"]["application_id"] = len(nodes[pid]) + 1
                    nodes[pid].append(node)
                    return reply(201, node)
                if len(path) == 3 and req.method == "GET":
                    return reply(200, nodes[pid])
                if len(path) == 4 and req.method == "GET":
                    for n in nodes[pid]:
                        if n["node_id"] == path[3]:
                            return reply(200, n)
                    return reply(404, {"message": "No node"})
                if len(path) == 4 and path[3] in ("start", "stop"):
                    for n in nodes[pid]:
                        if n["name"] != "bad-start":
                            n["status"] = "started" if path[3] == "start" else "stopped"
                    return httpx.Response(204)
            if path[2] == "links":
                if req.method == "POST":
                    obj = json.loads(req.content)
                    links[pid].append(obj)
                    return reply(201, obj)
                return reply(200, links[pid])
        return reply(404, {"message": "Unknown endpoint"})

    with TestClient(app) as client:
        client.headers.update({"X-Lab-Epoch": "1", "X-Lab-Generation": "1"})
        claimed = client.post("/v1/fence", json={"epoch": 1, "generation": 1},
                              headers={"Authorization": "Bearer test-token"})
        assert claimed.status_code == 200, claimed.text
        fake = httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="http://127.0.0.1:3080/v2/"
        )
        # Real AsyncClient is created in lifespan; substitute HTTP mock in the test.
        app.state.gns3_http = fake
        yield client, projects, nodes


def test_deploy_resume_topology_and_auth(mock_server):
    client, _projects, nodes = mock_server
    assert client.get("/healthz").status_code == 200
    assert client.get("/v1/labs").status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    payload = {
        "name": "hello",
        "project_id": str(uuid4()),
        "nodes": [
            {"name": "SW1", "kind": "iou", "image": "cisco.bin"},
            {"name": "D1", "kind": "qemu", "image": "debian12.qcow2"},
        ],
        "links": [{"a": {"node": "SW1", "port": 0}, "b": {"node": "D1"}}],
    }
    response = client.post("/v1/labs/deploy", headers=headers, json=payload)
    assert response.status_code == 202, response.text
    assert wait(client, response)["status"] == "SUCCEEDED"
    pid = payload["project_id"]
    assert len(nodes[pid]) == 2
    assert nodes[pid][1]["properties"]["linked_clone"] is True
    assert "guest_agent.0" in nodes[pid][1]["properties"]["options"]
    assert (
        wait(client, client.post("/v1/labs/deploy", headers=headers, json=payload))[
            "status"
        ]
        == "SUCCEEDED"
    )
    topo = client.get(f"/v1/labs/{pid}/topology", headers=headers).json()
    assert len(topo["links"]) == 1
    assert (
        client.post(
            f"/v1/labs/{pid}/nodes/{nodes[pid][0]['node_id']}/start", headers=headers
        ).status_code
        == 404
    )
    assert all(n["status"] == "started" for n in nodes[pid])


def test_invalid_manifest_and_telnet_filter():
    with pytest.raises(ValueError):
        DeployTicket.model_validate(
            {
                "name": "bad",
                "nodes": [{"name": "a", "kind": "qemu", "image": "../../hack.qcow2"}],
            }
        )
    parser = TelnetStripper()
    data, reply = parser.feed(b"hello\xff\xfd\x01 world\xff\xff")
    assert data == b"hello world\xff"
    assert reply == b"\xff\xfc\x01"


HEADERS = {"Authorization": "Bearer test-token"}


def wait(client, response):
    assert response.status_code == 202, response.text
    for _ in range(300):
        op = client.get(
            "/v1/operations/" + response.json()["operation_id"], headers=HEADERS
        ).json()
        if op["status"] != "RUNNING":
            return op
        time.sleep(0.01)
    raise AssertionError("Operation stuck")


def payload(name="D1"):
    return {
        "name": "lab",
        "project_id": str(uuid4()),
        "nodes": [{"name": name, "kind": "qemu", "image": "debian12.qcow2"}],
    }


@pytest.mark.parametrize("name", ["bad-start", "bad-provision"])
def test_failures(mock_server, name):
    client, _, _ = mock_server
    data = payload(name)
    assert (
        wait(client, client.post("/v1/labs/deploy", json=data, headers=HEADERS))[
            "status"
        ]
        == "FAILED"
    )
    assert (
        client.get(f"/v1/labs/{data['project_id']}/status", headers=HEADERS).json()[
            "state"
        ]
        == "FAILED"
    )


def test_stop_resume_wipe_and_dedup(mock_server):
    client, _projects, nodes = mock_server
    data = payload()
    pid = data["project_id"]
    headers = dict(HEADERS, **{"Idempotency-Key": "deploy-1"})
    first = client.post("/v1/labs/deploy", json=data, headers=headers)
    assert wait(client, first)["status"] == "SUCCEEDED"
    assert (
        client.post("/v1/labs/deploy", json=data, headers=headers).json()[
            "operation_id"
        ]
        == first.json()["operation_id"]
    )
    assert (
        client.post("/v1/labs/deploy", json=payload(), headers=HEADERS).status_code
        == 409
    )
    original = json.loads(json.dumps(nodes[pid]))
    nid = nodes[pid][0]["node_id"]
    assert (
        client.post(
            f"/v1/labs/{pid}/nodes/{nid}/wipe", json={"confirm": False}, headers=HEADERS
        ).status_code
        == 422
    )
    op = wait(
        client,
        client.post(
            f"/v1/labs/{pid}/nodes/{nid}/wipe", json={"confirm": True}, headers=HEADERS
        ),
    )
    assert op["status"] == "NOT_SUPPORTED"
    assert nodes[pid] == original
    assert (
        client.get(f"/v1/labs/{pid}/status", headers=HEADERS).json()["state"] == "READY"
    )
    assert (
        wait(client, client.post(f"/v1/labs/{pid}/stop", headers=HEADERS))["status"]
        == "SUCCEEDED"
    )
    assert nodes[pid][0]["status"] == "stopped"
    assert (
        wait(client, client.post(f"/v1/labs/{pid}/resume", headers=HEADERS))["status"]
        == "SUCCEEDED"
    )
    assert nodes[pid] == original


def test_competing_requests(mock_server, monkeypatch):
    import asyncio

    async def slow(self, *args):
        await asyncio.sleep(0.1)

    monkeypatch.setattr(Lifecycle, "provision", slow)
    client, _, _ = mock_server
    data = payload()
    first = client.post("/v1/labs/deploy", json=data, headers=HEADERS)
    assert (
        client.post("/v1/labs/deploy", json=data, headers=HEADERS).json()[
            "operation_id"
        ]
        == first.json()["operation_id"]
    )
    assert (
        client.post(f"/v1/labs/{data['project_id']}/stop", headers=HEADERS).status_code
        == 409
    )
    assert wait(client, first)["status"] == "SUCCEEDED"


def test_restart_marks_interrupted_and_ready_failed(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    monkeypatch.setenv("SLOT_STATE_DIR", str(tmp_path))

    async def scenario():
        service = Lifecycle(SimpleNamespace())
        service.save("lab", "p", {"project_id": "p", "state": "STARTING"})
        service.save(
            "operation",
            "o",
            {"operation_id": "o", "project_id": "p", "status": "RUNNING"},
        )
        service.save("lab", "ready", {"project_id": "ready", "state": "READY"})
        await service.close()
        service = Lifecycle(SimpleNamespace())
        assert service.operation("o")["status"] == "FAILED"
        assert service.status("p")["state"] == "FAILED"
        assert service.status("ready")["state"] == "FAILED"
        await service.close()

    asyncio.run(scenario())


def test_resume_missing_project_never_recreates(mock_server):
    client, projects, nodes = mock_server
    data = payload()
    pid = data["project_id"]
    assert (
        wait(client, client.post("/v1/labs/deploy", json=data, headers=HEADERS))[
            "status"
        ]
        == "SUCCEEDED"
    )
    assert (
        wait(client, client.post(f"/v1/labs/{pid}/stop", headers=HEADERS))["status"]
        == "SUCCEEDED"
    )
    projects.pop(pid)
    original = nodes[pid].copy()
    op = wait(client, client.post(f"/v1/labs/{pid}/resume", headers=HEADERS))
    assert op["status"] == "FAILED"
    assert op["error"]["code"] == "PROJECT_MISSING"
    # The manager continues a project with deploy of the first ticket: same refusal.
    op = wait(client, client.post("/v1/labs/deploy", json=data, headers=HEADERS))
    assert op["status"] == "FAILED"
    assert op["error"]["code"] == "PROJECT_MISSING"
    assert op["error"]["message"].startswith("Saved project is missing")
    assert pid not in projects
    assert nodes[pid] == original


def test_changed_manifest_and_disabled_autostart_rejected(mock_server):
    client, _, _ = mock_server
    data = payload()
    disabled = dict(data, auto_start=False)
    assert (
        client.post("/v1/labs/deploy", json=disabled, headers=HEADERS).status_code
        == 422
    )
    assert (
        wait(client, client.post("/v1/labs/deploy", json=data, headers=HEADERS))[
            "status"
        ]
        == "SUCCEEDED"
    )
    data["nodes"][0]["ram_mb"] = 4096
    assert client.post("/v1/labs/deploy", json=data, headers=HEADERS).status_code == 409


def test_real_provision_checks_exit_codes_and_resume_does_not_replay(
    tmp_path, monkeypatch
):
    import asyncio
    from types import SimpleNamespace

    calls = []

    async def scenario():
        async def handler(reader, writer):
            await reader.read()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setenv("SLOT_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("SLOT_READY_TIMEOUT", "0")

        class Qga:
            async def call(self, node_id, command, args=None, **options):
                return {}

            async def exec(self, node_id, command, **options):
                calls.append(command)
                return {"exit_code": 1 if command == "fail" else 0}

            async def close(self):
                pass

        service = Lifecycle(SimpleNamespace())
        service.qga = Qga()
        node = {"node_id": str(uuid4()), "console": port, "console_type": "telnet"}
        spec = {
            "kind": "qemu",
            "provisioning": [{"command": "fail", "timeout_seconds": 1}],
        }
        with pytest.raises(RuntimeError, match="provisioning"):
            await service.provision({}, node, spec, True)
        assert "enable --now" in calls[0]
        calls.clear()
        await service.provision({}, node, spec, False)
        assert len(calls) == 1 and "is-active" in calls[0]
        await service.close()
        server.close()
        await server.wait_closed()

    asyncio.run(scenario())


def test_monitor_detects_runtime_node_failure(mock_server, monkeypatch):
    monkeypatch.setenv("SLOT_MONITOR_INTERVAL", "0.02")
    client, _, nodes = mock_server
    data = payload()
    pid = data["project_id"]
    assert (
        wait(client, client.post("/v1/labs/deploy", json=data, headers=HEADERS))[
            "status"
        ]
        == "SUCCEEDED"
    )
    nodes[pid][0]["status"] = "stopped"
    for _ in range(200):
        status = client.get(f"/v1/labs/{pid}/status", headers=HEADERS).json()
        if status["state"] == "FAILED":
            assert status["stage"] == "monitor"
            break
        time.sleep(0.01)
    else:
        raise AssertionError("Monitor did not detect node failure")


def test_shared_project_lock_and_single_worker_lock(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    monkeypatch.setenv("SLOT_PROJECT_LOCK_DIR", str(tmp_path / "locks"))

    async def scenario():
        monkeypatch.setenv("SLOT_STATE_DIR", str(tmp_path / "a"))
        a = Lifecycle(SimpleNamespace())
        with pytest.raises(BlockingIOError):
            Lifecycle(SimpleNamespace())
        # Block scheduling at a real await, then attempt same UUID from another state-dir.
        event = asyncio.Event()

        async def blocked(*args):
            await event.wait()

        a.api = blocked
        data = DeployTicket.model_validate(payload())
        a.submit("deploy", ticket=data)
        monkeypatch.setenv("SLOT_STATE_DIR", str(tmp_path / "b"))
        b = Lifecycle(SimpleNamespace())
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as error:
            b.submit("deploy", ticket=data)
        assert error.value.status_code == 409
        await a.close()
        await b.close()

    asyncio.run(scenario())


def test_iol_cli_prompt_and_command_error():
    import asyncio

    from api.cli import run_commands

    async def scenario():
        commands = []

        async def handler(reader, writer):
            while line := await reader.readline():
                commands.append(line.strip())
                writer.write(b"% Invalid input\r\nSW1#" if b"bad" in line else b"SW1#")
                await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        await run_commands(
            port, [{"command": "show clock", "timeout_seconds": 1}], r"SW1#$"
        )
        with pytest.raises(RuntimeError, match="Cisco rejected"):
            await run_commands(
                port, [{"command": "bad", "timeout_seconds": 1}], r"SW1#$"
            )
        assert b"show clock" in commands and b"bad" in commands
        server.close()
        await server.wait_closed()

    asyncio.run(scenario())
