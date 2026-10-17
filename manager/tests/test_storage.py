import os

import pytest

from lab_manager.storage import StorageError, StudentStorage, open_dir, pve_mount_value, safe_component


def make(tmp_path, mode="handover"):
    students = tmp_path / "students"
    slots = tmp_path / "slots"
    students.mkdir()
    (slots / "201" / "mnt").mkdir(parents=True)
    (slots / "202" / "mnt").mkdir(parents=True)
    return StudentStorage(mode=mode, student_root=students, slot_mount_root=slots,
                          pve_host_root="/mnt/pve/cephfs/lab/students")


@pytest.mark.parametrize("bad", ["..", ".", "a/b", "", "-x", "a\x00", "ü", "a..b", "x" * 65, "a b"])
def test_unsafe_components_rejected(bad):
    with pytest.raises(StorageError):
        safe_component(bad)


@pytest.mark.parametrize("slug,uid", [("../etc", 1), ("Main", 1), ("main", 0), ("main", -3), ("main", "5"),
                                      ("main", True), ("a,mp=/", 1)])
def test_student_identity_validated(tmp_path, slug, uid):
    st = make(tmp_path)
    with pytest.raises(StorageError):
        st.ensure_student(slug, uid)


def test_pve_mount_value_and_option_injection():
    value = pve_mount_value("/mnt/pve/cephfs/lab/students", "main", 42, "/opt/gns3/student")
    assert value == "/mnt/pve/cephfs/lab/students/main/42/work,mp=/opt/gns3/student,backup=0"
    for root in ("/mnt/x,shared=1", "/mnt/../etc", "relative", "/mnt/x=y"):
        with pytest.raises(StorageError):
            pve_mount_value(root, "main", 42, "/opt/gns3/student")
    with pytest.raises(StorageError):
        pve_mount_value("/mnt/ok", "main", 42, "/opt/gns3/student,ro=0")


def test_ensure_student_creates_work_and_results(tmp_path):
    st = make(tmp_path)
    st.ensure_student("main", 42)
    base = tmp_path / "students" / "main" / "42"
    assert (base / "work").is_dir() and (base / "results").is_dir()
    (base / "work" / "keep.txt").write_text("data")
    st.ensure_student("main", 42)  # Idempotent, never touches content
    assert (base / "work" / "keep.txt").read_text() == "data"


def test_symlink_in_student_path_is_refused(tmp_path):
    st = make(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "students" / "main").mkdir()
    os.symlink(outside, tmp_path / "students" / "main" / "42")
    with pytest.raises(StorageError):
        st.ensure_student("main", 42)
    assert list(outside.iterdir()) == []


def test_symlinked_work_dir_is_refused(tmp_path):
    st = make(tmp_path)
    st.ensure_student("main", 42)
    base = tmp_path / "students" / "main" / "42"
    os.rmdir(base / "work")
    os.symlink(tmp_path / "outside", base / "work")
    with pytest.raises(StorageError):
        st.ensure_student("main", 42)
    with pytest.raises(StorageError):
        st.attach(201, "main", 42)


def test_symlinked_slot_dir_is_refused(tmp_path):
    st = make(tmp_path)
    st.ensure_student("main", 42)
    os.symlink(tmp_path / "students", tmp_path / "slots" / "203")
    with pytest.raises(StorageError):
        st.attach(203, "main", 42)


def test_attach_and_detach_roundtrip(tmp_path):
    st = make(tmp_path)
    st.ensure_student("main", 42)
    work = tmp_path / "students" / "main" / "42" / "work"
    (work / "result.json").write_text("{}")
    st.attach(201, "main", 42)
    mnt = tmp_path / "slots" / "201" / "mnt"
    assert (mnt / "result.json").exists()
    assert not work.exists()
    assert st.handover_state(201, "main", 42) == "slot"
    st.attach(201, "main", 42)  # Idempotent
    st.ensure_student("main", 42)  # Does not create a second work/ while mounted
    assert not work.exists()
    st.detach(201, "main", 42)
    assert (work / "result.json").exists()
    assert list(mnt.iterdir()) == []  # Placeholder is back and empty
    assert not (tmp_path / "slots" / "201" / "empty").exists()
    st.detach(201, "main", 42)  # Idempotent
    st.check_slot_placeholder(201)


def test_attach_resumes_after_crash_between_renames(tmp_path):
    st = make(tmp_path)
    st.ensure_student("main", 42)
    base = tmp_path / "students" / "main" / "42"
    (base / ".mounted").write_text("201\n")
    os.rename(tmp_path / "slots" / "201" / "mnt", tmp_path / "slots" / "201" / "empty")
    st.attach(201, "main", 42)
    assert st.handover_state(201, "main", 42) == "slot"
    # And detach after a crash in the middle of detaching.
    os.rename(tmp_path / "slots" / "201" / "mnt", base / "work")
    st.detach(201, "main", 42)
    assert (base / "work").is_dir() and (tmp_path / "slots" / "201" / "mnt").is_dir()


def test_slot_occupied_by_another_student_is_refused(tmp_path):
    st = make(tmp_path)
    st.ensure_student("main", 1)
    st.ensure_student("main", 2)
    st.attach(201, "main", 1)
    with pytest.raises(StorageError):
        st.attach(201, "main", 2)
    with pytest.raises(StorageError):
        st.detach(201, "main", 2)  # Student 2 has no marker for slot 201
    st.detach(201, "main", 1)
    st.attach(201, "main", 2)
    assert st.handover_state(201, "main", 2) == "slot"


def test_marker_for_other_slot_is_not_trusted(tmp_path):
    st = make(tmp_path)
    st.ensure_student("main", 1)
    st.attach(201, "main", 1)
    with pytest.raises(StorageError):
        st.detach(202, "main", 1)


def test_results_are_never_part_of_the_mount(tmp_path):
    st = make(tmp_path)
    st.ensure_student("main", 42)
    st.attach(201, "main", 42)
    assert not (tmp_path / "slots" / "201" / "mnt" / "results").exists()
    assert (tmp_path / "students" / "main" / "42" / "results").is_dir()


def test_open_dir_nofollow(tmp_path):
    (tmp_path / "a").mkdir()
    os.symlink(tmp_path / "a", tmp_path / "b")
    with open_dir(tmp_path, ("a",)):
        pass
    with pytest.raises(StorageError):
        with open_dir(tmp_path, ("b",)):
            pass


def test_pve_config_mode_checks_local_tree(tmp_path):
    st = make(tmp_path, mode="pve_config")
    st.ensure_student("main", 42)
    assert st.pve_mount("main", 42) == {
        "mp0": "/mnt/pve/cephfs/lab/students/main/42/work,mp=/opt/gns3/student,backup=0"}
    os.rmdir(tmp_path / "students" / "main" / "42" / "work")
    os.symlink("/etc", tmp_path / "students" / "main" / "42" / "work")
    with pytest.raises(StorageError):
        st.pve_mount("main", 42)
