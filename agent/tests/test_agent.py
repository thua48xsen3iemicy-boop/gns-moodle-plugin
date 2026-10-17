import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
import agent


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "ADMIN_UIDS", (0, os.getuid(), os.stat("/").st_uid))
    # This sandbox maps / ownership to nobody and places fixtures below /tmp.
    # Model only those two system ancestors as a protected production root.
    real_fstat = os.fstat
    system_dirs = {(os.stat(p).st_dev, os.stat(p).st_ino) for p in ("/", "/tmp")}
    def protected_system_ancestors(fd):
        st = real_fstat(fd)
        if (st.st_dev, st.st_ino) in system_dirs:
            fields = list(st)
            fields[0] = (fields[0] & ~0o022)
            fields[4] = 0
            return os.stat_result(fields)
        return st
    monkeypatch.setattr(agent.os, "fstat", protected_system_ancestors)
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "2201.conf").write_text("unprivileged: 1\ntags: training;labslot\n")
    ceph = tmp_path / "ceph"
    (ceph / "slot").mkdir(parents=True)
    (ceph / "students" / "student42").mkdir(parents=True)
    lock = tmp_path / "locks"
    monkeypatch.setattr(agent, "CEPH_ROOT", ceph)
    monkeypatch.setattr(agent, "CONF_DIR", conf)
    monkeypatch.setattr(agent, "LOCK_DIR", lock)
    # Emulate only the presence of CephFS in this unit test (there is no CephFS here).
    monkeypatch.setattr(agent, "validate_cephfs", lambda: None)
    calls = []
    state = {"value": "stopped"}

    def run(args, timeout=45):
        calls.append(args)
        if args[:2] == [agent.PCT, "status"]:
            return "status: " + state["value"]
        if args[:2] == [agent.PCT, "start"]:
            state["value"] = "running"
        if args[:2] in ([agent.PCT, "stop"], [agent.PCT, "shutdown"]):
            state["value"] = "stopped"
        if args[0] == agent.FINDMNT:
            return str(ceph)
        return ""

    monkeypatch.setattr(agent, "run_cmd", run)
    return TestClient(agent.app), calls, state, conf, ceph


def test_start_with_bind_and_allowlisted_guest_action(env):
    client, calls, state, _, ceph = env
    response = client.post("/slots/2201/start", json={
        "student_key": "student42", "data_mode": "rw", "actions": ["daemon_reload"]
    })
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "running"
    assert [agent.PCT, "set", "2201",
            "--mp0", f"{ceph}/slot,mp=/opt/slot,ro=1,shared=1",
            "--mp1", f"{ceph}/students/student42,mp=/srv/student,ro=0,shared=1"] in calls
    assert [agent.PCT, "exec", "2201", "--", "/usr/bin/systemctl", "daemon-reload"] in calls


def test_reject_traversal_and_arbitrary_guest_command(env):
    client, calls, *_ = env
    for body in (
        {"student_key": "../../etc", "actions": []},
        {"student_key": "student42\n", "actions": []},
        {"student_key": "student42", "code_revision": "..\n"},
        {"student_key": "student42", "actions": ["bash -c id"]},
        {"student_key": "student42", "command": "rm -rf /"},
        {"student_key": "student42", "data_mode": "host-rw"},
    ):
        r = client.post("/slots/2201/start", json=body)
        assert r.status_code == 422, (body, r.text)
    assert calls == []


def test_reject_unmarked_or_privileged_or_outside_range(env):
    client, calls, _, conf, _ = env
    assert client.post("/slots/150/start", json={"student_key": "student42"}).status_code == 403
    (conf / "2201.conf").write_text("unprivileged: 0\ntags: labslot\n")
    assert client.post("/slots/2201/start", json={"student_key": "student42"}).status_code == 403
    (conf / "2201.conf").write_text("unprivileged: 1\ntags: production\n")
    assert client.post("/slots/2201/start", json={"student_key": "student42"}).status_code == 403
    assert calls == []


def test_stopped_required_for_changes(env):
    client, calls, state, *_ = env
    state["value"] = "running"
    r = client.put("/slots/2201/mounts", json={"student_key": "student42"})
    assert r.status_code == 409
    assert not any(x[:2] == [agent.PCT, "set"] for x in calls)


def test_no_symlinks(env):
    client, calls, _, _, ceph = env
    (ceph / "students" / "evil").symlink_to("/etc")
    r = client.post("/slots/2201/start", json={"student_key": "evil"})
    assert r.status_code == 422
    assert not any(x[:2] == [agent.PCT, "set"] for x in calls)


def test_stop_gracefully_and_force(env):
    client, calls, state, *_ = env
    state["value"] = "running"
    r = client.post("/slots/2201/stop", json={"force": False})
    assert r.status_code == 200
    assert [agent.PCT, "shutdown", "2201", "--timeout", "60"] in calls
    state["value"] = "running"
    r = client.post("/slots/2201/stop", json={"force": True})
    assert r.status_code == 200
    assert [agent.PCT, "stop", "2201"] in calls


@pytest.mark.parametrize('vmid', ['0', '-1', '1000000000', 'abc', '2201;id'])
def test_invalid_vmid(env, vmid):
    client, calls, *_ = env
    assert client.get(f'/slots/{vmid}/status').status_code == 422
    assert calls == []


@pytest.mark.parametrize('key', ['/etc', 'x;id', '$(id)', 'a,b', 'a/b', '..'])
def test_injection(env, key):
    client, calls, *_ = env
    assert client.put('/slots/2201/mounts', json={'student_key': key}).status_code == 422
    assert calls == []


@pytest.mark.parametrize('mode,ro', [('ro', 1), ('rw', 0)])
def test_mount_mode_and_repeat(env, mode, ro):
    client, calls, _, _, ceph = env
    for _ in range(2):
        assert client.put('/slots/2201/mounts', json={'student_key': 'student42', 'data_mode': mode}).status_code == 200
    assert f'{ceph}/students/student42,mp=/srv/student,ro={ro},shared=1' in calls[-1]


def test_repeated_start_and_stop(env):
    client, calls, *_ = env
    assert client.post('/slots/2201/start', json={'student_key': 'student42'}).status_code == 200
    before = len(calls)
    assert client.post('/slots/2201/start', json={'student_key': 'student42'}).status_code == 409
    assert len(calls) == before + 1
    for _ in range(2):
        assert client.post('/slots/2201/stop', json={}).status_code == 200
    assert sum(c[:2] == [agent.PCT, 'shutdown'] for c in calls) == 1


def test_unsafe_parent_and_missing_source(env):
    client, calls, _, _, ceph = env
    (ceph / 'students').chmod(0o777)
    assert client.post('/slots/2201/start', json={'student_key': 'student42'}).status_code == 422
    (ceph / 'students').chmod(0o755)
    assert client.post('/slots/2201/start', json={'student_key': 'missing'}).status_code == 422
    assert not any(c[:2] == [agent.PCT, 'set'] for c in calls)


def test_ceph_unavailable(env, monkeypatch):
    client, calls, *_ = env
    def unavailable():
        raise agent.HTTPException(503, 'CephFS unavailable')
    monkeypatch.setattr(agent, 'validate_cephfs', unavailable)
    assert client.post('/slots/2201/start', json={'student_key': 'student42'}).status_code == 503
    assert not any(c[:2] == [agent.PCT, 'set'] for c in calls)


@pytest.mark.parametrize('failure,code', [('exit', 409), ('timeout', 504), ('missing', 503)])
def test_command_failures(monkeypatch, failure, code):
    import subprocess
    def run(args, **kwargs):
        assert isinstance(args, list)
        assert not kwargs.get('shell', False)
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args, 1)
        if failure == 'missing':
            raise FileNotFoundError()
        return subprocess.CompletedProcess(args, 1, '', 'failed')
    monkeypatch.setattr(agent.subprocess, 'run', run)
    with pytest.raises(agent.HTTPException) as err:
        agent.run_cmd([agent.PCT, 'status', '2201'])
    assert err.value.status_code == code


def test_strict_boolean(env):
    client, calls, *_ = env
    assert client.post('/slots/2201/stop', json={'force': 'false'}).status_code == 422
    assert calls == []


def test_operation_conflict(env):
    client, calls, *_ = env
    with agent.slot_lock(2201):
        assert client.post('/slots/2201/stop', json={}).status_code == 409
    assert calls == []


def test_acl_inspection_fails_closed(env, monkeypatch):
    client, calls, *_ = env
    monkeypatch.setattr(agent.os, 'getxattr', lambda *args: b'extended-acl')
    assert client.post('/slots/2201/start', json={'student_key': 'student42'}).status_code == 422
    assert not any(c[:2] == [agent.PCT, 'set'] for c in calls)


def test_pct_mount_failure_prevents_start(env, monkeypatch):
    client, calls, *_ = env
    original = agent.run_cmd
    def run(args, timeout=45):
        if args[:2] == [agent.PCT, 'set']:
            raise agent.HTTPException(409, 'pct failed')
        return original(args, timeout)
    monkeypatch.setattr(agent, 'run_cmd', run)
    assert client.post('/slots/2201/start', json={'student_key': 'student42'}).status_code == 409
    assert not any(c[:2] == [agent.PCT, 'start'] for c in calls)


def test_real_ceph_mount_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, 'CEPH_ROOT', tmp_path)
    monkeypatch.setattr(agent, 'validate_no_symlinks', lambda path: None)
    monkeypatch.setattr(Path, 'is_mount', lambda path: False)
    with pytest.raises(agent.HTTPException) as err:
        agent.validate_cephfs()
    assert err.value.status_code == 503
    monkeypatch.setattr(Path, 'is_mount', lambda path: True)
    monkeypatch.setattr(agent, 'run_cmd', lambda *args, **kw: 'ext4')
    with pytest.raises(agent.HTTPException) as err:
        agent.validate_cephfs()
    assert err.value.status_code == 503
