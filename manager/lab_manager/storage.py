"""Student directories on the RW CephFS (manager-local path STUDENT_ROOT).

The node agent mounts LAB_CEPH_ROOT/students/<student_key> into the slot as /srv/student
(mp1). The agent requires that directory to exist, to be a plain directory (no symlinks)
and to be on the CephFS mount; creating it is the manager's job:

    students/<student_key>/            created by the manager on the first session
    students/<student_key>/results/    check results (written by the manager, slot read-only)
    students/<placeholder>/            empty, mounted read-only into a released slot

student_key = "<site_slug>-<user_id>": one path component, unique per Moodle site and user.
The tree is walked with O_NOFOLLOW so a planted symlink cannot redirect the manager.
"""

import errno
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .config import KEY_RE, SLUG_RE

COMPONENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class StorageError(RuntimeError):
    pass


def safe_component(value) -> str:
    text = str(value)
    if not COMPONENT_RE.fullmatch(text) or ".." in text:
        raise StorageError("Unsafe path component")
    return text


def student_key(site_slug: str, user_id: int) -> str:
    if not SLUG_RE.fullmatch(site_slug or ""):
        raise StorageError("Unsafe site slug")
    if type(user_id) is not int or user_id <= 0:
        raise StorageError("user_id must be a positive integer")
    key = f"{site_slug}-{user_id}"
    if not KEY_RE.fullmatch(key):
        raise StorageError("Student key does not fit the node agent rules")
    return key


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
                    os.chmod(part, mode, dir_fd=fd, follow_symlinks=False)  # Not reduced by umask
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


@dataclass
class StudentStorage:
    student_root: Path
    # setgid + group rwx: gns3 in the slot (member of the shared group) writes here.
    student_mode: int = 0o2770
    # Group r-x only: the slot can read results but never change them.
    results_mode: int = 0o2750

    def ensure_student(self, key: str):
        """Create students/<key>/ and results/. Never changes or removes existing content."""
        if not KEY_RE.fullmatch(key):
            raise StorageError("Unsafe student key")
        with open_dir(self.student_root, (key,), create=True, mode=self.student_mode) as fd:
            try:
                os.mkdir("results", self.results_mode, dir_fd=fd)
                os.chmod("results", self.results_mode, dir_fd=fd, follow_symlinks=False)
            except FileExistsError:
                pass
            with open_dir(self.student_root, (key, "results")):
                pass  # Refuses a symlink planted as results/

    def check_student(self, key: str) -> bool:
        try:
            with open_dir(self.student_root, (safe_component(key),)):
                return True
        except (OSError, StorageError):
            return False
