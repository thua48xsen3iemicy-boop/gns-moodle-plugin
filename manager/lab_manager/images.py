"""Image catalog (plugin request П10): what labs defined in Moodle may run, from IMAGES_FILE.

The administrator describes every image installed on the slots: an id for the plugin, the file
on the slots and the node parameters that are never taken from Moodle (disk interface, CLI
prompt, provisioning...). The plugin gets only what its lab editor needs (public()).

console_type: telnet (default), vnc (QEMU screen instead of a serial console; qemu images only) or none
(no console for anybody). A vnc image makes the manager put `console: "vnc"` into the node of the
slot's ticket (labdef.deploy_ticket); nodes of other images get no such field.

net_config (qemu only): which mechanism configures the network inside the guest - ifupdown, networkmanager, netplan,
networkd, or routeros (MikroTik CHR: a RouterOS script instead of /bin/sh). It is what lets the lab constructor
preconfigure addresses (netconfig.py, lab constructor README §4.1); an image without it (and not vpcs) cannot be a
member of a segment. `preconfig` in public() says whether it can.

random_ifnames (qemu with a /bin/sh net_config only): the guest can take random interface names (ifnames.py), so the
lab editor offers the teacher a checkbox for nodes of the image. public() carries `random_ifnames: true` then.

extra_disks (qemu only): the teacher may give a node of the image additional blank disks (node field `disks`,
CONTRACTS 2.6 and 4.2). public() then carries how many and how large; the numbers are the manager's, not the catalog's.

qga (qemu only): false for an image without a QEMU guest agent. Nodes of the image get `qga: false` in the slot's ticket
(labdef.deploy_ticket): the slot never asks them anything (no facts, no guest shutdown). Absent means true, and nodes of
other images get no such field. Everything that works through the agent - net_config, random_ifnames, role, uplink_port,
slot.provisioning - is refused with it. Not in public().

slot.cpu (qemu only): the QEMU CPU model and its flags, the value of "-cpu" (CONTRACTS 2.6, 4.2). It reaches the ticket
as NodeSpec.cpu through the slot options. Without it the slot uses its own default, "host".

Port names follow GNS3 2.2.61 without port_name_format (slot ALIGNMENT §4, В8):
iou Ethernet{adapter}/{port} (4 ports per adapter), qemu Ethernet{adapter}, vpcs Ethernet0.
"""

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

IMAGE_ID_RE = r"^[a-z0-9][a-z0-9._-]{0,63}$"
PORT_NAMES = {"iou": "Ethernet{adapter}/{port}", "qemu": "Ethernet{adapter}", "vpcs": "Ethernet{adapter}"}
PORTS_PER_ADAPTER = {"iou": 4, "qemu": 1, "vpcs": 1}
DEFAULT_SYMBOLS = {"iou": ":/symbols/router.svg", "qemu": ":/symbols/computer.svg",
                   "vpcs": ":/symbols/vpcs_guest.svg"}
MECHANISMS = ("ifupdown", "networkmanager", "netplan", "networkd")  # netconfig.MECHANISMS: /bin/sh guests
MAX_EXTRA_DISKS = 3  # GNS3 2.2.61 has hda..hdd, hda is the image
EXTRA_DISK_GB = {"default": 1, "min": 1, "max": 20}
# slot.cpu: the value of QEMU "-cpu" (NodeSpec.cpu). The same pattern as slot/api/models.py CPU_PATTERN: no spaces
# and no "%", so the value never becomes another QEMU option.
CPU_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
    r"(,[+-]?[A-Za-z0-9][A-Za-z0-9._-]{0,63}(=[A-Za-z0-9._-]{1,64})?){0,32}$"
)
VPCS_MEMORY_MB = 32  # Counted for the CT size; VPCS has no memory setting


class ImageError(RuntimeError):
    pass


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Range(Strict):
    default: int
    min: int
    max: int

    @model_validator(mode="after")
    def ordered(self):
        if not self.min <= self.default <= self.max:
            raise ValueError("needs min <= default <= max")
        return self


class ProvisionCommand(Strict):
    command: str = Field(min_length=1, max_length=8192)
    timeout_seconds: int = Field(default=20, ge=1, le=120)


class SlotOptions(Strict):
    """NodeSpec fields of the slot that belong to the image, never to a lab from Moodle."""

    disk_interface: Literal["virtio", "ide", "sata", "scsi"] | None = None
    cli_prompt: str | None = Field(default=None, min_length=1, max_length=256)
    l1_keepalives: bool | None = None
    provisioning: list[ProvisionCommand] = Field(default_factory=list, max_length=32)
    startup_script: str | None = Field(default=None, max_length=8192)  # VPCS only
    # QEMU only: "-cpu" of the nodes, e.g. "host" or "SandyBridge,+rdrand,+avx2". Absent: the slot's default ("host").
    cpu: str | None = Field(default=None, max_length=512, pattern=CPU_PATTERN)


class Image(Strict):
    id: str = Field(pattern=IMAGE_ID_RE)
    title: str = Field(min_length=1, max_length=120)
    kind: Literal["iou", "qemu", "vpcs"]
    category: str = Field(default="other", pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    symbol: str | None = Field(default=None, max_length=200)
    console_type: Literal["telnet", "vnc", "none"] = "telnet"
    net_config: Literal["ifupdown", "networkmanager", "netplan", "networkd", "routeros"] | None = None
    file: str | None = Field(default=None, max_length=255)  # Basename in /opt/gns3/images/{QEMU,IOU}
    ram_mb: Range | None = None
    cpus: Range | None = None
    adapters: Range | None = None
    capture: bool | None = None
    random_ifnames: bool = False
    extra_disks: bool = False
    uplink_port: int | None = Field(default=None, strict=True, ge=0, le=15)
    # What a node of the image is beyond its kind (NodeSpec.role, CONTRACTS 4.2): "switch" is the Linux switch,
    # the only node the slot asks switch.net and switch.fdb. Never told by the guest.
    role: Literal["switch"] | None = None
    qga: bool = Field(default=True, strict=True)  # False: no guest agent in the image (CONTRACTS 2.6, 4.2)
    sha256: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    derived: dict | None = None
    featured: bool = False
    retired: bool = False
    slot: SlotOptions = Field(default_factory=SlotOptions)

    @model_validator(mode="after")
    def fits_the_slot(self):
        # Bounds of slot/api/models.py NodeSpec, so a valid lab never fails in the slot.
        if self.kind == "vpcs":
            if self.file or self.ram_mb or self.cpus or self.adapters:
                raise ValueError("vpcs takes no file, ram_mb, cpus or adapters")
        else:
            if not self.file or self.file in (".", "..") or "/" in self.file or "\\" in self.file:
                raise ValueError(f"{self.kind} needs a file name (no path)")
            if not self.ram_mb or not self.adapters:
                raise ValueError(f"{self.kind} needs ram_mb and adapters")
            if not (128 <= self.ram_mb.min and self.ram_mb.max <= 32768):
                raise ValueError("ram_mb must stay within 128..32768")
            if not (1 <= self.adapters.min and self.adapters.max <= 16):
                raise ValueError("adapters must stay within 1..16")
        if self.cpus and (self.kind != "qemu" or not (1 <= self.cpus.min and self.cpus.max <= 16)):
            raise ValueError("cpus is for qemu only, within 1..16")
        if self.net_config is not None and self.kind != "qemu":
            raise ValueError("net_config is for qemu only")
        if self.random_ifnames and (self.kind != "qemu" or self.net_config not in MECHANISMS):
            raise ValueError("random_ifnames needs qemu and net_config ifupdown, networkmanager, netplan or networkd")
        if self.extra_disks and (self.kind != "qemu" or self.uplink_port is not None):
            raise ValueError("extra_disks is for qemu images without uplink_port")
        if self.console_type == "vnc" and self.kind != "qemu":
            raise ValueError("console_type vnc is for qemu only")  # CONTRACTS 2.6
        if self.uplink_port is not None:
            if self.kind != "qemu" or self.uplink_port != 0 or self.adapters.min < 2:
                raise ValueError("ISP images need qemu, uplink_port 0 and at least two adapters")
            if self.console_type != "none" or self.net_config is not None or self.capture is True:
                raise ValueError("ISP images have no console, network preconfiguration or capture")
            self.capture = False
        if self.role is not None and (self.kind != "qemu" or self.uplink_port is not None):
            raise ValueError("role is for qemu images without uplink_port")
        if not self.qga:
            if self.kind != "qemu":
                raise ValueError("qga false is for qemu only")
            if (self.net_config is not None or self.random_ifnames or self.role is not None
                    or self.uplink_port is not None or self.slot.provisioning):
                raise ValueError("qga false excludes net_config, random_ifnames, role, uplink_port and slot.provisioning")
        if self.slot.cli_prompt is not None:
            try:
                pattern = re.compile(self.slot.cli_prompt.encode())
            except re.error as exc:
                raise ValueError("slot.cli_prompt is not a valid regular expression") from exc
            if pattern.search(b""):
                raise ValueError("slot.cli_prompt must not match an empty response")
        if self.slot.cpu is not None and self.kind != "qemu":
            raise ValueError("slot.cpu is for qemu only")
        if self.slot.startup_script is not None and self.kind != "vpcs":
            raise ValueError("slot.startup_script is for vpcs only")
        if self.kind == "iou" and any("\n" in c.command or "\r" in c.command for c in self.slot.provisioning):
            raise ValueError("IOL provisioning commands must be single lines")
        return self

    @property
    def preconfig(self) -> bool:
        """Whether the lab constructor can set the network of a node with this image."""
        return self.kind == "vpcs" or self.net_config is not None

    def public(self) -> dict:
        """What the plugin's editor gets: no file names, no slot options."""
        return {
            "id": self.id, "title": self.title, "kind": self.kind, "category": self.category,
            "symbol": self.symbol or DEFAULT_SYMBOLS[self.kind], "console_type": self.console_type,
            "ram_mb": self.ram_mb.model_dump() if self.ram_mb else None,
            "cpus": self.cpus.model_dump() if self.cpus else None,
            "adapters": self.adapters.model_dump() if self.adapters else None,
            "port_name": PORT_NAMES[self.kind], "ports_per_adapter": PORTS_PER_ADAPTER[self.kind],
            "preconfig": self.preconfig,
            "capture": self.kind == "qemu" if self.capture is None else self.capture,
            **({"uplink_port": self.uplink_port} if self.uplink_port is not None else {}),
            # The plugin picks console snippets by it; only RouterOS images carry the field (CONTRACTS 2.6).
            **({"guest": "routeros"} if self.net_config == "routeros" else {}),
            **({"random_ifnames": True} if self.random_ifnames else {}),
            **({"extra_disks": {"max": MAX_EXTRA_DISKS, "size_gb": dict(EXTRA_DISK_GB)}} if self.extra_disks else {}),
            **({"derived": self.derived, "featured": self.featured, "retired": self.retired} if self.derived else {}),
        }


def load_images(path: Path) -> dict[str, Image]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ImageError(f"Cannot read {path}: {type(exc).__name__}") from exc
    items = raw.get("images") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ImageError(f"{path} must be {{\"images\": [...]}}")
    images = {}
    for i, item in enumerate(items):
        try:
            image = Image.model_validate(item)
        except ValidationError as exc:
            where = "; ".join(".".join(map(str, e["loc"])) + ": " + e["msg"]
                              for e in exc.errors(include_input=False, include_url=False))
            raise ImageError(f"{path}: images[{i}]: {where}") from exc
        if image.id in images:
            raise ImageError(f"{path}: duplicate image id {image.id}")
        images[image.id] = image
    return images
