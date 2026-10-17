"""Slot consumes a supplied directory; it does not arrange versions or mounts."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import release
import run


def test_fixed_mount_unit_and_no_activation_tool():
    assert run.ROOT == Path("/srv/gns3-ro/slot")
    unit = (release.ROOT / "lab-slot.service").read_text()
    assert "WorkingDirectory=/srv/gns3-ro/slot" in unit
    assert (
        "ExecStart=/srv/gns3-ro/slot/venv/bin/python /srv/gns3-ro/slot/run.py" in unit
    )
    assert not (release.ROOT / "tools/activate_release.py").exists()


def test_runner_plain_directory_correct_venv(tmp_path, monkeypatch):
    root = tmp_path / "slot"
    root.mkdir()
    (root / "venv").mkdir()
    assert not root.is_symlink()
    monkeypatch.setattr(run, "__file__", str(root / "run.py"))
    monkeypatch.setattr(sys, "prefix", str(root / "venv"))
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(release, "preflight", lambda: {"ok": True})
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: calls.append((a, kw)))
    run.main(root)
    assert Path.cwd() == root
    assert calls == [(("main:app",), {"host": "127.0.0.1", "port": 8080, "workers": 1})]
    assert sorted(p.name for p in root.iterdir()) == ["venv"]


def test_wrong_venv_and_incomplete_code_do_not_start(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "__file__", str(tmp_path / "run.py"))
    with pytest.raises(SystemExit, match="RELEASE_VENV_MISMATCH"):
        run.main(tmp_path)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        release, "preflight", lambda: {"ok": False, "code": "RELEASE_INTEGRITY_ERROR"}
    )
    with pytest.raises(SystemExit, match="RELEASE_INTEGRITY_ERROR"):
        run.main(tmp_path)


def test_check_plain_readonly_directory_without_source_writes(tmp_path):
    root = tmp_path / "slot"
    shutil.copytree(
        release.ROOT,
        root,
        ignore=shutil.ignore_patterns(
            "venv", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"
        ),
    )
    assert not root.is_symlink()
    before = {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    for p in root.rglob("*"):
        p.chmod(0o555 if p.is_dir() else 0o444)
    root.chmod(0o555)
    try:
        result = subprocess.run(
            [sys.executable, str(root / "release.py"), "check"],
            cwd=root,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        after = {
            p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
        }
        assert before == after
        assert not list(root.rglob("__pycache__"))
        (root / "api").chmod(0o755)
        (root / "api/health.py").chmod(0o644)
        (root / "api/health.py").unlink()
        assert release.verify(root)["ok"] is False
    finally:
        root.chmod(0o755)
        for p in root.rglob("*"):
            p.chmod(0o755 if p.is_dir() else 0o644)
