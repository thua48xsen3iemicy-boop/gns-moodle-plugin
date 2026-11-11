from datetime import timedelta

import pytest
from conftest import make_env

from lab_manager import admin
from lab_manager.maintenance import CANCEL_MESSAGE, STOP_MESSAGE, parse_delay


async def start(env, user_id=42, instance_id=1):
    return await env.client.post("/api/v1/sessions", json={"user_id": user_id, "instance_id": instance_id,
                                                           "lab_ref": "cisco-3x2"})


async def session(env, sid):
    return await env.services.store.session(sid)


def test_parse_delay():
    assert parse_delay("5m") == timedelta(minutes=5)
    assert parse_delay("5") == timedelta(minutes=5)
    assert parse_delay("90s") == timedelta(seconds=90)
    assert parse_delay("1h") == timedelta(hours=1)
    assert parse_delay("0") == timedelta(0)
    for bad in ("", "-5m", "5d", "five", "8000h"):
        with pytest.raises(ValueError):
            parse_delay(bad)


def test_cli_rejects_a_bad_delay_before_touching_the_database():
    with pytest.raises(SystemExit, match="Invalid delay"):
        admin.main(["maintenance", "on", "--in", "soon"])


async def test_off_by_default(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        r = await env.client.get("/api/v1/maintenance")
        assert r.status_code == 200 and r.json() == {"schema": "gns3lab.maintenance/1", "state": "off"}
        assert "maintenance" in (await env.client.get("/api/v1/info")).json()["features"]
        r = await start(env)
        assert r.status_code == 201 and "maintenance" not in r.json()


async def test_announced_refuses_new_labs_and_keeps_running_ones(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        running = (await start(env, user_id=42)).json()["session_id"]
        await env.run()
        queued = (await start(env, user_id=43)).json()["session_id"]  # No second slot: waits
        await env.run(3)
        assert (await session(env, queued))["stage"] == "wait_slot"

        await env.services.maintenance.switch_on(timedelta(minutes=5), "Обновление образов")
        state = (await env.client.get("/api/v1/maintenance")).json()
        assert state["state"] == "announced" and state["message"] == "Обновление образов"
        assert state["starts_at"].endswith("Z") and state["announced_at"].endswith("Z")

        r = await start(env, user_id=44)
        assert r.status_code == 503 and r.json()["error"] == "maintenance"
        assert r.json()["maintenance"]["state"] == "announced"
        # Repeating the POST of a live session stays idempotent.
        r = await start(env, user_id=42)
        assert r.status_code == 200 and r.json()["maintenance"]["state"] == "announced"

        await env.run()
        s = await session(env, queued)
        assert s["status"] == "stopped" and s["status_message"] == CANCEL_MESSAGE
        assert (await session(env, running))["status"] == "running"
        body = (await env.client.get(f"/api/v1/sessions/{running}")).json()
        assert body["status"] == "running" and body["maintenance"]["starts_at"] == state["starts_at"]
        listed = (await env.client.get("/api/v1/sessions", params={"instance_id": 1})).json()
        assert all(e["maintenance"]["state"] == "announced" for e in listed)
        node = next(iter(body["nodes"]))
        r = await env.client.post(f"/api/v1/sessions/{running}/console-tickets", json={"node_id": node})
        assert r.status_code == 200  # Consoles keep working until the deadline


async def test_deadline_stops_every_lab_then_off_restores(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        student = (await start(env, user_id=42)).json()["session_id"]
        await env.run()
        assert (await session(env, student))["status"] == "running"

        await env.services.maintenance.switch_on(timedelta(0))
        body = (await env.client.get(f"/api/v1/sessions/{student}")).json()
        assert body["maintenance"]["state"] == "draining"
        r = await env.client.post(f"/api/v1/sessions/{student}/console-tickets",
                                  json={"node_id": next(iter(body["nodes"]))})
        assert r.status_code == 503 and r.json()["error"] == "maintenance"

        await env.run()
        s = await session(env, student)
        assert s["status"] == "stopped" and s["status_message"] == STOP_MESSAGE
        assert s["stop_reason"] == "maintenance"
        assert all(slot["state"] == "free" for slot in await env.services.store.slots())
        state, sessions, captures = await env.services.maintenance.state()
        assert state["state"] == "active" and sessions == [] and captures == []

        assert await env.services.maintenance.switch_off() is True
        assert await env.services.maintenance.switch_off() is False
        r = await start(env, user_id=42)
        assert r.status_code == 201  # A new session; it resumes the student's project
        await env.run()
        assert (await session(env, r.json()["session_id"]))["status"] == "running"


async def test_a_user_stop_keeps_its_reason(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid = (await start(env)).json()["session_id"]
        await env.run()
        await env.client.post(f"/api/v1/sessions/{sid}/stop")
        await env.services.maintenance.switch_on(timedelta(0))
        await env.run()
        assert (await session(env, sid))["stop_reason"] == "user"
