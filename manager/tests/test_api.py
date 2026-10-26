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
        assert running["topology"]["name"] == "cisco-3x2-w1"  # The workspace's project, kept between sessions
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


# ---------------------------------------------------------------- plugin requests (PLUGIN_REQUESTS.md)

async def start(env, **extra):
    body = {"user_id": 42, "instance_id": 1, "lab_ref": "cisco-3x2", **extra}
    r = await env.client.post("/api/v1/sessions", json=body)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def test_info(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        r = await env.client.get("/api/v1/info")
        assert r.json() == {"schema": "gns3lab.info/1", "version": r.json()["version"],
                            "session_schemas": ["gns3lab.session/1"], "options": ["max_duration"],
                            "features": ["consoles", "observer", "resume", "facts"]}
        async with anon(env) as c:
            assert (await c.get("/api/v1/info")).status_code == 401


async def test_unknown_fields_are_refused(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        base = {"user_id": 42, "instance_id": 1, "lab_ref": "cisco-3x2"}
        for body in ({**base, "maxduration": 5}, {**base, "options": {"mode": "exam"}},
                     {**base, "client": {"browser": "x"}}):
            r = await env.client.post("/api/v1/sessions", json=body)
            assert r.status_code == 400 and r.json()["error"] == "unsupported_field", body
        s = await start(env, client={"plugin_version": "2026101700", "moodle_release": "5.1"})
        r = await env.client.post(f"/api/v1/sessions/{s['session_id']}/stop", json={"by": 1})
        assert r.status_code == 400 and r.json()["error"] == "unsupported_field"


async def test_max_duration_option(tmp_path, dsn):
    async with make_env(tmp_path, dsn, max_duration_limit=120) as env:
        for requested, expected in ((90, 90), (0, 180), (600, 120)):
            s = await start(env, options={"max_duration": requested})
            assert (await env.services.store.session(s["session_id"]))["max_duration"] == expected
            await env.client.post(f"/api/v1/sessions/{s['session_id']}/stop")
            await env.run()


async def test_session_fields_stage_progress_features(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        s = await start(env)
        assert s["schema"] == "gns3lab.session/1" and s["lab_ref"] == "cisco-3x2"
        assert s["stage"] == "wait_slot" and s["error"] is None and s["features"] == []
        assert "progress" not in s
        env.slots[201].op_polls = 10**6
        await env.run(6)
        s = (await env.client.get(f"/api/v1/sessions/{s['session_id']}")).json()
        assert s["stage"] == "lab_wait" and s["progress"] == 50
        for op in env.slots[201].ops.values():
            op["remaining"] = 0
        await env.run()
        s = (await env.client.get(f"/api/v1/sessions/{s['session_id']}")).json()
        assert s["status"] == "running" and s["stage"] == "ready" and "progress" not in s
        assert s["features"] == ["consoles"] and "nodes_stale" not in s
        ports = s["topology"]["topology"]["nodes"][0]["ports"]
        assert ports[0]["short_name"] == "e0"  # Port names kept for the console port links (П7)


async def test_failed_session_error_codes(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.slots[201].fail_next = {"code": "RuntimeError", "message": "Missing QEMU image"}
        s = await start(env)
        await env.run()
        s = (await env.client.get(f"/api/v1/sessions/{s['session_id']}")).json()
        assert s["status"] == "failed"
        assert s["error"] == {"code": "SLOT_OPERATION_FAILED", "retryable": True}
        # The lab disappears from the catalog: not retryable.
        import shutil

        shutil.rmtree(env.settings.labs_dir / "cisco-3x2")
        async with env.services.pool.connection() as conn, conn.transaction():
            await conn.execute("INSERT INTO sessions (id, site, user_id, instance_id, lab_ref, workspace_id,"
                               " student_key, project_id, project_name, status, stage, max_duration)"
                               " SELECT 's-0000000000000001', site, 7, 1, lab_ref, workspace_id, 'main-7',"
                               " gen_random_uuid(), 'x', 'queued', 'wait_slot', 60 FROM sessions LIMIT 1")
        await env.run(2)
        async with anon(env, **{"Authorization": "Bearer " + "s" * 40, "X-Moodle-Site": SITE_URL}) as c:
            r = await c.get("/api/v1/sessions/s-0000000000000001")
        assert r.json()["error"] == {"code": "LAB_UNAVAILABLE", "retryable": False}


async def test_activity_session_list(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201, 202, 203)) as env:
        a = await start(env)
        await env.run()
        await env.client.post(f"/api/v1/sessions/{a['session_id']}/stop")
        await env.run()
        b = await start(env)  # Student 42 again: the live one wins over the stopped one
        c = await start(env, user_id=7)
        await start(env, user_id=8, instance_id=2)  # Another activity: not listed
        r = await env.client.get("/api/v1/sessions", params={"instance_id": 1})
        rows = sorted(r.json(), key=lambda x: x["user_id"])
        assert [(x["user_id"], x["session_id"]) for x in rows] == [(7, c["session_id"]), (42, b["session_id"])]
        assert all("topology" not in x and "nodes" not in x for x in rows)
        assert (await env.client.get("/api/v1/sessions", params={"instance_id": 99})).json() == []
        async with anon(env, **{"Authorization": "Bearer " + "o" * 40,
                                "X-Moodle-Site": "https://other.example.edu"}) as other:
            assert (await other.get("/api/v1/sessions", params={"instance_id": 1})).json() == []


async def test_conflict_details(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        await start(env, course_id=3)
        r = await env.client.post("/api/v1/sessions",
                                  json={"user_id": 42, "instance_id": 2, "lab_ref": "cisco-3x2"})
        assert r.status_code == 409
        assert r.json()["conflict"] == {"instance_id": 1, "course_id": 3, "lab_ref": "cisco-3x2",
                                        "lab_def": None, "status": "queued"}


async def test_ticket_to_stopped_node_and_stale_nodes(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        s = await start(env)
        await env.run()
        s = (await env.client.get(f"/api/v1/sessions/{s['session_id']}")).json()
        by_name = {n["name"]: n["node_id"] for n in s["topology"]["topology"]["nodes"]}
        env.slots[201].stopped_nodes = {"SW1"}
        env.orch._live_cache.clear()
        r = await env.client.post(f"/api/v1/sessions/{s['session_id']}/console-tickets",
                                  json={"node_id": by_name["SW1"], "readonly": False})
        assert r.status_code == 409 and r.json()["error"] == "node_not_running"
        r = await env.client.post(f"/api/v1/sessions/{s['session_id']}/console-tickets",
                                  json={"node_id": by_name["SW2"], "readonly": False})
        assert r.status_code == 200
        # Slot unreachable: the snapshot is marked stale and tickets are not refused on it.
        env.slots[201].reachable = False
        env.orch._live_cache.clear()
        s2 = (await env.client.get(f"/api/v1/sessions/{s['session_id']}")).json()
        assert s2["nodes_stale"] is True
        r = await env.client.post(f"/api/v1/sessions/{s['session_id']}/console-tickets",
                                  json={"node_id": by_name["SW1"], "readonly": False})
        assert r.status_code == 200


async def test_stop_by_teacher(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        s = await start(env)
        await env.run()
        r = await env.client.post(f"/api/v1/sessions/{s['session_id']}/stop", json={"requested_by_user_id": 17})
        assert r.json()["status"] == "stopping"
        await env.run()
        s = (await env.client.get(f"/api/v1/sessions/{s['session_id']}")).json()
        assert s["status"] == "stopped" and s["status_message"] == "Лаба остановлена преподавателем"
        async with env.services.pool.connection() as conn:
            cur = await conn.execute("SELECT detail FROM events WHERE kind = 'stop_requested_by'")
            assert (await cur.fetchone())["detail"] == {"user_id": 17}
