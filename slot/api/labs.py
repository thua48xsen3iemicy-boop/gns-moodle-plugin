"""Project lifecycle. Never deletes an existing student project implicitly."""

import os
import re
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request

from api import disks, iol_nvram, uplink
from api.client import call, get_lifecycle
from api.models import CPU_PATTERN, DEFAULT_ADAPTER_TYPE, DeployTicket, NodeSpec

router = APIRouter(prefix="/v1/labs", tags=["labs"])
IMAGES = Path("/opt/gns3/images")
QGA_DIR = Path("/opt/gns3/qga")
OVMF_DIR = Path("/usr/share/OVMF")  # GNS3 2.2.61's default ovmf_firmware_dir; the "ovmf" package of Ubuntu 24.04
OVMF_FILES = ("ovmf_code_4m.fd", "ovmf_vars_4m.fd")  # what GNS3 looks for there, case-insensitively
DEFAULT_CPU = "host"  # KVM is required in the slot (lab-provision); nested virtualization needs the host's flags


def qemu_cpu(spec: NodeSpec):
    """"-cpu" of a new QEMU node: the ticket's, else SLOT_QEMU_CPU ("host"); an empty value leaves QEMU's own model."""
    if spec.cpu is not None:
        return spec.cpu
    value = os.getenv("SLOT_QEMU_CPU", DEFAULT_CPU).strip()
    if value and not re.fullmatch(CPU_PATTERN, value):
        raise HTTPException(500, "SLOT_QEMU_CPU is not a valid QEMU CPU model")
    return value or None


def uefi_enabled() -> bool:
    """Can GNS3 boot a node with "uefi": true? It takes the OVMF code and the variables store template from OVMF_DIR."""
    try:
        names = {f.name.lower() for f in OVMF_DIR.iterdir() if f.is_file()}
    except OSError:
        return False
    return all(name in names for name in OVMF_FILES)


def node_payload(spec: NodeSpec, node_id: str):
    common = {
        "name": spec.name,
        "node_id": node_id,
        "node_type": spec.kind,
        "compute_id": "local",
        "x": spec.x,
        "y": spec.y,
    }
    if spec.kind == "qemu":
        img = IMAGES / "QEMU" / spec.image
        if not img.is_file():
            raise HTTPException(422, f"Missing QEMU image: {spec.image}")
        options = ""
        if spec.qga:
            options = (
                f"-chardev socket,id=qga0,path={QGA_DIR / (node_id + '.sock')},"
                "server=on,wait=off -device virtio-serial-pci "
                "-device virtserialport,chardev=qga0,name=org.qemu.guest_agent.0"
            )
        # "vnc": GNS3 2.2.61 picks a free port from 5900 up (display = port - 5900) and starts
        # QEMU with "-vnc <console_host>:<display>"; console_host is the "[Server] host" of
        # gns3_server.conf (127.0.0.1 here). The node gets no serial port ("-serial" is added
        # for telnet only), so there is nothing for serial-getty to talk to. GNS3 itself appends
        # "-machine usb=on -device usb-tablet" for vnc (absolute pointer, no mouse offset), so the
        # slot adds only the display adapter: virtio-vga instead of QEMU's default std VGA. It is
        # VGA compatible (BIOS, boot loader and text mode work as before) and gives Linux guests
        # the virtio_gpu driver with resolutions a desktop needs.
        if spec.console == "vnc":
            options = f"{options} -vga virtio".strip()
        # GNS3 2.2.61 adds no "-cpu" of its own: without one QEMU emulates a basic model even under KVM, and a guest
        # that needs newer instructions or runs its own VMs (nested virtualization) does not work. Set once, when
        # the node is created: nodes of existing projects keep the options they were created with.
        cpu = qemu_cpu(spec)
        if cpu:
            options = f"{options} -cpu {cpu}".strip()
        # GNS3 2.2.61 sets no machine type (QEMU's "pc" then) but may add "-machine usb=on" (vnc) or "smm=off":
        # QEMU merges several -machine options, so the type of the ticket and those properties coexist.
        if spec.machine is not None:
            options = f"{options} -machine type={spec.machine}".strip()
        common.update(
            console_type=spec.console,
            properties={
                "qemu_path": "/usr/bin/qemu-system-x86_64",
                "ram": spec.ram_mb,
                "cpus": spec.cpus,
                "adapters": spec.adapters,
                # GNS3 2.2.61 starts QEMU with "-device <adapter_type>,mac=…" per adapter and adds
                # "speed=10000,duplex=full" to virtio-net-pci itself.
                "adapter_type": spec.adapter_type or DEFAULT_ADAPTER_TYPE,
                "hda_disk_image": spec.image,
                "hda_disk_interface": spec.disk_interface,
                "linked_clone": True,
                "options": options,
                # GNS3 2.2.61 then boots OVMF from pflash and copies the variables store into the node directory, so
                # boot entries persist with the project. No Secure Boot: under KVM GNS3 adds "-machine smm=off".
                **({"uefi": True} if spec.firmware == "uefi" else {}),
                **disks.properties(spec),
                **(
                    {"mac_address": uplink.base_mac(spec.uplink_port, spec.adapters)}
                    if spec.uplink_port is not None
                    else {}
                ),
            },
        )
    elif spec.kind == "iou":
        img = IMAGES / "IOU" / spec.image
        if not img.is_file():
            raise HTTPException(422, f"Missing IOL image: {spec.image}")
        common.update(
            properties={
                "path": spec.image,
                "ethernet_adapters": spec.ethernet_adapters,
                "serial_adapters": 0,
                "ram": spec.ram_mb,
                "nvram": 256,
                "l1_keepalives": spec.l1_keepalives,
                "use_default_iou_values": False,
            }
        )
    else:
        common.update(properties={"startup_script": spec.startup_script})
    return common


def require_uplink(ticket: DeployTicket):
    """A ticket with uplink_port needs the `uplink` feature of this slot: 422, nothing is created."""
    if uplink.spec_of(ticket) is not None and not uplink.enabled():
        raise HTTPException(
            422,
            "uplink_port needs the uplink feature of the slot: "
            "SLOT_UPLINK_MAC (locally administered unicast MAC) and the eth1 interface",
        )


def require_disks(ticket: DeployTicket):
    """A ticket with disks needs the `extra-disks` feature of this slot: 422, nothing is created."""
    if any(node.disks for node in ticket.nodes) and not disks.enabled(IMAGES):
        raise HTTPException(
            422,
            "disks need the extra-disks feature of the slot: "
            f"a blank qcow2 image {disks.blank_name()} in the QEMU images directory",
        )


def require_uefi(ticket: DeployTicket):
    """A ticket with firmware "uefi" needs the `qemu-uefi` feature of this slot: 422, nothing is created."""
    if any(node.firmware == "uefi" for node in ticket.nodes) and not uefi_enabled():
        raise HTTPException(
            422,
            "firmware uefi needs the qemu-uefi feature of the slot: "
            f"OVMF_CODE_4M.fd and OVMF_VARS_4M.fd in {OVMF_DIR} (the ovmf package)",
        )


@router.get("")
async def list_labs(request: Request):
    return await call(request, "GET", "projects")


async def create_project(request: Request, ticket: DeployTicket):
    """Create once; if UUID already exists, resume without modifying student work."""
    project_id = str(ticket.project_id or uuid4())
    # Check every image before we create a project, to avoid most half-built labs.
    existing = await call(request, "GET", "projects")
    if any(project["project_id"] == project_id for project in existing):
        p = await call(request, "GET", f"projects/{project_id}")
        if p["name"] != ticket.name:
            raise HTTPException(409, "Project UUID already used with a different name")
        if p.get("status") == "closed":
            await call(request, "POST", f"projects/{project_id}/open", {})
        return {
            "result": "resumed",
            "project_id": project_id,
            "nodes": uplink.visible_nodes(
                ticket, await call(request, "GET", f"projects/{project_id}/nodes")
            ),
        }
    # Do not overwrite another project just because it happens to have the same name.
    if any(p["name"] == ticket.name for p in existing):
        raise HTTPException(
            409, "Project name already exists; specify its project_id to resume"
        )
    require_uplink(ticket)
    require_disks(ticket)
    require_uefi(ticket)
    prepared = [(node, node_payload(node, str(uuid4()))) for node in ticket.nodes]
    cloud = None
    if uplink.spec_of(ticket) is not None:
        cloud = uplink.cloud_payload(str(uuid4()))
    QGA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    project = await call(
        request,
        "POST",
        "projects",
        {
            "project_id": project_id,
            "name": ticket.name,
            "auto_close": False,
            "auto_start": False,
        },
    )
    ids = {}
    for node, payload in prepared:
        created = await call(request, "POST", f"projects/{project_id}/nodes", payload)
        ids[node.name] = created["node_id"]
        if node.nvram_config is not None:
            # Once, here: the node has not started yet, and a resumed project never gets here.
            try:
                iol_nvram.preset(project_id, created, node.nvram_config)
            except iol_nvram.NvramError as exc:
                from api.lifecycle import LabError

                raise LabError(exc.code, str(exc)) from exc
        if node.disks:
            # Once, here, like the NVRAM: the node has not started yet, and a resumed project keeps its disks.
            async def resize(drive, extend, node_id=created["node_id"]):
                await call(request, "POST", f"projects/{project_id}/nodes/{node_id}/resize_disk",
                           {"drive_name": drive, "extend": extend})

            try:
                await disks.grow(resize, IMAGES, project_id, created, node)
            except disks.DiskError as exc:
                from api.lifecycle import LabError

                raise LabError(exc.code, str(exc)) from exc
    cloud_id = None
    if cloud is not None:
        cloud_id = (await call(request, "POST", f"projects/{project_id}/nodes", cloud))["node_id"]
    for link in ticket.links:
        payload = {
            "nodes": [
                {
                    "node_id": ids[ep.node],
                    "adapter_number": ep.adapter,
                    "port_number": ep.port,
                }
                for ep in (link.a, link.b)
            ]
        }
        await call(request, "POST", f"projects/{project_id}/links", payload)
    if cloud_id is not None:
        owner = uplink.spec_of(ticket)
        await call(
            request,
            "POST",
            f"projects/{project_id}/links",
            {
                "nodes": [
                    {"node_id": ids[owner.name], "adapter_number": owner.uplink_port, "port_number": 0},
                    {"node_id": cloud_id, "adapter_number": 0, "port_number": 0},
                ]
            },
        )
    return {
        "result": "created",
        "project_id": project["project_id"],
        "nodes": uplink.visible_nodes(
            ticket, await call(request, "GET", f"projects/{project_id}/nodes")
        ),
    }


def lab_ticket(request: Request, pid: str):
    lab = get_lifecycle(request).get("lab", pid)
    return lab["ticket"] if lab else {}


@router.get("/{project_id}")
async def status(request: Request, project_id: UUID):
    pid = str(project_id)
    project = await call(request, "GET", f"projects/{pid}")
    nodes = await call(request, "GET", f"projects/{pid}/nodes")
    return {"project": project, "nodes": uplink.visible_nodes(lab_ticket(request, pid), nodes)}


@router.get("/{project_id}/topology")
async def topology(request: Request, project_id: UUID):
    pid = str(project_id)
    ticket = lab_ticket(request, pid)
    nodes = await call(request, "GET", f"projects/{pid}/nodes")
    links = await call(request, "GET", f"projects/{pid}/links")
    return {
        "nodes": uplink.visible_nodes(ticket, nodes),
        "links": uplink.visible_links(ticket, nodes, links),
    }


@router.post("/deploy", status_code=202)
async def deploy(request: Request, ticket: DeployTicket):
    require_uplink(ticket)
    require_disks(ticket)
    require_uefi(ticket)
    return get_lifecycle(request).submit(
        "deploy", ticket=ticket, key=request.headers.get("Idempotency-Key")
    )


@router.get("/{project_id}/status")
async def lab_status(request: Request, project_id: UUID):
    return get_lifecycle(request).status(str(project_id))


@router.post("/{project_id}/resume", status_code=202)
async def resume(request: Request, project_id: UUID):
    return get_lifecycle(request).submit(
        "resume", str(project_id), key=request.headers.get("Idempotency-Key")
    )


@router.post("/{project_id}/stop", status_code=202)
async def stop(request: Request, project_id: UUID):
    return get_lifecycle(request).submit(
        "stop", str(project_id), key=request.headers.get("Idempotency-Key")
    )
