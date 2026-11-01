"""Small privileged LXC helper, authenticated at the TLS layer by Uvicorn mTLS.

Intentionally NOT a general-purpose command runner or an alternative Proxmox API.
This service must be accessible ONLY from the Lab Manager management network.
"""
from __future__ import annotations

import errno
import hashlib
import shutil
import time
import uuid
import json
import tempfile
from contextvars import ContextVar
import logging
import fcntl
import os
import re
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Request, Path as ApiPath
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

log = logging.getLogger("uvicorn.error")
ADMIN_UIDS = (0,)

PCT = "/usr/sbin/pct"
FINDMNT = "/usr/bin/findmnt"
CEPH_ROOT = Path(os.environ.get("LAB_CEPH_ROOT", "/mnt/pve/cfs"))
CONF_DIR = Path("/etc/pve/lxc")
LOCK_DIR = Path("/run/lab-node-agent")
FENCE_DIR = Path(os.environ.get("LAB_FENCE_DIR", "/var/lib/lab-node-agent"))
ACTIVE_SLOT = ContextVar("agent_active_slot", default=None)
VMID_MIN = int(os.environ.get("LAB_VMID_MIN", "2000"))
VMID_MAX = int(os.environ.get("LAB_VMID_MAX", "2999"))
REQUIRED_TAG = "labslot"
# Host-side mapped IDs; intentionally unset until the template mapping is known.
STUDENT_UID = os.environ.get("LAB_STUDENT_HOST_UID")
STUDENT_GID = os.environ.get("LAB_STUDENT_HOST_GID")
ALLOWED_FS = frozenset(("ceph", "fuse.ceph", "fuse.ceph-fuse"))

# Limits for `pct start` and for one `pct exec` (a guest start action) on the route /start.
PCT_START_TIMEOUT = 120
GUEST_ACTION_TIMEOUT_MIN, GUEST_ACTION_TIMEOUT_MAX = 30, 600
# Most guest actions one /start request may carry (Start.actions max_length).
START_ACTIONS_MAX = 3


def guest_action_timeout() -> int:
    """Seconds for one `pct exec` inside the CT; administrator-defined, bounded."""
    raw = os.environ.get("LAB_GUEST_ACTION_TIMEOUT", "120").strip()
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit("LAB_GUEST_ACTION_TIMEOUT must be an integer number of seconds")
    if not GUEST_ACTION_TIMEOUT_MIN <= value <= GUEST_ACTION_TIMEOUT_MAX:
        raise SystemExit(
            f"LAB_GUEST_ACTION_TIMEOUT must be {GUEST_ACTION_TIMEOUT_MIN}-{GUEST_ACTION_TIMEOUT_MAX} seconds")
    return value


GUEST_ACTION_TIMEOUT = guest_action_timeout()


def start_budget_seconds(actions: int = START_ACTIONS_MAX) -> int:
    """Longest the route /start can take for this many guest actions (mount edits not counted)."""
    return PCT_START_TIMEOUT + actions * GUEST_ACTION_TIMEOUT


def ceph_subdir(name: str, default: str) -> Path:
    """Administrator-defined directory below CEPH_ROOT, on the same CephFS mount."""
    parts = os.environ.get(name, default).strip().strip("/").split("/")
    # Leading '.' is not a safe first character, so '.' and '..' are rejected too.
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", part) for part in parts):
        raise SystemExit(f"{name} must be a relative path of safe directory names")
    return Path(*parts)


def snapshot_name(name: str, default: str) -> str:
    """Administrator-defined Proxmox snapshot name."""
    value = os.environ.get(name, default)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,39}", value) is None:
        raise SystemExit(f"{name} must be a Proxmox snapshot name")
    return value


# Slot code (git checkout, RO) and student data (RW) are separate trees on one CephFS.
CODE_DIR = ceph_subdir("LAB_CODE_DIR", "gns3-ro/slot")
STUDENTS_DIR = ceph_subdir("LAB_STUDENTS_DIR", "gns3-rw/students")
# Optional shared GNS3 images (QEMU/, IOU/, firmware), mounted read-only at /opt/gns3/images as mp2.
# Unset: mp2 is left alone (images in the slot's rootfs or on a mount point of the administrator).
IMAGES_DIR = ceph_subdir("LAB_IMAGES_DIR", "") if os.environ.get("LAB_IMAGES_DIR", "").strip() else None

# The clean state of a slot: the template plus the slot's own identity, snapshotted once by
# install/lab-provision. Never a client value: `reset` rolls back to this name and no other.
CLEAN_SNAPSHOT = snapshot_name("LAB_CLEAN_SNAPSHOT", "lab-clean")

# Each action runs as root INSIDE the container, not in the host namespace.
# Do not expose client-provided executable names, shell strings or argument lists.
GUEST_ACTIONS: dict[str, tuple[str, ...]] = {
    "init_slot": ("/usr/local/sbin/lab-slot-init",),
    "daemon_reload": ("/usr/bin/systemctl", "daemon-reload"),
    "start_slot_service": ("/usr/bin/systemctl", "start", "lab-slot.service"),
    "restart_slot_service": ("/usr/bin/systemctl", "restart", "lab-slot.service"),
}

Key = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"),
]
VMID = Annotated[int, ApiPath(ge=100, le=999999999)]

class Mounts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    # None = shared <CEPH_ROOT>/<CODE_DIR> directory; otherwise <CODE_DIR>/<revision>.
    code_revision: Key | None = None
    student_key: Key
    data_mode: Literal["ro", "rw"] = "rw"

    @field_validator("code_revision", "student_key")
    @classmethod
    def strict_key(cls, value: str | None) -> str | None:
        # Fullmatch, not regex '$' (which may accept a trailing newline).
        if value is not None and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value) is None:
            raise ValueError("Expected one safe path component")
        return value

class Start(Mounts):
    actions: list[Literal["init_slot", "daemon_reload", "start_slot_service", "restart_slot_service"]] = Field(
        default_factory=list, max_length=3
    )

class Stop(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    force: bool = False


class Fence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    epoch: int = Field(ge=1, le=2**63 - 1)
    generation: int = Field(ge=1, le=2**63 - 1)


class Reset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Size(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    cores: int = Field(ge=1, le=4096)
    memory_mb: int = Field(ge=128, le=2**31 - 1)


def fence_state(vmid):
    try:
        data = json.loads((FENCE_DIR / f"{vmid}.json").read_text())
        if (type(data["epoch"]) is not int or type(data["generation"]) is not int
                or data["epoch"] < 1 or data["generation"] < 1):
            raise ValueError()
        return data
    except FileNotFoundError:
        return {"epoch": 0, "generation": 0}
    except (OSError, ValueError, KeyError, TypeError):
        raise api_error(503, "FENCE_STORAGE_ERROR", "Cannot verify command ownership")


def save_fence(vmid, data):
    FENCE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=FENCE_DIR, prefix=f".{vmid}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, FENCE_DIR / f"{vmid}.json")
        directory = os.open(FENCE_DIR, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def require_fence(vmid, request):
    state = fence_state(vmid)
    if state.get("uncertain") or state.get("inflight"):
        raise api_error(409, "COMMAND_UNCERTAIN", "Previous host command needs operator reconciliation")
    if (request.headers.get("X-Lab-Epoch"), request.headers.get("X-Lab-Generation")) != (
            str(state["epoch"]), str(state["generation"])) or state["epoch"] == 0:
        raise api_error(409, "STALE_OWNER", "Claim current command ownership first")

app = FastAPI(
    title="Lab Node Agent",
    version="1.3.0",
    docs_url=None, redoc_url=None, openapi_url=None,
)


def api_error(status: int, code: str, message: str, **extra) -> HTTPException:
    detail = {"code": code, "message": message}
    detail.update({k: v for k, v in extra.items() if v is not None})
    return HTTPException(status, detail)


def conf_sections(vmid: int) -> dict[str | None, dict[str, str]]:
    """The CT's Proxmox config as {section: {key: value}}.

    None is the current config; the others are snapshots ([name]) and [pve:pending]. A snapshot
    repeats `unprivileged:` and `tags:` in its own section, so only the current one may decide
    what the agent is allowed to touch."""
    try:
        content = (CONF_DIR / f"{vmid}.conf").read_text(encoding="utf-8")
    except FileNotFoundError:
        raise api_error(404, "CT_NOT_ON_NODE", "LXC not found on this node")
    except OSError:
        raise api_error(503, "STORAGE_UNAVAILABLE", "Cannot read Proxmox configuration")
    sections: dict[str | None, dict[str, str]] = {None: {}}
    current = sections[None]
    for line in content.splitlines():
        header = re.fullmatch(r"\[([^\]]+)\]\s*", line)
        if header:
            current = sections.setdefault(header.group(1), {})
        elif ":" in line and not line.startswith("#"):
            key, _, value = line.partition(":")
            current[key.strip()] = value.strip()
    return sections


def section_tags(section: dict[str, str]) -> set[str]:
    return {t for t in re.split(r"[;,\s]+", section.get("tags", "")) if t}


def vmid_allowed(vmid: int) -> None:
    if not VMID_MIN <= vmid <= VMID_MAX:
        raise api_error(403, "VMID_OUT_OF_RANGE", "VMID outside allowed lab range")
    current = conf_sections(vmid)[None]
    # Do not attach writable host paths to privileged containers.
    if current.get("unprivileged") != "1":
        raise api_error(403, "PRIVILEGED_CT", "Only unprivileged lab containers are accepted")
    if REQUIRED_TAG not in section_tags(current):
        raise api_error(403, "NOT_LAB_SLOT", "Container is not marked as a lab slot")


def run_cmd(args: list[str], timeout: int = 45) -> str:
    """Never invoke a shell, even for trusted action names."""
    log.info("operation argv=%r timeout=%s", args, timeout)
    vmid = ACTIVE_SLOT.get()
    mutating = vmid is not None and args[:2] in (
        [PCT, "start"], [PCT, "set"], [PCT, "stop"], [PCT, "shutdown"], [PCT, "exec"], [PCT, "rollback"])
    if mutating:
        state = fence_state(vmid)
        # Write ahead of starting pct. A killed/restarted agent cannot forget an
        # in-flight host command just because its process-local lock disappeared.
        save_fence(vmid, dict(state, inflight=True, command=args[1]))
    try:
        p = subprocess.run(
            args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            check=False, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        )
    except subprocess.TimeoutExpired:
        log.warning("operation timed out argv=%r", args)
        if mutating:
            state = fence_state(vmid)
            save_fence(vmid, dict(state, uncertain=True, command=args[1]))
        raise api_error(504, "PCT_TIMEOUT", "Proxmox command timed out; check actual container state",
                        command=args[1] if len(args) > 1 and args[0] == PCT else None)
    except OSError:
        if mutating:
            save_fence(vmid, state)  # No process could be started.
        raise api_error(503, "PCT_UNAVAILABLE", "Proxmox command could not be executed")
    if mutating:
        save_fence(vmid, state)  # The synchronous command returned; re-read its actual effect.
    if p.returncode != 0:
        log.warning("operation failed argv=%r returncode=%s", args, p.returncode)
        detail = (p.stderr or p.stdout or "Command failed").strip()[:800]
        if args[0] == FINDMNT:
            raise api_error(503, "STORAGE_UNAVAILABLE", "Cannot inspect CephFS mount")
        raise api_error(409, "PCT_FAILED", f"Proxmox command failed: {detail}")
    return p.stdout.strip()


def lxc_state(vmid: int) -> str:
    output = run_cmd([PCT, "status", str(vmid)], timeout=15)
    match = re.fullmatch(r"status:\s*(running|stopped)(?:\s*.*)?", output)
    if not match:
        raise api_error(503, "PCT_UNAVAILABLE", "Unexpected LXC status output")
    return match.group(1)


def validate_no_symlinks(path: Path) -> None:
    """Walk using directory FDs; reject paths whose ancestors can be replaced.

    Source leaf ownership may belong to a mapped guest UID. Every parent must
    remain admin-controlled, including ACLs. Ceph administrators are trusted.
    """
    if not path.is_absolute() or ".." in path.parts:
        raise api_error(500, "AGENT_CONFIG_INVALID", "CephFS root must be an absolute canonical path")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            st = os.fstat(fd)
            if st.st_uid not in ADMIN_UIDS or (
                st.st_mode & 0o022
            ):
                raise api_error(422, "SOURCE_UNSAFE", "Mount ancestor is not administrator-controlled")
            try:
                os.getxattr(fd, "system.posix_acl_access")
            except OSError as exc:
                if exc.errno not in (errno.ENODATA, errno.ENOTSUP):
                    raise api_error(503, "STORAGE_UNAVAILABLE", "Cannot verify mount ancestor ACL")
            else:
                raise api_error(422, "SOURCE_UNSAFE", "Extended ACL on mount ancestor is unsupported")
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
    except FileNotFoundError:
        raise api_error(422, "SOURCE_MISSING", "Required CephFS directory does not exist")
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise api_error(422, "SOURCE_UNSAFE", "Mount path has a symbolic link or non-directory component")
        raise api_error(503, "STORAGE_UNAVAILABLE", "Cannot inspect source directory")
    finally:
        os.close(fd)


def validate_cephfs() -> None:
    validate_no_symlinks(CEPH_ROOT)
    if not CEPH_ROOT.is_mount():
        raise api_error(503, "STORAGE_UNAVAILABLE", "CephFS is not mounted at its expected root")
    fs_type = run_cmd([FINDMNT, "--mountpoint", str(CEPH_ROOT), "-n", "-o", "FSTYPE"], timeout=10)
    if fs_type not in ALLOWED_FS:
        raise api_error(503, "STORAGE_UNAVAILABLE", "Unexpected filesystem mounted at CephFS root")


def source_paths(spec: Mounts) -> tuple[Path, Path, Path | None]:
    validate_cephfs()
    code_path = CEPH_ROOT / CODE_DIR
    if spec.code_revision is not None:
        code_path = code_path / spec.code_revision
    data_path = CEPH_ROOT / STUDENTS_DIR / spec.student_key
    images_path = CEPH_ROOT / IMAGES_DIR if IMAGES_DIR is not None else None
    for p in (code_path, data_path, images_path):
        if p is None:
            continue
        validate_no_symlinks(p)
        if not p.is_relative_to(CEPH_ROOT):
            raise api_error(422, "SOURCE_UNSAFE", "Source path escapes CephFS root")
        # A nested mount could otherwise replace a student directory with a host filesystem.
        mounted_at = run_cmd([FINDMNT, "-T", str(p), "-n", "-o", "TARGET"], timeout=10)
        if mounted_at != str(CEPH_ROOT):
            raise api_error(422, "SOURCE_UNSAFE", "Source directory is not on expected CephFS mount")
    return code_path, data_path, images_path


def apply_mounts(vmid: int, mounts: Mounts) -> None:
    code_path, data_path, images_path = source_paths(mounts)
    # Targets and mount indices are constants. No host paths accepted from callers.
    ro = 1 if mounts.data_mode == "ro" else 0
    images = ["--mp2", f"{images_path},mp=/opt/gns3/images,ro=1,shared=1"] if images_path else []
    run_cmd([
        PCT, "set", str(vmid),
        "--mp0", f"{code_path},mp=/srv/gns3-ro/slot,ro=1,shared=1",
        "--mp1", f"{data_path},mp=/opt/gns3/projects,ro={ro},shared=1",
        *images,
    ], timeout=40)


@contextmanager
def slot_lock(vmid: int):
    LOCK_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(LOCK_DIR / f"{vmid}.lock", "a+", encoding="utf-8") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise api_error(409, "SLOT_BUSY", "Another operation on this slot is in progress")
        try:
            token = ACTIVE_SLOT.set(vmid)
            try:
                yield
            finally:
                ACTIVE_SLOT.reset(token)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def require_stopped(vmid: int, action: str = "change mount points") -> None:
    if lxc_state(vmid) != "stopped":
        raise api_error(409, "NOT_STOPPED", f"Slot must be stopped to {action}")


@app.get("/health")
def health():
    # TLS client cert is mandatory even for this endpoint.
    return {"status": "ok"}


@app.get("/capabilities")
def capabilities():
    return {
        "version": app.version,
        "start_budget_seconds": start_budget_seconds(),
        "guest_action_timeout_seconds": GUEST_ACTION_TIMEOUT,
        "images_capture": IMAGES_DIR is not None and shutil.which("qemu-img") is not None,
        "capture_timeout_seconds": CAPTURE_TIMEOUT,
    }


@app.get("/slots/{vmid}/status")
def status(vmid: VMID):
    if not VMID_MIN <= vmid <= VMID_MAX:
        raise api_error(403, "VMID_OUT_OF_RANGE", "VMID outside allowed lab range")
    with slot_lock(vmid):
        vmid_allowed(vmid)
        return {"vmid": vmid, "state": lxc_state(vmid), "ownership": fence_state(vmid)}


@app.put("/slots/{vmid}/mounts")
def mounts(vmid: VMID, spec: Mounts, request: Request):
    if not VMID_MIN <= vmid <= VMID_MAX:
        raise api_error(403, "VMID_OUT_OF_RANGE", "VMID outside allowed lab range")
    with slot_lock(vmid), student_lock(spec.student_key):
        vmid_allowed(vmid)
        require_fence(vmid, request)
        require_stopped(vmid)
        apply_mounts(vmid, spec)
        return {"vmid": vmid, "mounts_configured": True}


@app.post("/slots/{vmid}/start")
def start(vmid: VMID, spec: Start, request: Request):
    if not VMID_MIN <= vmid <= VMID_MAX:
        raise api_error(403, "VMID_OUT_OF_RANGE", "VMID outside allowed lab range")
    with slot_lock(vmid), student_lock(spec.student_key):
        vmid_allowed(vmid)
        require_fence(vmid, request)
        require_stopped(vmid)
        apply_mounts(vmid, spec)
        vmid_allowed(vmid)
        source_paths(spec)  # Recheck immediately before pct resolves the configured paths.
        # Start happens only after the privileged mount edit succeeds.
        run_cmd([PCT, "start", str(vmid)], timeout=PCT_START_TIMEOUT)
        completed: list[str] = []
        for name in dict.fromkeys(spec.actions):
            # pct exec executes INSIDE guest; -- marks the following command.
            run_cmd([PCT, "exec", str(vmid), "--", *GUEST_ACTIONS[name]], timeout=GUEST_ACTION_TIMEOUT)
            completed.append(name)
        return {"vmid": vmid, "state": lxc_state(vmid), "completed_actions": completed}


@app.post("/slots/{vmid}/stop")
def stop(vmid: VMID, options: Stop, request: Request):
    if not VMID_MIN <= vmid <= VMID_MAX:
        raise api_error(403, "VMID_OUT_OF_RANGE", "VMID outside allowed lab range")
    with slot_lock(vmid):
        vmid_allowed(vmid)
        require_fence(vmid, request)
        if lxc_state(vmid) == "stopped":
            return {"vmid": vmid, "state": "stopped"}
        if options.force:
            run_cmd([PCT, "stop", str(vmid)], timeout=60)
        else:
            run_cmd([PCT, "shutdown", str(vmid), "--timeout", "60"], timeout=80)
        return {"vmid": vmid, "state": lxc_state(vmid)}


@app.put("/students/{student_key}")
def ensure_student(student_key: Key):
    # Path parameters need the same fullmatch protection as mount body keys.
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", student_key) is None:
        raise api_error(422, "INVALID_REQUEST", "Expected one safe path component")
    try:
        uid, gid = int(STUDENT_UID), int(STUDENT_GID)
        if not (1 <= uid < 2**32 - 1 and 1 <= gid < 2**32 - 1):
            raise ValueError()
    except (TypeError, ValueError):
        raise api_error(503, "STUDENT_IDS_NOT_CONFIGURED", "Configure mapped gns3 host UID/GID first")
    validate_cephfs()
    parent = CEPH_ROOT / STUDENTS_DIR
    validate_no_symlinks(parent)
    if run_cmd([FINDMNT, "-T", str(parent), "-n", "-o", "TARGET"], timeout=10) != str(CEPH_ROOT):
        raise api_error(422, "SOURCE_UNSAFE", "Students parent is not on expected CephFS mount")
    try:
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            st = os.fstat(parent_fd)
            if st.st_uid not in ADMIN_UIDS or st.st_mode & 0o022:
                raise api_error(422, "SOURCE_UNSAFE", "Students parent must be administrator-controlled")
            # Reject access and default ACLs (mkdir must not inherit extra access).
            for acl in ("system.posix_acl_access", "system.posix_acl_default"):
                try:
                    os.getxattr(parent_fd, acl)
                except OSError as exc:
                    if exc.errno not in (errno.ENODATA, errno.ENOTSUP):
                        raise api_error(503, "STORAGE_UNAVAILABLE", "Cannot verify students parent ACL")
                else:
                    raise api_error(422, "SOURCE_UNSAFE", "Students parent ACL is unsupported")
            created = False
            try:
                os.mkdir(student_key, mode=0o700, dir_fd=parent_fd)
                created = True
            except FileExistsError:
                pass
            fd = os.open(student_key, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=parent_fd)
            try:
                # Do not chown nested mounts, including a substituted host filesystem.
                target = parent / student_key
                if run_cmd([FINDMNT, "-T", str(target), "-n", "-o", "TARGET"], timeout=10) != str(CEPH_ROOT):
                    raise api_error(422, "SOURCE_UNSAFE", "Student directory is not on expected CephFS mount")
                if created:
                    os.fchown(fd, uid, gid)
                    log.info("created student directory key=%s uid=%s gid=%s", student_key, uid, gid)
                st = os.fstat(fd)
                if (st.st_uid, st.st_gid) != (uid, gid):
                    raise api_error(409, "STUDENT_DIR_OWNER", "Existing student directory has a different owner")
            finally:
                os.close(fd)
        finally:
            os.close(parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise api_error(422, "SOURCE_UNSAFE", "Student path is not a real directory")
        log.warning("student directory operation failed key=%s errno=%s", student_key, exc.errno)
        raise api_error(503, "STORAGE_UNAVAILABLE", "Cannot provision student directory; inspect ownership before retry")
    return {"student_key": student_key, "ready": True}


@app.post("/slots/{vmid}/claim")
def claim(vmid: VMID, spec: Fence):
    with slot_lock(vmid):
        vmid_allowed(vmid)
        state = fence_state(vmid)
        if state.get("uncertain") or state.get("inflight"):
            raise api_error(409, "COMMAND_UNCERTAIN", "Previous host command needs operator reconciliation")
        if (spec.epoch, spec.generation) < (state["epoch"], state["generation"]):
            raise api_error(409, "STALE_OWNER", "Command owner superseded")
        if (spec.epoch, spec.generation) != (state["epoch"], state["generation"]):
            save_fence(vmid, spec.model_dump())
        return {"vmid": vmid, **spec.model_dump()}


@app.post("/slots/{vmid}/reset")
def reset(vmid: VMID, spec: Reset, request: Request):
    """Roll the stopped slot back to its clean snapshot: guest disk and CT config together."""
    if not VMID_MIN <= vmid <= VMID_MAX:
        raise api_error(403, "VMID_OUT_OF_RANGE", "VMID outside allowed lab range")
    with slot_lock(vmid):
        vmid_allowed(vmid)
        require_fence(vmid, request)
        require_stopped(vmid, "roll back to the clean snapshot")
        snapshot = conf_sections(vmid).get(CLEAN_SNAPSHOT)
        if snapshot is None:
            raise api_error(422, "SNAPSHOT_MISSING", f"Slot has no '{CLEAN_SNAPSHOT}' snapshot")
        # The rollback replaces the whole config. A snapshot without the lab markers would
        # leave a slot that this agent refuses to manage.
        if snapshot.get("unprivileged") != "1" or REQUIRED_TAG not in section_tags(snapshot):
            raise api_error(422, "SNAPSHOT_UNSAFE",
                            f"Snapshot '{CLEAN_SNAPSHOT}' is not an unprivileged '{REQUIRED_TAG}' slot")
        run_cmd([PCT, "rollback", str(vmid), CLEAN_SNAPSHOT], timeout=180)
        return {"vmid": vmid, "state": lxc_state(vmid), "reset": True, "snapshot": CLEAN_SNAPSHOT}


@app.put("/slots/{vmid}/size")
def size(vmid: VMID, spec: Size, request: Request):
    with slot_lock(vmid):
        vmid_allowed(vmid)
        require_fence(vmid, request)
        require_stopped(vmid)
        run_cmd([PCT, "set", str(vmid), "--cores", str(spec.cores), "--memory", str(spec.memory_mb)], timeout=40)
        return {"vmid": vmid, **spec.model_dump()}


# Capture shares the student lock with mount/start, so its source cannot become writable
# between the CT configuration check and publication. The manager also reserves the
# workspace across nodes for the entire multi-node capture.
@contextmanager
def student_lock(key):
    LOCK_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(LOCK_DIR / f"student-{key}.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise api_error(409, "STUDENT_DIR_BUSY", "Student directory has an active operation") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def capture_timeout():
    try:
        value = int(os.environ.get("LAB_CAPTURE_TIMEOUT", "900"))
        if 60 <= value <= 3600:
            return value
    except ValueError:
        pass
    raise SystemExit("LAB_CAPTURE_TIMEOUT must be 60-3600 seconds")


CAPTURE_TIMEOUT = capture_timeout()


class Capture(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    student_key: Key
    project_id: str
    node_name: str
    parent_file: str
    base_file: str
    base_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    image_file: str
    max_bytes: int = Field(ge=1, le=2**63 - 1)

    @field_validator("student_key")
    @classmethod
    def key_component(cls, value):
        return Mounts.strict_key(value)

    @field_validator("project_id")
    @classmethod
    def project_uuid(cls, value):
        if str(uuid.UUID(value)) != value:
            raise ValueError("Expected a canonical UUID")
        return value

    @field_validator("node_name")
    @classmethod
    def node_component(cls, value):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
            raise ValueError("Invalid node name")
        return value

    @field_validator("parent_file", "base_file", "image_file", "base_sha256")
    @classmethod
    def file_component(cls, value, info):
        pattern = (r"sha256:[0-9a-f]{64}" if info.field_name == "base_sha256" else
                   r"d-[0-9a-f]{12}\.qcow2" if info.field_name == "image_file" else
                   r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.qcow2")
        if not re.fullmatch(pattern, value):
            raise ValueError("Invalid image file name or digest")
        return value


@contextmanager
def capture_file(root, parts):
    """Pin every component with openat/O_NOFOLLOW, including guest-owned directories."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if index != len(parts) - 1:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise api_error(422, "CAPTURE_PATH_UNSAFE", "Capture source must be a regular file")
        yield fd
    finally:
        os.close(fd)


def capture_idle(student):
    # Parse current CT configuration with the existing parser, including CTs outside
    # our VMID range: capture must not read a disk that any local CT can change.
    for path in CONF_DIR.glob("*.conf"):
        if not path.stem.isdecimal():
            continue
        current = conf_sections(int(path.stem))[None]
        source = current.get("mp1", "").split(",", 1)[0]
        if source and Path(source).resolve() == student.resolve():
            raise api_error(409, "STUDENT_DIR_BUSY", "Student directory is still assigned to a CT")


def capture_remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise api_error(504, "TIMEOUT", "Image capture timed out")
    return remaining


def capture_qemu(args, deadline, fds=()):
    try:
        proc = subprocess.run([shutil.which("qemu-img") or "qemu-img", *args],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=capture_remaining(deadline), pass_fds=fds, check=False)
    except subprocess.TimeoutExpired:
        raise api_error(504, "TIMEOUT", "Image capture timed out") from None
    if proc.returncode:
        raise api_error(422, "CAPTURE_BASE_MISMATCH", "qemu-img rejected the disk or backing chain")
    return proc.stdout


def capture_info(path, deadline, fds=()):
    info = json.loads(capture_qemu(["info", "-U", "--output=json", str(path)], deadline, fds))
    if info.get("format") != "qcow2" or "data-file" in json.dumps(info.get("format-specific", {})):
        raise api_error(422, "CAPTURE_BASE_MISMATCH", "Expected qcow2 without an external data file")
    return info


def capture_hash(fd, algorithm, deadline):
    digest = hashlib.new(algorithm)
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, 1024 * 1024):
        capture_remaining(deadline)
        digest.update(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest()


def cleanup_capture_parts():
    if IMAGES_DIR is None:
        return
    directory = CEPH_ROOT / IMAGES_DIR / "QEMU"
    if not directory.exists():
        return
    validate_no_symlinks(directory)
    for path in directory.glob(".d-*.qcow2*.part"):
        if path.is_symlink() or time.time() - path.stat().st_mtime <= CAPTURE_TIMEOUT:
            continue
        # An active operation holds this lock, even if a long-running filesystem
        # syscall has exceeded its deadline. Never unlink another worker's file.
        image = path.name[1:].split(".qcow2", 1)[0]
        try:
            with student_lock("capture-" + image):
                path.unlink(missing_ok=True)
        except HTTPException:
            continue


@app.on_event("startup")
def capture_startup():
    cleanup_capture_parts()


@contextmanager
def capture_cleanup(owned):
    try:
        yield
    finally:
        for path in owned:
            path.unlink(missing_ok=True)


def capture_project(student_key, project_id):
    root = os.open(CEPH_ROOT / STUDENTS_DIR, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in (student_key, project_id):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            os.close(root)
            root = child
        names = [name for name in os.listdir(root) if name.endswith(".gns3")]
        if len(names) != 1:
            raise api_error(404, "CAPTURE_NODE_MISSING", "Expected one project definition")
        fd = os.open(names[0], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_size > 8 * 1024 * 1024:
                raise api_error(422, "CAPTURE_PATH_UNSAFE", "Invalid project definition")
            return json.loads(os.read(fd, 8 * 1024 * 1024))
        finally:
            os.close(fd)
    finally:
        os.close(root)


@app.post("/images/capture")
def capture_image(spec: Capture):
    if not capabilities()["images_capture"]:
        raise api_error(503, "AGENT_CONFIG_INVALID", "Configure LAB_IMAGES_DIR and install qemu-img")
    deadline = time.monotonic() + CAPTURE_TIMEOUT
    directory = CEPH_ROOT / IMAGES_DIR / "QEMU"
    student = CEPH_ROOT / STUDENTS_DIR / spec.student_key
    temp = directory / ("." + spec.image_file + ".part")
    md5temp = directory / ("." + spec.image_file + ".md5sum.part")
    target = directory / spec.image_file
    sidecar = directory / (spec.image_file + ".md5sum")
    owned = []
    try:
        validate_cephfs()
        validate_no_symlinks(CEPH_ROOT / STUDENTS_DIR)
        validate_no_symlinks(directory)
        cleanup_capture_parts()
        with student_lock(spec.student_key), student_lock("capture-" + spec.image_file[:-6]), capture_cleanup(owned):
            capture_idle(student)
            if target.exists() or target.is_symlink() or sidecar.exists() or sidecar.is_symlink():
                raise api_error(409, "IMAGE_EXISTS", "Image file already exists")
            # Enumerate via pinned directory FD; opening the selected JSON is nofollow too.
            project = capture_project(spec.student_key, spec.project_id)
            nodes = project.get("topology", {}).get("nodes", [])
            matching = [n for n in nodes if n.get("name") == spec.node_name and n.get("node_type") == "qemu"]
            if len(matching) != 1:
                raise api_error(404, "CAPTURE_NODE_MISSING", "QEMU node not found in project")
            node_id = str(uuid.UUID(matching[0]["node_id"]))
            with capture_file(directory, [spec.base_file]) as base_fd:
                if capture_hash(base_fd, "sha256", deadline) != spec.base_sha256[7:]:
                    raise api_error(422, "CAPTURE_BASE_MISMATCH", "Base SHA-256 does not match")
            base = capture_info(directory / spec.base_file, deadline)
            if base.get("backing-filename"):
                raise api_error(422, "CAPTURE_BASE_MISMATCH", "Base must not itself have a backing image")
            if spec.parent_file != spec.base_file:
                if not (directory / spec.parent_file).exists():
                    raise api_error(422, "CAPTURE_BASE_MISMATCH", "Expected parent is missing")
                with capture_file(directory, [spec.parent_file]):
                    parent = capture_info(directory / spec.parent_file, deadline)
                if parent.get("backing-filename") != spec.base_file:
                    raise api_error(422, "CAPTURE_BASE_MISMATCH", "Parent does not point to base")
            parts = [spec.student_key, spec.project_id, "project-files", "qemu", node_id, "hda_disk.qcow2"]
            with capture_file(CEPH_ROOT / STUDENTS_DIR, parts) as disk_fd:
                info = capture_info(f"/proc/self/fd/{disk_fd}", deadline, (disk_fd,))
                if info.get("backing-filename") != "/opt/gns3/images/QEMU/" + spec.parent_file:
                    raise api_error(422, "CAPTURE_BASE_MISMATCH", "Node backing does not match expected parent")
                if os.fstat(disk_fd).st_size > spec.max_bytes:
                    raise api_error(422, "CAPTURE_TOO_LARGE", "Node disk exceeds capture limit")
                out_fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                owned.append(temp)
                with os.fdopen(out_fd, "wb") as output:
                    while chunk := os.read(disk_fd, 1024 * 1024):
                        capture_remaining(deadline)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
            capture_qemu(["rebase", "-u", "-f", "qcow2", "-F", "qcow2", "-b", spec.parent_file, str(temp)], deadline)
            if spec.parent_file != spec.base_file:
                capture_qemu(["rebase", "-f", "qcow2", "-F", "qcow2", "-b", spec.base_file, str(temp)], deadline)
            capture_qemu(["check", "-f", "qcow2", str(temp)], deadline)
            final_info = capture_info(temp, deadline)
            if final_info.get("backing-filename") != spec.base_file:
                raise api_error(422, "CAPTURE_BASE_MISMATCH", "Final backing does not match base")
            size = temp.stat().st_size
            if size > spec.max_bytes:
                raise api_error(422, "CAPTURE_TOO_LARGE", "Rebased image exceeds capture limit")
            with capture_file(directory, [temp.name]) as fd:
                sha = capture_hash(fd, "sha256", deadline)
                md5 = capture_hash(fd, "md5", deadline)
                os.fsync(fd)
            with open(md5temp, "x", encoding="ascii") as output:
                owned.append(md5temp)
                output.write(md5)  # GNS3 checksum cache contains only the hexadecimal digest.
                output.flush()
                os.fsync(output.fileno())
            temp.chmod(0o444)
            md5temp.chmod(0o444)
            os.link(md5temp, sidecar)
            os.link(temp, target)  # Publication cannot replace an existing immutable image.
            dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            return {"image_file": spec.image_file, "size": size, "virtual_size": final_info["virtual-size"],
                    "sha256": "sha256:" + sha, "md5": md5, "backing": spec.base_file}
    except FileExistsError:
        raise api_error(409, "IMAGE_EXISTS", "Image or pending capture already exists") from None
    except FileNotFoundError:
        raise api_error(404, "CAPTURE_NODE_MISSING", "Project, node disk or backing image is missing") from None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise api_error(422, "CAPTURE_PATH_UNSAFE", "Capture path contains a symlink") from None
        raise api_error(503, "STORAGE_UNAVAILABLE", "Image storage operation failed") from None
    except (ValueError, KeyError, TypeError):
        raise api_error(422, "CAPTURE_BASE_MISMATCH", "Malformed project or disk metadata") from None
