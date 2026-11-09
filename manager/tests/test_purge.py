"""Deleting student work: POST /api/v1/purges, /purges/reconcile and the project directory queue (CONTRACTS §2.10)."""
import pytest

from conftest import make_env
from test_derived import start as start_authoring, lab, catalog


async def start(env, user=42, instance=1, expect=(200, 201)):
    r = await env.client.post("/api/v1/sessions", json={"user_id": user, "instance_id": instance, "lab_ref": "cisco-3x2"})
    assert r.status_code in expect, r.text
    return r.json()


async def stop(env, sid):
    await env.client.post(f"/api/v1/sessions/{sid}/stop")
    await env.run()


async def rows(env, sql, *args):
    async with env.services.store.pool.connection() as conn:
        return await (await conn.execute(sql, args)).fetchall()


async def purge(env, **body):
    r = await env.client.post("/api/v1/purges", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_wipe_of_a_stopped_lab_is_immediate_and_the_next_start_is_fresh(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        s = await start(env)
        await env.run()
        await stop(env, s["session_id"])
        [ws] = await rows(env, "SELECT project_id FROM workspaces")
        [key] = await rows(env, "SELECT student_key FROM sessions")

        assert await purge(env, instance_id=1, user_id=42) == {"workspaces": 1, "pending": 0}
        r = await env.client.get("/api/v1/sessions", params={"user_id": 42, "instance_id": 1})
        assert r.status_code == 404
        assert await rows(env, "SELECT * FROM sessions") == []
        assert await rows(env, "SELECT * FROM slot_calls") == []
        [queued] = await rows(env, "SELECT project_id, student_key FROM project_purges")
        assert queued == {"project_id": ws["project_id"], "student_key": key["student_key"]}

        await env.services.purges.step(force=True)
        assert env.agent.deleted_projects == [(key["student_key"], str(ws["project_id"]))]
        assert await rows(env, "SELECT * FROM project_purges") == []

        again = await start(env)
        await env.run()
        [fresh] = await rows(env, "SELECT project_id FROM workspaces")
        assert fresh["project_id"] != ws["project_id"] and again["session_id"] != s["session_id"]
        assert await purge(env, instance_id=1, user_id=42) == {"workspaces": 1, "pending": 1}  # Live: stops first
        assert await purge(env, instance_id=1, user_id=42) == {"workspaces": 1, "pending": 1}  # Repeat is harmless


async def test_live_lab_is_stopped_then_deleted_and_blocks_a_restart_meanwhile(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        s = await start(env)
        await env.run()
        assert await purge(env, instance_id=1) == {"workspaces": 1, "pending": 1}
        r = await env.client.get(f"/api/v1/sessions/{s['session_id']}")
        assert r.json()["status"] == "stopping"
        await env.run()
        assert await rows(env, "SELECT * FROM workspaces") == []
        assert [e["detail"]["workspace_id"] for e in await rows(
            env, "SELECT detail FROM events WHERE kind = 'workspace_purged'")] == [1]


async def test_start_into_a_workspace_being_purged_is_refused(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        s = await start(env)
        await env.run()
        await stop(env, s["session_id"])
        async with env.services.store.pool.connection() as conn:
            await conn.execute("UPDATE workspaces SET purge_requested_at = now()")
        r = await env.client.post("/api/v1/sessions", json={"user_id": 42, "instance_id": 1, "lab_ref": "cisco-3x2"})
        assert r.status_code == 409 and r.json()["error"] == "workspace_purging"
        await env.run()
        await start(env, expect=(201,))


async def test_scope_activity_user_and_site(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        for user, instance in ((42, 1), (43, 1), (42, 2), (44, 3)):
            s = await start(env, user, instance)
            await env.run()
            await stop(env, s["session_id"])
        assert (await purge(env, instance_id=1))["workspaces"] == 2
        assert sorted((r["user_id"], r["instance_id"]) for r in await rows(
            env, "SELECT user_id, instance_id FROM workspaces")) == [(42, 2), (44, 3)]
        assert (await purge(env, user_id=42))["workspaces"] == 1
        assert [r["user_id"] for r in await rows(env, "SELECT user_id FROM workspaces")] == [44]
        async with env.services.store.pool.connection() as conn:
            await conn.execute("UPDATE workspaces SET site = 'other'")
        assert (await purge(env, user_id=44))["workspaces"] == 0


async def test_reconcile_judges_only_ids_up_to_the_largest_listed(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        for user, instance in ((42, 1), (43, 2), (44, 5)):
            s = await start(env, user, instance)
            await env.run()
            await stop(env, s["session_id"])
        r = await env.client.post("/api/v1/purges/reconcile", json={"instance_ids": [2, 3]})
        assert r.json() == {"workspaces": 1, "pending": 0}
        assert sorted(r["instance_id"] for r in await rows(env, "SELECT instance_id FROM workspaces")) == [2, 5]
        r = await env.client.post("/api/v1/purges/reconcile", json={"instance_ids": []})
        assert r.status_code == 400


@pytest.mark.parametrize("body,error", [({}, "invalid_request"), ({"instance_id": 1, "library_lab_id": 2}, "invalid_request"),
                                        ({"instance_id": 0}, "invalid_request"), ({"instance_id": 1, "x": 1}, "unsupported_field")])
async def test_validation(tmp_path, dsn, body, error):
    async with make_env(tmp_path, dsn) as env:
        r = await env.client.post("/api/v1/purges", json=body)
        assert r.status_code == 400 and r.json()["error"] == error, r.text


async def test_directory_waits_for_the_slot_the_agent_and_retries(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        s = await start(env)
        await env.run()
        await stop(env, s["session_id"])
        [key] = await rows(env, "SELECT student_key FROM sessions")
        await purge(env, instance_id=1)
        purges = env.services.purges

        async def due():
            async with env.services.store.pool.connection() as conn:
                await conn.execute("UPDATE project_purges SET next_attempt_at = now()")

        async with env.services.store.pool.connection() as conn:
            await conn.execute("UPDATE slots SET student_key = %s WHERE vmid = 201", (key["student_key"],))
        await purges.step(force=True)
        [row] = await rows(env, "SELECT attempts, last_error FROM project_purges")
        assert row == {"attempts": 1, "last_error": "student directory is held by a slot"}
        async with env.services.store.pool.connection() as conn:
            await conn.execute("UPDATE slots SET student_key = NULL")

        await due()
        env.agent.delete_fails = (503, "STORAGE_UNAVAILABLE")
        await purges.step(force=True)
        [row] = await rows(env, "SELECT attempts, last_error, next_attempt_at > now() AS later FROM project_purges")
        assert row == {"attempts": 2, "last_error": "STORAGE_UNAVAILABLE", "later": True}
        await purges.step(force=True)  # Not due yet: nothing is called
        assert env.agent.deleted_projects == []

        await due()
        env.agent.projects_delete = False
        purges._caps.clear()
        await purges.step(force=True)
        [row] = await rows(env, "SELECT last_error FROM project_purges")
        assert row["last_error"] == "no agent with projects_delete"

        await due()
        env.agent.projects_delete = True
        purges._caps.clear()
        await purges.step(force=True)
        assert len(env.agent.deleted_projects) == 1 and await rows(env, "SELECT * FROM project_purges") == []


async def test_changed_authoring_definition_purges_the_earlier_project(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=catalog()) as env:
        r = await start_authoring(env)
        assert r.status_code == 201, r.text
        await env.run()
        await stop(env, r.json()["session_id"])
        [first] = await rows(env, "SELECT project_id FROM workspaces")
        r = await start_authoring(env, lab(2))
        assert r.status_code == 201, r.text
        await env.run()
        assert [w["project_id"] for w in await rows(env, "SELECT project_id FROM workspaces")] != [first["project_id"]]
        assert [p["project_id"] for p in await rows(env, "SELECT project_id FROM project_purges")] == [first["project_id"]]
        assert (await purge(env, user_id=42, library_lab_id=12))["workspaces"] == 1
