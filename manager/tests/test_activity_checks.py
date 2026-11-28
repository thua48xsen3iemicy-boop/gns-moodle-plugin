"""Checks of the course activity, outside the lab and its lab_def (CONTRACTS 2.9, activity checks)."""

import copy

from conftest import make_env
from test_check_runs import ADDRS, CHECKS, host, with_checks
from test_facts import started
from test_labdef import IMAGES, LAB

from lab_manager import checks as checks_mod
from lab_manager import orchestrator

SSH = {"id": "ssh", "title": "SSH на D1", "node": "D1", "run": "systemctl is-active ssh", "expect": {"match": "^active$"}}
ACTIVITY = {"format": "gns3lab.checks/1", "network": "off", "scripts": [SSH]}
OTHER = {"format": "gns3lab.checks/1", "network": "off"}


async def validate(env, **body):
    return await env.client.post("/api/v1/checks/validate", json=body)


async def start(env, checks=None, user_id=42, lab_ref="cisco-3x2"):
    body = {"user_id": user_id, "instance_id": 1, "lab_ref": lab_ref}
    if checks is not None:
        body["checks"] = checks
    return await env.client.post("/api/v1/sessions", json=body)


async def put(env, sid, checks):
    return await env.client.put(f"/api/v1/sessions/{sid}/checks-definition", json={"checks": checks})


async def session(env, sid):
    return (await env.client.get(f"/api/v1/sessions/{sid}")).json()


async def run_check(env, sid):
    r = await env.client.post(f"/api/v1/sessions/{sid}/checks", json={"requested_by_user_id": 42})
    assert r.status_code == 202, r.text
    await env.run(5)
    return (await env.client.get(f"/api/v1/sessions/{sid}/checks/{r.json()['run_id']}")).json()


def with_facts(env, slot, pid, ids):
    slot.facts[pid] = {ids[n]: {"name": n, "collectors": {"linux.net": host(a)}} for n, a in ADDRS}
    env.orch._facts_next.clear()


async def test_validate_against_a_catalog_lab_and_a_definition(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=IMAGES) as env:
        r = await validate(env, lab_ref="cisco-3x2", checks=ACTIVITY)
        assert r.status_code == 200, r.text
        assert r.json() == {"checks_def": checks_mod.digest(checks_mod.check_definition(
            ACTIVITY, {f"D{i}" for i in range(1, 7)})[0]), "warnings": []}

        r = await validate(env, lab_ref="cisco-3x2", checks={**ACTIVITY, "scripts": [{**SSH, "node": "PC9"}]})
        assert r.status_code == 422 and r.json()["error"] == "checks_invalid"
        assert [(p["path"], p["code"]) for p in r.json()["problems"]] == [("checks.scripts[0].node", "unknown_node")]

        # A definition of Moodle: the document is checked against its nodes and images, not taken into it.
        ssh_pc1 = {**ACTIVITY, "scripts": [{**SSH, "node": "PC1"}]}
        r = await validate(env, lab=LAB, checks=ssh_pc1)
        assert r.status_code == 200 and r.json()["checks_def"].startswith("sha256:")
        r = await validate(env, lab=LAB, checks={**ACTIVITY, "scripts": [{**SSH, "node": "PC2"}]})  # VPCS
        assert r.status_code == 422
        assert r.json()["problems"][0]["code"] == "script_node_unsupported"

        bad = copy.deepcopy(LAB)
        bad["nodes"][0]["image"] = "removed"
        r = await validate(env, lab=bad, checks=ssh_pc1)
        assert r.status_code == 422 and r.json()["error"] == "lab_invalid"
        assert (await validate(env, lab_ref="nope", checks=OTHER)).status_code == 404
        assert (await validate(env, checks=OTHER)).status_code == 400
        assert (await validate(env, lab=LAB, lab_ref="cisco-3x2", checks=OTHER)).status_code == 400
        assert (await validate(env, lab_ref="cisco-3x2")).status_code == 400  # checks is required
        r = await validate(env, lab_ref="cisco-3x2", checks=OTHER, extra=1)
        assert r.status_code == 400 and r.json()["error"] == "unsupported_field"


async def test_the_activity_document_replaces_the_labs_and_follows_edits(tmp_path, dsn, monkeypatch):
    monkeypatch.setattr(orchestrator, "FORECAST_INTERVAL", 0)
    async with make_env(tmp_path, dsn) as env:
        for slot in env.slots.values():
            slot.facts_enabled = True
        with_checks(env)  # The lab's own checks (meta.json): not used while the activity sends its document
        activity_def = (await validate(env, lab_ref="cisco-3x2", checks=OTHER)).json()["checks_def"]
        r = await start(env, OTHER)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["checks_def"] == activity_def and body["checks_source"] == "activity" and "checks_error" not in body
        sid = body["session_id"]
        await env.run()
        s = await env.services.store.session(sid)
        slot, pid = env.slots[s["slot_vmid"]], str(s["project_id"])
        with_facts(env, slot, pid, slot.node_ids[pid])
        await env.run(3)
        assert (await session(env, sid))["features"] == ["consoles", "facts", "checks"]

        run = await run_check(env, sid)
        assert run["status"] == "done" and run["checks_def"] == activity_def
        rules = {r["code"]: r for r in run["result"]["rules"]}
        assert rules["gateway_missing"]["status"] == "off"  # network: off of the activity, not the lab's groups
        forecast = await env.client.post("/api/v1/forecasts", json={"session_ids": [sid]})
        assert forecast.json()["forecasts"][0]["forecast"]["checks_def"] == activity_def

        # An edit reaches the live lab: the same project, the next run and the forecast by the new document.
        edited = {**OTHER, "network": "auto"}
        r = await put(env, sid, edited)
        assert r.status_code == 200, r.text
        edited_def = r.json()["checks_def"]
        assert edited_def != activity_def and r.json()["checks_source"] == "activity"
        assert (await session(env, sid))["checks_def"] == edited_def
        await env.run(3)  # The same facts snapshot, reassessed by the new description
        forecast = await env.client.post("/api/v1/forecasts", json={"session_ids": [sid]})
        assert forecast.json()["forecasts"][0]["forecast"]["checks_def"] == edited_def
        run = await run_check(env, sid)
        assert run["checks_def"] == edited_def
        assert str((await env.services.store.session(sid))["project_id"]) == pid

        # A document that does not fit leaves the session as it was.
        r = await put(env, sid, {**OTHER, "scripts": [{**SSH, "node": "PC9"}]})
        assert r.status_code == 422 and r.json()["problems"][0]["path"] == "checks.scripts[0].node"
        assert (await session(env, sid))["checks_def"] == edited_def

        # null: back to the lab's own checks.
        lab_def = checks_mod.digest(checks_mod.check_definition(CHECKS, {f"D{i}" for i in range(1, 7)})[0])
        r = await put(env, sid, None)
        assert r.status_code == 200 and r.json() == {"checks_def": lab_def, "checks_source": "lab"}
        assert (await run_check(env, sid))["checks_def"] == lab_def

        assert (await env.client.put(f"/api/v1/sessions/{sid}/checks-definition", json={})).status_code == 400
        r = await env.client.put(f"/api/v1/sessions/{sid}/checks-definition", json={"checks": None, "x": 1})
        assert r.status_code == 400 and r.json()["error"] == "unsupported_field"
        assert (await put(env, "s-0000000000000000", None)).status_code == 404

        await env.client.post(f"/api/v1/sessions/{sid}/stop")
        await env.run(10)
        r = await put(env, sid, OTHER)
        assert r.status_code == 409 and r.json()["error"] == "session_not_running"


async def test_without_a_document_the_lab_decides(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        body = (await start(env)).json()
        assert body["checks_source"] == "lab" and body["checks_def"] is None  # The example lab has no checks
        listed = (await env.client.get("/api/v1/sessions", params={"instance_id": 1})).json()
        assert listed[0]["checks_source"] == "lab" and listed[0]["checks_def"] is None


async def test_a_document_that_does_not_fit_does_not_stop_the_lab(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        for slot in env.slots.values():
            slot.facts_enabled = True
        r = await start(env, {**OTHER, "scripts": [{**SSH, "node": "PC9"}]})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["checks_source"] == "activity" and body["checks_def"] is None
        assert body["checks_error"]["code"] == "checks_invalid"
        assert body["checks_error"]["problems"][0]["path"] == "checks.scripts[0].node"
        sid = body["session_id"]
        await env.run()
        s = await env.services.store.session(sid)
        slot, pid = env.slots[s["slot_vmid"]], str(s["project_id"])
        with_facts(env, slot, pid, slot.node_ids[pid])
        await env.run(3)
        shown = await session(env, sid)
        assert shown["status"] == "running" and shown["features"] == ["consoles", "facts"]
        r = await env.client.post(f"/api/v1/sessions/{sid}/checks", json={"requested_by_user_id": 42})
        assert r.status_code == 409 and r.json()["error"] == "checks_invalid" and r.json()["problems"]
        forecast = await env.client.post("/api/v1/forecasts", json={"session_ids": [sid]})
        assert forecast.json()["forecasts"][0]["forecast"] is None  # Nothing to assess by

        # The repeated start does not apply another unfit document; a fitting edit brings the checks back.
        again = await start(env, {**OTHER, "rules": {"nope": "off"}})
        assert again.status_code == 200 and again.json()["checks_error"]["code"] == "checks_invalid"
        assert (await put(env, sid, OTHER)).status_code == 200
        shown = await session(env, sid)
        assert "checks_error" not in shown and shown["features"] == ["consoles", "facts", "checks"]


async def test_a_repeated_start_brings_the_activitys_document(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid = (await start(env, OTHER)).json()["session_id"]
        edited = {**OTHER, "network": "auto"}
        again = await start(env, edited)
        assert again.status_code == 200 and again.json()["session_id"] == sid
        edited_def = (await validate(env, lab_ref="cisco-3x2", checks=edited)).json()["checks_def"]
        assert again.json()["checks_def"] == edited_def
        unfit = await start(env, {**OTHER, "scripts": [{**SSH, "node": "PC9"}]})
        assert unfit.status_code == 200 and unfit.json()["checks_def"] == edited_def  # Not applied
        assert "checks_error" not in unfit.json()


async def test_checks_are_not_part_of_the_definition_of_the_lab(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=IMAGES) as env:
        lab_def = (await env.client.post("/api/v1/labs/validate", json={"lab": LAB})).json()["lab_def"]
        ssh_pc1 = {**OTHER, "scripts": [{**SSH, "node": "PC1"}]}
        r = await env.client.post("/api/v1/sessions", json={"user_id": 42, "instance_id": 1, "lab": LAB,
                                                            "checks": ssh_pc1})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["lab_def"] == lab_def and body["checks_source"] == "activity"
        assert body["checks_def"] == (await validate(env, lab=LAB, checks=ssh_pc1)).json()["checks_def"]
        # Another document: the same live lab, not a conflict and not another workspace.
        r = await env.client.post("/api/v1/sessions", json={"user_id": 42, "instance_id": 1, "lab": LAB,
                                                            "checks": OTHER})
        assert r.status_code == 200 and r.json()["session_id"] == body["session_id"]

        r = await env.client.post("/api/v1/sessions", json={
            "user_id": 7, "library_lab_id": 3, "lab": LAB, "options": {"mode": "authoring"}, "checks": OTHER})
        assert r.status_code == 400 and r.json()["error"] == "invalid_request"
