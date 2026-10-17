"""Contract of plugin/docs/SPEC.md §3 (plugin -> manager)."""

import json

import httpx
from conftest import SITE_URL, make_env


def anon(env, **headers):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://manager",
                             headers=headers)


async def test_auth_required_with_error_shape(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        cases = [
            {},
            {"Authorization": "Bearer " + "s" * 40},  # No X-Moodle-Site
            {"Authorization": "Bearer wrong", "X-Moodle-Site": SITE_URL},
            {"Authorization": "Bearer " + "s" * 40, "X-Moodle-Site": "https://evil.example"},
            {"Authorization": "Bearer " + "o" * 40, "X-Moodle-Site": SITE_URL},  # Other site's secret
            {"Authorization": "Basic " + "s" * 40, "X-Moodle-Site": SITE_URL},
        ]
        for headers in cases:
            async with anon(env, **headers) as c:
                r = await c.get("/api/v1/labs")
                assert r.status_code == 401, headers
                assert set(r.json()) == {"error", "message"}
        async with anon(env, **{"Authorization": "Bearer " + "s" * 40, "X-Moodle-Site": SITE_URL + "/"}) as c:
            assert (await c.get("/api/v1/labs")).status_code == 200  # wwwroot with trailing slash


async def test_labs_and_topology(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        r = await env.client.get("/api/v1/labs")
        assert r.status_code == 200
        assert r.json() == [{"lab_ref": "cisco-3x2", "title": "Три Cisco в кольце и шесть Debian",
                             "description": "Пример лабы из slot/examples/cisco-3x2.json"}]
        r = await env.client.get("/api/v1/labs/cisco-3x2/topology")
        expected = json.loads((env.settings.labs_dir / "cisco-3x2" / "lab.gns3").read_text())
        assert r.json() == expected
        r = await env.client.get("/api/v1/labs/nope/topology")
        assert r.status_code == 404 and r.json()["error"] == "lab_not_found"
        r = await env.client.get("/api/v1/labs/..%2Fetc/topology")
        assert r.status_code == 404


async def test_session_lifecycle_contract(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        r = await env.client.get("/api/v1/sessions", params={"user_id": 42, "instance_id": 1})
        assert r.status_code == 404 and set(r.json()) == {"error", "message"}

        body = {"user_id": 42, "instance_id": 1, "lab_ref": "cisco-3x2", "course_id": 3}
        r = await env.client.post("/api/v1/sessions", json=body)
        assert r.status_code == 201
        s = r.json()
        assert s["session_id"].startswith("s-")
        assert s["status"] == "queued"
        assert s["status_message"] == "Ожидание свободного слота…"
        assert s["started_at"] is None and s["expires_at"] is None
        assert "topology" not in s and "nodes" not in s

        r = await env.client.post("/api/v1/sessions", json=body)
        assert r.status_code == 200 and r.json()["session_id"] == s["session_id"]  # Idempotent

        await env.run()
        r = await env.client.get(f"/api/v1/sessions/{s['session_id']}")
        running = r.json()
        assert running["status"] == "running"
        assert running["started_at"].endswith("Z") and running["expires_at"].endswith("Z")
        assert running["topology"]["name"] == f"cisco-3x2-{s['session_id']}"
        assert set(running["topology"]["topology"]) == {"nodes", "links", "drawings"}
        node_ids = {n["node_id"] for n in running["topology"]["topology"]["nodes"]}
        assert set(running["nodes"]) == node_ids
        assert all(v == {"status": "started", "console_type": "telnet"} for v in running["nodes"].values())

        r = await env.client.get("/api/v1/sessions", params={"user_id": 42, "instance_id": 1})
        assert r.json()["session_id"] == s["session_id"]

        r = await env.client.post(f"/api/v1/sessions/{s['session_id']}/stop")
        assert r.status_code == 200 and r.json()["status"] == "stopping"
        await env.run()
        r = await env.client.get(f"/api/v1/sessions/{s['session_id']}")
        assert r.json()["status"] == "stopped" and "topology" not in r.json()
        r = await env.client.post(f"/api/v1/sessions/{s['session_id']}/stop")
        assert r.json()["status"] == "stopped"

        # After a stop, POST creates a new session (201) for the same lab.
        r = await env.client.post("/api/v1/sessions", json=body)
        assert r.status_code == 201 and r.json()["session_id"] != s["session_id"]


async def test_sessions_are_isolated_by_site(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        r = await env.client.post("/api/v1/sessions", json={"user_id": 42, "instance_id": 1, "lab_ref": "cisco-3x2"})
        sid = r.json()["session_id"]
        async with anon(env, **{"Authorization": "Bearer " + "o" * 40,
                                "X-Moodle-Site": "https://other.example.edu"}) as other:
            assert (await other.get(f"/api/v1/sessions/{sid}")).status_code == 404
            assert (await other.post(f"/api/v1/sessions/{sid}/stop")).status_code == 404
            r = await other.get("/api/v1/sessions", params={"user_id": 42, "instance_id": 1})
            assert r.status_code == 404
            r = await other.post("/api/v1/sessions", json={"user_id": 42, "instance_id": 1, "lab_ref": "cisco-3x2"})
            assert r.status_code == 201 and r.json()["session_id"] != sid


async def test_validation_errors(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        for body in ({}, {"user_id": "x", "instance_id": 1, "lab_ref": "a"},
                     {"user_id": 0, "instance_id": 1, "lab_ref": "a"}):
            r = await env.client.post("/api/v1/sessions", json=body)
            assert r.status_code == 400 and r.json()["error"] == "invalid_request"
        r = await env.client.post("/api/v1/sessions", json={"user_id": 1, "instance_id": 1, "lab_ref": "missing"})
        assert r.status_code == 404 and r.json()["error"] == "lab_not_found"
        r = await env.client.get("/api/v1/sessions/s-unknown")
        assert r.status_code == 404 and r.json()["error"] == "session_not_found"


async def test_console_tickets(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        r = await env.client.post("/api/v1/sessions", json={"user_id": 42, "instance_id": 1, "lab_ref": "cisco-3x2"})
        sid = r.json()["session_id"]
        r = await env.client.post(f"/api/v1/sessions/{sid}/console-tickets",
                                  json={"node_id": "x", "readonly": False, "issued_for_user_id": 42})
        assert r.status_code == 409 and r.json()["error"] == "session_not_running"
        await env.run()
        s = (await env.client.get(f"/api/v1/sessions/{sid}")).json()
        node_id = next(iter(s["nodes"]))

        r = await env.client.post(f"/api/v1/sessions/{sid}/console-tickets",
                                  json={"node_id": "6b1f3c2e-4a7d-4e5b-9c10-000000000001", "readonly": False,
                                        "issued_for_user_id": 42})
        assert r.status_code == 404 and r.json()["error"] == "node_not_found"  # Reference node_id, not live
        assert env.gateway.requests == []

        r = await env.client.post(f"/api/v1/sessions/{sid}/console-tickets",
                                  json={"node_id": node_id, "readonly": True, "issued_for_user_id": 9})
        assert r.status_code == 200
        assert r.json() == {"ticket": "t-1", "gateway_url": "wss://gateway.example.edu", "expires_in": 30}
        pid = (await env.services.store.session(sid))["project_id"]
        assert env.gateway.requests == [{"slot": "10.0.0.1", "project_id": str(pid),
                                         "nodes": [node_id], "readonly": True, "who": f"{sid}:observer"}]


async def test_health_endpoints(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        async with anon(env) as c:
            assert (await c.get("/healthz")).json()["status"] == "ok"
            r = await c.get("/readyz")
            # Orchestrator is not running in this test app: not ready, and says why.
            assert r.status_code == 503
            assert r.json()["checks"]["database"]["ok"] is True
            assert r.json()["checks"]["orchestrator"]["ok"] is False


async def test_one_live_lab_per_student(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        first = {"user_id": 42, "instance_id": 1, "lab_ref": "cisco-3x2"}
        r = await env.client.post("/api/v1/sessions", json=first)
        assert r.status_code == 201
        sid = r.json()["session_id"]
        # Another activity of the same student while the first lab is live: refused.
        r = await env.client.post("/api/v1/sessions", json={**first, "instance_id": 2})
        assert r.status_code == 409 and r.json()["error"] == "session_conflict"
        r = await env.client.get("/api/v1/sessions", params={"user_id": 42, "instance_id": 2})
        assert r.status_code == 404  # Lookups stay per activity
        # Another student is not affected.
        r = await env.client.post("/api/v1/sessions", json={**first, "user_id": 7, "instance_id": 2})
        assert r.status_code == 201
        # After the first lab stops, the other activity can start.
        await env.client.post(f"/api/v1/sessions/{sid}/stop")
        await env.run()
        r = await env.client.post("/api/v1/sessions", json={**first, "instance_id": 2})
        assert r.status_code == 201


async def test_concurrent_starts_in_two_activities_give_one_lab(tmp_path, dsn):
    import asyncio

    async with make_env(tmp_path, dsn) as env:
        bodies = [{"user_id": 42, "instance_id": i % 2 + 1, "lab_ref": "cisco-3x2"} for i in range(6)]
        results = await asyncio.gather(*(env.client.post("/api/v1/sessions", json=b) for b in bodies))
        codes = sorted(r.status_code for r in results)
        assert codes.count(201) == 1 and set(codes) <= {200, 201, 409}
        ids = {r.json()["session_id"] for r in results if r.status_code in (200, 201)}
        assert len(ids) == 1
