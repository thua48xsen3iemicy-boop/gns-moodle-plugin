"""The image audit rejects broken backing chains without changing any files."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('image_audit', Path(__file__).parents[1] / 'check-derived-images.py')
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


@pytest.fixture
def directory(tmp_path, monkeypatch):
    base = tmp_path / 'base.qcow2'
    base.write_bytes(b'base image')
    image = tmp_path / 'd-123456789abc.qcow2'
    image.write_bytes(b'changes')
    image.chmod(0o444)
    checksum = image.with_name(image.name + '.md5sum')
    checksum.write_text(hashlib.md5(image.read_bytes()).hexdigest())
    checksum.chmod(0o444)
    monkeypatch.setattr(audit, 'info', lambda path: {'format': 'qcow2', **(
        {'backing-filename': 'base.qcow2'} if path.name.startswith('d-') else {})})
    return tmp_path, image, checksum


def test_valid_directory_and_pinned_hash(directory):
    root, image, _ = directory
    assert audit.check(root) == {'ok': True, 'images': 1, 'errors': []}
    catalog = root / 'images.json'
    catalog.write_text(json.dumps({'images': [{'kind': 'qemu', 'file': 'base.qcow2',
        'sha256': 'sha256:' + hashlib.sha256((root / 'base.qcow2').read_bytes()).hexdigest()}]}))
    assert audit.check(root, catalog)['ok']
    (root / 'base.qcow2').write_bytes(b'changed base')
    assert 'SHA-256' in audit.check(root, catalog)['errors'][0]


@pytest.mark.parametrize('backing', ['/tmp/base.qcow2', '../base.qcow2', 'd-000000000000.qcow2', 'missing.qcow2'])
def test_invalid_backing(directory, monkeypatch, backing):
    root, *_ = directory
    monkeypatch.setattr(audit, 'info', lambda path: {'format': 'qcow2', 'backing-filename': backing})
    assert not audit.check(root)['ok']


def test_permissions_sidecar_and_symlinks(directory):
    root, image, checksum = directory
    image.chmod(0o644)
    assert '0444' in audit.check(root)['errors'][0]
    image.chmod(0o444)
    checksum.unlink()
    assert 'sidecar' in audit.check(root)['errors'][0]
    image.unlink(); image.symlink_to(root / 'base.qcow2')
    assert 'symlink' in audit.check(root)['errors'][0]
