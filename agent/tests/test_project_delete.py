"""DELETE /students/{key}/projects/{id}: a stopped project and the slot's state for it go away."""
import os
import uuid

import pytest
import agent
from test_agent import env


@pytest.fixture
def project(env):
    client, calls, state, conf, ceph = env
    pid = str(uuid.uuid4())
    student = ceph / agent.STUDENTS_DIR / 'student42'
    disk = student / pid / 'project-files' / 'qemu' / str(uuid.uuid4()) / 'hda_disk.qcow2'
    disk.parent.mkdir(parents=True)
    disk.write_bytes(b'overlay')
    (student / pid / 'lab.gns3').write_text('{}')
    (student / '.lab-slot' / 'labs').mkdir(parents=True)
    (student / '.lab-slot' / 'labs' / f'{pid}.json').write_text('{}')
    (student / '.lab-slot' / 'labs' / f'.{pid}.abc.tmp').write_text('{}')
    (student / '.slot-locks').mkdir()
    (student / '.slot-locks' / f'{pid}.lock').write_text('')
    other = str(uuid.uuid4())
    (student / other).mkdir()
    (student / '.lab-slot' / 'labs' / f'{other}.json').write_text('{}')
    return client, student, pid, other, conf


def url(pid, key='student42'):
    return f'/students/{key}/projects/{pid}'


def test_deletes_project_and_slot_state_only(project):
    client, student, pid, other, _ = project
    response = client.delete(url(pid))
    assert response.status_code == 200, response.text
    assert response.json() == {'student_key': 'student42', 'project_id': pid, 'deleted': True}
    assert sorted(p.name for p in student.iterdir()) == ['.lab-slot', '.slot-locks', other]
    assert [p.name for p in (student / '.lab-slot' / 'labs').iterdir()] == [f'{other}.json']
    assert list((student / '.slot-locks').iterdir()) == []


def test_idempotent(project):
    client, student, pid, *_ = project
    assert client.delete(url(pid)).json()['deleted'] is True
    assert client.delete(url(pid)).json()['deleted'] is False
    assert client.delete(url(pid, 'nobody7')).json()['deleted'] is False


def test_finishes_an_interrupted_delete(project):
    client, student, pid, *_ = project
    (student / pid).rename(student / f'.purge-{pid}')
    assert client.delete(url(pid)).json()['deleted'] is True
    assert not (student / f'.purge-{pid}').exists()


def test_symlinks_are_removed_not_followed(project, tmp_path):
    client, student, pid, _, _ = project
    victim = tmp_path / 'victim'
    victim.mkdir()
    (victim / 'keep').write_text('host file')
    inner = student / pid / 'project-files' / 'escape'
    inner.symlink_to(victim, target_is_directory=True)
    labs = student / '.lab-slot' / 'labs'
    (labs / f'{pid}.json').unlink()
    (labs / f'{pid}.json').symlink_to(victim / 'keep')
    assert client.delete(url(pid)).status_code == 200
    assert (victim / 'keep').read_text() == 'host file'


def test_project_replaced_by_symlink(project, tmp_path):
    client, student, pid, _, _ = project
    victim = tmp_path / 'victim'
    victim.mkdir()
    (victim / 'keep').write_text('host file')
    os.rename(student / pid, tmp_path / 'moved')
    (student / pid).symlink_to(victim, target_is_directory=True)
    assert client.delete(url(pid)).json()['deleted'] is True
    assert (victim / 'keep').exists() and not os.path.lexists(student / pid)


def test_state_directory_replaced_by_symlink_is_skipped(project, tmp_path):
    client, student, pid, _, _ = project
    victim = tmp_path / 'victim'
    victim.mkdir()
    (victim / f'{pid}.lock').write_text('host file')
    os.rename(student / '.slot-locks', tmp_path / 'locks-moved')
    (student / '.slot-locks').symlink_to(victim, target_is_directory=True)
    assert client.delete(url(pid)).status_code == 200
    assert (victim / f'{pid}.lock').exists()


def test_busy_while_assigned_to_a_ct(project):
    client, student, pid, _, conf = project
    (conf / '2202.conf').write_text(f'mp1: {student},mp=/opt/gns3/projects\n')
    response = client.delete(url(pid))
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'STUDENT_DIR_BUSY'
    assert (student / pid).exists()


def test_student_directory_symlink_rejected(project, tmp_path):
    client, student, pid, _, _ = project
    os.rename(student, tmp_path / 'moved')
    student.symlink_to(tmp_path / 'moved', target_is_directory=True)
    response = client.delete(url(pid))
    assert response.status_code == 422
    assert (tmp_path / 'moved' / pid).exists()


@pytest.mark.parametrize('pid', ['not-a-uuid', '..', str(uuid.uuid4()).upper()])
def test_project_id_must_be_canonical_uuid(project, pid):
    client, student, own, *_ = project
    assert client.delete(url(pid)).status_code in (404, 405, 422)  # `..` is normalized to another route
    assert (student / own).exists()


def test_capability(project):
    client, *_ = project
    assert client.get('/capabilities').json()['projects_delete'] is True
