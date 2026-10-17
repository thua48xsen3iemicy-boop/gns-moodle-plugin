"""Run only the embedded preparation block, never systemctl or pct."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def prepare(tmp_path):
    source = (Path(__file__).resolve().parents[1] / 'guest/lab-slot-init').read_text()
    block = source.split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
    projects = tmp_path / 'projects'
    projects.mkdir()
    # Substitute the test directory and current identity; never need actual root.
    block = block.replace('"/opt/gns3/projects"', repr(str(projects)))
    block = block.replace('pwd.getpwnam("gns3")', 'pwd.getpwuid(os.getuid())')
    script = tmp_path / 'prepare.py'
    script.write_text(block)
    # Exercise and assert privilege-drop order without privileged test setup.
    harness = '''
import os, runpy, sys
calls = []
def groups(value):
    assert value == []
    calls.append("groups")
def gid(value):
    assert value == os.getgid() and calls == ["groups"]
    calls.append("gid")
def uid(value):
    assert value == os.getuid() and calls == ["groups", "gid"]
    calls.append("uid")
os.setgroups, os.setgid, os.setuid = groups, gid, uid
runpy.run_path(sys.argv[1])
assert calls == ["groups", "gid", "uid"]
'''
    def run():
        return subprocess.run([sys.executable, '-c', harness, str(script)],
                              capture_output=True, text=True, timeout=5)
    return projects, run


def test_guest_locks_created_and_repeat_preserves_data(prepare):
    projects, run = prepare
    assert run().returncode == 0
    locks = projects / '.slot-locks'
    assert locks.stat().st_mode & 0o777 == 0o700
    (locks / 'project.lock').write_text('keep')
    assert run().returncode == 0
    assert (locks / 'project.lock').read_text() == 'keep'


@pytest.mark.parametrize('kind', ['symlink', 'file', 'unsafe_mode'])
def test_guest_locks_reject_unsafe_existing_entry(prepare, kind):
    projects, run = prepare
    locks = projects / '.slot-locks'
    other = projects / 'other'
    other.mkdir(mode=0o755)
    other.chmod(0o755)
    if kind == 'symlink':
        locks.symlink_to(other)
    elif kind == 'file':
        locks.write_text('keep')
    else:
        locks.mkdir(mode=0o755)
        locks.chmod(0o755)
    assert run().returncode != 0
    assert other.stat().st_mode & 0o777 == 0o755
    if kind == 'file':
        assert locks.read_text() == 'keep'
