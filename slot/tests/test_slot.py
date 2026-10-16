import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from api import labs
from api.console import TelnetStripper
from api.models import DeployTicket
from main import app


@pytest.fixture
def mock_server(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOT_API_TOKEN", "test-token")
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
            p = {"project_id": obj["project_id"], "name": obj["name"], "status": "opened"}
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
                    nodes[pid].append(node)
                    return reply(201, node)
                if len(path) == 3 and req.method == "GET":
                    return reply(200, nodes[pid])
                if len(path) == 4 and path[3] in ("start", "stop"):
                    for n in nodes[pid]:
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
        fake = httpx.AsyncClient(transport=httpx.MockTransport(respond),
                                 base_url="http://127.0.0.1:3080/v2/")
        # Real AsyncClient is created in lifespan; substitute HTTP mock in the test.
        app.state.gns3_http = fake
        yield client, projects, nodes


def test_deploy_resume_topology_and_auth(mock_server):
    client, projects, nodes = mock_server
    assert client.get("/healthz").status_code == 200
    assert client.get("/v1/labs").status_code == 401
    headers = {"Authorization": "Bearer test-token"}
    payload = {
        "name": "hello", "project_id": str(uuid4()),
        "nodes": [
            {"name": "SW1", "kind": "iou", "image": "cisco.bin"},
            {"name": "D1", "kind": "qemu", "image": "debian12.qcow2"},
        ],
        "links": [{"a": {"node": "SW1", "port": 0}, "b": {"node": "D1"}}],
    }
    response = client.post("/v1/labs/deploy", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["result"] == "created"
    pid = payload["project_id"]
    assert len(nodes[pid]) == 2
    assert nodes[pid][1]["properties"]["linked_clone"] is True
    assert "guest_agent.0" in nodes[pid][1]["properties"]["options"]
    assert client.post("/v1/labs/deploy", headers=headers, json=payload).json()["result"] == "resumed"
    topo = client.get(f"/v1/labs/{pid}/topology", headers=headers).json()
    assert len(topo["links"]) == 1
    assert client.post(f"/v1/labs/{pid}/start", headers=headers).status_code == 200
    assert all(n["status"] == "started" for n in nodes[pid])


def test_invalid_manifest_and_telnet_filter():
    with pytest.raises(ValueError):
        DeployTicket.model_validate({"name": "bad", "nodes": [
            {"name": "a", "kind": "qemu", "image": "../../hack.qcow2"}]})
    parser = TelnetStripper()
    data, reply = parser.feed(b"hello\xff\xfd\x01 world\xff\xff")
    assert data == b"hello world\xff"
    assert reply == b"\xff\xfc\x01"
