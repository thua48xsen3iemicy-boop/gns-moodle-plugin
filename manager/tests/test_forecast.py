"""The teacher's forecast: the latest facts snapshot graded without scripts, kept after the stop (CONTRACTS 2.9a)."""

from conftest import make_env
from test_check_runs import ADDRS, host, with_checks
from test_facts import started

from lab_manager import checks as checks_mod
from lab_manager import orchestrator


async def forecasts(env, *sids, **body):
    r = await env.client.post("/api/v1/forecasts", json={"session_ids": list(sids), **body})
    assert r.status_code == 200, r.text
    return {f["session_id"]: f for f in r.json()["forecasts"]}


async def test_the_forecast_follows_the_facts_and_outlives_the_lab(tmp_path, dsn, monkeypatch):
    monkeypatch.setattr(orchestrator, "FORECAST_INTERVAL", 0)
    async with make_env(tmp_path, dsn) as env:
        for slot in env.slots.values():
            slot.facts_enabled = True
        with_checks(env)
        sid, slot, pid, ids = await started(env)
        assert (await forecasts(env, sid))[sid] == {"session_id": sid, "found": True, "forecast": None}

        slot.facts[pid] = {ids[n]: {"name": n, "collectors": {"linux.net": host(a)}} for n, a in ADDRS}
        env.orch._facts_next.clear()
        await env.run(3)
        got = await forecasts(env, sid, "s-0000000000000000", grading={"profile": "lenient"})
        assert got["s-0000000000000000"] == {"session_id": "s-0000000000000000", "found": False, "forecast": None}
        forecast = got[sid]["forecast"]
        rev = (await env.services.store.session(sid))["facts_rev"]
        assert forecast["schema"] == "gns3lab.forecast/1" and forecast["facts_rev"] == rev and forecast["assessed_at"]
        assert forecast["outcome"] == "graded" and isinstance(forecast["mark"], int)
        assert forecast["scripts_ignored"] is False and "findings" not in forecast
        assert set(forecast["counts"]) == {"critical", "major", "minor", "note"}
        # A forecast is not a check run: the student's attempts stay untouched, the slot was not asked to collect.
        assert (await env.client.get(f"/api/v1/sessions/{sid}/checks")).json() == {"runs": []}
        assert pid not in slot.refreshes

        detailed = (await forecasts(env, sid, details=True))[sid]["forecast"]
        assert "config_not_saved" in {f["code"] for f in detailed["findings"]}
        # The grading of the activity is applied on request: the same snapshot, another mark.
        strict = (await forecasts(env, sid, grading={"thresholds": [{"mark": 5, "critical": 0, "major": 0,
                                                                       "minor": 0}], "fail_mark": 1}))
        assert strict[sid]["forecast"]["mark"] == 1

        slot.facts[pid][ids["D1"]]["collectors"]["linux.net"] = host("192.168.1.11/24")
        env.orch._facts_next.clear()
        await env.run(3)
        newer = (await forecasts(env, sid))[sid]["forecast"]
        assert newer["facts_rev"] > rev

        await env.client.post(f"/api/v1/sessions/{sid}/stop")
        await env.run(10)
        assert (await env.services.store.session(sid))["status"] == "stopped"
        after = (await forecasts(env, sid))[sid]["forecast"]
        assert after["facts_rev"] >= newer["facts_rev"] and isinstance(after["mark"], int)


async def test_a_booting_guest_makes_the_forecast_incomplete_but_still_marked(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        for slot in env.slots.values():
            slot.facts_enabled = True
        with_checks(env)
        sid, slot, pid, ids = await started(env)
        slot.facts[pid] = {ids[n]: {"name": n, "collectors": {"linux.net": host(a)}} for n, a in ADDRS if n != "D3"}
        env.orch._facts_next.clear()
        await env.run(3)
        forecast = (await forecasts(env, sid))[sid]["forecast"]
        assert forecast["outcome"] == "incomplete" and isinstance(forecast["mark"], int)


async def test_a_bad_request_is_refused(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        post = env.client.post
        assert (await post("/api/v1/forecasts", json={"session_ids": []})).status_code == 400
        assert (await post("/api/v1/forecasts", json={"session_ids": ["s-1"], "extra": 1})).status_code == 400
        r = await post("/api/v1/forecasts", json={"session_ids": ["s-1"], "grading": {"profile": "harsh"}})
        assert r.status_code == 400 and r.json()["error"] == "invalid_request"


def test_scripts_are_named_but_not_graded():
    basis = checks_mod.forecast_basis([], [], {"focus": [], "scripts": [{"id": "a"}]})
    grading = checks_mod.normalize_grading(None)
    assert checks_mod.forecast(basis, grading) == {
        "outcome": "graded", "mark": grading["thresholds"][0]["mark"],
        "counts": {"critical": 0, "major": 0, "minor": 0, "note": 0}, "scripts_ignored": True}
