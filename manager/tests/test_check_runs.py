"""Auto-check runs through the API and the orchestrator: queued -> running -> done (CONTRACTS 2.9)."""

import json

from conftest import make_env
from test_facts import collected, started

LATER = "2099-01-01T00:00:00Z"  # Collected after any run started: the facts count as fresh
CHECKS = {"format": "gns3lab.checks/1",
          "groups": {"explicit": [{"name": "A", "members": ["D1", "D2"]}, {"name": "B", "members": ["D3"]}]}}


def host(addr, gw=None):
    data = {"interfaces": [{"name": "ens3", "kind": "physical", "port": {"adapter": 0, "port": 0}, "admin": "up",
                            "oper": "up", "ipv4": [addr]}],
            "routes": [{"dst": "default", "via": gw, "dev": "ens3"}] if gw else []}
    return collected(data, attempted=LATER, observed=LATER)


def with_checks(env, checks=CHECKS):
    path = env.settings.labs_dir / "cisco-3x2" / "meta.json"
    meta = json.loads(path.read_text())
    meta["checks"] = checks
    path.write_text(json.dumps(meta))


async def test_a_run_goes_from_queued_to_done(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        for slot in env.slots.values():
            slot.facts_enabled = True
        with_checks(env)
        sid, slot, pid, ids = await started(env)
        slot.facts[pid] = {ids[n]: {"name": n, "collectors": {"linux.net": host(a)}}
                           for n, a in (("D1", "192.168.1.1/24"), ("D2", "192.168.1.2/24"), ("D3", "192.168.2.1/24"))}
        env.orch._facts_next.clear()
        await env.run(3)
        assert (await env.client.get(f"/api/v1/sessions/{sid}")).json()["features"] == ["consoles", "facts", "checks"]

        r = await env.client.post(f"/api/v1/sessions/{sid}/checks",
                                  json={"requested_by_user_id": 42, "grading": {"profile": "lenient"}})
        assert r.status_code == 202
        run = r.json()
        assert run["schema"] == "gns3lab.check-run/1" and run["status"] == "queued" and run["run_id"].startswith("c-")
        assert run["grading"]["profile"] == "lenient" and run["grading"]["fail_mark"] == 2 and run["result"] is None
        again = await env.client.post(f"/api/v1/sessions/{sid}/checks", json={"requested_by_user_id": 42})
        assert again.status_code == 409 and again.json() == {
            "error": "check_in_progress", "message": "Проверка уже идёт", "run_id": run["run_id"]}

        await env.run(5)
        assert pid in slot.refreshes  # The run asked the slot for fresh facts
        done = (await env.client.get(f"/api/v1/sessions/{sid}/checks/{run['run_id']}")).json()
        assert done["status"] == "done" and done["error"] is None and done["started_at"] and done["finished_at"]
        assert done["checks_def"].startswith("sha256:") and done["facts_rev"] >= 1
        result = done["result"]
        assert result["outcome"] == "graded" and isinstance(result["mark"], int)
        codes = {f["code"] for f in result["findings"]}
        assert "config_not_saved" in codes  # The IOL switches of the example lab never saved a configuration
        assert {r["code"] for r in result["rules"]} >= {"segment_split", "address_duplicate"}

        listed = (await env.client.get(f"/api/v1/sessions/{sid}/checks")).json()["runs"]
        assert [r["run_id"] for r in listed] == [run["run_id"]]
        assert "findings" not in listed[0]["result"] and listed[0]["result"]["mark"] == result["mark"]

        # A new run is possible once the previous one is over.
        r = await env.client.post(f"/api/v1/sessions/{sid}/checks", json={"requested_by_user_id": 7})
        assert r.status_code == 202


async def test_a_run_waits_for_the_collection_it_asked_for(tmp_path, dsn, monkeypatch):
    """The slot drops a refresh within its rate limit and stamps attempts in whole seconds: a collection made in
    the second the run started, after the run asked again, is fresh."""
    import time

    from lab_manager import orchestrator

    monkeypatch.setattr(orchestrator, "CHECK_REASK", 0)
    async with make_env(tmp_path, dsn) as env:
        for slot in env.slots.values():
            slot.facts_enabled = True
        with_checks(env)
        sid, slot, pid, ids = await started(env)
        old = "2000-01-01T00:00:00Z"
        addrs = (("D1", "192.168.1.1/24"), ("D2", "192.168.1.2/24"), ("D3", "192.168.2.1/24"))
        slot.facts[pid] = {ids[n]: {"name": n, "collectors": {"linux.net": collected(
            host(a)["data"], attempted=old, observed=old)}} for n, a in addrs}
        env.orch._facts_next.clear()
        await env.run(3)

        def collect(project_id):
            if slot.refreshes.count(project_id) < 2:
                return  # Within SLOT_FACTS_MIN_REFRESH of a previous request: dropped
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            slot.facts[pid] = {ids[n]: {"name": n, "collectors": {"linux.net": collected(
                host(a)["data"], attempted=stamp, observed=stamp)}} for n, a in addrs}

        slot.on_refresh = collect
        r = await env.client.post(f"/api/v1/sessions/{sid}/checks", json={"requested_by_user_id": 42})
        run = r.json()
        for _ in range(10):
            env.orch._facts_next.clear()
            await env.run(1)
        assert slot.refreshes.count(pid) >= 2  # The run asked again
        done = (await env.client.get(f"/api/v1/sessions/{sid}/checks/{run['run_id']}")).json()
        assert done["status"] == "done" and done["result"]["outcome"] == "graded"
        assert not [r for r in done["result"]["rules"] if r.get("reason") == "stale_facts"]


async def test_requests_that_are_refused(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid, _slot, _pid, _ids = await started(env)
        post = env.client.post
        r = await post(f"/api/v1/sessions/{sid}/checks", json={"requested_by_user_id": 42, "grading": {"profile": "exam"}})
        assert r.status_code == 400 and r.json()["error"] == "invalid_request"
        r = await post(f"/api/v1/sessions/{sid}/checks", json={"requested_by_user_id": 42, "mode": "x"})
        assert r.status_code == 400 and r.json()["error"] == "unsupported_field"
        r = await post(f"/api/v1/sessions/{sid}/checks", json={})
        assert r.status_code == 400 and r.json()["error"] == "invalid_request"
        r = await post(f"/api/v1/sessions/{sid}/checks",
                       json={"requested_by_user_id": 42, "grading": {"thresholds": [{"mark": 5, "major": -1}]}})
        assert r.status_code == 400
        r = await env.client.get(f"/api/v1/sessions/{sid}/checks/c-0000000000000000")
        assert r.status_code == 404 and r.json()["error"] == "check_not_found"
        assert (await env.client.get("/api/v1/sessions/s-0000000000000000/checks")).status_code == 404

        # A run left in progress by a stop fails; a stopped session takes no new run.
        r = await post(f"/api/v1/sessions/{sid}/checks", json={"requested_by_user_id": 42})
        run_id = r.json()["run_id"]
        await post(f"/api/v1/sessions/{sid}/stop")
        await env.run()
        run = (await env.client.get(f"/api/v1/sessions/{sid}/checks/{run_id}")).json()
        assert run["status"] in ("failed", "done")
        if run["status"] == "failed":
            assert run["error"]["code"] == "CHECK_FAILED" and run["result"] is None
        r = await post(f"/api/v1/sessions/{sid}/checks", json={"requested_by_user_id": 42})
        assert r.status_code == 409 and r.json()["error"] == "session_not_running"


async def test_check_runs_go_away_with_their_session(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        async with env.services.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT confdeltype FROM pg_constraint WHERE contype = 'f' AND confrelid = 'sessions'::regclass"
                " AND conrelid = 'check_runs'::regclass")
            assert [r["confdeltype"] for r in await cur.fetchall()] == ["c"]
