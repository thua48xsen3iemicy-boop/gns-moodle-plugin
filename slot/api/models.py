"""Initial ticket format. A published lab template will eventually generate this ticket."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_serializer, model_validator


class ProvisionCommand(BaseModel):
    command: str = Field(min_length=1, max_length=8192)
    timeout_seconds: int = Field(default=20, ge=1, le=120)


class NodeSpec(BaseModel):
    name: str = Field(
        min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )
    kind: Literal["qemu", "iou", "vpcs"]
    x: int = 0
    y: int = 0
    image: str | None = None  # Basename inside standard GNS3 images directory
    ram_mb: int = Field(default=1024, ge=128, le=32768)
    cpus: int = Field(default=1, ge=1, le=16)
    adapters: int = Field(default=1, ge=1, le=16)  # QEMU only
    ethernet_adapters: int = Field(default=1, ge=1, le=16)  # IOL: 4 ports per adapter
    disk_interface: Literal["virtio", "ide", "sata", "scsi"] = "virtio"
    cli_prompt: str = Field(
        default=r"(?m)^[^\r\n]+[>#]\s*$", min_length=1, max_length=256
    )
    provisioning: list[ProvisionCommand] = Field(default_factory=list)
    qga: Literal[True] = True
    l1_keepalives: bool = True
    startup_script: str = ""  # VPCS only
    # QEMU only. "telnet" is the serial console; "vnc" is the graphic screen and the node then
    # has no serial console at all (CONTRACTS.md 4.2).
    console: Literal["telnet", "vnc"] = "telnet"

    @model_serializer(mode="wrap")
    def omit_default_console(self, handler) -> dict[str, Any]:
        # The dumped ticket is the project passport (compared on every repeated deploy and
        # stored in the lab record) and part of the idempotency fingerprint. The default
        # "telnet" must not appear in it: records written before this field existed, and a
        # manifest that omits it, keep their exact representation.
        data = handler(self)
        if isinstance(data, dict) and data.get("console") == "telnet":
            del data["console"]
        return data

    @model_validator(mode="after")
    def image_is_safe(self):
        if self.kind == "iou":
            import re

            try:
                pattern = re.compile(self.cli_prompt.encode())
            except re.error as exc:
                raise ValueError("Invalid CLI prompt pattern") from exc
            if pattern.search(b""):
                raise ValueError("CLI prompt must not match an empty response")
            if any("\n" in c.command or "\r" in c.command for c in self.provisioning):
                raise ValueError("IOL commands must be single lines")
        if self.kind in ("qemu", "iou") and not self.image:
            raise ValueError(f"image is required for {self.kind}")
        if self.console == "vnc" and self.kind != "qemu":
            raise ValueError("console vnc is only supported for qemu nodes")
        if self.image and (
            self.image in (".", "..") or "/" in self.image or "\\" in self.image
        ):
            raise ValueError("image must be a filename, not a path")
        return self


class Endpoint(BaseModel):
    node: str
    adapter: int = Field(default=0, ge=0)
    port: int = Field(default=0, ge=0)


class LinkSpec(BaseModel):
    a: Endpoint
    b: Endpoint


class DeployTicket(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    project_id: UUID | None = None  # Supply a stable UUID for idempotent resume
    auto_start: Literal[True] = True
    nodes: list[NodeSpec] = Field(min_length=1, max_length=100)
    links: list[LinkSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_links(self):
        names = [n.name for n in self.nodes]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate node names")
        used = set()
        by_name = {n.name: n for n in self.nodes}
        for link in self.links:
            if link.a.node not in by_name or link.b.node not in by_name:
                raise ValueError("Unknown node in link")
            if link.a == link.b:
                raise ValueError("Self-link to same port")
            for ep in (link.a, link.b):
                node = by_name[ep.node]
                if node.kind == "iou":
                    valid = ep.adapter < node.ethernet_adapters and ep.port < 4
                elif node.kind == "qemu":
                    valid = ep.adapter < node.adapters and ep.port == 0
                else:
                    valid = ep.adapter == 0 and ep.port == 0
                if not valid:
                    raise ValueError(
                        f"Invalid port: {ep.node} adapter={ep.adapter} port={ep.port}"
                    )
                port_key = (ep.node, ep.adapter, ep.port)
                if port_key in used:
                    raise ValueError(f"Port used more than once: {port_key}")
                used.add(port_key)
        return self


class ExecTicket(BaseModel):
    command: str = Field(min_length=1, max_length=8192)
    timeout_seconds: int = Field(default=20, ge=1, le=120)


class WipeTicket(BaseModel):
    confirm: bool = Field(strict=True)

    @model_validator(mode="after")
    def explicit_confirmation(self):
        if self.confirm is not True:
            raise ValueError("Wipe requires explicit confirm: true")
        return self
