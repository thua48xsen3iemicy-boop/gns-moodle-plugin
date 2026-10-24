"""Safety regressions from the pilot audit; PostgreSQL tests use the disposable dsn fixture."""
import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx
import psycopg
import pytest

from conftest import make_env, make_settings
from lab_manager.agent_client import AgentClient
from lab_manager.app import create_app
from lab_manager.config import ConfigError
from lab_manager.db import latest_version
from lab_manager.errors import public_error
from lab_manager.orchestrator import Orchestrator
from lab_manager.ownership import command_scope
from lab_manager.slot_client import SlotClient
from lab_manager.store import Fenced, Store


async def test_no_external_mutation_after_authority_is_lost():
    sent = []
    def transport(request):
        sent.append(request)
        return httpx.Response(200, json={})
    agent = AgentClient("https://{node}", transport=httpx.MockTransport(transport))
    slot = SlotClient("slot.invalid", 8080, "dummy", transport=httpx.MockTransport(transport))
    try:
        with command_scope(7, 4, lambda: False):
            with pytest.raises(Fenced):
                await agent.stop("node", 201)
            with pytest.raises(Fenced):
                await slot.stop("project", "key")
        assert sent == []
    finally:
        await agent.close()
        await slot.close()


async def test_restarted_slot_rejects_cached_owner_and_session_enters_safe_cleanup():
    requests = []
    def transport(request):
        requests.append(request)
        assert request.url.path == "/v1/labs/deploy"
        assert request.headers["X-Lab-Epoch"] == "7"
        assert request.headers["X-Lab-Generation"] == "4"
        return httpx.Response(409, json={"detail": {
            "code": "STALE_OWNER", "message": "Command owner superseded"}})

    slot = SlotClient("slot.invalid", 8080, "dummy", transport=httpx.MockTransport(transport))
    session = dict(id="restart", status="starting", stage="lab_submit", slot_vmid=201,
                   slot_generation=2, status_message="Запуск лабы…", stop_requested=False,
                   error=None)
    call = dict(status="pending", operation_id=None, body={}, idempotency_key="deploy-1")
    store = NS(session=AsyncMock(return_value=session), current_call=AsyncMock(return_value=call),
               update_session=AsyncMock())
    orch = Orchestrator(NS(), store, NS(find_ct_node=AsyncMock(return_value="pve1")),
                        NS(), {201: slot}, None)
    orch.leader, orch.epoch = True, 7
    orch._claims = {("agent", (201, "pve1", 7, 4)), ("slot", (201, "pve1", 7, 4))}
    orch._ticket = AsyncMock(return_value=({}, {}))
    try:
        await orch.drive("restart")
        assert len(requests) == 1
        store.update_session.assert_awaited_once()
        args, changes = store.update_session.await_args
        assert args == (session,)
        assert changes["stage"] == "lab_stop"
        assert changes["status"] == "stopping"
        assert changes["slot_state"] == "releasing"
        assert changes["error"]["code"] == "SLOT_OWNER_LOST"
        assert changes["error"]["slot_state"] == "maintenance"
        assert changes["error"]["source"] == {"receiver": "slot", "code": "STALE_OWNER"}
        assert public_error(changes["error"]) == {"code": "SLOT_OWNER_LOST", "retryable": True}
    finally:
        await slot.close()


async def test_slot_rejection_after_local_leadership_loss_does_not_change_session():
    session = dict(id="old-leader", status="starting", stage="lab_submit", slot_vmid=201,
                   slot_generation=2, status_message="Запуск лабы…", stop_requested=False)
    call = dict(status="pending", operation_id=None, body={}, idempotency_key="deploy-1")
    store = NS(session=AsyncMock(return_value=session), current_call=AsyncMock(return_value=call),
               update_session=AsyncMock())
    orch = Orchestrator(NS(), store, NS(find_ct_node=AsyncMock(return_value="pve1")),
                        NS(), {}, None)
    orch.leader, orch.epoch = True, 7
    orch._claims = {("agent", (201, "pve1", 7, 4)), ("slot", (201, "pve1", 7, 4))}
    orch._ticket = AsyncMock(return_value=({}, {}))
    def transport(_):
        orch.leader = False
        return httpx.Response(409, json={"detail": {"code": "STALE_OWNER"}})
    slot = SlotClient("slot.invalid", 8080, "dummy", transport=httpx.MockTransport(transport))
    orch.slot_clients[201] = slot
    try:
        await orch.drive("old-leader")
        store.update_session.assert_not_awaited()
    finally:
        await slot.close()


@pytest.mark.parametrize("parked", [True, False])
async def test_slot_owner_loss_cleanup_never_releases_slot_for_reuse(tmp_path, parked):
    settings = make_settings(tmp_path)
    student = "placeholder" if parked else "main-42"
    config = {
        "mp0": "/mnt/pve/cfs/gns3-ro/slot,mp=/srv/gns3-ro/slot,ro=1",
        "mp1": f"/mnt/pve/cfs/gns3-rw/students/{student},mp=/opt/gns3/projects,ro={int(parked)}",
    }
    store = NS(set_slot_student=AsyncMock(), finish_session=AsyncMock(),
               slot=AsyncMock(return_value={"state": "releasing", "state_message": ""}))
    agent = NS(set_mounts=AsyncMock())
    pve = NS(find_ct_node=AsyncMock(return_value="pve1"), ct_config=AsyncMock(return_value=config))
    orch = Orchestrator(settings, store, pve, agent, {}, None)
    session = dict(id="restart", slot_vmid=201, stage="ct_park", slot_node="pve1",
                   error={"code": "SLOT_OWNER_LOST", "message": "Слот отверг владельца",
                          "slot_state": "maintenance"})

    assert await orch._stage_ct_park(session) is False
    _, finished = store.finish_session.await_args
    assert finished["status"] == "failed"
    assert finished["slot_state"] == "maintenance"
    if parked:
        store.set_slot_student.assert_awaited_once_with(session, None)
    else:
        store.set_slot_student.assert_not_awaited()


async def test_leader_connection_loss_cancels_and_joins_session_tasks():
    entered, cancelled, lost = asyncio.Event(), asyncio.Event(), asyncio.Event()
    s = dict(id="audit", status="stopping", stage="ct_stop", slot_vmid=201,
             slot_generation=2, student_key="main-42", error=None)
    async def status(*_):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    async def pause(_):
        lost.set()
        await asyncio.Event().wait()
    store = NS(next_epoch=AsyncMock(return_value=1), live_sessions=AsyncMock(return_value=[s]),
               session=AsyncMock(return_value=s))
    agent = NS(status=status, stop=AsyncMock())
    orch = Orchestrator(NS(worker_interval=0.001), store,
                        NS(find_ct_node=AsyncMock(return_value="pve1")), agent, {}, None, sleep=pause)
    orch._claim = AsyncMock()
    class Connection:
        ticks = 0
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def execute(self, sql, *_):
            if "pg_try_advisory_lock" in sql:
                return NS(fetchone=AsyncMock(return_value={"ok": True}))
            self.ticks += 1
            if self.ticks > 1:
                await entered.wait()
                raise psycopg.OperationalError("lost lock connection")
    runner = asyncio.create_task(orch.run_forever(AsyncMock(return_value=Connection())))
    try:
        await asyncio.wait_for(lost.wait(), 2)
        assert cancelled.is_set()
        assert not orch.leader and orch.epoch is None and orch._tasks == {}
        agent.stop.assert_not_awaited()
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)


async def test_readiness_requires_live_fresh_leader(tmp_path):
    @asynccontextmanager
    async def connection():
        yield object()
    orch = NS(leader=False, last_tick=None)
    services = NS(settings=NS(labs_dir=tmp_path), store=NS(),
                  pool=NS(connection=connection), orchestrator=orch)
    app = create_app(services, run_orchestrator=False)
    app.state.orchestrator_task = NS(done=lambda: False)
    with patch("lab_manager.app.schema_version", AsyncMock(return_value=latest_version())):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/readyz")
            assert r.status_code == 503
            assert r.json()["checks"]["orchestrator"]["ok"] is False
            orch.leader = True
            assert (await c.get("/readyz")).status_code == 503  # Not ticking yet
            orch.last_tick = __import__("time").monotonic()
            assert (await c.get("/readyz")).status_code == 200


def test_parking_and_transport_cannot_be_silently_disabled(tmp_path):
    settings = make_settings(tmp_path)
    with pytest.raises(ConfigError, match="PLACEHOLDER"):
        replace(settings, placeholder_student_key=None)
    with pytest.raises(ConfigError, match="ALLOW_PLAINTEXT"):
        replace(settings, allow_plaintext_slots=False)


@pytest.mark.parametrize("budget", ["memory", "cpu"])
async def test_competing_allocators_never_double_reserve_node(tmp_path, dsn, budget):
    # Two Store objects have different in-process locks: PostgreSQL must arbitrate.
    async with make_env(tmp_path, dsn, vmids=(201, 202), nodes_of={201: "pve1", 202: "pve1"},
                        node_guest_memory_mb=16384 if budget == "memory" else 0,
                        node_cpu_overcommit=0.5 if budget == "cpu" else 0) as env:
        other = Orchestrator(env.settings, Store(env.services.pool), env.services.pve,
                             env.services.agent, env.services.slots, env.services.catalog)
        for iteration in range(50):
            sessions = []
            for user in (iteration * 2 + 1, iteration * 2 + 2):
                r = await env.client.post("/api/v1/sessions", json={
                    "user_id": user, "instance_id": 1, "lab_ref": "cisco-3x2"})
                assert r.status_code == 201
                sessions.append(await env.services.store.session(r.json()["session_id"]))
            results = await asyncio.gather(env.orch._try_allocate(sessions[0]), other._try_allocate(sessions[1]))
            assert results.count(None) == 1
            reservations = await env.services.store.reservations()
            assert len(reservations) == 1
            assert reservations[0]["resources"] == {"cores": 8, "memory_mb": 16384}
            # Isolated allocation test: no host operation was issued, so release DB-only records.
            for s in sessions:
                await env.services.store.finish_session(await env.services.store.session(s["id"]),
                                                        status="stopped", message="allocation test")


async def test_failed_slot_keeps_its_resource_reservation(tmp_path, dsn):
    async with make_env(tmp_path, dsn, nodes_of={201: "pve1", 202: "pve1"},
                        node_guest_memory_mb=16384) as env:
        async def create(user):
            r = await env.client.post("/api/v1/sessions", json={"user_id": user, "instance_id": 1,
                                                               "lab_ref": "cisco-3x2"})
            return await env.services.store.session(r.json()["session_id"])
        a = await create(1)
        await env.orch._try_allocate(a)
        a = await env.services.store.session(a["id"])
        await env.services.store.finish_session(a, status="failed", message="unknown CT outcome", slot_state="failed")
        b = await create(2)
        assert await env.orch._try_allocate(b) is not None
        assert (await env.services.store.session(b["id"]))["slot_vmid"] is None


async def test_catalog_changes_do_not_change_reserved_size_or_ticket(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        r = await env.client.post("/api/v1/sessions", json={"user_id": 42, "instance_id": 1,
                                                           "lab_ref": "cisco-3x2"})
        sid = r.json()["session_id"]
        ws = await env.services.store.workspace((await env.services.store.session(sid))["workspace_id"])
        original_ticket = ws["ticket"]
        path = env.settings.labs_dir / "cisco-3x2"
        meta = json.loads((path / "meta.json").read_text())
        meta["resources"] = {"cores": 16, "memory_mb": 32768}
        (path / "meta.json").write_text(json.dumps(meta))
        ticket = json.loads((path / "ticket.json").read_text())
        ticket["nodes"][0]["ram_mb"] = 4096
        (path / "ticket.json").write_text(json.dumps(ticket))
        await env.run()
        s = await env.services.store.session(sid)
        assert s["status"] == "running", s
        assert env.pve.cts[s["slot_vmid"]]["config"]["memory"] == "16384"
        call = await env.services.store.current_call(s, ("deploy",))
        assert call["body"] == original_ticket


async def test_remote_agent_refuses_previous_allocation_commands(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        with command_scope(10, 4):
            await env.services.agent.claim("pve2", 201)
        with command_scope(11, 6):
            await env.services.agent.claim("pve2", 201)
        before = list(env.timeline)
        with command_scope(10, 4):
            with pytest.raises(Fenced):
                await env.services.agent.start("pve2", 201, student_key="placeholder")
        assert env.timeline == before


async def test_old_leader_cannot_commit_after_takeover(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        r = await env.client.post("/api/v1/sessions", json={"user_id": 1, "instance_id": 1,
                                                           "lab_ref": "cisco-3x2"})
        s = await env.services.store.session(r.json()["session_id"])
        await env.orch._try_allocate(s)
        s = await env.services.store.session(s["id"])
        newer = await env.services.store.next_epoch()
        with command_scope(env.orch.epoch, 2 * s["slot_generation"]):
            with pytest.raises(Fenced):
                await env.services.store.update_session(s, stage="ct_start")
            with pytest.raises(Fenced):
                await env.services.store.finish_session(s, status="stopped", message="stale release")
        assert (await env.services.store.session(s["id"]))["stage"] == "ct_prepare"
        with command_scope(newer, 2 * s["slot_generation"]):
            await env.services.store.update_session(s, stage="student_dir")


async def test_maintenance_preserves_uncertain_reservation_and_student(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        r = await env.client.post("/api/v1/sessions", json={"user_id": 1, "instance_id": 1,
                                                           "lab_ref": "cisco-3x2"})
        s = await env.services.store.session(r.json()["session_id"])
        await env.orch._try_allocate(s)
        s = await env.services.store.session(s["id"])
        await env.services.store.set_slot_student(s, s["student_key"])
        await env.services.store.finish_session(s, status="failed", message="uncertain", slot_state="failed")
        await env.services.store.set_slot_admin_state(s["slot_vmid"], "maintenance")
        slot = await env.services.store.slot(s["slot_vmid"])
        assert slot["student_key"] == s["student_key"] and slot["session_id"] == s["id"]
        assert len(await env.services.store.reservations()) == 1


async def test_claim_error_cannot_drop_epoch_check_during_cleanup(tmp_path, dsn):
    from lab_manager.agent_client import AgentRejected
    from lab_manager.ownership import current

    async with make_env(tmp_path, dsn) as env:
        r = await env.client.post("/api/v1/sessions", json={"user_id": 1, "instance_id": 1,
                                                           "lab_ref": "cisco-3x2"})
        s = await env.services.store.session(r.json()["session_id"])
        await env.orch._try_allocate(s)
        env.orch._claim = AsyncMock(side_effect=AgentRejected(403, "injected rejection"))
        await env.services.store.next_epoch()  # Another leader took over during the request.
        token = current.set(None)  # Production has no implicit test authority.
        try:
            with pytest.raises(Fenced):
                await env.orch.drive(s["id"])
        finally:
            current.reset(token)
        assert (await env.services.store.session(s["id"]))["status"] == "starting"
