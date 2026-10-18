"""Liveness is separate from bounded, non-destructive service readiness checks."""

import asyncio
import fcntl
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from release import VERSION, verify

from api.auth import require_token
from api.client import call

router = APIRouter(tags=["health"])


# Manager negotiation (manager/docs/CONTRACTS.md §4.1); names are registered in its §7.
FEATURES = ["qemu", "iou", "vpcs", "qga", "iol-cli", "student-state"]


@router.get("/healthz")
async def healthz():
    return {"status": "ok", "version": VERSION}


@router.get("/v1/capabilities", dependencies=[Depends(require_token)])
async def capabilities():
    return {
        "schema": "gns3lab.capabilities/1",
        "slot_version": VERSION,
        "deploy_schemas": ["gns3lab.deploy/1"],
        "features": FEATURES,
    }


def probe_directory(root):
    # Create only a private temporary file, never touch project or lock files.
    with tempfile.TemporaryFile(dir=root) as probe:
        probe.write(b"slot-readiness")
        probe.flush()
        os.fsync(probe.fileno())
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return {"ok": True}


@router.get("/v1/readyz")
async def readyz(request: Request):
    checks = {}
    lifecycle = getattr(request.app.state, "lifecycle", None)
    monitor = getattr(lifecycle, "monitor", None)
    checks["lifecycle"] = {
        "ok": bool(
            lifecycle
            and getattr(request.app.state, "initialized", False)
            and monitor is not None
            and not monitor.done()
        ),
        "code": "LIFECYCLE_NOT_INITIALIZED",
    }
    if checks["lifecycle"]["ok"]:
        checks["lifecycle"] = {"ok": True}
    try:
        checks["release"] = await asyncio.wait_for(asyncio.to_thread(verify), 3)
        if checks["release"] != getattr(request.app.state, "release_identity", None):
            checks["release"] = {
                **checks["release"],
                "ok": False,
                "code": "RELEASE_CHANGED",
            }
    except (OSError, TimeoutError):
        checks["release"] = {"ok": False, "code": "RELEASE_CHECK_FAILED"}
    try:
        if lifecycle is None:
            raise RuntimeError("Missing lifecycle")
        lifecycle.db.execute("SAVEPOINT readiness")
        try:
            lifecycle.db.execute(
                "INSERT OR REPLACE INTO records VALUES ('readiness','probe','{}')"
            )
        finally:
            lifecycle.db.execute("ROLLBACK TO readiness")
            lifecycle.db.execute("RELEASE readiness")
        checks["sqlite"] = {"ok": True}
    except Exception:  # noqa: BLE001 - expose a stable diagnostic, never database contents.
        checks["sqlite"] = {"ok": False, "code": "SQLITE_UNAVAILABLE"}
    for name, root in (
        ("storage", getattr(lifecycle, "root", None)),
        ("lab_state", getattr(getattr(lifecycle, "labs", None), "root", None)),
        (
            "locks",
            Path(os.getenv("SLOT_PROJECT_LOCK_DIR", "/opt/gns3/projects/.slot-locks")),
        ),
    ):
        try:
            if root is None:
                raise OSError("No state storage")
            checks[name] = await asyncio.wait_for(
                asyncio.to_thread(probe_directory, root), 3
            )
        except (OSError, TimeoutError):
            checks[name] = {"ok": False, "code": name.upper() + "_UNAVAILABLE"}
    gns3_version = None
    try:
        gns3 = await asyncio.wait_for(call(request, "GET", "version"), 3)
        gns3_version = gns3.get("version")
        checks["gns3"] = (
            {"ok": gns3_version == "2.2.61", "code": "GNS3_VERSION_UNSUPPORTED"}
            if gns3_version != "2.2.61"
            else {"ok": True}
        )
    except Exception:  # noqa: BLE001 - dependency failure is a readiness result.
        checks["gns3"] = {"ok": False, "code": "GNS3_UNAVAILABLE"}
    ready = all(check["ok"] for check in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "version": VERSION,
            "build_id": checks["release"].get("build_id"),
            "gns3_version": gns3_version,
            "checks": checks,
        },
    )
