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
    (ceph / agent.CODE_DIR).mkdir(parents=True)
    (ceph / agent.STUDENTS_DIR / "student42").mkdir(parents=True)
    lock = tmp_path / "locks"
    monkeypatch.setattr(agent, "CEPH_ROOT", ceph)
    monkeypatch.setattr(agent, "CONF_DIR", conf)
    monkeypatch.setattr(agent, "LOCK_DIR", lock)
    monkeypatch.setattr(agent, "FENCE_DIR", tmp_path / "authority")
    agent.save_fence(2201, {"epoch": 1, "generation": 1})
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
    return TestClient(agent.app, headers={"X-Lab-Epoch": "1", "X-Lab-Generation": "1"}), calls, state, conf, ceph


def test_start_with_bind_and_allowlisted_guest_action(env):
    client, calls, state, _, ceph = env
    response = client.post("/slots/2201/start", json={
        "student_key": "student42", "data_mode": "rw", "actions": ["daemon_reload"]
    })
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "running"
    assert [agent.PCT, "set", "2201",
            "--mp0", f"{ceph}/gns3-ro/slot,mp=/srv/gns3-ro/slot,ro=1,shared=1",
            "--mp1", f"{ceph}/gns3-rw/students/student42,mp=/opt/gns3/projects,ro=0,shared=1"] in calls
    assert [agent.PCT, "exec", "2201", "--", "/usr/bin/systemctl", "daemon-reload"] in calls


def test_images_mounted_read_only_as_mp2_when_configured(env, monkeypatch):
    client, calls, _, _, ceph = env
    assert client.put("/slots/2201/mounts", json={"student_key": "student42"}).status_code == 200
    assert "--mp2" not in next(c for c in calls if c[:2] == [agent.PCT, "set"])  # Unset: mp2 untouched
    calls.clear()
    monkeypatch.setattr(agent, "IMAGES_DIR", Path("gns3-images"))
    assert client.put("/slots/2201/mounts", json={"student_key": "student42"}).json()["detail"]["code"] == "SOURCE_MISSING"
    (ceph / "gns3-images").mkdir()
    response = client.post("/slots/2201/start", json={"student_key": "student42", "data_mode": "rw"})
    assert response.status_code == 200, response.text
    command = next(c for c in calls if c[:2] == [agent.PCT, "set"])
    assert command[command.index("--mp2") + 1] == f"{ceph}/gns3-images,mp=/opt/gns3/images,ro=1,shared=1"


def test_images_dir_setting(monkeypatch):
    monkeypatch.setenv("LAB_IMAGES_DIR", "gns3-images/")
    assert agent.ceph_subdir("LAB_IMAGES_DIR", "") == Path("gns3-images")


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
    (ceph / agent.STUDENTS_DIR / "evil").symlink_to("/etc")
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
    assert f'{ceph}/{agent.STUDENTS_DIR}/student42,mp=/opt/gns3/projects,ro={ro},shared=1' in calls[-1]


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
    (ceph / agent.STUDENTS_DIR).chmod(0o777)
    assert client.post('/slots/2201/start', json={'student_key': 'student42'}).status_code == 422
    (ceph / agent.STUDENTS_DIR).chmod(0o755)
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


def test_ceph_subdirs(monkeypatch):
    assert agent.CODE_DIR == Path('gns3-ro/slot')
    assert agent.STUDENTS_DIR == Path('gns3-rw/students')
    monkeypatch.setenv('LAB_CODE_DIR', '/code/slot/')
    assert agent.ceph_subdir('LAB_CODE_DIR', 'x') == Path('code/slot')
    for value in ('', '..', 'a/../b', 'a//b', '.hidden', 'a b', '/'):
        monkeypatch.setenv('LAB_CODE_DIR', value)
        with pytest.raises(SystemExit):
            agent.ceph_subdir('LAB_CODE_DIR', 'x')


@pytest.fixture
def student_env(env, monkeypatch):
    monkeypatch.setattr(agent, 'STUDENT_UID', str(os.getuid()))
    monkeypatch.setattr(agent, 'STUDENT_GID', str(os.getgid()))
    chowns = []
    def chown(fd, uid, gid):
        # Unprivileged test directory already belongs to these IDs.
        assert (uid, gid) == (os.getuid(), os.getgid())
        chowns.append((uid, gid))
    monkeypatch.setattr(agent.os, 'fchown', chown)
    return env, chowns


def test_create_student_idempotently(student_env):
    (client, calls, _, _, ceph), chowns = student_env
    for _ in range(2):
        r = client.put('/students/new_student')
        assert r.status_code == 200, r.text
        assert r.json() == {'student_key': 'new_student', 'ready': True}
    assert (ceph / agent.STUDENTS_DIR / 'new_student').stat().st_mode & 0o777 == 0o700
    assert len(chowns) == 1
    assert not any(c[0] == agent.PCT for c in calls)


def test_existing_student_contents_untouched(student_env):
    (client, _, _, _, ceph), chowns = student_env
    marker = ceph / agent.STUDENTS_DIR / 'student42/data'
    marker.write_text('keep')
    assert client.put('/students/student42').status_code == 200
    assert marker.read_text() == 'keep'
    assert chowns == []


def test_wrong_owner_is_not_repaired(student_env, monkeypatch):
    (client, _, _, _, ceph), chowns = student_env
    monkeypatch.setattr(agent, 'STUDENT_UID', str(os.getuid() + 1))
    assert client.put('/students/student42').status_code == 409
    assert chowns == []


@pytest.mark.parametrize('key', ['bad;id', 'bad\n', '..', '$(id)'])
def test_student_invalid_key(student_env, key):
    (client, calls, *_), chowns = student_env
    from urllib.parse import quote
    assert client.put('/students/' + quote(key, safe='')).status_code in (404, 422)
    assert calls == [] and chowns == []


def test_student_symlink_and_unsafe_parent(student_env):
    (client, calls, _, _, ceph), chowns = student_env
    (ceph / agent.STUDENTS_DIR / 'evil').symlink_to(ceph / agent.CODE_DIR)
    assert client.put('/students/evil').status_code == 422
    (ceph / agent.STUDENTS_DIR).chmod(0o777)
    assert client.put('/students/new').status_code == 422
    assert not (ceph / agent.STUDENTS_DIR / 'new').exists()
    assert chowns == []


def test_student_missing_config_and_storage(student_env, monkeypatch):
    (client, calls, *_), chowns = student_env
    monkeypatch.setattr(agent, 'STUDENT_UID', None)
    assert client.put('/students/new').status_code == 503
    assert calls == []
    monkeypatch.setattr(agent, 'STUDENT_UID', str(os.getuid()))
    def unavailable():
        raise agent.HTTPException(503, 'unavailable')
    monkeypatch.setattr(agent, 'validate_cephfs', unavailable)
    assert client.put('/students/new').status_code == 503
    assert chowns == []


def test_student_nested_mount_rejected(student_env, monkeypatch):
    (client, _, _, _, ceph), chowns = student_env
    monkeypatch.setattr(agent, 'run_cmd', lambda *a, **kw: '/elsewhere')
    assert client.put('/students/new').status_code == 422
    assert not (ceph / agent.STUDENTS_DIR / 'new').exists()
    assert chowns == []


def test_student_chown_failure_fails_closed(student_env, monkeypatch):
    (client, _, _, _, ceph), _ = student_env
    def denied(*args):
        raise PermissionError('Ceph denied ownership change')
    monkeypatch.setattr(agent.os, 'fchown', denied)
    assert client.put('/students/new').status_code == 503
    assert (ceph / agent.STUDENTS_DIR / 'new').is_dir()
    assert not list((ceph / agent.STUDENTS_DIR / 'new').iterdir())


def test_student_parent_acl_rejected(student_env, monkeypatch):
    (client, _, _, _, ceph), chowns = student_env
    monkeypatch.setattr(agent.os, 'getxattr', lambda *args: b'acl')
    assert client.put('/students/new').status_code == 422
    assert not (ceph / agent.STUDENTS_DIR / 'new').exists()
    assert chowns == []


def test_mount_destinations_match_repository_slot(env):
    """Compare generated pct mounts to the existing Slot contract across components."""
    import ast
    slot = Path(__file__).resolve().parents[2] / 'slot'
    if not slot.is_dir():
        pytest.skip('Standalone agent checkout has no Slot sources')
    tree = ast.parse((slot / 'run.py').read_text())
    root = next(node.value.args[0].value for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'ROOT' for t in node.targets))
    unit = (slot / 'lab-slot.service').read_text()
    assert f'WorkingDirectory={root}\n' in unit
    client, calls, *_ = env
    assert client.put('/slots/2201/mounts', json={'student_key': 'student42'}).status_code == 200
    command = next(c for c in calls if c[:2] == [agent.PCT, 'set'])
    assert f',mp={root},ro=1,shared=1' in command[command.index('--mp0') + 1]
    config = dict(line.split('=', 1) for line in (slot / 'slot.env.example').read_text().splitlines()
                  if line and not line.startswith('#') and '=' in line)
    projects = str(Path(config['SLOT_PROJECT_LOCK_DIR']).parent)
    assert f',mp={projects},ro=0,shared=1' in command[command.index('--mp1') + 1]


def test_revision_mounted_at_same_slot_destination(env):
    client, calls, _, _, ceph = env
    (ceph / agent.CODE_DIR / 'release1').mkdir()
    result = client.post('/slots/2201/start', json={
        'student_key': 'student42', 'code_revision': 'release1', 'data_mode': 'ro'})
    assert result.status_code == 200, result.text
    command = next(c for c in calls if c[:2] == [agent.PCT, 'set'])
    assert f'{ceph}/{agent.CODE_DIR}/release1,mp=/srv/gns3-ro/slot,ro=1,shared=1' in command
    assert f'{ceph}/{agent.STUDENTS_DIR}/student42,mp=/opt/gns3/projects,ro=1,shared=1' in command


@pytest.mark.parametrize('url,body,status,code', [
    ('/slots/150/start', {'student_key': 'student42'}, 403, 'VMID_OUT_OF_RANGE'),
    ('/slots/2201/start', {'student_key': 'missing'}, 422, 'SOURCE_MISSING'),
])
def test_structured_api_errors(env, url, body, status, code):
    client, *_ = env
    result = client.post(url, json=body)
    assert result.status_code == status
    assert result.json()['detail']['code'] == code
    assert isinstance(result.json()['detail']['message'], str)


def test_student_configuration_and_owner_codes(student_env, monkeypatch):
    (client, *_), _ = student_env
    monkeypatch.setattr(agent, 'STUDENT_UID', None)
    result = client.put('/students/new')
    assert result.status_code == 503
    assert result.json()['detail']['code'] == 'STUDENT_IDS_NOT_CONFIGURED'
    monkeypatch.setattr(agent, 'STUDENT_UID', str(os.getuid() + 1))
    result = client.put('/students/student42')
    assert result.status_code == 409
    assert result.json()['detail']['code'] == 'STUDENT_DIR_OWNER'


def test_validation_errors_remain_fastapi_list(env):
    client, *_ = env
    result = client.post('/slots/2201/stop', json={'force': 'false'})
    assert result.status_code == 422
    assert isinstance(result.json()['detail'], list)


@pytest.mark.parametrize('tool,status,code', [
    ('pct', 409, 'PCT_FAILED'), ('findmnt', 503, 'STORAGE_UNAVAILABLE'),
])
def test_command_error_codes(monkeypatch, tool, status, code):
    import subprocess
    monkeypatch.setattr(agent.subprocess, 'run',
                        lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, '', 'failed'))
    with pytest.raises(agent.HTTPException) as err:
        agent.run_cmd([agent.PCT if tool == 'pct' else agent.FINDMNT, 'status'])
    assert err.value.status_code == status
    assert err.value.detail['code'] == code


def test_membership_and_state_error_codes(env):
    client, _, state, conf, _ = env
    assert client.get('/slots/2202/status').json()['detail']['code'] == 'CT_NOT_ON_NODE'
    (conf / '2201.conf').write_text('unprivileged: 0\ntags: labslot\n')
    assert client.get('/slots/2201/status').json()['detail']['code'] == 'PRIVILEGED_CT'
    (conf / '2201.conf').write_text('unprivileged: 1\ntags: production\n')
    assert client.get('/slots/2201/status').json()['detail']['code'] == 'NOT_LAB_SLOT'
    (conf / '2201.conf').write_text('unprivileged: 1\ntags: labslot\n')
    state['value'] = 'running'
    result = client.post('/slots/2201/start', json={'student_key': 'student42'})
    assert result.json()['detail']['code'] == 'NOT_STOPPED'
    with agent.slot_lock(2201):
        assert client.get('/slots/2201/status').json()['detail']['code'] == 'SLOT_BUSY'


def test_source_unsafe_code(env):
    client, _, _, _, ceph = env
    (ceph / agent.STUDENTS_DIR / 'evil').symlink_to('/etc')
    result = client.post('/slots/2201/start', json={'student_key': 'evil'})
    assert result.status_code == 422
    assert result.json()['detail']['code'] == 'SOURCE_UNSAFE'
