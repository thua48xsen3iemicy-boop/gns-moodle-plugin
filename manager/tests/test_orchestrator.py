from datetime import timedelta

from conftest import make_env

from lab_manager.orchestrator import Orchestrator


async def start(env, user_id=42, instance_id=1):
    r = await env.client.post("/api/v1/sessions", json={"user_id": user_id, "instance_id": instance_id,
                                                        "lab_ref": "cisco-3x2", "course_id": 3})
    assert r.status_code in (200, 201), r.text
    return r.json()["session_id"]


async def session(env, sid):
    return await env.services.store.session(sid)


def names(timeline):
    return [t[0] for t in timeline]


def wrap_attach(env):
    """Record the mount in the shared timeline and check the CT is stopped at that moment."""
    storage = env.services.storage
    original_attach, original_detach = storage.attach, storage.detach

    def attach(vmid, slug, uid):
        assert env.pve.cts[vmid]["status"] == "stopped", "mount on a running CT"
        env.timeline.append(("mount", vmid))
        return original_attach(vmid, slug, uid)

    def detach(vmid, slug, uid):
        assert env.pve.cts[vmid]["status"] == "stopped", "unmount on a running CT"
        env.timeline.append(("unmount", vmid))
        return original_detach(vmid, slug, uid)

    storage.attach, storage.detach = attach, detach


async def test_full_cycle_order_mount_start_ready_deploy_stop_release(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        wrap_attach(env)
        env.pve.cts[201]["status"] = "running"  # A free slot left running must be stopped first
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "running", s["status_message"]
        assert s["slot_vmid"] == 201
        order = names(env.timeline)
        assert order == ["pve:vzshutdown", "mount", "pve:vzstart", "slot:readyz", "slot:deploy"]
        assert (tmp_path / "rw" / "slots" / "201" / "mnt").is_dir()
        assert not (tmp_path / "rw" / "students" / "main" / "42" / "work").exists()
        slot = await env.services.store.slot(201)
        assert slot["state"] == "busy" and slot["session_id"] == sid
        # Session topology comes from the live project, internal properties stripped.
        nodes = s["topology"]["topology"]["nodes"]
        assert {n["name"] for n in nodes} == {"SW1", "SW2", "SW3", "D1", "D2", "D3", "D4", "D5", "D6"}
        assert all(n["properties"] == {} and "console" not in n for n in nodes)

        env.timeline.clear()
        r = await env.client.post(f"/api/v1/sessions/{sid}/stop")
        assert r.json()["status"] == "stopping"
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "stopped"
        assert names(env.timeline) == ["slot:stop", "pve:vzshutdown", "unmount"]
        assert (tmp_path / "rw" / "students" / "main" / "42" / "work").is_dir()
        slot = await env.services.store.slot(201)
        assert slot["state"] == "free" and slot["session_id"] is None


async def test_returning_student_resumes_on_the_same_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid = await start(env)
        await env.run()
        pid = next(iter(env.slots[201].labs))
        await env.client.post(f"/api/v1/sessions/{sid}/stop")
        await env.run()
        # Someone else takes the other slot; the student comes back.
        other = await start(env, user_id=7)
        await env.run()
        assert (await session(env, other))["slot_vmid"] == 202
        env.timeline.clear()
        sid2 = await start(env)
        await env.run()
        s = await session(env, sid2)
        assert s["status"] == "running" and s["slot_vmid"] == 201
        assert ("slot:resume", env.slots[201].address) in env.timeline
        assert list(env.slots[201].labs) == [pid]  # Same GNS3 project, no new deploy


async def test_student_waits_while_pinned_slot_is_busy(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        sid = await start(env, user_id=1)
        await env.run()
        await env.client.post(f"/api/v1/sessions/{sid}/stop")
        await env.run()
        other = await start(env, user_id=2)
        await env.run()
        assert (await session(env, other))["status"] == "running"
        back = await start(env, user_id=1)
        await env.run(3)
        s = await session(env, back)
        assert s["status"] == "queued"
        assert s["status_message"] == "Ожидание освобождения вашего слота…"


async def test_maxduration_stops_and_releases(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        await env.services.store.update_session(s, expires_at=s["started_at"] - timedelta(seconds=1))
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "stopped"
        assert s["status_message"] == "Время лабы истекло"
        assert (await env.services.store.slot(201))["state"] == "free"


async def test_slot_failed_operation_fails_session_and_keeps_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.slots[201].fail_next = {"code": "RuntimeError", "message": "Missing QEMU image"}
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed"
        assert "Missing QEMU image" in s["status_message"]
        slot = await env.services.store.slot(201)
        assert slot["state"] == "failed"  # Operator decides; nothing recreated or cleaned up
        assert env.pve.cts[201]["status"] == "running"
        # The student's project lives in the failed slot: a new start waits for the operator.
        again = await start(env)
        await env.run(3)
        assert (await session(env, again))["status"] == "queued"


async def test_slot_lab_failing_while_running(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid = await start(env)
        await env.run()
        env.slots[201].restart()
        env.orch._health_checked.clear()
        await env.run(2)
        s = await session(env, sid)
        assert s["status"] == "failed"
        assert (await env.services.store.slot(201))["state"] == "failed"


async def test_stop_before_slot_is_allocated(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,)) as env:
        await env.services.store.set_slot_admin_state(201, "maintenance")
        sid = await start(env)
        await env.run(2)
        assert (await session(env, sid))["status"] == "queued"
        await env.client.post(f"/api/v1/sessions/{sid}/stop")
        await env.run(2)
        assert (await session(env, sid))["status"] == "stopped"


async def test_stop_during_start_cleans_up_without_deploy(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.slots[201].ready = False
        sid = await start(env)
        await env.run(5)
        assert (await session(env, sid))["stage"] == "slot_ready"
        await env.client.post(f"/api/v1/sessions/{sid}/stop")
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "stopped"
        assert env.slots[201].labs == {}
        assert env.pve.cts[201]["status"] == "stopped"
        assert (tmp_path / "rw" / "students" / "main" / "42" / "work").is_dir()


async def test_recovery_after_restart_with_pve_task_in_flight(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.pve.task_polls = 10**6  # Start task never finishes while the first manager runs
        env.services.pve.task_interval = 0
        sid = await start(env)
        await env.orch.tick()
        await env.orch.drain()
        # First manager "crashes" in ct_start with the UPID stored.
        for _ in range(5):
            s = await session(env, sid)
            if s["stage"] == "ct_start" and s["pve_upid"]:
                break
            await env.orch.step(s)
        assert s["stage"] == "ct_start" and s["pve_upid"]
        upid = s["pve_upid"]
        # Proxmox finishes the task meanwhile.
        env.pve.tasks[upid]["remaining"] = 0
        fresh = Orchestrator(env.settings, env.services.store, env.services.pve, env.services.slots,
                             env.services.catalog, env.services.storage)
        for _ in range(20):
            await fresh.tick()
            await fresh.drain()
        s = await session(env, sid)
        assert s["status"] == "running"
        assert names(env.timeline).count("pve:vzstart") == 1  # Not started twice


async def test_recovery_when_task_node_is_down_rechecks_ct(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid = await start(env)
        s = None
        for _ in range(10):
            s = await session(env, sid)
            if s["stage"] == "ct_start":
                break
            await env.orch.step(s)
        node = env.pve.cts[201]["node"]
        env.pve.task_node_down = {node}
        await env.orch.step(await session(env, sid))
        s = await session(env, sid)
        assert s["stage"] == "ct_start" and s["status"] == "starting"  # Unknown -> not assumed done
        env.pve.cts[201]["status"] = "running"  # The CT did start in reality
        await env.run()
        assert (await session(env, sid))["status"] == "running"


async def test_recovery_after_deploy_submitted_replays_same_key(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        env.slots[201].op_polls = 10**6
        sid = await start(env)
        await env.run(5)
        s = await session(env, sid)
        assert s["stage"] == "lab_wait"
        keys = [k for m, p, k in env.slots[201].requests if p == "/v1/labs/deploy"]
        assert len(keys) == 1
        # Simulate a crash right after the POST: call recorded as pending, stage back at submit.
        call = await env.services.store.current_call(s, ("deploy",))
        await env.services.store.update_call(call["id"], status="pending")
        await env.services.store.update_session(s, stage="lab_submit")
        env.slots[201].op_polls = 0
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
        resumes = [k for m, p, k in env.slots[201].requests if p.endswith("/resume")]
        assert deploys == [old["idempotency_key"]] * 2  # Replayed once, answered STALE
        assert len(resumes) == 1 and resumes[0] != old["idempotency_key"]  # New intentional key
        rows = await env.services.store.current_call(await session(env, sid), ("deploy", "resume"))
        assert rows["action"] == "resume"


async def test_stale_key_on_failed_lab_goes_to_operator(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid, _ = await _replay_after_lab_changed(env, "FAILED")
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "SLOT_LAB_FAILED"
        assert not [p for m, p, k in env.slots[201].requests if p.endswith("/resume")]


async def test_operation_in_progress_waits(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        # Another (foreign) operation runs in the slot: our deploy gets 409 OPERATION_IN_PROGRESS.
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


async def test_pve_config_mode_sets_and_removes_mount_point(tmp_path, dsn):
    async with make_env(tmp_path, dsn, mode="pve_config") as env:
        sid = await start(env)
        await env.run()
        assert (await session(env, sid))["status"] == "running"
        assert env.pve.cts[201]["config"]["mp0"] == (
            "/mnt/pve/cephfs/lab/students/main/42/work,mp=/opt/gns3/student,backup=0")
        order = names(env.timeline)
        assert order.index("pve:config") < order.index("pve:vzstart") < order.index("slot:deploy")
        await env.client.post(f"/api/v1/sessions/{sid}/stop")
        await env.run()
        assert (await session(env, sid))["status"] == "stopped"
        assert "mp0" not in env.pve.cts[201]["config"]


async def test_pve_permission_error_fails_without_retry(tmp_path, dsn):
    async with make_env(tmp_path, dsn, mode="pve_config") as env:
        env.pve.responses[("PUT", r"/nodes/[^/]+/lxc/201/config")] = (
            403, {"data": None, "message": "mount point type bind is only allowed for root@pam"})
        sid = await start(env)
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "failed"
        assert "root@pam" in s["status_message"]
        assert env.pve.cts[201]["status"] == "stopped"  # Never started without the mount


async def test_slot_never_ready_times_out_and_keeps_slot_for_operator(tmp_path, dsn):
    async with make_env(tmp_path, dsn, slot_ready_timeout=10) as env:
        env.slots[201].ready = False
        sid = await start(env)
        await env.run(5)
        s = await session(env, sid)
        assert s["stage"] == "slot_ready" and "Слот не готов" in s["status_message"]
        async with env.services.pool.connection() as conn, conn.transaction():
            await conn.execute("UPDATE sessions SET stage_started_at = now() - interval '1 hour' WHERE id = %s", (sid,))
        await env.run(2)
        s = await session(env, sid)
        assert s["status"] == "failed" and s["error"]["code"] == "TIMEOUT"
        assert (await env.services.store.slot(201))["state"] == "failed"
