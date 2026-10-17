import os
import stat

import pytest

from lab_manager.storage import StorageError, StudentStorage, open_dir, safe_component, student_key


@pytest.mark.parametrize("bad", ["..", ".", "a/b", "", "-x", "a\x00", "ü", "a..b", "x" * 65, "a b"])
def test_unsafe_components_rejected(bad):
    with pytest.raises(StorageError):
        safe_component(bad)


def test_student_key_fits_agent_rules():
    assert student_key("main", 42) == "main-42"
    for slug, uid in [("../etc", 1), ("Main", 1), ("main", 0), ("main", -3), ("main", "5"), ("main", True),
                      ("a,b", 1), ("a.b", 1)]:
        with pytest.raises(StorageError):
            student_key(slug, uid)


def test_ensure_student_creates_dirs_with_modes(tmp_path):
    st = StudentStorage(tmp_path)
    st.ensure_student("main-42")
    base = tmp_path / "main-42"
    assert stat.S_IMODE(base.stat().st_mode) == 0o2770
    assert stat.S_IMODE((base / "results").stat().st_mode) == 0o2750  # Group (slot) cannot write
    (base / "project.txt").write_text("data")
    st.ensure_student("main-42")  # Idempotent, never touches content
    assert (base / "project.txt").read_text() == "data"
    assert st.check_student("main-42")


def test_symlinked_student_dir_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "students"
    root.mkdir()
    os.symlink(outside, root / "main-42")
    st = StudentStorage(root)
    with pytest.raises(StorageError):
        st.ensure_student("main-42")
    assert not st.check_student("main-42")
    assert list(outside.iterdir()) == []


def test_symlinked_results_is_refused(tmp_path):
    st = StudentStorage(tmp_path)
    (tmp_path / "main-42").mkdir()
    os.symlink("/etc", tmp_path / "main-42" / "results")
    with pytest.raises(StorageError):
        st.ensure_student("main-42")


@pytest.mark.parametrize("key", ["../x", "a/b", "", ".hidden", "a b"])
def test_bad_keys(tmp_path, key):
    with pytest.raises(StorageError):
        StudentStorage(tmp_path).ensure_student(key)


def test_open_dir_nofollow(tmp_path):
    (tmp_path / "a").mkdir()
    os.symlink(tmp_path / "a", tmp_path / "b")
    with open_dir(tmp_path, ("a",)):
        pass
    with pytest.raises(StorageError):
        with open_dir(tmp_path, ("b",)):
            pass
