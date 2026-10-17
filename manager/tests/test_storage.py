import pytest

from lab_manager.storage import StorageError, student_key


def test_student_key_fits_agent_rules():
    assert student_key("main", 42) == "main-42"
    assert student_key("site_2", 7) == "site_2-7"


@pytest.mark.parametrize("slug,uid", [("../etc", 1), ("Main", 1), ("main", 0), ("main", -3), ("main", "5"),
                                      ("main", True), ("a,b", 1), ("a.b", 1), ("", 1), ("x" * 40, 1)])
def test_unsafe_identity_rejected(slug, uid):
    with pytest.raises(StorageError):
        student_key(slug, uid)
