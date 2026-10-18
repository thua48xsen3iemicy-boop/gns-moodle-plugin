"""Small privileged LXC helper, authenticated at the TLS layer by Uvicorn mTLS.

Intentionally NOT a general-purpose command runner or an alternative Proxmox API.
This service must be accessible ONLY from the Lab Manager management network.
"""
from __future__ import annotations

import errno
import logging
import fcntl
import os
import re
import stat
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Path as ApiPath
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

log = logging.getLogger("uvicorn.error")
ADMIN_UIDS = (0,)

PCT = "/usr/sbin/pct"
FINDMNT = "/usr/bin/findmnt"
CEPH_ROOT = Path(os.environ.get("LAB_CEPH_ROOT", "/mnt/pve/cfs"))
CONF_DIR = Path("/etc/pve/lxc")
LOCK_DIR = Path("/run/lab-node-agent")
VMID_MIN = int(os.environ.get("LAB_VMID_MIN", "2000"))
VMID_MAX = int(os.environ.get("LAB_VMID_MAX", "2999"))
REQUIRED_TAG = "labslot"
# Host-side mapped IDs; intentionally unset until the template mapping is known.
STUDENT_UID = os.environ.get("LAB_STUDENT_HOST_UID")
STUDENT_GID = os.environ.get("LAB_STUDENT_HOST_GID")
ALLOWED_FS = frozenset(("ceph", "fuse.ceph", "fuse.ceph-fuse"))


def ceph_subdir(name: str, default: str) -> Path:
    """Administrator-defined directory below CEPH_ROOT, on the same CephFS mount."""
    parts = os.environ.get(name, default).strip().strip("/").split("/")
    # Leading '.' is not a safe first character, so '.' and '..' are rejected too.
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", part) for part in parts):
        raise SystemExit(f"{name} must be a relative path of safe directory names")
    return Path(*parts)


# Slot code (git checkout, RO) and student data (RW) are separate trees on one CephFS.
CODE_DIR = ceph_subdir("LAB_CODE_DIR", "gns3-ro/slot")
STUDENTS_DIR = ceph_subdir("LAB_STUDENTS_DIR", "gns3-rw/students")
# Optional shared GNS3 images (QEMU/, IOU/, firmware), mounted read-only at /opt/gns3/images as mp2.
# Unset: mp2 is left alone (images in the slot's rootfs or on a mount point of the administrator).
IMAGES_DIR = ceph_subdir("LAB_IMAGES_DIR", "") if os.environ.get("LAB_IMAGES_DIR", "").strip() else None

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

app = FastAPI(
    title="Lab Node Agent",
    version="1.0.0",
    docs_url=None, redoc_url=None, openapi_url=None,
)


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


def vmid_allowed(vmid: int) -> None:
    if not VMID_MIN <= vmid <= VMID_MAX:
        raise api_error(403, "VMID_OUT_OF_RANGE", "VMID outside allowed lab range")
    conf = CONF_DIR / f"{vmid}.conf"
    try:
        content = conf.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise api_error(404, "CT_NOT_ON_NODE", "LXC not found on this node")
    except OSError:
        raise api_error(503, "STORAGE_UNAVAILABLE", "Cannot read Proxmox configuration")
    # Do not attach writable host paths to privileged containers.
    if not re.search(r"(?m)^unprivileged:\s*1\s*$", content):
        raise api_error(403, "PRIVILEGED_CT", "Only unprivileged lab containers are accepted")
    tags = set()
    for line in content.splitlines():
        if line.startswith("tags:"):
            tags.update(t for t in re.split(r"[;,\s]+", line.partition(":")[2].strip()) if t)
    if REQUIRED_TAG not in tags:
        raise api_error(403, "NOT_LAB_SLOT", "Container is not marked as a lab slot")


def run_cmd(args: list[str], timeout: int = 45) -> str:
    """Never invoke a shell, even for trusted action names."""
    log.info("operation argv=%r timeout=%s", args, timeout)
    try:
        p = subprocess.run(
            args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            check=False, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
        )
    except subprocess.TimeoutExpired:
        log.warning("operation timed out argv=%r", args)
        raise api_error(504, "PCT_TIMEOUT", "Proxmox command timed out; check actual container state")
    except OSError:
        raise api_error(503, "PCT_UNAVAILABLE", "Proxmox command could not be executed")
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
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def require_stopped(vmid: int) -> None:
    if lxc_state(vmid) != "stopped":
        raise api_error(409, "NOT_STOPPED", "Slot must be stopped to change mount points")


@app.get("/health")
def health():
    # TLS client cert is mandatory even for this endpoint.
    return {"status": "ok"}


@app.get("/slots/{vmid}/status")
def status(vmid: VMID):
    if not VMID_MIN <= vmid <= VMID_MAX:
        raise api_error(403, "VMID_OUT_OF_RANGE", "VMID outside allowed lab range")
    with slot_lock(vmid):
        vmid_allowed(vmid)
        return {"vmid": vmid, "state": lxc_state(vmid)}


@app.put("/slots/{vmid}/mounts")
def mounts(vmid: VMID, spec: Mounts):
    if not VMID_MIN <= vmid <= VMID_MAX:
        raise api_error(403, "VMID_OUT_OF_RANGE", "VMID outside allowed lab range")
    with slot_lock(vmid):
        vmid_allowed(vmid)
        require_stopped(vmid)
        apply_mounts(vmid, spec)
        return {"vmid": vmid, "mounts_configured": True}


@app.post("/slots/{vmid}/start")
def start(vmid: VMID, spec: Start):
    if not VMID_MIN <= vmid <= VMID_MAX:
        raise api_error(403, "VMID_OUT_OF_RANGE", "VMID outside allowed lab range")
    with slot_lock(vmid):
        vmid_allowed(vmid)
        require_stopped(vmid)
        apply_mounts(vmid, spec)
        vmid_allowed(vmid)
        source_paths(spec)  # Recheck immediately before pct resolves the configured paths.
        # Start happens only after the privileged mount edit succeeds.
        run_cmd([PCT, "start", str(vmid)], timeout=120)
        completed: list[str] = []
        for name in dict.fromkeys(spec.actions):
            # pct exec executes INSIDE guest; -- marks the following command.
            run_cmd([PCT, "exec", str(vmid), "--", *GUEST_ACTIONS[name]], timeout=45)
            completed.append(name)
        return {"vmid": vmid, "state": lxc_state(vmid), "completed_actions": completed}


@app.post("/slots/{vmid}/stop")
def stop(vmid: VMID, options: Stop):
    if not VMID_MIN <= vmid <= VMID_MAX:
        raise api_error(403, "VMID_OUT_OF_RANGE", "VMID outside allowed lab range")
    with slot_lock(vmid):
        vmid_allowed(vmid)
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
