"""Lab Slot: a single active lab runtime backed by GNS3 2.2.x."""

import asyncio
import os
import sqlite3
from contextlib import asynccontextmanager

import httpx
from api.auth import require_token
from api.console import router as console_router
from api.health import router as health_router
from api.labs import router as labs_router
from api.lifecycle import Lifecycle
from api.lifecycle import router as operations_router
from api.nodes import router as nodes_router
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from release import VERSION, verify
from pydantic import BaseModel, ConfigDict, Field


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.initialized = False
    release = verify()
    if not release["ok"]:
        raise RuntimeError("RELEASE_INTEGRITY_ERROR: run release.py check")
    app.state.release_identity = release
    # Fail closed: the Slot API has privileged access to its GNS3 runtime.
    if not os.environ.get("SLOT_API_TOKEN"):
        raise RuntimeError("SLOT_API_TOKEN is required (see slot.env.example)")
    base = os.getenv("GNS3_API_URL", "http://127.0.0.1:3080/v2").rstrip("/")
    if base != "http://127.0.0.1:3080/v2":
        raise RuntimeError("First prototype requires local GNS3 at 127.0.0.1:3080")
    async with httpx.AsyncClient(
        base_url=base + "/", timeout=30.0, trust_env=False
    ) as client:
        app.state.gns3_http = client
        app.state.lifecycle = Lifecycle(app)
        app.state.initialized = True
        try:
            yield
        finally:
            app.state.initialized = False
            await app.state.lifecycle.close()


app = FastAPI(
    title="Lab Slot API",
    version=VERSION,
    lifespan=lifespan,
    description="Prototype. GNS3 project lifecycle and a controlled console/QGA gateway.",
)
app.include_router(health_router)
app.include_router(labs_router, dependencies=[Depends(require_token)])
app.include_router(nodes_router, dependencies=[Depends(require_token)])
app.include_router(console_router)  # WebSockets authenticate in their handler.

app.include_router(operations_router, dependencies=[Depends(require_token)])


@app.middleware("http")
async def initialization_guard(request: Request, call_next):
    if request.url.path not in ("/healthz", "/v1/readyz") and (
        not getattr(app.state, "initialized", False)
        or getattr(app.state, "lifecycle", None) is None
    ):
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "SERVICE_NOT_INITIALIZED",
                    "message": "Lifecycle is not initialized",
                }
            },
        )
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        try:
            identity = await asyncio.wait_for(asyncio.to_thread(verify), 3)
        except (OSError, TimeoutError):
            identity = None
        if (
            not identity
            or not identity.get("ok")
            or identity != getattr(app.state, "release_identity", None)
        ):
            return JSONResponse(
                status_code=503,
                content={
                    "detail": {
                        "code": "RELEASE_INTEGRITY_ERROR",
                        "message": "Release changed or is incomplete",
                    }
                },
            )
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        lifecycle = app.state.lifecycle
        async with lifecycle.command_lock:
            if request.url.path != "/v1/fence" and not lifecycle.owns(request.headers):
                return JSONResponse(status_code=409, content={"detail": {
                    "code": "STALE_OWNER", "message": "Claim current command ownership first"}})
            return await call_next(request)
    return await call_next(request)


class Fence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    epoch: int = Field(ge=1, le=2**63 - 1)
    generation: int = Field(ge=1, le=2**63 - 1)


@app.post("/v1/fence", dependencies=[Depends(require_token)])
async def claim_slot(spec: Fence):
    return await app.state.lifecycle.claim(spec.epoch, spec.generation)


@app.exception_handler(sqlite3.Error)
async def storage_error(request: Request, exc: sqlite3.Error):
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": "STATE_STORAGE_ERROR",
                "message": "Persistent state unavailable",
            }
        },
    )


@app.exception_handler(OSError)
async def filesystem_error(request: Request, exc: OSError):
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": "FILESYSTEM_ERROR",
                "message": "Service filesystem unavailable",
            }
        },
    )
