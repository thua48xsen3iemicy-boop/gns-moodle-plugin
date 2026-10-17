"""Session error codes shown to the plugin (docs/CONTRACTS.md §2.5).

retryable: the student may simply press "start" again. Slots are not bound to students, so a
failure tied to one slot is retryable (the next session gets another slot; the failed slot
waits for the operator). Problems of configuration, of the lab itself or of the student's
directory are not: they repeat until an administrator acts.
"""

SESSION_ERRORS: dict[str, bool] = {
    # Slot or node level: another slot will be used next time.
    "SLOT_START_FAILED": True,
    "SLOT_LAB_FAILED": True,
    "SLOT_OPERATION_FAILED": True,
    "SLOT_OPERATION_UNKNOWN": True,
    "SLOT_STOP_FAILED": True,
    "SLOT_PROJECT_UNKNOWN": True,
    "SLOT_REJECTED": True,
    "SLOT_NOT_CONFIGURED": True,
    "WRONG_STUDENT_MOUNT": True,
    "CT_SIZE_NOT_APPLIED": True,
    "TIMEOUT": True,
    "QUEUE_TIMEOUT": True,
    "INTERNAL": True,
    # Configuration, lab or student directory: an administrator has to act.
    "PVE_REJECTED": False,
    "AGENT_REJECTED": False,
    "FILESYSTEM": False,
    "STUDENT_DIR_MISSING": False,
    "STUDENT_DIR_REJECTED": False,
    "LAB_UNAVAILABLE": False,
    "SLOT_COLD_RESUME_UNSUPPORTED": False,
    "NO_SLOTS": False,
    "STUDENT_DIR_BUSY": False,
}


def public_error(error: dict | None) -> dict | None:
    """What the plugin gets: code and retryable only; the text is in status_message."""
    if not error:
        return None
    code = str(error.get("code", "INTERNAL"))
    return {"code": code, "retryable": SESSION_ERRORS.get(code, False)}
