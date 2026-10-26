"""Read-only audit probes against repository code; no servers or production data.

DB/PVE/agent doubles are explicit: these probes are not integration tests.
Run from the repository root: python tools/audit/check_pilot_hypotheses.py
Passing assertions reproduce known defects; they do not certify pilot readiness.
"""
import asyncio
import json
import tempfile
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx
import psycopg

# Resolve the local checkout rather than a globally installed manager package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "manager"))

from lab_manager.app import create_app
from lab_manager.agent_client import _classify
from lab_manager.orchestrator import Orchestrator
from lab_manager.slot_client import SlotClient


async def leader_loss():
    entered, resume, lost = asyncio.Event(), asyncio.Event(), asyncio.Event()
    s = dict(id="audit", status="stopping", stage="ct_stop", slot_vmid=201,
             slot_generation=1, student_key="main-42", stop_reason="user", error=None)
    state = "running"
    effects = []

    async def status(*_):
        if not entered.is_set():
            entered.set()
            await resume.wait()
        return state

    async def stop(*_, **__):
        nonlocal state
        effects.append({"action": "agent.stop", "leader": orch.leader})
        state = "stopped"
        return {"state": state}

    async def update(_s, **fields):
        s.update({k: v for k, v in fields.items() if k in s})

    async def finish(_s, **fields):
        s["status"] = fields["status"]

    store = NS(live_sessions=AsyncMock(return_value=[s]),
               session=AsyncMock(side_effect=lambda _: dict(s)),
               set_slot_student=AsyncMock(), update_session=update,
               finish_session=finish, event=AsyncMock())
    settings = NS(worker_interval=1, placeholder_student_key=None)
    async def sleep(_):
        lost.set()
        await asyncio.Event().wait()
    orch = Orchestrator(settings, store,
                        NS(find_ct_node=AsyncMock(return_value="pve1")),
                        NS(status=status, stop=stop), {}, None, sleep=sleep)

    class Conn:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        async def execute(self, sql, *_):
            if "pg_try_advisory_lock" in sql:
                return NS(fetchone=AsyncMock(return_value={"ok": True}))
            await entered.wait()
            raise psycopg.OperationalError("injected leader connection loss")

    runner = asyncio.create_task(orch.run_forever(AsyncMock(return_value=Conn())))
    await asyncio.wait_for(lost.wait(), 2)
    assert orch.leader is False
    assert any(not t.done() for t in orch._tasks.values())
    resume.set()
    await asyncio.wait_for(orch.drain(), 2)
    runner.cancel()
    await asyncio.gather(runner, return_exceptions=True)
    assert effects == [{"action": "agent.stop", "leader": False}]
    return effects


async def placement_race():
    """Two real placement calls see the same snapshot; allocation is a model
    of distinct-row SKIP LOCKED, not an execution of PostgreSQL SQL."""
    barrier = asyncio.Event()
    arrivals = 0
    free = [201, 202]
    reservations = []
    async def snapshot():
        nonlocal arrivals
        seen = list(reservations)
        arrivals += 1
        if arrivals == 2:
            barrier.set()
        await barrier.wait()
        return seen

    async def allocate(s, candidates):
        for vmid, node in candidates:
            if vmid in free:
                free.remove(vmid)
                reservations.append(dict(slot_vmid=vmid, slot_node=node, resources=s["resources"]))
                return vmid

    mib = 1024**2
    resources = [
        dict(type="node", node="pve1", status="online", maxmem=65536*mib, mem=0, maxcpu=8),
        *[dict(type="lxc", vmid=v, node="pve1", status="stopped",
               maxmem=16384*mib, mem=0, maxcpu=8) for v in free],
    ]
    settings = NS(node_memory_reserve_mb=2048, node_guest_memory_mb=16384, node_cpu_overcommit=1)
    store = NS(free_slots=AsyncMock(side_effect=lambda: list(free)),
               reservations=snapshot, allocate_slot=allocate)
    orch = Orchestrator(settings, store, NS(cluster_resources=AsyncMock(return_value=resources)), None, {}, None)
    s = dict(resources=dict(memory_mb=16384, cores=8))
    outcomes = await asyncio.gather(orch._try_allocate(s), orch._try_allocate(s))
    assert outcomes == [None, None]
    memory = sum(r["resources"]["memory_mb"] for r in reservations)
    cpu = sum(r["resources"]["cores"] for r in reservations)
    assert memory == 32768 and cpu == 16
    return dict(model="real placement, modeled SQL allocator", slots=[r["slot_vmid"] for r in reservations],
                memory_mb=memory, memory_budget_mb=16384, vcpus=cpu, cpu_budget=8)


async def park_disabled():
    store = NS(finish_session=AsyncMock())
    orch = Orchestrator(NS(placeholder_student_key=None), store, None, None, {}, None)
    await orch._stage_ct_park(dict(id="audit", error=None, stop_reason="user"))
    result = store.finish_session.call_args.kwargs
    assert result["slot_state"] == "free"
    return result


async def ready_without_leader():
    @asynccontextmanager
    async def connection():
        yield object()
    with tempfile.TemporaryDirectory() as root:
        svc = NS(settings=NS(labs_dir=Path(root)), store=NS(), pool=NS(connection=connection),
                 orchestrator=NS(leader=False, last_tick=None))
        app = create_app(svc, run_orchestrator=False)
        app.state.orchestrator_task = NS(done=lambda: False)
        with patch("lab_manager.app.schema_version", AsyncMock(return_value=5)), \
                patch("lab_manager.app.latest_version", return_value=5):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://audit") as client:
                r = await client.get("/readyz")
        assert r.status_code == 200
        assert r.json()["checks"]["orchestrator"] == dict(ok=True, leader=False, fresh=False)
        return dict(http_status=r.status_code, body=r.json())


async def error_detail_loss():
    client = NS(lab_status=AsyncMock(return_value=dict(
        state="FAILED", error=dict(code="RuntimeError", message="specific node/device failure"))))
    store = NS(current_call=AsyncMock(return_value={"action": "deploy"}), update_session=AsyncMock())
    orch = Orchestrator(NS(), store, None, None, {201: client}, None)
    s = dict(id="audit", stage="ready", status="running", slot_vmid=201,
             stop_requested=False, expires_at=None, project_id="project", error=None)
    await orch.step(s)
    error = store.update_session.call_args.kwargs["error"]
    assert "specific node/device failure" not in json.dumps(error)
    return error


async def plaintext():
    captured = []
    def transport(req):
        captured.append(dict(scheme=req.url.scheme, auth=req.headers.get("authorization")))
        return httpx.Response(200, json={})
    client = SlotClient("slot.invalid", 8080, "dummy-audit-token", transport=httpx.MockTransport(transport))
    try:
        await client.healthz()
    finally:
        await client.close()
    assert captured == [dict(scheme="http", auth="Bearer dummy-audit-token")]
    return captured


async def owner_contract():
    old = _classify(httpx.Response(409, json={"detail": "Existing student directory has a different owner"}))
    actual = _classify(httpx.Response(409, json={"detail": {
        "code": "STUDENT_DIR_OWNER", "message": "Existing student directory has a different owner"}}))
    result = dict(test_double=type(old).__name__, actual_agent_payload=type(actual).__name__)
    assert result == dict(test_double="AgentCommandFailed", actual_agent_payload="AgentRejected")
    return result


async def sizing_drift():
    config = {"cores": "4", "memory": "8192"}
    s = dict(id="audit", stop_requested=False, slot_vmid=201, lab_ref="catalog-lab",
             resources={"cores": 4, "memory_mb": 8192})
    async def set_config(_vmid, wanted):
        config.update(wanted)
    store = NS(update_session=AsyncMock(), event=AsyncMock())
    pve = NS(find_ct_node=AsyncMock(return_value="pve1"),
             ct_config=AsyncMock(side_effect=lambda _: dict(config)),
             ct_set_config=set_config)
    catalog = NS(get=lambda _: NS(resources={"cores": 8, "memory_mb": 16384}))
    orch = Orchestrator(NS(), store, pve, NS(status=AsyncMock(return_value="stopped")), {}, catalog)
    await orch._stage_ct_prepare(s)
    assert s["resources"]["memory_mb"] == 8192 and config["memory"] == "16384"
    return dict(reserved=s["resources"], actual_config=config)


async def main():
    for check in (leader_loss, placement_race, park_disabled, ready_without_leader,
                  error_detail_loss, plaintext, owner_contract, sizing_drift):
        print(json.dumps(dict(check=check.__name__, observed=await check()), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
