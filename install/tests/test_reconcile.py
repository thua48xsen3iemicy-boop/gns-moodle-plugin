"""lab-reconcile.py on a model of the agent's state directory and of /proc; no pct, no agent."""

import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lab_reconcile", REPO / "install/lab-reconcile.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

VMID = 501


@pytest.fixture
def node(tmp_path):
    d = {name: tmp_path / name for name in ("fence", "lock", "backup", "proc")}
    for p in d.values():
        p.mkdir()
    d["record"] = d["fence"] / f"{VMID}.json"
    return d


def write(node, **record):
    node["record"].write_text(json.dumps(record))


def run(node, *, yes=True, ct_state="stopped", manager_state="maintenance", out=None):
    return rc.reconcile(VMID, yes=yes, ct_state=ct_state, manager_state=manager_state, fence_dir=node["fence"],
                        lock_dir=node["lock"], backup_dir=node["backup"], proc=node["proc"],
                        stamp="20261028T004500", out=(out or (lambda line: None)))


def fake_process(node, pid, *argv):
    d = node["proc"] / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")


def test_marks_are_cleared_and_epoch_and_generation_are_kept(node):
    write(node, epoch=7, generation=12, inflight=True, uncertain=True, command="exec")
    assert run(node) is True
    assert json.loads(node["record"].read_text()) == {"epoch": 7, "generation": 12}
    assert stat.S_IMODE(node["record"].stat().st_mode) == 0o600
    assert not [p for p in node["fence"].iterdir() if p.name != node["record"].name]  # No temporary file left


def test_a_copy_of_the_old_record_is_saved_first(node):
    write(node, epoch=7, generation=12, uncertain=True, command="exec")
    old = node["record"].read_text()
    run(node)
    (copy,) = node["backup"].iterdir()
    assert copy.name == f"lab-fence-{VMID}-20261028T004500.json"
    assert copy.read_text() == old and stat.S_IMODE(copy.stat().st_mode) == 0o600


def test_other_fields_of_the_record_survive(node):
    write(node, epoch=3, generation=4, uncertain=True, command="start", note="kept")
    run(node)
    assert json.loads(node["record"].read_text()) == {"epoch": 3, "generation": 4, "note": "kept"}


def test_without_yes_it_only_shows(node):
    write(node, epoch=7, generation=12, uncertain=True, command="exec")
    before = node["record"].read_text()
    lines = []
    assert run(node, yes=False, out=lines.append) is False
    assert node["record"].read_text() == before and not list(node["backup"].iterdir())
    assert any("--yes" in line for line in lines) and any('"uncertain": true' in line for line in lines)


@pytest.mark.parametrize("state", ["running", "", "unknown"])
def test_a_ct_that_is_not_stopped_is_refused(node, state):
    write(node, epoch=7, generation=12, uncertain=True)
    with pytest.raises(rc.Refused, match="pct shutdown 501"):
        run(node, ct_state=state)
    assert json.loads(node["record"].read_text())["uncertain"] is True


def test_nothing_to_reconcile_is_refused(node):
    write(node, epoch=7, generation=12)
    with pytest.raises(rc.Refused, match="nothing to reconcile"):
        run(node)
    write(node, epoch=7, generation=12, command="start")  # A leftover command is not a mark
    with pytest.raises(rc.Refused, match="nothing to reconcile"):
        run(node)


def test_no_record_and_damaged_records_are_refused_and_left_alone(node):
    with pytest.raises(rc.Refused, match="no record"):
        run(node)
    for text in ("{broken", json.dumps({"epoch": 0, "generation": 1, "uncertain": True}),
                 json.dumps({"epoch": "1", "generation": 1, "uncertain": True}), json.dumps([1])):
        node["record"].write_text(text)
        with pytest.raises(rc.Refused):
            run(node)
        assert node["record"].read_text() == text
    assert not list(node["backup"].iterdir())


@pytest.mark.parametrize("state", ["free", "busy", "releasing", "", "unknown"])
def test_a_slot_the_manager_still_uses_is_refused(node, state):
    write(node, epoch=7, generation=12, uncertain=True)
    with pytest.raises(rc.Refused, match="manager reports"):
        run(node, manager_state=state)


@pytest.mark.parametrize("state", ["maintenance", "failed"])
def test_held_slots_are_accepted(node, state):
    write(node, epoch=7, generation=12, uncertain=True)
    assert run(node, manager_state=state) is True


@pytest.mark.parametrize("argv", [
    ("/usr/bin/perl", "/usr/sbin/pct", "start", "501"),
    ("pct", "exec", "501", "--", "/usr/local/sbin/lab-slot-init"),
    ("lxc-start", "-n", "501", "-F"),
    ("/usr/bin/lxc-stop", "-n", "501"),
])
def test_a_running_pct_or_lxc_command_is_refused(node, argv):
    write(node, epoch=7, generation=12, uncertain=True)
    fake_process(node, 4242, *argv)
    with pytest.raises(rc.Refused, match="4242"):
        run(node)
    assert json.loads(node["record"].read_text())["uncertain"] is True


def test_the_monitor_of_a_stopped_ct_and_other_slots_do_not_count(node):
    write(node, epoch=7, generation=12, uncertain=True)
    fake_process(node, 10, "[lxc monitor] /var/lib/lxc 501")
    fake_process(node, 11, "/usr/bin/perl", "/usr/sbin/pct", "start", "502")
    fake_process(node, 12, "lxc-start", "-n", "5010")
    fake_process(node, 13, "/usr/bin/python3", "-m", "something", "501")
    (node["proc"] / "self").mkdir()  # Not a pid
    assert run(node) is True


def test_a_slot_the_agent_is_working_on_is_refused(node):
    import fcntl

    write(node, epoch=7, generation=12, uncertain=True)
    with open(node["lock"] / f"{VMID}.lock", "a+") as agent_lock:
        fcntl.flock(agent_lock, fcntl.LOCK_EX)
        with pytest.raises(rc.Refused, match="lock is held"):
            run(node)
    assert json.loads(node["record"].read_text())["uncertain"] is True and not list(node["backup"].iterdir())


def test_an_existing_copy_is_never_overwritten(node):
    write(node, epoch=7, generation=12, uncertain=True)
    (node["backup"] / f"lab-fence-{VMID}-20261028T004500.json").write_text("older")
    with pytest.raises(FileExistsError):
        run(node)
    assert json.loads(node["record"].read_text())["uncertain"] is True


def test_the_command_line_refuses_a_running_ct_before_it_touches_anything(monkeypatch, capsys):
    monkeypatch.setattr(rc.os, "geteuid", lambda: 0)
    assert rc.main([str(VMID), "--yes", "--ct-state", "running", "--manager-state", "maintenance"]) == 1
    assert "pct shutdown 501" in capsys.readouterr().err


def test_the_command_line_needs_root(monkeypatch, capsys):
    monkeypatch.setattr(rc.os, "geteuid", lambda: 1000)
    assert rc.main([str(VMID), "--ct-state", "stopped", "--manager-state", "failed"]) == 1
    assert "root" in capsys.readouterr().err
