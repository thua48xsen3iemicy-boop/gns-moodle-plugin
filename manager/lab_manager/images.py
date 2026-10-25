"""Image catalog (plugin request П10): what labs defined in Moodle may run, from IMAGES_FILE.

The administrator describes every image installed on the slots: an id for the plugin, the file
on the slots and the node parameters that are never taken from Moodle (disk interface, CLI
prompt, provisioning...). The plugin gets only what its lab editor needs (public()).

console_type: telnet (default), vnc (QEMU screen instead of a serial console; qemu images only) or none
(no console for anybody). A vnc image makes the manager put `console: "vnc"` into the node of the
slot's ticket (labdef.deploy_ticket); nodes of other images get no such field.

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


class Image(Strict):
    id: str = Field(pattern=IMAGE_ID_RE)
    title: str = Field(min_length=1, max_length=120)
    kind: Literal["iou", "qemu", "vpcs"]
    category: str = Field(default="other", pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    symbol: str | None = Field(default=None, max_length=200)
    console_type: Literal["telnet", "vnc", "none"] = "telnet"
    file: str | None = Field(default=None, max_length=255)  # Basename in /opt/gns3/images/{QEMU,IOU}
    ram_mb: Range | None = None
    cpus: Range | None = None
    adapters: Range | None = None
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
        if self.console_type == "vnc" and self.kind != "qemu":
            raise ValueError("console_type vnc is for qemu only")  # CONTRACTS 2.6
        if self.slot.cli_prompt is not None:
            try:
                pattern = re.compile(self.slot.cli_prompt.encode())
            except re.error as exc:
                raise ValueError("slot.cli_prompt is not a valid regular expression") from exc
            if pattern.search(b""):
                raise ValueError("slot.cli_prompt must not match an empty response")
        if self.slot.startup_script is not None and self.kind != "vpcs":
            raise ValueError("slot.startup_script is for vpcs only")
        if self.kind == "iou" and any("\n" in c.command or "\r" in c.command for c in self.slot.provisioning):
            raise ValueError("IOL provisioning commands must be single lines")
        return self

    def public(self) -> dict:
        """What the plugin's editor gets: no file names, no slot options."""
        return {
            "id": self.id, "title": self.title, "kind": self.kind, "category": self.category,
            "symbol": self.symbol or DEFAULT_SYMBOLS[self.kind], "console_type": self.console_type,
            "ram_mb": self.ram_mb.model_dump() if self.ram_mb else None,
            "cpus": self.cpus.model_dump() if self.cpus else None,
            "adapters": self.adapters.model_dump() if self.adapters else None,
            "port_name": PORT_NAMES[self.kind], "ports_per_adapter": PORTS_PER_ADAPTER[self.kind],
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
