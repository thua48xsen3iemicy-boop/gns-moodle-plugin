"""Student identity on the CephFS side.

The node agent mounts <LAB_CEPH_ROOT>/students/<student_key> into the slot as
/opt/gns3/projects (mp1), the GNS3 project directory of Lab Slot. It requires every ancestor of that path to be owned by host root and not writable by
group/others, so the manager (an unprivileged LXC) can neither create nor own student
directories: they are provisioned on the Proxmox side (docs/DESIGN.md, "Каталог студента").
The manager only derives the key and reports a missing directory clearly.

student_key = "<site_slug>-<user_id>": one path component, unique per Moodle site and user.
"""

from .config import KEY_RE, SLUG_RE


class StorageError(RuntimeError):
    pass


def student_key(site_slug: str, user_id: int) -> str:
    if not SLUG_RE.fullmatch(site_slug or ""):
        raise StorageError("Unsafe site slug")
    if type(user_id) is not int or user_id <= 0:
        raise StorageError("user_id must be a positive integer")
    key = f"{site_slug}-{user_id}"
    if not KEY_RE.fullmatch(key):
        raise StorageError("Student key does not fit the node agent rules")
    return key
