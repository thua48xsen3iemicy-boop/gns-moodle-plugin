"""Capture uses real subprocesses with a tiny qemu-img protocol fake."""
import hashlib
import json
import os
from pathlib import Path
import uuid

import pytest
import agent
from test_agent import env


@pytest.fixture
def capture(env, monkeypatch, tmp_path):
    client, calls, state, conf, ceph = env
    images = ceph / 'images' / 'QEMU'
    images.mkdir(parents=True)
    monkeypatch.setattr(agent, 'IMAGES_DIR', Path('images'))
    executable = tmp_path / 'qemu-img'
    executable.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
p = Path(args[-1])
if args[0] == 'info':
    print(p.read_text())
elif args[0] == 'rebase':
    data = json.loads(p.read_text())
    data['backing-filename'] = args[args.index('-b') + 1]
    if '-u' not in args:
        data['merged'] = True
    p.write_text(json.dumps(data))
elif args[0] != 'check':
    sys.exit(1)
''')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    base = images / 'base.qcow2'
    base.write_text(json.dumps({'format': 'qcow2', 'virtual-size': 1048576}))
    project_id, node_id = str(uuid.uuid4()), str(uuid.uuid4())
    project = ceph / agent.STUDENTS_DIR / 'student42' / project_id
    disk = project / 'project-files' / 'qemu' / node_id / 'hda_disk.qcow2'
    disk.parent.mkdir(parents=True)
    disk.write_text(json.dumps({'format': 'qcow2', 'virtual-size': 1048576,
                               'backing-filename': '/opt/gns3/images/QEMU/base.qcow2'}))
    (project / 'lab.gns3').write_text(json.dumps({'topology': {'nodes': [
        {'name': 'R1', 'node_id': node_id, 'node_type': 'qemu'}]}}))
    body = dict(student_key='student42', project_id=project_id, node_name='R1',
                parent_file='base.qcow2', base_file='base.qcow2',
                base_sha256='sha256:' + hashlib.sha256(base.read_bytes()).hexdigest(),
                image_file='d-123456789abc.qcow2', max_bytes=100000)
    return client, body, images, disk, conf


def test_first_capture_and_no_overwrite(capture):
    client, body, images, disk, _ = capture
    original = disk.read_bytes()
    response = client.post('/images/capture', json=body)
    assert response.status_code == 200, response.text
    image = images / body['image_file']
    assert json.loads(image.read_text())['backing-filename'] == 'base.qcow2'
    assert image.stat().st_mode & 0o777 == 0o444
    assert image.with_suffix('.qcow2.md5sum').read_text() == hashlib.md5(image.read_bytes()).hexdigest()
    assert response.json()['sha256'] == 'sha256:' + hashlib.sha256(image.read_bytes()).hexdigest()
    assert disk.read_bytes() == original
    assert not list(images.glob('.*.part'))
    assert client.post('/images/capture', json=body).json()['detail']['code'] == 'IMAGE_EXISTS'


def test_repeat_merges_parent_safely(capture):
    client, body, images, disk, _ = capture
    parent = 'd-000000000000.qcow2'
    (images / parent).write_text(json.dumps({'format': 'qcow2', 'virtual-size': 1048576,
                                           'backing-filename': 'base.qcow2'}))
    info = json.loads(disk.read_text())
    info['backing-filename'] = '/opt/gns3/images/QEMU/' + parent
    disk.write_text(json.dumps(info))
    response = client.post('/images/capture', json={**body, 'parent_file': parent})
    assert response.status_code == 200, response.text
    assert json.loads((images / body['image_file']).read_text())['merged']


@pytest.mark.parametrize('change,code', [
    ({'base_sha256': 'sha256:' + '0' * 64}, 'CAPTURE_BASE_MISMATCH'),
    ({'max_bytes': 1}, 'CAPTURE_TOO_LARGE'),
    ({'node_name': 'absent'}, 'CAPTURE_NODE_MISSING'),
    ({'parent_file': 'wrong.qcow2'}, 'CAPTURE_BASE_MISMATCH'),
])
def test_capture_rejections(capture, change, code):
    client, body, images, _, _ = capture
    response = client.post('/images/capture', json={**body, **change})
    assert response.json()['detail']['code'] == code, response.text
    assert not (images / body['image_file']).exists()
    assert not list(images.glob('.*.part'))


def test_busy_directory_even_with_stopped_ct(capture):
    client, body, _, disk, conf = capture
    student = disk.parents[4]
    (conf / '2201.conf').write_text(f'mp1: {student},mp=/opt/gns3/projects\n')
    response = client.post('/images/capture', json=body)
    assert response.json()['detail']['code'] == 'STUDENT_DIR_BUSY', response.text


@pytest.mark.parametrize('directory', [False, True])
def test_no_symlink_components(capture, directory):
    client, body, _, disk, _ = capture
    if directory:
        original = disk.parent
        moved = original.with_name('moved')
    else:
        original = disk
        moved = disk.with_name('moved.qcow2')
    original.rename(moved)
    original.symlink_to(moved, target_is_directory=directory)
    response = client.post('/images/capture', json=body)
    assert response.json()['detail']['code'] == 'CAPTURE_PATH_UNSAFE', response.text


@pytest.mark.parametrize('change', [{'max_bytes': True}, {'parent_file': '../base.qcow2'},
                                   {'image_file': 'd-123456789abc.qcow2\n'}, {'extra': 1}])
def test_capture_strict_model(capture, change):
    client, body, *_ = capture
    assert client.post('/images/capture', json={**body, **change}).status_code == 422
