"""Student directories and how they reach a slot.

Layout on the RW CephFS (manager-local paths from the config):

    STUDENT_ROOT/<site_slug>/<user_id>/work/      what the slot sees as STUDENT_MP_TARGET
    STUDENT_ROOT/<site_slug>/<user_id>/results/   check results; never mounted into a slot
    SLOT_MOUNT_ROOT/<vmid>/mnt                    fixed bind-mount source of slot <vmid> (handover)
    SLOT_MOUNT_ROOT/<vmid>/empty                  parked placeholder while a student is mounted

Modes (docs/DESIGN.md, "Каталог студента"):
  handover   - the CT has a fixed mpN configured once by an administrator; the manager moves
               the student's work/ into SLOT_MOUNT_ROOT/<vmid>/mnt with rename(2) while the
               CT is stopped and moves it back on release. Needs no Proxmox root rights.
  pve_config - the manager sets mpN through PUT /lxc/{vmid}/config. Proxmox allows bind
               mounts only for the literal user root@pam, never for an API token.
  none       - no student directory (first stand tests).

All paths are built from validated components and walked with O_NOFOLLOW, so a symlink
planted anywhere below the roots cannot redirect a mount or a rename.
"""

import errno
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .config import SLUG_RE

COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
PVE_PATH_RE = re.compile(r"^(/[A-Za-z0-9_.-]+)+$")


class StorageError(RuntimeError):
    pass


def safe_component(value) -> str:
    text = str(value)
    if not COMPONENT_RE.fullmatch(text) or text in (".", "..") or ".." in text:
        raise StorageError("Unsafe path component")
    return text


def student_parts(site_slug: str, user_id: int) -> tuple[str, str]:
    if not SLUG_RE.fullmatch(site_slug):
        raise StorageError("Unsafe site slug")
    if type(user_id) is not int or user_id <= 0:
        raise StorageError("user_id must be a positive integer")
    return safe_component(site_slug), str(user_id)


def pve_mount_value(host_root: str, site_slug: str, user_id: int, target: str) -> str:
    """mpN property string. Commas or '=' in a path would inject extra mount options."""
    slug, uid = student_parts(site_slug, user_id)
    path = f"{host_root.rstrip('/')}/{slug}/{uid}/work"
    for value in (path, target):
        if not PVE_PATH_RE.fullmatch(value) or "/../" in value + "/" or "/./" in value + "/":
            raise StorageError("Unsafe mount path")
    return f"{path},mp={target},backup=0"


@contextmanager
def open_dir(root: Path, parts=(), *, create=False, mode=0o750):
    """Open root/parts... as a directory fd, refusing symlinks in every component below root."""
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts:
            safe_component(part)
            if create:
                try:
                    os.mkdir(part, mode, dir_fd=fd)
                except FileExistsError:
                    pass
            try:
                nfd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise StorageError("Path component is not a plain directory") from exc
                raise
            os.close(fd)
            fd = nfd
        yield fd
    finally:
        os.close(fd)


def entry_kind(dir_fd: int, name: str) -> str | None:
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISDIR(st.st_mode):
        return "dir"
    return "other"


@dataclass
class StudentStorage:
    mode: str
    student_root: Path | None = None
    slot_mount_root: Path | None = None
    pve_host_root: str = ""
    mp_index: int = 0
    mp_target: str = "/opt/gns3/student"

    @property
    def mp_key(self) -> str:
        return f"mp{self.mp_index}"

    # ------------------------------------------------------------------ student tree
    def ensure_student(self, site_slug: str, user_id: int):
        """Create <slug>/<uid>/{work,results}. Never changes or removes existing content."""
        if self.mode == "none":
            return
        slug, uid = student_parts(site_slug, user_id)
        with open_dir(self.student_root, (slug, uid), create=True) as fd:
            if self.mode == "handover" and self._mounted_somewhere_marker(fd):
                return  # work/ is currently inside a slot; do not create a second one
            for name, mode in (("work", 0o770), ("results", 0o750)):
                try:
                    os.mkdir(name, mode, dir_fd=fd)
                except FileExistsError:
                    if entry_kind(fd, name) != "dir":
                        raise StorageError(f"{name} is not a plain directory") from None

    @staticmethod
    def _mounted_somewhere_marker(fd) -> bool:
        return entry_kind(fd, "work") is None and entry_kind(fd, ".mounted") == "other"

    # ------------------------------------------------------------------ handover
    def handover_state(self, vmid: int, site_slug: str, user_id: int) -> str:
        """'home' (work/ at the student), 'slot' (in the slot) or 'inconsistent'."""
        slug, uid = student_parts(site_slug, user_id)
        with open_dir(self.student_root, (slug, uid)) as sfd, open_dir(
            self.slot_mount_root, (safe_component(vmid),)
        ) as vfd:
            home = entry_kind(sfd, "work")
            mnt, empty = entry_kind(vfd, "mnt"), entry_kind(vfd, "empty")
            if home == "dir" and mnt == "dir" and empty is None:
                return "home"
            if home is None and mnt == "dir" and empty == "dir" and self._marker_vmid(sfd) == vmid:
                return "slot"
            return "inconsistent"

    def attach(self, vmid: int, site_slug: str, user_id: int):
        """Move work/ into the slot's bind source. The CT must be stopped (caller checks)."""
        if self.mode != "handover":
            return
        slug, uid = student_parts(site_slug, user_id)
        with open_dir(self.student_root, (slug, uid)) as sfd, open_dir(
            self.slot_mount_root, (safe_component(vmid),)
        ) as vfd:
            home, mnt, empty = entry_kind(sfd, "work"), entry_kind(vfd, "mnt"), entry_kind(vfd, "empty")
            marked = self._marker_vmid(sfd) == vmid
            if home is None and mnt == "dir" and empty == "dir" and marked:
                return  # Already attached
            if home == "dir" and mnt == "dir" and empty is None:
                # Marker first: after a crash between the renames the state stays recognizable.
                marker = os.open(
                    ".mounted", os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o640, dir_fd=sfd
                )
                try:
                    os.write(marker, f"{vmid}\n".encode())
                finally:
                    os.close(marker)
                os.rename("mnt", "empty", src_dir_fd=vfd, dst_dir_fd=vfd)
                mnt, empty, marked = None, "dir", True
            if home == "dir" and mnt is None and empty == "dir" and marked:
                os.rename("work", "mnt", src_dir_fd=sfd, dst_dir_fd=vfd)
                return
            raise StorageError("Student/slot directories are in an unexpected state; operator check needed")

    def detach(self, vmid: int, site_slug: str, user_id: int):
        """Move work/ back home and restore the placeholder. The CT must be stopped."""
        if self.mode != "handover":
            return
        slug, uid = student_parts(site_slug, user_id)
        with open_dir(self.student_root, (slug, uid)) as sfd, open_dir(
            self.slot_mount_root, (safe_component(vmid),)
        ) as vfd:
            home, mnt, empty = entry_kind(sfd, "work"), entry_kind(vfd, "mnt"), entry_kind(vfd, "empty")
            # The marker proves the directory in the slot belongs to this student.
            marked = self._marker_vmid(sfd) == vmid
            if home == "dir" and mnt == "dir" and empty is None:
                self._unlink_marker(sfd)
                return  # Already detached
            if home is None and mnt == "dir" and empty == "dir" and marked:
                os.rename("mnt", "work", src_dir_fd=vfd, dst_dir_fd=sfd)
                home, mnt = "dir", None
            if home == "dir" and mnt is None and empty == "dir" and marked:
                os.rename("empty", "mnt", src_dir_fd=vfd, dst_dir_fd=vfd)
                self._unlink_marker(sfd)
                return
            raise StorageError("Cannot detach: unexpected directory state; operator check needed")

    @staticmethod
    def _marker_vmid(fd) -> int | None:
        try:
            mfd = os.open(".mounted", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        except OSError:
            return None
        try:
            text = os.read(mfd, 32).decode("ascii", "replace").strip()
        finally:
            os.close(mfd)
        return int(text) if text.isdigit() else None

    @staticmethod
    def _unlink_marker(fd):
        try:
            os.unlink(".mounted", dir_fd=fd)
        except FileNotFoundError:
            pass

    def check_slot_placeholder(self, vmid: int):
        """Free slot: mnt exists and is an (empty) placeholder; nothing parked."""
        if self.mode != "handover":
            return
        with open_dir(self.slot_mount_root, (safe_component(vmid),)) as vfd:
            if entry_kind(vfd, "mnt") != "dir" or entry_kind(vfd, "empty") is not None:
                raise StorageError(f"Slot {vmid} bind source is not a free placeholder")

    # ------------------------------------------------------------------ pve_config
    def pve_mount(self, site_slug: str, user_id: int) -> dict:
        value = pve_mount_value(self.pve_host_root, site_slug, user_id, self.mp_target)
        if self.student_root is not None:
            slug, uid = student_parts(site_slug, user_id)
            # Same tree as seen by the manager: refuses symlinks on the way to work/.
            with open_dir(self.student_root, (slug, uid, "work")):
                pass
        return {self.mp_key: value}
