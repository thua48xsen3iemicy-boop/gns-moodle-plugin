"""Explicit node operations. Guest commands are for trusted Manager requests only."""

import asyncio
import base64
import json
import socket
import time
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from api.client import call, get_lifecycle
from api.models import ExecTicket, WipeTicket

router = APIRouter(prefix="/v1/labs/{project_id}/nodes", tags=["nodes"])
QGA_DIR = "/opt/gns3/qga"


def qga_call(
    sock_path: str, command: str, args: dict | None = None, timeout: float = 5.0
):
    message = {"execute": command}
    if args is not None:
        message["arguments"] = args
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(timeout)
        sock.connect(sock_path)
        sock.sendall((json.dumps(message) + "\n").encode())
        with sock.makefile("rb") as f:
            while True:
                line = f.readline()
                if not line:
                    raise ConnectionError("QGA connection closed")
                response = json.loads(line)
                if "error" in response:
                    raise RuntimeError(str(response["error"]))
                if "return" in response:
                    return response["return"]


def exec_sync(sock_path: str, command: str, seconds: int):
    task = qga_call(
        sock_path,
        "guest-exec",
        {"path": "/bin/sh", "arg": ["-c", command], "capture-output": True},
    )
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = qga_call(sock_path, "guest-exec-status", {"pid": task["pid"]})
        if result.get("exited"):
            return {
                "exit_code": result.get("exitcode"),
                "stdout": base64.b64decode(result.get("out-data", "")).decode(
                    errors="replace"
                ),
                "stderr": base64.b64decode(result.get("err-data", "")).decode(
                    errors="replace"
                ),
            }
        time.sleep(0.25)
    raise TimeoutError("Guest command did not finish before timeout")


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
        return await asyncio.to_thread(
            exec_sync,
            f"{QGA_DIR}/{node_id}.sock",
            ticket.command,
            ticket.timeout_seconds,
        )
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(502, f"QGA error: {type(exc).__name__}: {exc}") from exc


@router.post("/{node_id}/serial/enable")
async def enable_serial_getty(request: Request, project_id: UUID, node_id: UUID):
    """Enable a persistent Linux login console on ttyS0; QEMU QGA must be reachable."""
    node = await call(request, "GET", f"projects/{project_id}/nodes/{node_id}")
    if node.get("node_type") != "qemu" or node.get("status") != "started":
        raise HTTPException(409, "Serial getty setup requires a running QEMU node")
    sock = f"{QGA_DIR}/{node_id}.sock"
    for _ in range(45):
        try:
            await asyncio.to_thread(qga_call, sock, "guest-ping", None, 2.0)
            break
        except (OSError, ValueError, RuntimeError):
            await asyncio.sleep(1)
    else:
        raise HTTPException(504, "QGA did not become ready")
    try:
        result = await asyncio.to_thread(
            exec_sync, sock, "systemctl enable --now serial-getty@ttyS0.service", 15
        )
    except (OSError, RuntimeError, TimeoutError) as exc:
        raise HTTPException(502, f"QGA error: {type(exc).__name__}: {exc}") from exc
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
