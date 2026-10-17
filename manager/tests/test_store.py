import asyncio

import pytest
from conftest import make_env

from lab_manager.labs import project_name
from lab_manager.store import Fenced


async def new_session(store, user_id, instance_id=1, lab_ref="cisco-3x2"):
    s, created = await store.create_session(
        site="main", user_id=user_id, instance_id=instance_id, course_id=5, lab_ref=lab_ref,
        max_duration=60, project_name=lambda wid: project_name(lab_ref, wid))
    return s, created


async def test_create_session_is_idempotent_under_concurrency(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        store = env.services.store
        results = await asyncio.gather(*(new_session(store, 7) for _ in range(8)))
        ids = {s["id"] for s, _ in results}
        assert len(ids) == 1
        assert sum(created for _, created in results) == 1


async def test_concurrent_allocation_gives_distinct_slots(tmp_path, dsn):
    vmids = tuple(range(201, 206))
    async with make_env(tmp_path, dsn, vmids=vmids) as env:
        store = env.services.store
        sessions = [(await new_session(store, uid))[0] for uid in range(1, 9)]
        got = await asyncio.gather(*(store.allocate_slot(s, profile="default") for s in sessions))
        allocated = [v for v in got if v is not None]
        assert sorted(allocated) == list(vmids)  # 5 slots, 8 students: 5 distinct winners
        assert got.count(None) == 3
        rows = {r["vmid"]: r for r in await store.slots()}
        assert all(rows[v]["state"] == "preparing" and rows[v]["generation"] == 2 for v in vmids)


async def test_same_session_is_not_allocated_twice(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        store = env.services.store
        s, _ = await new_session(store, 1)
        got = await asyncio.gather(*(store.allocate_slot(s, profile="default") for _ in range(4)))
        assert len([v for v in got if v]) == 1


async def test_pinned_workspace_waits_for_its_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        store = env.services.store
        a, _ = await new_session(store, 1)
        assert await store.allocate_slot(a, profile="default") == 201
        a = await store.session(a["id"])
        await store.finish_session(a, status="stopped", message="done")
        b, _ = await new_session(store, 2)
        assert await store.allocate_slot(b, profile="default") == 202  # Least pinned slot first
        b = await store.session(b["id"])
        # Student 1 returns: pinned to 201, which is free -> gets 201 even though 202 is busy.
        a2, _ = await new_session(store, 1)
        assert await store.allocate_slot(a2, profile="default") == 201
        await store.finish_session(await store.session(a2["id"]), status="stopped", message="done")
        # Slot 201 into maintenance: student 1 waits, does not move to another slot.
        await store.set_slot_admin_state(201, "maintenance")
        await store.finish_session(b, status="stopped", message="done")
        a3, _ = await new_session(store, 1)
        assert await store.allocate_slot(a3, profile="default") is None


async def test_profile_is_respected(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        store = env.services.store
        s, _ = await new_session(store, 1)
        assert await store.allocate_slot(s, profile="big") is None


async def test_fencing_rejects_stale_worker(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        store = env.services.store
        s, _ = await new_session(store, 1)
        await store.allocate_slot(s, profile="default")
        stale = await store.session(s["id"])
        await store.update_session(stale, stage="mount")  # Current generation: fine
        await store.finish_session(stale, status="stopped", message="released")  # Bumps generation
        with pytest.raises(Fenced):
            await store.update_session(stale, stage="ct_start")
        s2, _ = await new_session(store, 2)
        assert await store.allocate_slot(s2, profile="default") == 202
        with pytest.raises(Fenced):
            await store.finish_session(stale, status="failed", message="late")
        slot = await store.slot(201)
        assert slot["state"] == "free"


async def test_admin_state_refuses_slot_in_use(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        store = env.services.store
        s, _ = await new_session(store, 1)
        await store.allocate_slot(s, profile="default")
        with pytest.raises(ValueError):
            await store.set_slot_admin_state(201, "maintenance")


async def test_new_slots_start_in_maintenance(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        from lab_manager.config import SlotConfig

        extra = SlotConfig(vmid=301, address="10.0.1.1", api_token="b" * 40)
        await env.services.store.sync_slots([*env.settings.slots.values(), extra])
        assert (await env.services.store.slot(301))["state"] == "maintenance"
        await env.services.store.sync_slots([extra])
        assert (await env.services.store.slot(201))["in_config"] is False
