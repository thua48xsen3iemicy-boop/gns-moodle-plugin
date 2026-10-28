from datetime import timedelta

import pytest
from conftest import make_env
from fakes import GIB

from lab_manager.admin import quarantine_report
from lab_manager.agent_client import start_budget_warnings
from lab_manager.orchestrator import QUARANTINE_MESSAGE, Orchestrator


async def start(env, user_id=42, instance_id=1):
    r = await env.client.post("/api/v1/sessions", json={"user_id": user_id, "instance_id": instance_id,
                                                        "lab_ref": "cisco-3x2", "course_id": 3})
    assert r.status_code in (200, 201), r.text
    return r.json()["session_id"]


async def stop(env, sid):
    r = await env.client.post(f"/api/v1/sessions/{sid}/stop")
    assert r.status_code == 200, r.text


async def session(env, sid):
    return await env.services.store.session(sid)


def names(timeline):
    return [t[0] for t in timeline]


def new_orchestrator(env):
    sv = env.services
    orch = Orchestrator(env.settings, sv.store, sv.pve, sv.agent, sv.slots, sv.catalog)
    orch.leader = True
    orch.epoch = env.orch.epoch
    return orch


async def test_full_cycle_order(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.pve.cts[201]["status"] = "running"  # A free slot left running is stopped first
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "running", s["status_message"]
        assert s["slot_vmid"] == 201
        assert names(env.timeline) == ["agent:stop", "agent:mounts", "agent:size", "agent:student", "agent:start",
                                       "slot:readyz", "slot:deploy"]
        assert ("agent:student", "main-42") in env.timeline
        config = env.pve.cts[201]["config"]
        assert config["cores"] == "8" and config["memory"] == "16384"  # Sized to the lab
        assert config["mp1"] == "/mnt/pve/cfs/gns3-rw/students/main-42,mp=/opt/gns3/projects,ro=0,shared=1"
        assert config["mp0"] == "/mnt/pve/cfs/gns3-ro/slot,mp=/srv/gns3-ro/slot,ro=1,shared=1"
        start_req = [r for r in env.agent.requests if r[2].endswith("/start")][0]
        assert start_req[3] == {"code_revision": None, "student_key": "main-42", "data_mode": "rw",
                                "actions": ["init_slot"]}
        slot = await env.services.store.slot(201)
        assert slot["state"] == "busy" and slot["session_id"] == sid
        nodes = s["topology"]["topology"]["nodes"]
        assert {n["name"] for n in nodes} == {"SW1", "SW2", "SW3", "D1", "D2", "D3", "D4", "D5", "D6"}
        assert all(n["properties"] == {} and "console" not in n for n in nodes)

        env.timeline.clear()
        await stop(env, sid)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "stopped"
        assert env.timeline == [("slot:stop", "10.0.0.1"), ("agent:stop", "shutdown"), ("agent:reset", 201),
                                ("agent:mounts", "placeholder", "ro")]  # Rolled back, then parked
        assert "students/placeholder,mp=/opt/gns3/projects,ro=1" in env.pve.cts[201]["config"]["mp1"]
        slot = await env.services.store.slot(201)
        assert slot["state"] == "free" and slot["session_id"] is None


async def test_student_is_not_bound_to_a_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid = await start(env)
        await env.run()
        assert (await session(env, sid))["slot_vmid"] == 201
        await stop(env, sid)
        await env.run()
        other = await start(env, user_id=7)  # Someone else takes slot 201 now
        await env.run()
        assert (await session(env, other))["slot_vmid"] == 201
        again = await start(env)  # The first student just gets another slot, no waiting
        await env.run()
        s = await session(env, again)
        assert s["status"] == "running" and s["slot_vmid"] == 202
        assert "students/main-42," in env.pve.cts[202]["config"]["mp1"]


async def _continue_on_another_slot(env):
    sid = await start(env)
    await env.run()
    first = await session(env, sid)
    await stop(env, sid)
    await env.run()
    await start(env, user_id=7)  # Slot 201 is taken by someone else now
    await env.run()
    again = await start(env)
    await env.run()
    s = await session(env, again)
    assert s["project_id"] == first["project_id"] and s["slot_vmid"] == 202
    return first, s


async def test_project_continues_on_another_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        first, s = await _continue_on_another_slot(env)
        assert s["status"] == "running"
        # The same GNS3 project from the student directory: same nodes, nothing recreated.
        ids = lambda x: {n["name"]: n["node_id"] for n in x["topology"]["topology"]["nodes"]}  # noqa: E731
        assert ids(s) == ids(first)
        assert len(env.projects) == 2  # One project per student and lab, not one per session


async def test_slot_03_cannot_continue_a_project_from_another_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn, student_state=False) as env:
        _, s = await _continue_on_another_slot(env)
        # Lab Slot 0.3 kept its lab record in its own SQLite: another slot refuses the project.
        assert s["status"] == "failed" and s["error"]["code"] == "SLOT_COLD_RESUME_UNSUPPORTED"


async def test_project_of_slot_03_on_slot_041_and_missing_project(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        sid = await start(env)
        await env.run()
        await stop(env, sid)
        await env.run()
        pid = str((await session(env, sid))["project_id"])
        env.slots[201].labs.clear()  # Another slot (or a restarted one) sees only the directory
        env.projects[pid]["student_state"] = False  # Created by slot 0.3: no record in the directory
        again = await start(env)
        await env.run()
        s = await session(env, again)
        assert s["status"] == "failed" and s["error"]["code"] == "SLOT_COLD_RESUME_UNSUPPORTED"
        assert (await env.services.store.slot(201))["state"] == "free"  # The slot is not at fault

        env.slots[201].labs.clear()
        env.projects[pid].update(student_state=True, missing=True)  # Record without the project
        again = await start(env)
        await env.run()
        s = await session(env, again)
        assert s["status"] == "failed" and s["error"]["code"] == "PROJECT_MISSING"
        assert (await env.services.store.slot(201))["state"] == "free"


async def test_project_continues_on_the_same_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        sid = await start(env)
        await env.run()
        pid = (await session(env, sid))["project_id"]
        await stop(env, sid)
        await env.run()
        again = await start(env)
        await env.run()
        s = await session(env, again)
        assert s["status"] == "running" and s["project_id"] == pid
        assert list(env.slots[201].labs) == [str(pid)]  # The same GNS3 project, not a new one


async def test_placement_spreads_labs_round_the_nodes(tmp_path, dsn):
    nodes = {201: "pve1", 202: "pve1", 203: "pve2", 204: "pve2", 205: "pve3", 206: "pve3"}
    async with make_env(tmp_path, dsn, vmids=tuple(nodes), nodes_of=nodes) as env:
        got = []
        for user in range(1, 5):
            sid = await start(env, user_id=user)
            await env.run(1)
            got.append((await session(env, sid))["slot_vmid"])
        # The first slot of every node, then the second slot of the first node.
        assert got == [201, 203, 205, 202]


async def test_placement_skips_a_node_without_room_in_its_turn(tmp_path, dsn):
    nodes = {201: "pve1", 202: "pve1", 203: "pve2"}
    async with make_env(tmp_path, dsn, vmids=tuple(nodes), nodes_of=nodes, node_memory_reserve_mb=0) as env:
        env.pve.node_mem["pve2"]["mem"] = 64 * GIB - 10 * GIB  # 10 GiB free: a 16 GiB lab does not fit
        a, b = await start(env, user_id=1), await start(env, user_id=2)
        await env.run(1)
        assert {(await session(env, a))["slot_vmid"], (await session(env, b))["slot_vmid"]} == {201, 202}


async def test_node_guest_memory_limit_keeps_room_for_the_host(tmp_path, dsn):
    nodes = {201: "pve1", 202: "pve1", 203: "pve2"}
    async with make_env(tmp_path, dsn, vmids=tuple(nodes), nodes_of=nodes,
                        node_guest_memory_mb=40 * 1024) as env:
        # A running VM that is not a slot (Moodle) counts with its memory limit: 30 + 16 > 40 GiB.
        env.pve.vms[900] = {"node": "pve2", "status": "running", "maxmem": 30 * GIB, "maxcpu": 4}
        got = []
        for user in (1, 2, 3):
            sid = await start(env, user_id=user)
            await env.run(1)
            got.append(await session(env, sid))
        assert [s["slot_vmid"] for s in got[:2]] == [201, 202]  # pve1: 16 + 16 <= 40 GiB
        # pve1 still has live room (56 - 32 GiB) but 48 GiB of guests would cross its limit.
        assert got[2]["status"] == "queued" and got[2]["status_message"] == "Недостаточно ресурсов на узлах, ожидание…"


async def test_node_cpu_overcommit_limits_vcpus(tmp_path, dsn):
    nodes = {201: "pve1", 202: "pve1", 203: "pve1", 204: "pve2"}
    async with make_env(tmp_path, dsn, vmids=tuple(nodes), nodes_of=nodes, node_cpu_overcommit=1) as env:
        env.pve.add_ct(900, node="pve2", status="running", config={"cores": 12, "memory": 4096})  # Not a slot
        got = []
        for user in (1, 2, 3):
            sid = await start(env, user_id=user)
            await env.run(1)
            got.append(await session(env, sid))
        # 16 CPUs x 1: pve1 takes two 8-core labs, pve2 has 12 cores in use already.
        assert [s["slot_vmid"] for s in got[:2]] == [201, 202]
        assert got[2]["status"] == "queued"
        env.pve.cts[900]["status"] = "stopped"  # Its cores are free again: the queued lab fits pve2
        await env.run(1)
        assert (await session(env, got[2]["id"]))["slot_vmid"] == 204


async def test_offline_node_is_skipped(tmp_path, dsn):
    async with make_env(tmp_path, dsn, nodes_of={201: "pve1", 202: "pve2"}) as env:
        env.pve.node_mem["pve1"]["status"] = "offline"
        sid = await start(env)
        await env.run()
        assert (await session(env, sid))["slot_vmid"] == 202


async def test_maxduration_stops_and_releases(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        await env.services.store.update_session(s, expires_at=s["started_at"] - timedelta(seconds=1))
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "stopped" and s["status_message"] == "Время лабы истекло"
        assert (await env.services.store.slot(201))["state"] == "free"


async def test_slot_failed_operation_fails_session_and_frees_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.slots[201].fail_next = {"code": "RuntimeError", "message": "Missing QEMU image"}
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed" and "Missing QEMU image" in s["status_message"]
        slot = await env.services.store.slot(201)
        # The lab is to blame, not the slot: rolled back and parked, it is free again, no operator.
        assert slot["state"] == "free" and slot["student_key"] is None
        assert ("agent:reset", 201) in env.timeline
        # The student's directory is released: lab stopped, CT stopped and parked.
        assert env.slots[201].labs[str(s["project_id"])]["state"] == "STOPPED"
        assert env.pve.cts[201]["status"] == "stopped"
        assert "students/placeholder," in env.pve.cts[201]["config"]["mp1"]
        again = await start(env)  # The student can start right away
        await env.run()
        assert (await session(env, again))["slot_vmid"] in (201, 202)


async def test_slot_lab_failing_while_running(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid = await start(env)
        await env.run()
        env.slots[201].restart()
        env.orch._health_checked.clear()
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "SLOT_LAB_FAILED"
        assert (await env.services.store.slot(201))["state"] == "free"  # A FAILED lab is the lab's problem
        assert env.pve.cts[201]["status"] == "stopped"  # The FAILED lab is stopped, the CT too
        assert ("agent:reset", 201) in env.timeline


async def test_stop_before_slot_is_allocated(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        await start(env, user_id=7)  # The only slot is busy: a queue, not "no slots"
        await env.run()
        sid = await start(env)
        await env.run(2)
        assert (await session(env, sid))["status"] == "queued"
        await stop(env, sid)
        await env.run(2)
        assert (await session(env, sid))["status"] == "stopped"


async def test_stop_during_start_cleans_up_without_deploy(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.slots[201].ready = False
        sid = await start(env)
        await env.run(5)
        assert (await session(env, sid))["stage"] == "slot_ready"
        await stop(env, sid)
        await env.run()
        assert (await session(env, sid))["status"] == "stopped"
        assert env.slots[201].labs == {}
        assert env.pve.cts[201]["status"] == "stopped"
        assert "students/placeholder," in env.pve.cts[201]["config"]["mp1"]


async def test_lost_start_response_is_recovered_without_second_start(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.agent.lose_start_response = True  # pct start happened, the HTTP answer was lost
        sid = await start(env)
        await env.run()
        assert (await session(env, sid))["status"] == "running"
        assert names(env.timeline).count("agent:start") == 1


async def test_restart_of_manager_mid_start(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.slots[201].op_polls = 10**6
        sid = await start(env)
        await env.run(6)
        assert (await session(env, sid))["stage"] == "lab_wait"
        for op in env.slots[201].ops.values():
            op["remaining"] = 0
        fresh = new_orchestrator(env)  # A new manager process with only the database
        for _ in range(10):
            await fresh.tick()
            await fresh.drain()
        assert (await session(env, sid))["status"] == "running"
        assert names(env.timeline).count("agent:start") == 1
        assert names(env.timeline).count("slot:deploy") == 1


@pytest.mark.parametrize("mp0,mp1", [
    # Another student's directory.
    ("/mnt/pve/cfs/gns3-ro/slot,mp=/srv/gns3-ro/slot,ro=1,shared=1",
     "/mnt/pve/cfs/gns3-rw/students/main-7,mp=/opt/gns3/projects,ro=0,shared=1"),
    # Right suffix under a foreign root.
    ("/mnt/pve/cfs/gns3-ro/slot,mp=/srv/gns3-ro/slot,ro=1,shared=1",
     "/mnt/other/students/main-42,mp=/opt/gns3/projects,ro=0,shared=1"),
    # Right source, wrong destination.
    ("/mnt/pve/cfs/gns3-ro/slot,mp=/srv/gns3-ro/slot,ro=1,shared=1",
     "/mnt/pve/cfs/gns3-rw/students/main-42,mp=/srv/student,ro=0,shared=1"),
    # Read-only student directory.
    ("/mnt/pve/cfs/gns3-ro/slot,mp=/srv/gns3-ro/slot,ro=1,shared=1",
     "/mnt/pve/cfs/gns3-rw/students/main-42,mp=/opt/gns3/projects,ro=1,shared=1"),
    # Code at the old /opt/slot, or writable code.
    ("/mnt/pve/cfs/gns3-ro/slot,mp=/opt/slot,ro=1,shared=1",
     "/mnt/pve/cfs/gns3-rw/students/main-42,mp=/opt/gns3/projects,ro=0,shared=1"),
    ("/mnt/pve/cfs/gns3-ro/slot,mp=/srv/gns3-ro/slot,ro=0,shared=1",
     "/mnt/pve/cfs/gns3-rw/students/main-42,mp=/opt/gns3/projects,ro=0,shared=1"),
])
async def test_running_ct_with_unexpected_mounts_is_never_used(tmp_path, dsn, mp0, mp1):
    async with make_env(tmp_path, dsn) as env:
        sid = await start(env)
        await env.orch.step(await session(env, sid))  # Only the allocation step
        s = await session(env, sid)
        assert s["stage"] == "ct_prepare"
        await env.services.store.update_session(s, stage="ct_start")
        env.pve.cts[201]["status"] = "running"
        env.pve.cts[201]["config"].update(mp0=mp0, mp1=mp1)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "WRONG_STUDENT_MOUNT"
        assert env.slots[201].labs == {}


async def test_custom_ceph_layout_follows_agent(tmp_path, dsn):
    layout = dict(agent_ceph_root="/mnt/pve/data", agent_code_dir="code/slot", agent_students_dir="rw/students")
    async with make_env(tmp_path, dsn, slot_code_revision="r1", **layout) as env:
        env.agent.ceph_root, env.agent.code_dir, env.agent.students_dir = layout.values()
        sid = await start(env)
        await env.run()
        assert (await session(env, sid))["status"] == "running"
        config = env.pve.cts[201]["config"]
        assert config["mp0"] == "/mnt/pve/data/code/slot/r1,mp=/srv/gns3-ro/slot,ro=1,shared=1"
        assert config["mp1"] == "/mnt/pve/data/rw/students/main-42,mp=/opt/gns3/projects,ro=0,shared=1"


async def test_agent_busy_and_migration_are_waited_out(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.agent.busy = True
        sid = await start(env)
        await env.run(4)
        assert (await session(env, sid))["stage"] == "ct_prepare"
        env.agent.busy = False
        env.pve.cts[201]["node"] = "pve3"  # Migrated while waiting
        await env.run()
        assert (await session(env, sid))["error"]["code"] == "SLOT_NODE_CHANGED"
        assert all(r[0] == "pve3" for r in env.agent.requests if r[2].endswith("/start"))


async def test_agent_rejection_fails_session(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        env.agent.untagged = {201}
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "AGENT_REJECTED"
        assert "not marked as a lab slot" in s["status_message"]


async def test_student_dir_with_wrong_owner_fails_immediately(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        env.agent.wrong_owner = {"main-42"}
        sid = await start(env)
        await env.run(4)
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "STUDENT_DIR_REJECTED"
        async with env.services.pool.connection() as conn, conn.transaction():
            await conn.execute("UPDATE sessions SET stage_started_at = now() - interval '1 hour' WHERE id = %s",
                               (sid,))
        await env.run(2)
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "STUDENT_DIR_REJECTED"
        assert (await env.services.store.slot(201))["state"] == "free"
        assert env.pve.cts[201]["status"] == "stopped"


async def test_student_dir_not_configured_on_agent_waits(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.agent.student_ids_configured = False  # 503 until LAB_STUDENT_HOST_UID/GID are set
        sid = await start(env)
        await env.run(4)
        assert (await session(env, sid))["stage"] == "student_dir"
        env.agent.student_ids_configured = True
        await env.run()
        assert (await session(env, sid))["status"] == "running"


async def test_failed_park_puts_slot_into_maintenance(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        sid = await start(env)
        await env.run()
        env.agent.park_fails = True
        await stop(env, sid)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "stopped"  # For the student the lab did stop
        slot = await env.services.store.slot(201)
        assert slot["state"] == "maintenance"  # The placeholder is not confirmed
        assert "mp1" not in env.pve.cts[201]["config"]  # The rollback took the student's directory away
        other = await start(env, user_id=7)
        await env.run(3)
        s = await session(env, other)  # Never handed out unparked; nothing else to run on
        assert s["status"] == "failed" and s["error"]["code"] == "NO_SLOTS" and s["slot_vmid"] is None


async def test_shutdown_error_is_rechecked_before_force(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid = await start(env)
        await env.run()
        env.agent.shutdown_fails_but_stops = True
        env.timeline.clear()
        await stop(env, sid)
        await env.run()
        assert (await session(env, sid))["status"] == "stopped"
        assert ("agent:stop", "force") not in env.timeline


@pytest.mark.parametrize("error_codes", [False, True])
async def test_missing_student_dir_fails_session_but_frees_slot(tmp_path, dsn, error_codes):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        env.agent.missing_students = {"main-42"}
        env.agent.error_codes = error_codes
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "STUDENT_DIR_MISSING"
        assert env.pve.cts[201]["status"] == "stopped"
        assert "students/placeholder," in env.pve.cts[201]["config"]["mp1"]  # Parked before release
        assert (await env.services.store.slot(201))["state"] == "free"
        other = await start(env, user_id=7)
        await env.run()
        assert (await session(env, other))["status"] == "running"


async def test_start_failure_on_node_fails_session(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.agent.start_fails = "startup for container '201' failed"
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "SLOT_START_FAILED"


async def test_hanging_shutdown_falls_back_to_hard_stop(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid = await start(env)
        await env.run()
        env.agent.shutdown_hangs = True
        env.timeline.clear()
        await stop(env, sid)
        await env.run()
        assert (await session(env, sid))["status"] == "stopped"
        assert ("agent:stop", "shutdown") in env.timeline and ("agent:stop", "force") in env.timeline


async def test_recovery_after_deploy_submitted_replays_same_key(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.slots[201].op_polls = 10**6
        sid = await start(env)
        await env.run(6)
        s = await session(env, sid)
        assert s["stage"] == "lab_wait"
        keys = [k for m, p, k in env.slots[201].requests if p == "/v1/labs/deploy"]
        assert len(keys) == 1
        call = await env.services.store.current_call(s, ("deploy",))
        await env.services.store.update_call(call["id"], status="pending")  # Crash right after POST
        await env.services.store.update_session(s, stage="lab_submit")
        for op in env.slots[201].ops.values():
            op["remaining"] = 0
        await env.run()
        assert (await session(env, sid))["status"] == "running"
        keys2 = [k for m, p, k in env.slots[201].requests if p == "/v1/labs/deploy"]
        assert keys2 == keys * 2  # Same Idempotency-Key replayed, one operation
        assert len(env.slots[201].ops) == 1


async def _replay_after_lab_changed(env, lab_state):
    """Running session; the lab changes behind our back; the stored deploy key is replayed."""
    sid = await start(env)
    await env.run()
    s = await session(env, sid)
    pid = next(iter(env.slots[201].labs))
    env.slots[201].labs[pid]["state"] = lab_state
    call = await env.services.store.current_call(s, ("deploy",))
    await env.services.store.update_call(call["id"], status="pending")
    await env.services.store.update_session(s, stage="lab_submit", status="starting")
    await env.run()
    return sid, call


async def test_stale_idempotency_key_gets_new_key(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid, old = await _replay_after_lab_changed(env, "STOPPED")
        assert (await session(env, sid))["status"] == "running"
        deploys = [k for m, p, k in env.slots[201].requests if p == "/v1/labs/deploy"]
        assert deploys[:2] == [old["idempotency_key"]] * 2  # Replayed once, answered STALE
        assert len(deploys) == 3 and deploys[2] != old["idempotency_key"]  # New intentional key


async def test_stale_key_on_failed_lab_goes_to_operator(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid, _ = await _replay_after_lab_changed(env, "FAILED")
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "SLOT_LAB_FAILED"


async def test_operation_in_progress_waits(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.slots[201].ops["x"] = {"operation_id": "x", "project_id": "p", "action": "stop",
                                   "status": "RUNNING", "fingerprint": "", "remaining": 0, "error": None}
        sid = await start(env)
        await env.run(8)
        s = await session(env, sid)
        assert s["status"] == "starting" and s["stage"] == "lab_submit"
        del env.slots[201].ops["x"]
        await env.run()
        assert (await session(env, sid))["status"] == "running"


async def test_slot_unreachable_during_start_waits_then_continues(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.slots[201].reachable = False
        sid = await start(env)
        await env.run(5)
        s = await session(env, sid)
        assert s["status"] == "starting" and s["stage"] == "slot_ready"
        env.slots[201].reachable = True
        await env.run()
        assert (await session(env, sid))["status"] == "running"


async def test_slot_never_ready_times_out_and_keeps_slot_for_operator(tmp_path, dsn):
    async with make_env(tmp_path, dsn, slot_ready_timeout=10) as env:
        env.slots[201].ready = False
        sid = await start(env)
        await env.run(5)
        s = await session(env, sid)
        assert s["stage"] == "slot_ready" and "Слот не готов" in s["status_message"]
        async with env.services.pool.connection() as conn, conn.transaction():
            await conn.execute("UPDATE sessions SET stage_started_at = now() - interval '1 hour' WHERE id = %s",
                               (sid,))
        await env.run(2)
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "TIMEOUT"
        assert (await env.services.store.slot(201))["state"] == "failed"
        assert ("agent:reset", 201) not in env.timeline  # Kept as it is for the operator


async def test_proxmox_unavailable_keeps_session_queued(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.pve.down_hosts = {"pve1", "pve2", "pve3"}
        sid = await start(env)
        await env.run(3)
        s = await session(env, sid)
        assert s["status"] == "queued" and s["status_message"] == "Proxmox недоступен, ожидание…"
        env.pve.down_hosts = set()
        env.services.pve._down_until.clear()
        await env.run()
        assert (await session(env, sid))["status"] == "running"


async def test_running_slot_keeps_its_unused_promised_memory_reserved(tmp_path, dsn):
    nodes = {201: "pve2", 202: "pve2", 203: "pve1"}
    async with make_env(tmp_path, dsn, vmids=(201, 202, 203), nodes_of=nodes, node_memory_reserve_mb=0) as env:
        env.pve.node_mem["pve1"]["mem"] = 64 * GIB - 10 * GIB  # 10 GiB free: never fits
        env.pve.node_mem["pve2"]["mem"] = 64 * GIB - 30 * GIB  # 30 GiB free
        a = await start(env, user_id=1)
        await env.run()
        assert (await session(env, a))["slot_vmid"] in (201, 202)
        # CT running but GNS3 devices use only 2 GiB so far; node usage shows those 2 GiB.
        vmid = (await session(env, a))["slot_vmid"]
        env.pve.cts[vmid]["mem"] = 2 * GIB
        env.pve.node_mem["pve2"]["mem"] += 2 * GIB
        b = await start(env, user_id=2)
        await env.run(1)
        # pve2: 28 GiB free minus 14 GiB still promised to the first lab -> 14 GiB < 16 GiB.
        s = await session(env, b)
        assert s["slot_vmid"] is None and s["status"] == "queued"


async def _backdate_stage(env, sid, interval="1 hour"):
    async with env.services.pool.connection() as conn, conn.transaction():
        await conn.execute(f"UPDATE sessions SET stage_started_at = now() - interval '{interval}' WHERE id = %s",
                           (sid,))


async def test_no_usable_slots_fails_at_once(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        await env.services.store.set_slot_admin_state(201, "maintenance")
        await env.services.store.set_slot_admin_state(202, "failed")
        sid = await start(env)
        await env.run(2)
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "NO_SLOTS"


async def test_queue_timeout(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,), queue_timeout=600) as env:
        await start(env, user_id=7)
        await env.run()
        sid = await start(env)
        await env.run(2)
        assert (await session(env, sid))["status"] == "queued"  # Busy slot: an ordinary queue
        await _backdate_stage(env, sid, "11 minutes")
        await env.run(2)
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "QUEUE_TIMEOUT"


async def test_queue_timeout_zero_waits_forever(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,), queue_timeout=0) as env:
        await start(env, user_id=7)
        await env.run()
        sid = await start(env)
        await _backdate_stage(env, sid, "1 day")
        await env.run(2)
        assert (await session(env, sid))["status"] == "queued"


async def test_student_dir_is_never_writable_in_two_slots(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201, 202, 203)) as env:
        sid = await start(env)
        await env.run()
        assert (await env.services.store.slot(201))["student_key"] == "main-42"
        env.agent.busy = True  # The CT cannot be stopped after the failure below
        env.slots[201].restart()
        env.orch._health_checked.clear()
        await env.run()
        assert (await session(env, sid))["stage"] == "lab_stop"
        await _backdate_stage(env, sid)
        await env.run()
        await _backdate_stage(env, sid)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "SLOT_LAB_FAILED"
        assert (await env.services.store.slot(201))["student_key"] == "main-42"  # CT may still run
        env.agent.busy = False

        again = await start(env)
        await env.run()
        s = await session(env, again)
        assert s["status"] == "failed" and s["error"]["code"] == "STUDENT_DIR_BUSY"
        assert s["slot_vmid"] is None
        other = await start(env, user_id=7)  # Other students are not affected
        await env.run()
        assert (await session(env, other))["status"] == "running"

        await env.services.store.set_slot_admin_state(201, "free")  # slot-enable: CT checked stopped
        again = await start(env)
        await env.run()
        assert (await session(env, again))["status"] == "running"


async def test_live_symbols_come_from_the_reference_lab(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid = await start(env)
        await env.run()
        nodes = {n["name"]: n for n in (await session(env, sid))["topology"]["topology"]["nodes"]}
        assert nodes["SW1"]["symbol"] == ":/symbols/multilayer_switch.svg"
        assert nodes["D1"]["symbol"] == ":/symbols/computer.svg"


@pytest.mark.parametrize("fault,code", [("no_snapshot", "SNAPSHOT_MISSING"), ("reset_fails", "PCT_FAILED"),
                                        ("reset_unsupported", "RESET_UNSUPPORTED")])
async def test_slot_that_cannot_be_reset_is_parked_but_never_handed_out(tmp_path, dsn, fault, code):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        sid = await start(env)
        await env.run()
        if fault == "no_snapshot":
            env.agent.no_snapshot.add(201)
        else:
            setattr(env.agent, fault, True)
        await stop(env, sid)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "stopped"  # For the student the lab did stop
        slot = await env.services.store.slot(201)
        assert slot["state"] == "maintenance" and f"reset failed: {code}" in slot["state_message"]
        assert "students/placeholder," in env.pve.cts[201]["config"]["mp1"]  # Parked all the same
        assert slot["student_key"] is None
        other = await start(env, user_id=7)
        await env.run(3)
        s = await session(env, other)
        assert s["status"] == "failed" and s["error"]["code"] == "NO_SLOTS"


async def test_every_release_rolls_the_slot_back_before_parking(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        for user in (42, 7):
            sid = await start(env, user_id=user)
            await env.run()
            assert (await session(env, sid))["status"] == "running"
            env.timeline.clear()
            await stop(env, sid)
            await env.run()
            assert (await session(env, sid))["status"] == "stopped"
            assert names(env.timeline).index("agent:reset") < names(env.timeline).index("agent:mounts")
            assert (await env.services.store.slot(201))["state"] == "free"


async def test_lab_timeout_frees_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        env.slots[201].op_polls = 10**6  # The deploy never finishes, and the slot cannot stop it either
        sid = await start(env)
        await env.run(6)
        assert (await session(env, sid))["stage"] == "lab_wait"
        await _backdate_stage(env, sid)
        await env.run()
        s = await session(env, sid)
        assert s["stage"] == "lab_stop" and s["error"]["code"] == "TIMEOUT"
        await _backdate_stage(env, sid)  # The stop times out too: the CT is stopped all the same
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "TIMEOUT"
        assert (await env.services.store.slot(201))["state"] == "free"  # Rolled back and parked
        assert ("agent:reset", 201) in env.timeline
        assert "students/placeholder," in env.pve.cts[201]["config"]["mp1"]


async def enable_slot(env, monkeypatch, vmid):
    """The operator's `slot-enable`, run against the fakes (the test loop is already running)."""
    from types import SimpleNamespace

    from lab_manager import admin

    captured = []
    monkeypatch.setattr(admin, "_with_services", lambda fn: fn(env.services))
    monkeypatch.setattr(admin, "asyncio", SimpleNamespace(run=captured.append))
    admin.cmd_slot_state(SimpleNamespace(vmid=vmid, state="free", message=""))
    await captured.pop()


async def test_slot_enable_resets_the_slot_before_it_is_freed(tmp_path, dsn, monkeypatch):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        await env.services.store.set_slot_admin_state(201, "failed")
        env.agent.no_snapshot.add(201)
        with pytest.raises(SystemExit, match="was not reset"):  # No snapshot: it does not come back unclean
            await enable_slot(env, monkeypatch, 201)
        assert (await env.services.store.slot(201))["state"] == "failed"
        env.agent.no_snapshot.clear()
        await enable_slot(env, monkeypatch, 201)
        assert (await env.services.store.slot(201))["state"] == "free"
        assert names(env.timeline).index("agent:reset") < names(env.timeline).index("agent:mounts")
        assert "students/placeholder," in env.pve.cts[201]["config"]["mp1"]


async def test_failed_slot_is_parked_but_not_rolled_back_until_the_operator_enables_it(tmp_path, dsn, monkeypatch):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        env.agent.start_fails = "startup for container '201' failed"
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "SLOT_START_FAILED"
        slot = await env.services.store.slot(201)
        assert slot["state"] == "failed"
        # The guest is left as it was, for the journal of the slot, GNS3 logs and journald of the guest.
        assert ("agent:reset", 201) not in env.timeline
        assert env.pve.cts[201]["status"] == "stopped"
        assert "students/placeholder," in env.pve.cts[201]["config"]["mp1"]  # The student's directory is off
        assert slot["student_key"] is None
        env.agent.start_fails = None
        await enable_slot(env, monkeypatch, 201)  # He has looked: now the slot is rolled back and freed
        assert ("agent:reset", 201) in env.timeline
        assert (await env.services.store.slot(201))["state"] == "free"


async def fail_a_lab(env, user_id):
    env.slots[201].fail_next = {"code": "RuntimeError", "message": "GNS3 does not answer"}
    sid = await start(env, user_id=user_id)
    await env.run()
    s = await session(env, sid)
    assert s["status"] == "failed" and s["error"]["code"] == "SLOT_OPERATION_FAILED", s["status_message"]
    return s


async def test_slot_where_different_students_fail_in_a_row_is_held(tmp_path, dsn, monkeypatch):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        for user in (1, 2):
            await fail_a_lab(env, user)
            slot = await env.services.store.slot(201)
            assert slot["state"] == "free"  # Rolled back and back to work, no operator
        await fail_a_lab(env, 3)
        slot = await env.services.store.slot(201)
        assert slot["state"] == "maintenance" and slot["lab_failures"] == ["main-1", "main-2", "main-3"]
        assert "3 labs of different students failed in a row" in slot["state_message"]
        assert ("agent:reset", 201) in env.timeline  # It was rolled back before it was held
        other = await start(env, user_id=9)
        await env.run(3)
        assert (await session(env, other))["error"]["code"] == "NO_SLOTS"
        await enable_slot(env, monkeypatch, 201)  # The operator has looked
        slot = await env.services.store.slot(201)
        assert slot["state"] == "free" and slot["lab_failures"] == []


async def test_one_student_failing_again_does_not_blame_the_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        for _ in range(4):
            await fail_a_lab(env, 42)  # E.g. a broken project in the student's directory
        slot = await env.services.store.slot(201)
        assert slot["state"] == "free" and slot["lab_failures"] == ["main-42"]


async def test_a_lab_that_ran_and_stopped_cleanly_clears_the_failures(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        await fail_a_lab(env, 1)
        await fail_a_lab(env, 2)
        assert (await env.services.store.slot(201))["lab_failures"] == ["main-1", "main-2"]
        sid = await start(env, user_id=3)
        await env.run()
        await stop(env, sid)
        await env.run()
        assert (await session(env, sid))["status"] == "stopped"
        assert (await env.services.store.slot(201))["lab_failures"] == []
        await fail_a_lab(env, 4)
        assert (await env.services.store.slot(201))["state"] == "free"  # One failure again, not the third


async def test_failure_limit_zero_never_holds_a_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,), slot_lab_failure_limit=0) as env:
        for user in (1, 2, 3, 4):
            await fail_a_lab(env, user)
        assert (await env.services.store.slot(201))["state"] == "free"


@pytest.mark.parametrize("error,counts", [
    ({"code": "SLOT_OPERATION_FAILED"}, True),
    ({"code": "SLOT_LAB_FAILED"}, True),
    ({"code": "SLOT_STOP_FAILED"}, True),
    ({"code": "TIMEOUT", "stage": "lab_wait"}, True),
    ({"code": "TIMEOUT", "stage": "lab_stop_wait"}, True),
    ({"code": "TIMEOUT", "stage": "slot_ready"}, False),  # The slot itself: it is failed anyway
    ({"code": "TIMEOUT", "stage": "student_dir"}, False),
    ({"code": "LAB_UNAVAILABLE"}, False),  # One broken lab definition must not hold every slot
    ({"code": "SLOT_PROJECT_UNKNOWN"}, False),
    ({"code": "PROJECT_MISSING"}, False),
])
def test_which_lab_failures_count_against_the_slot(error, counts):
    assert Orchestrator._counts_against_slot(error) is counts


async def events_of(env, sid):
    async with env.services.pool.connection() as conn:
        cur = await conn.execute("SELECT kind, detail FROM events WHERE session_id = %s ORDER BY id", (sid,))
        return [(r["kind"], r["detail"]) for r in await cur.fetchall()]


async def test_a_timed_out_start_quarantines_the_slot_at_once(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        env.agent.start_times_out = True  # pct exec ran too long: the CT runs, the agent marks the slot
        sid = await start(env)
        await env.run(4)
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "SLOT_QUARANTINED"
        assert s["error"]["slot_state"] == "maintenance" and s["status_message"] == QUARANTINE_MESSAGE
        slot = await env.services.store.slot(201)
        assert slot["state"] == "maintenance" and slot["student_key"] == s["student_key"]  # Held for the student
        kinds = [k for k, _ in await events_of(env, sid)]
        assert "slot_quarantined" in kinds and "ct_started" not in kinds
        assert "slot_ready" not in kinds and "ownership_wait" not in kinds
        assert not any(n in ("agent:stop", "agent:reset") for n in names(env.timeline))  # No cleanup is tried
        event = next(d for k, d in await events_of(env, sid) if k == "slot_quarantined")
        assert event["vmid"] == 201 and event["command"] == "exec" and event["node"]
        # The public error: not retryable, nothing about the node
        r = await env.client.get(f"/api/v1/sessions/{sid}")
        assert r.json()["error"] == {"code": "SLOT_QUARANTINED", "retryable": False}


async def test_a_lost_start_response_waits_and_then_succeeds(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        env.agent.lose_start_response = True
        sid = await start(env)
        await env.run()
        assert (await session(env, sid))["status"] == "running"
        kinds = [k for k, _ in await events_of(env, sid)]
        assert "slot_quarantined" not in kinds
        assert (await env.services.store.slot(201))["state"] == "busy"


async def test_quarantine_during_the_stop_does_not_wait_for_the_stage_timeout(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        sid = await start(env)
        await env.run()
        assert (await session(env, sid))["status"] == "running"
        env.agent.uncertain[201] = "exec"  # A command timed out meanwhile; the stop cannot get ownership
        await stop(env, sid)
        await env.run(3)  # Far less than the 10 minutes of ownership_wait
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "SLOT_QUARANTINED"
        slot = await env.services.store.slot(201)
        assert slot["state"] == "maintenance" and slot["student_key"] == s["student_key"]
        assert env.pve.cts[201]["status"] == "running"  # Nothing could be done to it: the operator does
        assert "ownership_wait" not in [k for k, _ in await events_of(env, sid)]


async def test_a_student_with_a_quarantined_slot_is_told_to_wait_for_the_check(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.agent.start_times_out = True
        first = await start(env)
        await env.run(4)
        assert (await session(env, first))["error"]["code"] == "SLOT_QUARANTINED"
        env.agent.start_times_out = False
        again = await start(env)  # The same student: the directory may still be writable in the held CT
        await env.run(4)
        s = await session(env, again)
        assert s["status"] == "failed" and s["error"]["code"] == "STUDENT_DIR_BUSY"
        assert s["status_message"] == QUARANTINE_MESSAGE
        assert s["slot_vmid"] is None
        other = await start(env, user_id=7)  # Another student gets the other slot
        await env.run()
        assert (await session(env, other))["status"] == "running"


async def test_no_path_to_a_quarantine_frees_the_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        env.agent.start_times_out = True
        sid = await start(env)
        for _ in range(6):
            await env.run(1)
            assert (await env.services.store.slot(201))["state"] != "free"
        assert (await session(env, sid))["status"] == "failed"


async def age_stage(env, sid):
    async with env.services.pool.connection() as conn, conn.transaction():
        await conn.execute("UPDATE sessions SET stage_started_at = now() - interval '2 hour' WHERE id = %s", (sid,))


async def test_a_failed_cleanup_does_not_leave_a_free_slot_in_the_error(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        sid = await start(env)
        await env.run()
        env.slots[201].op_polls = 10**6  # The lab never finishes stopping: TIMEOUT promises `free`
        await stop(env, sid)
        await env.run(4)
        assert (await session(env, sid))["stage"] == "lab_stop_wait"
        await age_stage(env, sid)
        env.agent.stop_never = True  # ... but the CT cannot be stopped either
        await env.run(4)
        s = await session(env, sid)
        assert s["stage"] == "ct_stop" and s["error"]["code"] == "TIMEOUT" and s["error"]["slot_state"] == "free"
        await age_stage(env, sid)
        await env.run(2)
        s = await session(env, sid)
        slot = await env.services.store.slot(201)
        assert s["status"] == "failed" and s["error"]["code"] == "TIMEOUT"
        assert slot["state"] == "failed" and s["error"]["slot_state"] == "failed"


# `lab-manager-admin check`: quarantined slots and the agents' start budget


async def test_the_report_names_the_slot_the_session_the_student_and_the_agent_command(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        assert await quarantine_report(env.services) == []
        env.agent.start_times_out = True
        sid = await start(env)
        await env.run(4)
        (line,) = await quarantine_report(env.services)
        s = await session(env, sid)
        assert "slot 201" in line and sid in line and s["student_key"] in line
        assert "agent command exec" in line and "mark present" in line
        assert "pct shutdown 201" in line and "lab-provision reconcile 201" in line and "lab-provision enable 201" in line
        env.agent.uncertain.clear()  # The operator reconciled the mark; the slot is still in maintenance
        assert "mark already cleared" in (await quarantine_report(env.services))[0]
        await env.services.store.set_slot_admin_state(201, "free")
        assert await quarantine_report(env.services) == []


async def test_other_maintenance_slots_are_not_reported(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        await env.services.store.set_slot_admin_state(201, "maintenance", "looking at the disk")
        assert await quarantine_report(env.services) == []


async def test_a_slow_agent_is_reported(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        nodes = await env.services.pve.online_nodes()
        assert nodes
        assert await start_budget_warnings(env.services.agent, nodes, env.settings.agent_start_timeout) == []
        env.agent.start_budget = 1920  # LAB_GUEST_ACTION_TIMEOUT=600 on the agents
        lines = await start_budget_warnings(env.services.agent, nodes, env.settings.agent_start_timeout)
        assert len(lines) == len(nodes) and "1980" in lines[0]
        env.agent.start_budget = None  # Agents older than 1.2.1 report nothing
        assert await start_budget_warnings(env.services.agent, nodes, env.settings.agent_start_timeout) == []


async def test_the_student_sees_why_the_stop_waits(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        sid = await start(env)
        await env.run()
        env.agent.busy = True  # Not a quarantine: the claim cannot be confirmed for now
        await stop(env, sid)
        await env.run(3)
        body = (await env.client.get(f"/api/v1/sessions/{sid}")).json()
        assert body["status"] == "stopping" and body["status_message"].startswith("Нет подтверждения владения слотом")
