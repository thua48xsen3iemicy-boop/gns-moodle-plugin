"""Initial ticket format. A published lab template will eventually generate this ticket."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class NodeSpec(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    kind: Literal["qemu", "iou", "vpcs"]
    x: int = 0
    y: int = 0
    image: str | None = None  # Basename inside standard GNS3 images directory
    ram_mb: int = Field(default=1024, ge=128, le=32768)
    cpus: int = Field(default=1, ge=1, le=16)
    adapters: int = Field(default=1, ge=1, le=16)  # QEMU only
    ethernet_adapters: int = Field(default=1, ge=1, le=16)  # IOL: 4 ports per adapter
    disk_interface: Literal["virtio", "ide", "sata", "scsi"] = "virtio"
    qga: bool = True
    l1_keepalives: bool = True
    startup_script: str = ""  # VPCS only

    @model_validator(mode="after")
    def image_is_safe(self):
        if self.kind in ("qemu", "iou") and not self.image:
            raise ValueError(f"image is required for {self.kind}")
        if self.image and (self.image in (".", "..") or "/" in self.image or "\\" in self.image):
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
    auto_start: bool = False
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
                    raise ValueError(f"Invalid port: {ep.node} adapter={ep.adapter} port={ep.port}")
                port_key = (ep.node, ep.adapter, ep.port)
                if port_key in used:
                    raise ValueError(f"Port used more than once: {port_key}")
                used.add(port_key)
        return self


class ExecTicket(BaseModel):
    command: str = Field(min_length=1, max_length=8192)
    timeout_seconds: int = Field(default=20, ge=1, le=120)
