import asyncio

import pytest
from conftest import make_env

from lab_manager.store import Fenced


async def new_session(store, user_id, instance_id=1, lab_ref="cisco-3x2"):
    return await store.create_session(
        site="main", user_id=user_id, instance_id=instance_id, course_id=5, lab_ref=lab_ref,
        max_duration=60, student_key=f"main-{user_id}", resources={"memory_mb": 1024})


def cand(*vmids):
    return [(v, "pve1") for v in vmids]


async def test_create_session_is_idempotent_under_concurrency(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        store = env.services.store
        results = await asyncio.gather(*(new_session(store, 7) for _ in range(8)))
        assert len({s["id"] for s, _ in results}) == 1
        assert sum(created for _, created in results) == 1


async def test_one_project_per_workspace(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        store = env.services.store
        a, _ = await new_session(store, 1)
        await store.finish_session(a, status="stopped", message="x")
        b, _ = await new_session(store, 1)
        assert a["project_id"] == b["project_id"]  # The next session continues the same project
        assert b["project_name"] == f"cisco-3x2-w{b['workspace_id']}"
        await store.finish_session(b, status="stopped", message="x")
        c, _ = await new_session(store, 1, instance_id=2)
        assert c["project_id"] != a["project_id"]  # Another activity: another workspace


async def test_concurrent_allocation_gives_distinct_slots(tmp_path, dsn):
    vmids = tuple(range(201, 206))
    async with make_env(tmp_path, dsn, vmids=vmids) as env:
        store = env.services.store
        sessions = [(await new_session(store, uid))[0] for uid in range(1, 9)]
        got = await asyncio.gather(*(store.allocate_slot(s, cand(*vmids)) for s in sessions))
        allocated = [v for v in got if v is not None]
        assert sorted(allocated) == list(vmids)  # 5 slots, 8 students: 5 distinct winners
        assert got.count(None) == 3
        rows = {r["vmid"]: r for r in await store.slots()}
        assert all(rows[v]["state"] == "preparing" and rows[v]["generation"] == 2 for v in vmids)


async def test_candidate_order_is_respected(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201, 202, 203)) as env:
        store = env.services.store
        s, _ = await new_session(store, 1)
        assert await store.allocate_slot(s, [(203, "pve3"), (201, "pve1")]) == 203
        assert (await store.session(s["id"]))["slot_node"] == "pve3"
        s2, _ = await new_session(store, 2)
        assert await store.allocate_slot(s2, [(203, "pve3"), (201, "pve1")]) == 201
        s3, _ = await new_session(store, 3)
        assert await store.allocate_slot(s3, []) is None


async def test_same_session_is_not_allocated_twice(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        store = env.services.store
        s, _ = await new_session(store, 1)
        got = await asyncio.gather(*(store.allocate_slot(s, cand(201, 202)) for _ in range(4)))
        assert len([v for v in got if v]) == 1


async def test_slots_are_not_bound_to_students(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        store = env.services.store
        a, _ = await new_session(store, 1)
        assert await store.allocate_slot(a, cand(201, 202)) == 201
        await store.finish_session(await store.session(a["id"]), status="stopped", message="done")
        b, _ = await new_session(store, 2)
        assert await store.allocate_slot(b, cand(201, 202)) == 201  # Same slot, other student
        a2, _ = await new_session(store, 1)
        assert await store.allocate_slot(a2, cand(201, 202)) == 202  # Student 1 just takes another
        assert {r["slot_vmid"] for r in await store.reservations()} == {201, 202}


async def test_fencing_rejects_stale_worker(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        store = env.services.store
        s, _ = await new_session(store, 1)
        await store.allocate_slot(s, cand(201))
        stale = await store.session(s["id"])
        await store.update_session(stale, stage="ct_start")  # Current generation: fine
        await store.finish_session(stale, status="stopped", message="released")  # Bumps generation
        with pytest.raises(Fenced):
            await store.update_session(stale, stage="slot_ready")
        s2, _ = await new_session(store, 2)
        assert await store.allocate_slot(s2, cand(201)) == 201
        with pytest.raises(Fenced):
            await store.finish_session(stale, status="failed", message="late")
        assert (await store.slot(201))["session_id"] == s2["id"]


async def test_admin_state_refuses_slot_in_use(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        store = env.services.store
        s, _ = await new_session(store, 1)
        await store.allocate_slot(s, cand(201))
        with pytest.raises(ValueError):
            await store.set_slot_admin_state(201, "maintenance")


async def test_new_slots_start_in_maintenance(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        from lab_manager.config import SlotConfig

        extra = SlotConfig(vmid=301, address="10.0.1.1", api_token="b" * 40)
        await env.services.store.sync_slots([*env.settings.slots.values(), extra])
        assert (await env.services.store.slot(301))["state"] == "maintenance"
        assert await env.services.store.free_slots() == [201]
        await env.services.store.sync_slots([extra])
        assert (await env.services.store.slot(201))["in_config"] is False
        assert await env.services.store.free_slots() == []
