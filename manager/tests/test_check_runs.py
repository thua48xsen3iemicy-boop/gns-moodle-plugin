"""Auto-check runs through the API and the orchestrator: queued -> running -> done (CONTRACTS 2.9)."""

import json
import time
from datetime import datetime, timedelta, timezone

from conftest import make_env
from test_facts import collected, started

LATER = "2099-01-01T00:00:00Z"  # Collected after any run started: the facts count as fresh
# Every QEMU node of the example lab (D1…D6) has a guest agent: a run waits for each to report once.
ADDRS = (("D1", "192.168.1.1/24"), ("D2", "192.168.1.2/24"), ("D3", "192.168.2.1/24"),
         ("D4", "192.168.9.4/24"), ("D5", "192.168.9.5/24"), ("D6", "192.168.9.6/24"))
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
        slot.facts[pid] = {ids[n]: {"name": n, "collectors": {"linux.net": host(a)}} for n, a in ADDRS}
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
        addrs = ADDRS
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


async def test_a_guest_that_never_reported_makes_the_run_incomplete(tmp_path, dsn):
    """Just after the lab started the guests are still booting and the slot publishes nothing about them yet
    (SLOT_FACTS_BOOT_GRACE). Such a node is not one "without a collector": the run waits for it, and if it stays
    silent the run asks to check again instead of grading an empty lab."""
    async with make_env(tmp_path, dsn, check_facts_wait=0) as env:
        for slot in env.slots.values():
            slot.facts_enabled = True
        with_checks(env)
        sid, slot, pid, ids = await started(env)
        slot.facts[pid] = {ids[n]: {"name": n, "collectors": {"linux.net": host(a)}} for n, a in ADDRS if n != "D3"}
        env.orch._facts_next.clear()
        await env.run(3)
        r = await env.client.post(f"/api/v1/sessions/{sid}/checks", json={"requested_by_user_id": 42})
        await env.run(5)
        done = (await env.client.get(f"/api/v1/sessions/{sid}/checks/{r.json()['run_id']}")).json()
        assert done["status"] == "done"
        assert done["result"]["outcome"] == "incomplete" and done["result"]["mark"] is None
        assert [r for r in done["result"]["rules"] if r.get("reason") == "stale_facts"]


def test_a_node_is_fresh_only_when_every_collector_is():
    from lab_manager.orchestrator import stale_nodes

    since = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
    after, before = since + timedelta(seconds=2), since - timedelta(seconds=40)

    def row(node, collector, at):
        return {"node_id": node, "collector": collector, "attempted_at": at}

    started = {"A": "started", "B": "started", "C": "started", "D": "started", "E": "stopped"}
    rows = [
        row("A", "linux.net", after), row("A", "linux.config", after),
        # The address is fresh, the saved configuration was read before the student fixed it.
        row("B", "linux.net", after), row("B", "linux.config", before),
        # iol.l1 follows its own cycle: an old one does not hold the node.
        row("C", "ios.config", after), row("C", "iol.l1", before),
        # A collector the slot stopped publishing long ago does not hold the node either.
        row("D", "linux.net", after), row("D", "switch.net", since - timedelta(hours=2)),
        row("E", "linux.net", before),  # Stopped: node_stopped, not stale
    ]
    assert stale_nodes(rows, started, since) == {"B"}


async def test_a_run_waits_for_every_collector_of_a_node(tmp_path, dsn):
    """linux.net was read after the run started, linux.config before it: the node is not fresh yet."""
    async with make_env(tmp_path, dsn, check_facts_wait=0) as env:
        for slot in env.slots.values():
            slot.facts_enabled = True
        with_checks(env)
        sid, slot, pid, ids = await started(env)
        stamp = lambda delta: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + delta))  # noqa: E731
        slot.facts[pid] = {ids[n]: {"name": n, "collectors": {"linux.net": host(a)}} for n, a in ADDRS}
        slot.facts[pid][ids["D3"]]["collectors"] = {
            "linux.net": collected(host("192.168.2.1/24")["data"], attempted=stamp(60), observed=stamp(60)),
            "linux.config": collected({"net_saved": {"mechanism": "ifupdown", "ok": True, "interfaces": []}},
                                      attempted=stamp(-60), observed=stamp(-60), schema="linux.config/1"),
        }
        env.orch._facts_next.clear()
        await env.run(3)
        r = await env.client.post(f"/api/v1/sessions/{sid}/checks", json={"requested_by_user_id": 42})
        await env.run(5)
        done = (await env.client.get(f"/api/v1/sessions/{sid}/checks/{r.json()['run_id']}")).json()
        assert done["status"] == "done"
        assert done["result"]["outcome"] == "incomplete" and done["result"]["mark"] is None


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


SCRIPTS = [
    {"id": "web", "title": "Веб-сервер на D1", "node": "D1", "run": "curl -s http://127.0.0.1/?n={{nonce}}",
     "expect": {"exit": 0, "contains": ["{{nonce}}"]}},
    {"id": "ping", "title": "D3 видит D1", "node": "D3", "run": "ping -c1 192.168.1.1", "timeout": 5,
     "group": "routing", "class": "minor"},
    {"id": "win", "title": "Служба на D4", "node": "D4", "shell": "powershell", "run": "Get-Service w32time",
     "expect": {"match": "Running"}},
]


async def test_teacher_scripts_run_in_the_guests(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        for slot in env.slots.values():
            slot.facts_enabled = True
        with_checks(env, {**CHECKS, "scripts": SCRIPTS})
        sid, slot, pid, ids = await started(env)
        slot.facts[pid] = {ids[n]: {"name": n, "collectors": {"linux.net": host(a)}} for n, a in ADDRS}
        env.orch._facts_next.clear()
        await env.run(3)

        def answer(node_id, body):
            if node_id == ids["D1"]:  # Echoes the marker it was given: the real path works
                return 200, {"exit_code": 0, "stdout": body["command"].rsplit("=", 1)[1] + "\n", "stderr": ""}
            if node_id == ids["D3"]:
                return 200, {"exit_code": 1, "stdout": "", "stderr": "Network is unreachable"}
            return 200, {"exit_code": 0, "stdout": "Running", "stderr": ""}

        slot.on_exec = answer
        r = await env.client.post(f"/api/v1/sessions/{sid}/checks", json={"requested_by_user_id": 42})
        run = r.json()
        for _ in range(4):
            await env.run(2)
        done = (await env.client.get(f"/api/v1/sessions/{sid}/checks/{run['run_id']}")).json()
        assert done["status"] == "done", done
        result = done["result"]
        nonce = result["nonce"]
        assert len(nonce) == 16
        # Without exec-shell the slot gets no shell field, and a PowerShell script is not sent at all.
        assert [(n, b) for _, n, b in slot.exec_requests] == [
            (ids["D1"], {"command": f"curl -s http://127.0.0.1/?n={nonce}", "timeout_seconds": 20}),
            (ids["D3"], {"command": "ping -c1 192.168.1.1", "timeout_seconds": 5})]
        assert [(s["id"], s["status"], s.get("reason")) for s in result["scripts"]] == [
            ("web", "pass", None), ("ping", "fail", "exit"), ("win", "skipped", "unsupported_os")]
        assert result["scripts"][1]["stderr"] == "Network is unreachable"
        failed = [f for f in result["findings"] if f["code"] == "script_failed"]
        assert [(f["script_id"], f["class"], f["group"], f["nodes"]) for f in failed] == [
            ("ping", "minor", "routing", ["D3"])]
        assert {"code": "script_failed", "group": "host", "status": "fail"} in result["rules"]
        listed = (await env.client.get(f"/api/v1/sessions/{sid}/checks")).json()["runs"]
        assert "scripts" not in listed[0]["result"] and listed[0]["result"]["nonce"] == nonce
        async with env.services.pool.connection() as conn:
            cur = await conn.execute("SELECT extra_seconds FROM check_runs WHERE id = %s", (run["run_id"],))
            assert (await cur.fetchone())["extra_seconds"] == 45

        # A slot with exec-shell: the shell goes along, its timeout and mismatch codes are told apart.
        slot.exec_shell = True
        slot.exec_requests.clear()

        def strict(node_id, body):
            if node_id == ids["D1"]:
                return 504, {"detail": {"code": "EXEC_TIMEOUT", "message": "timed out"}}
            if node_id == ids["D4"]:
                return 409, {"detail": {"code": "SHELL_MISMATCH", "message": "posix guest"}}
            return 502, {"detail": "QGA error: QgaUnavailable: closed"}

        slot.on_exec = strict
        r = await env.client.post(f"/api/v1/sessions/{sid}/checks", json={"requested_by_user_id": 42})
        for _ in range(4):
            await env.run(2)
        again = (await env.client.get(f"/api/v1/sessions/{sid}/checks/{r.json()['run_id']}")).json()
        assert [b.get("shell") for _, _, b in slot.exec_requests] == [None, None, "powershell"]
        assert [(s["status"], s.get("reason")) for s in again["result"]["scripts"]] == [
            ("fail", "timeout"), ("skipped", "collect_failed"), ("skipped", "unsupported_os")]
        # The ping script is outside no focus: its guest did not answer, so the run asks to check again.
        assert again["result"]["outcome"] == "incomplete" and again["result"]["mark"] is None
        assert again["result"]["nonce"] != nonce


async def test_a_run_with_scripts_times_out_later(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid, *_ = await started(env)
        store = env.services.store
        run = await store.create_check_run(sid, 42, {})
        await store.start_check_run(run["id"])
        await store.extend_check_run(run["id"], 3600)
        async with env.services.pool.connection() as conn:
            await conn.execute("UPDATE check_runs SET requested_at = now() - interval '10 minutes' WHERE id = %s",
                               (run["id"],))
        await store.fail_orphan_check_runs(120)
        assert (await store.check_run(sid, run["id"]))["status"] == "running"
        await store.extend_check_run(run["id"], 0)
        await store.fail_orphan_check_runs(120)
        assert (await store.check_run(sid, run["id"]))["status"] == "failed"
