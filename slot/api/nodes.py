"""Explicit node operations. Guest commands are for trusted Manager requests only."""

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from api.client import call, get_lifecycle
from api.models import ExecTicket, WipeTicket, ShutdownTicket
from api.qga import Priority, QgaError

router = APIRouter(prefix="/v1/labs/{project_id}/nodes", tags=["nodes"])


async def admitted(request: Request, work):
    """Run a guest command under the admission the middleware registered for it."""
    admission = getattr(request.state, "admission", None)
    return await (admission.run(work) if admission else work)


@router.get("/{node_id}/console")
async def console_info(request: Request, project_id: UUID, node_id: UUID):
    node = await call(request, "GET", f"projects/{project_id}/nodes/{node_id}")
    return {
        "name": node["name"],
        "status": node["status"],
        "console_type": node.get("console_type"),
        "console": node.get("console"),
        "websocket": f"/v1/console/{project_id}/{node_id}",
    }


@router.post("/{node_id}/qga/exec")
async def qga_exec(
    request: Request, project_id: UUID, node_id: UUID, ticket: ExecTicket
):
    node = await call(request, "GET", f"projects/{project_id}/nodes/{node_id}")
    if node.get("node_type") != "qemu" or node.get("status") != "started":
        raise HTTPException(409, "QGA command requires a running QEMU node")
    try:
        return await admitted(
            request,
            get_lifecycle(request).qga.exec(
                str(node_id),
                ticket.command,
                timeout=ticket.timeout_seconds,
                priority=Priority.COMMAND,
            ),
        )
    except (QgaError, OSError, ValueError) as exc:
        raise HTTPException(502, f"QGA error: {type(exc).__name__}: {exc}") from exc


@router.post("/{node_id}/serial/enable")
async def enable_serial_getty(request: Request, project_id: UUID, node_id: UUID):
    """Enable a persistent Linux login console on ttyS0; QEMU QGA must be reachable."""
    node = await call(request, "GET", f"projects/{project_id}/nodes/{node_id}")
    if node.get("console_type") == "vnc":
        # No serial port is attached to such a node: ttyS0 does not exist in the guest.
        raise HTTPException(
            409, "Serial getty setup is not available for a node with a VNC console"
        )
    if node.get("node_type") != "qemu" or node.get("status") != "started":
        raise HTTPException(409, "Serial getty setup requires a running QEMU node")
    qga = get_lifecycle(request).qga

    async def enable():
        for _ in range(45):
            try:
                await qga.call(
                    str(node_id), "guest-ping", timeout=2.0, priority=Priority.COMMAND
                )
                break
            except (QgaError, OSError, ValueError):
                await asyncio.sleep(1)
        else:
            raise HTTPException(504, "QGA did not become ready")
        try:
            return await qga.exec(
                str(node_id),
                "systemctl enable --now serial-getty@ttyS0.service",
                timeout=15,
                priority=Priority.COMMAND,
            )
        except (QgaError, OSError) as exc:
            raise HTTPException(
                502, f"QGA error: {type(exc).__name__}: {exc}"
            ) from exc

    result = await admitted(request, enable())
    if result["exit_code"] != 0:
        raise HTTPException(
            409, detail={"error": "serial-getty failed", "result": result}
        )
    return {
        "result": "serial_getty_enabled",
        "node_id": str(node_id),
        "command": result,
    }


@router.post("/{node_id}/wipe", status_code=202)
async def wipe(request: Request, project_id: UUID, node_id: UUID, ticket: WipeTicket):
    return get_lifecycle(request).submit(
        "wipe",
        str(project_id),
        node_id=str(node_id),
        key=request.headers.get("Idempotency-Key"),
    )


@router.post("/{node_id}/shutdown", status_code=202)
async def shutdown(request: Request, project_id: UUID, node_id: UUID, ticket: ShutdownTicket):
    lifecycle = get_lifecycle(request)
    lab = lifecycle.get("lab", str(project_id))
    if not lab or lab["state"] != "READY":
        raise HTTPException(409, {"code": "INVALID_TRANSITION", "message": "Shutdown requires a READY laboratory"})
    node = await call(request, "GET", f"projects/{project_id}/nodes/{node_id}")
    if node.get("node_type") != "qemu":
        raise HTTPException(409, {"code": "NOT_SUPPORTED", "message": "Guest shutdown requires a QEMU node"})
    result = lifecycle.submit_shutdown(str(project_id), str(node_id), ticket.timeout,
                                       request.headers.get("Idempotency-Key"), request.state.admission)
    if result.get("replayed") and result.get("status") == "SUCCEEDED" and node.get("status") != "stopped":
        raise HTTPException(409, {"code": "STALE_IDEMPOTENCY_KEY", "message": "Node was restarted; use a new shutdown key"})
    return result
