"""Preset the NVRAM of an IOL node: the startup-config of the ticket (`NodeSpec.nvram_config`, CONTRACTS.md 4.2).

The text is turned into NVRAM by `nvram_import` of the installed gns3server (the pair of `nvram_export` that
`ios_config.py` reads with), not by a format of ours. The file is written once, when the project is created, after
the node exists in GNS3 and before it starts for the first time. GNS3 does not touch NVRAM at its start when the node
has no `startup_config_content` (it never gets one here) and renames a stray `nvram_*` file to the id of the node.
Nothing of the text is put into error messages.
"""

import importlib.util
import os
from pathlib import Path

from api import ios_config

IMPORT_PATH = "compute/iou/utils/iou_import.py"  # in the gns3server package
DEFAULT_IMPORT = "/opt/gns3-server/lib/python3.12/site-packages/gns3server/" + IMPORT_PATH
NVRAM_KB = 256  # NodeSpec -> GNS3 property `nvram` (api/labs.py); the file must have exactly this size


class NvramError(RuntimeError):
    """The NVRAM could not be preset; the lab fails with this code and message."""

    code = "NVRAM_PRESET_FAILED"


_importers = {}  # path of iou_import.py -> its nvram_import


def importer():
    """`nvram_import` of the iou_import.py of the installed gns3server, loaded on its own like the exporter.

    Where: `SLOT_IOU_IMPORT`, else the venv of the slot template, else the gns3server this interpreter can see.
    """
    path = os.getenv("SLOT_IOU_IMPORT")
    if not path and os.path.isfile(DEFAULT_IMPORT):
        path = DEFAULT_IMPORT
    if not path:
        try:
            package = importlib.util.find_spec("gns3server")
        except (ImportError, ValueError):
            package = None
        for root in (package.submodule_search_locations or []) if package else []:
            if os.path.isfile(os.path.join(root, IMPORT_PATH)):
                path = os.path.join(root, IMPORT_PATH)
                break
    if not path:
        raise NvramError("gns3server's iou_import.py is not found (see SLOT_IOU_IMPORT)")
    if path not in _importers:
        try:
            spec = importlib.util.spec_from_file_location("slot_iou_import", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _importers[path] = module.nvram_import
        except (OSError, SyntaxError, AttributeError, ImportError) as exc:
            raise NvramError(f"Cannot load iou_import.py: {type(exc).__name__}") from exc
    return _importers[path]


def preset(project_id: str, node: dict, text: str) -> Path:
    """Write the NVRAM file of a just-created IOL node (`node`: GNS3's answer to its creation) from `text`."""
    name = node.get("name", "?")
    application_id = (node.get("properties") or {}).get("application_id")
    if type(application_id) is not int or not 0 < application_id < 100000:
        raise NvramError(f"Node {name}: GNS3 gave no application id, cannot name the NVRAM file")
    directory = ios_config.node_directory(project_id, node)
    if directory is None:
        raise NvramError(f"Node {name}: the node directory is unknown")
    try:
        nvram = importer()(None, text.encode("ascii"), None, NVRAM_KB)
    except NvramError:
        raise
    except (ValueError, TypeError, IndexError, UnicodeEncodeError) as exc:
        raise NvramError(f"Node {name}: cannot import the configuration into NVRAM: {type(exc).__name__}") from exc
    if len(nvram) != NVRAM_KB * 1024:
        raise NvramError(f"Node {name}: the imported NVRAM has an unexpected size")
    path = directory / f"nvram_{application_id:05d}"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # Never over an existing file: a node's NVRAM is the student's once there is one.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
        with os.fdopen(fd, "wb") as handle:
            handle.write(bytes(nvram))
            handle.flush()
            os.fsync(handle.fileno())
        owner = directory.stat()
        if os.geteuid() == 0 and (owner.st_uid, owner.st_gid) != (0, 0):
            os.chown(path, owner.st_uid, owner.st_gid)  # as the files GNS3 makes (the slot normally runs as gns3)
    except OSError as exc:
        raise NvramError(f"Node {name}: cannot write the NVRAM file: {type(exc).__name__}") from exc
    return path
