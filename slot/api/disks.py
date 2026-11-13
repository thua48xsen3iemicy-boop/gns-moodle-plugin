"""Additional blank disks of a QEMU node (`NodeSpec.disks`, CONTRACTS.md 4.2).

GNS3 2.2.61 has four drives per node, hda..hdd; hda is the image, so a node takes up to three more. A drive only
exists as a linked clone of a file in the images directory, so every additional disk is a clone of one small empty
image, `blank.qcow2` (the administrator puts it there once; the slot never changes the shared images). Before the
node first starts the slot asks GNS3 to grow the clone to the size of the ticket (`resize_disk`) and reads the size
back from the qcow2 header: GNS3 does not look at the exit code of `qemu-img resize`.
"""

import os
import struct
from pathlib import Path

DRIVES = ("hdb", "hdc", "hdd")
MIB = 1024 * 1024
MAX_BLANK = 1024 * MIB  # The smallest disk of a ticket is 1 GiB, and a disk cannot shrink


class DiskError(RuntimeError):
    """An additional disk could not be made; the lab fails with this code and message."""

    code = "DISK_CREATE_FAILED"


def blank_name() -> str:
    return os.getenv("SLOT_BLANK_DISK") or "blank.qcow2"


def virtual_size(path) -> int | None:
    """Virtual size of a qcow2 file in bytes from its header, None if it is not one."""
    try:
        with open(path, "rb") as file:
            header = file.read(32)
    except OSError:
        return None
    if len(header) < 32 or header[:4] != b"QFI\xfb":
        return None
    return struct.unpack(">Q", header[24:32])[0]


def backing(path) -> bool:
    """Does the qcow2 file name a backing file? (header: backing_file_offset, 8 bytes at 8)"""
    with open(path, "rb") as file:
        return struct.unpack(">Q", file.read(16)[8:16])[0] != 0


def blank_size(images: Path) -> int | None:
    """Size of the blank image in bytes if the slot can clone it, else None."""
    path = images / "QEMU" / blank_name()
    size = virtual_size(path) if path.is_file() else None
    if size is None or not 0 < size <= MAX_BLANK or size % MIB or backing(path):
        return None
    return size


def enabled(images: Path) -> bool:
    return blank_size(images) is not None


def properties(spec) -> dict:
    """GNS3 node properties of the additional disks of `spec`."""
    out = {}
    for drive, _ in zip(DRIVES, spec.disks):
        out[f"{drive}_disk_image"] = blank_name()
        out[f"{drive}_disk_interface"] = spec.disk_interface
    return out


def clone_path(project_id: str, node: dict, drive: str) -> Path:
    declared = node.get("node_directory")
    if isinstance(declared, str) and os.path.isabs(declared):
        return Path(declared) / f"{drive}_disk.qcow2"
    root = Path(os.getenv("SLOT_PROJECTS_DIR", "/opt/gns3/projects"))
    return root / project_id / "project-files" / "qemu" / node["node_id"] / f"{drive}_disk.qcow2"


async def grow(resize, images: Path, project_id: str, node: dict, spec):
    """Grow the clones of a just-created node (`node`: GNS3's answer) to the sizes of the ticket.

    `resize(drive, extend_mb)` posts GNS3's resize_disk; it makes the clone first if there is none yet.
    """
    blank = blank_size(images)
    if blank is None:
        raise DiskError(f"Node {spec.name}: {blank_name()} is missing or is not a blank qcow2 image")
    for drive, disk in zip(DRIVES, spec.disks):
        wanted = disk.size_gb * 1024 * MIB
        if wanted == blank:
            continue  # GNS3 makes the clone itself when the node starts
        await resize(drive, (wanted - blank) // MIB)
        got = virtual_size(clone_path(project_id, node, drive))
        if got != wanted:
            raise DiskError(f"Node {spec.name}: disk {drive} is {got} bytes after resize, expected {wanted}")
