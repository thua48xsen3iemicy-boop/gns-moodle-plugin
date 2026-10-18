from datetime import timedelta

import pytest
from conftest import make_env
from fakes import GIB

from lab_manager.orchestrator import Orchestrator


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
    return Orchestrator(env.settings, sv.store, sv.pve, sv.agent, sv.slots, sv.catalog)


async def test_full_cycle_order(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.pve.cts[201]["status"] = "running"  # A free slot left running is stopped first
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "running", s["status_message"]
        assert s["slot_vmid"] == 201
        assert names(env.timeline) == ["agent:stop", "pve:config", "agent:student", "agent:start",
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
        assert env.timeline == [("slot:stop", "10.0.0.1"), ("agent:stop", "shutdown"),
                                ("agent:mounts", "placeholder", "ro")]
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


async def test_placement_best_fit_packs_nodes(tmp_path, dsn):
    vmids = (201, 202, 203)
    nodes = {201: "pve1", 202: "pve2", 203: "pve3"}
    async with make_env(tmp_path, dsn, vmids=vmids, nodes_of=nodes, node_memory_reserve_mb=0) as env:
        env.pve.node_mem["pve1"]["mem"] = 64 * GIB - 40 * GIB  # 40 GiB free
        env.pve.node_mem["pve2"]["mem"] = 64 * GIB - 20 * GIB  # 20 GiB free: best fit for 16 GiB
        env.pve.node_mem["pve3"]["mem"] = 64 * GIB - 10 * GIB  # 10 GiB free: does not fit
        a = await start(env, user_id=1)
        await env.run(1)
        assert (await session(env, a))["slot_vmid"] == 202
        # 202's CT is not running yet: its 16 GiB are reserved, so pve2 has 4 GiB left.
        b = await start(env, user_id=2)
        await env.run(1)
        assert (await session(env, b))["slot_vmid"] == 201
        c = await start(env, user_id=3)
        await env.run(2)
        s = await session(env, c)
        assert s["status"] == "queued" and s["status_message"] == "Недостаточно ресурсов на узлах, ожидание…"


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


async def test_slot_failed_operation_fails_session_and_keeps_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.slots[201].fail_next = {"code": "RuntimeError", "message": "Missing QEMU image"}
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed" and "Missing QEMU image" in s["status_message"]
        slot = await env.services.store.slot(201)
        assert slot["state"] == "failed" and slot["student_key"] is None  # Operator decides
        # The student's directory is released: lab stopped, CT stopped and parked.
        assert env.slots[201].labs[str(s["project_id"])]["state"] == "STOPPED"
        assert env.pve.cts[201]["status"] == "stopped"
        assert "students/placeholder," in env.pve.cts[201]["config"]["mp1"]
        again = await start(env)  # The student can start right away on another slot
        await env.run()
        assert (await session(env, again))["slot_vmid"] == 202


async def test_slot_lab_failing_while_running(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid = await start(env)
        await env.run()
        env.slots[201].restart()
        env.orch._health_checked.clear()
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "SLOT_LAB_FAILED"
        assert (await env.services.store.slot(201))["state"] == "failed"
        assert env.pve.cts[201]["status"] == "stopped"  # The FAILED lab is stopped, the CT too


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
        assert (await session(env, sid))["status"] == "running"
        assert all(r[0] == "pve3" for r in env.agent.requests if r[2].endswith("/start"))


async def test_agent_rejection_fails_session(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        env.agent.untagged = {201}
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "AGENT_REJECTED"
        assert "not marked as a lab slot" in s["status_message"]


async def test_student_dir_with_wrong_owner_waits_then_fails_keeping_slot_free(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        env.agent.wrong_owner = {"main-42"}
        sid = await start(env)
        await env.run(4)
        s = await session(env, sid)
        assert s["stage"] == "student_dir" and s["status_message"] == "Ожидание каталога студента…"
        async with env.services.pool.connection() as conn, conn.transaction():
            await conn.execute("UPDATE sessions SET stage_started_at = now() - interval '1 hour' WHERE id = %s",
                               (sid,))
        await env.run(2)
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "TIMEOUT"
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
        assert slot["state"] == "maintenance"  # The last student's directory is still configured
        assert "students/main-42," in env.pve.cts[201]["config"]["mp1"]
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
        env.pve.node_mem["pve1"]["mem"] = 64 * GIB - 40 * GIB  # 40 GiB free
        env.pve.node_mem["pve2"]["mem"] = 64 * GIB - 30 * GIB  # 30 GiB free: best fit for the first lab
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
        assert (await session(env, b))["slot_vmid"] == 203


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
        assert (await session(env, sid))["stage"] == "ct_stop"
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
