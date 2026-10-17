"""HTTP API for the Moodle plugin (plugin/docs/SPEC.md §3) plus health endpoints."""

import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import timezone

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import VERSION
from .agent_client import AgentClient
from .db import latest_version, make_pool, schema_version
from .errors import public_error
from .labs import Catalog, LabError, LabNotFound
from .orchestrator import Orchestrator
from .proxmox import PveClient
from .slot_client import GatewayClient, SlotClient, SlotError
from .storage import StorageError, student_key
from .store import Store

log = logging.getLogger("lab_manager.api")


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str, **extra):
        self.status, self.error, self.message, self.extra = status, error, message, extra


class Strict(BaseModel):
    # Unknown fields are refused (CONTRACTS §1.2): the plugin learns it was not understood.
    model_config = ConfigDict(extra="forbid")


class SessionOptions(Strict):
    max_duration: int | None = Field(default=None, ge=0, le=7 * 24 * 60)  # Minutes; 0 = default


class ClientInfo(Strict):
    plugin_version: str | None = Field(default=None, max_length=64)
    moodle_release: str | None = Field(default=None, max_length=64)


class SessionCreate(Strict):
    user_id: int = Field(gt=0)
    instance_id: int = Field(gt=0)
    lab_ref: str = Field(min_length=1, max_length=64)
    course_id: int | None = Field(default=None, ge=0)
    options: SessionOptions | None = None
    client: ClientInfo | None = None


class TicketRequest(Strict):
    node_id: str = Field(min_length=1, max_length=64)
    readonly: bool = False
    issued_for_user_id: int | None = Field(default=None, gt=0)


class StopRequest(Strict):
    requested_by_user_id: int | None = Field(default=None, gt=0)  # Journal only (events)


OPTIONS = ["max_duration"]


def iso_or_none(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if value is not None else None


class Services:
    """Everything a request or the orchestrator needs; built once at startup."""

    def __init__(self, settings, *, pve_transport=None, agent_transport=None, slot_transport=None,
                 gateway_transport=None, pool=None):
        self.settings = settings
        self.pool = pool or make_pool(settings.database_url)
        self.store = Store(self.pool)
        self.catalog = Catalog(settings.labs_dir)
        self.pve = PveClient(
            settings.pve_nodes, settings.pve_token_id, settings.pve_token_secret,
            ca_file=settings.pve_ca_file, timeout=settings.pve_timeout,
            retry_after=settings.pve_retry_after, transport=pve_transport,
        )
        self.agent = AgentClient(
            settings.agent_url_template, ca_file=settings.agent_ca_file, cert_file=settings.agent_cert_file,
            key_file=settings.agent_key_file, timeout=settings.agent_timeout,
            start_timeout=settings.agent_start_timeout, mounts_timeout=settings.agent_mounts_timeout,
            stop_timeout=settings.agent_stop_timeout,
            transport=agent_transport,
        )
        self.slots = {
            vmid: SlotClient(s.address, s.port, s.api_token, timeout=settings.slot_timeout,
                             transport=slot_transport)
            for vmid, s in settings.slots.items()
        }
        self.gateway = GatewayClient(settings.gateway_internal_url, settings.gateway_internal_key,
                                     settings.gateway_public_url, transport=gateway_transport)
        self.orchestrator = Orchestrator(settings, self.store, self.pve, self.agent, self.slots,
                                         self.catalog)

    async def leader_connection(self):
        from psycopg.rows import dict_row

        return await psycopg.AsyncConnection.connect(self.settings.database_url, autocommit=True,
                                                     row_factory=dict_row)

    async def close(self):
        await self.pve.close()
        await self.agent.close()
        await self.gateway.close()
        for client in self.slots.values():
            await client.close()
        await self.pool.close()


def create_app(services: Services, *, run_orchestrator=True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await services.pool.open(wait=True, timeout=10)
        async with services.pool.connection() as conn:
            version = await schema_version(conn)
        if version != latest_version():
            raise RuntimeError(f"DB schema version {version}, expected {latest_version()}: run admin migrate")
        await services.store.sync_slots(list(services.settings.slots.values()))
        task = None
        if run_orchestrator:
            task = asyncio.create_task(services.orchestrator.run_forever(services.leader_connection))
        app.state.orchestrator_task = task
        try:
            yield
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await services.close()

    app = FastAPI(title="Lab Manager", version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    settings = services.settings
    store = services.store

    @app.exception_handler(ApiError)
    async def api_error(request, exc: ApiError):
        return JSONResponse(status_code=exc.status,
                            content={"error": exc.error, "message": exc.message, **exc.extra})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errors = exc.errors()
        unknown = [".".join(map(str, e["loc"][1:])) for e in errors if e.get("type") == "extra_forbidden"]
        if unknown:
            return JSONResponse(status_code=400, content={"error": "unsupported_field",
                                                          "message": f"Unsupported fields: {', '.join(unknown)}"})
        fields = ", ".join(".".join(map(str, e["loc"][1:])) for e in errors) or "body"
        return JSONResponse(status_code=400, content={"error": "invalid_request",
                                                      "message": f"Invalid fields: {fields}"})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return JSONResponse(status_code=exc.status_code,
                            content={"error": "not_found" if exc.status_code == 404 else "http_error",
                                     "message": str(exc.detail)})

    @app.exception_handler(psycopg.Error)
    async def db_error(request, exc):
        log.error("Database error: %s", type(exc).__name__)
        return JSONResponse(status_code=503, content={"error": "storage_unavailable",
                                                      "message": "Database unavailable"})

    def site_of(request: Request):
        """Bearer secret of the site named in X-Moodle-Site; constant-time comparison."""
        site = settings.site(request.headers.get("X-Moodle-Site"))
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        expected = site.secret if site else "x" * 64
        if not (secrets.compare_digest(token.encode(), expected.encode()) and site):
            raise ApiError(401, "unauthorized", "Invalid credentials or unknown X-Moodle-Site")
        return site

    def summary(s):
        body = {
            "schema": "gns3lab.session/1",
            "session_id": s["id"],
            "lab_ref": s["lab_ref"],
            "status": s["status"],
            "stage": s["stage"],
            "status_message": s["status_message"],
            "error": public_error(s["error"]) if s["status"] == "failed" else None,
            "started_at": iso_or_none(s["started_at"]),
            "expires_at": iso_or_none(s["expires_at"]),
        }
        if s["progress"] is not None:
            body["progress"] = s["progress"]
        return body

    async def present(s):
        body = summary(s)
        body["features"] = ["consoles"] if s["status"] == "running" else []
        if s["status"] == "running" and s["topology"]:
            body["topology"] = s["topology"]
            body["nodes"], stale = await services.orchestrator.live_nodes(s)
            if stale:
                body["nodes_stale"] = True
        return body

    async def own_session(session_id, site):
        s = await store.session(session_id, site.slug)
        if not s:
            raise ApiError(404, "session_not_found", "Unknown session")
        return s

    # ------------------------------------------------------------------ health
    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "version": VERSION}

    @app.get("/readyz")
    async def readyz():
        checks = {}
        try:
            async with services.pool.connection() as conn:
                ok = await asyncio.wait_for(schema_version(conn), 3) == latest_version()
            checks["database"] = {"ok": ok}
        except Exception:  # noqa: BLE001 - readiness reports, never raises
            checks["database"] = {"ok": False, "code": "DATABASE_UNAVAILABLE"}
        orch = services.orchestrator
        task = getattr(app.state, "orchestrator_task", None)
        alive = task is not None and not task.done()
        fresh = orch.last_tick is not None and time.monotonic() - orch.last_tick < 60
        checks["orchestrator"] = {"ok": alive, "leader": orch.leader, "fresh": fresh}
        try:
            checks["labs"] = {"ok": settings.labs_dir.is_dir()}
        except OSError:
            checks["labs"] = {"ok": False}
        ready = all(c["ok"] for c in checks.values())
        return JSONResponse(status_code=200 if ready else 503,
                            content={"status": "ready" if ready else "not_ready", "version": VERSION,
                                     "checks": checks})

    # ------------------------------------------------------------------ SPEC §3
    @app.get("/api/v1/info")
    async def info(site=Depends(site_of)):
        features = ["consoles", "observer"]
        if settings.lab_persistence == "resume":
            features.append("resume")  # The next session continues the student's project
        return {"schema": "gns3lab.info/1", "version": VERSION, "session_schemas": ["gns3lab.session/1"],
                "options": OPTIONS, "features": features}

    @app.get("/api/v1/labs")
    async def list_labs(site=Depends(site_of)):
        try:
            return await asyncio.to_thread(services.catalog.list)
        except LabError as exc:
            raise ApiError(503, "catalog_unavailable", str(exc)) from exc

    @app.get("/api/v1/labs/{lab_ref}/topology")
    async def lab_topology(lab_ref: str, site=Depends(site_of)):
        try:
            lab = await asyncio.to_thread(services.catalog.get, lab_ref)
        except LabNotFound as exc:
            raise ApiError(404, "lab_not_found", "Unknown lab_ref") from exc
        except LabError as exc:
            raise ApiError(409, "lab_invalid", str(exc)) from exc
        return lab.topology

    @app.get("/api/v1/sessions")
    async def find_session(instance_id: int, user_id: int | None = None, site=Depends(site_of)):
        if user_id is None:
            # Observer page: one entry per student of the activity, without topology (П1).
            rows = await store.activity_sessions(site.slug, instance_id)
            return [dict(summary(s), user_id=s["user_id"]) for s in rows]
        s = await store.latest_session(site.slug, user_id, instance_id)
        if not s:
            raise ApiError(404, "session_not_found", "No session for this user and instance")
        return await present(s)

    @app.post("/api/v1/sessions")
    async def create_session(body: SessionCreate, site=Depends(site_of)):
        def same(s):
            return s["instance_id"] == body.instance_id and s["lab_ref"] == body.lab_ref

        def conflict(s):
            # The student's own other lab, so the plugin can link to it (П3).
            return ApiError(409, "session_conflict", "У студента уже запущена другая лаба; сначала остановите её",
                            conflict={"instance_id": s["instance_id"], "course_id": s["course_id"],
                                      "lab_ref": s["lab_ref"], "status": s["status"]})

        existing = await store.live_session(site.slug, body.user_id)
        if existing:
            if not same(existing):
                raise conflict(existing)
            return JSONResponse(status_code=200, content=await present(existing))  # options not re-applied
        try:
            lab = await asyncio.to_thread(services.catalog.get, body.lab_ref)
            key = student_key(site.slug, body.user_id)
        except LabNotFound as exc:
            raise ApiError(404, "lab_not_found", "Unknown lab_ref") from exc
        except LabError as exc:
            raise ApiError(409, "lab_invalid", str(exc)) from exc
        except StorageError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        requested = body.options.max_duration if body.options else None
        max_duration = min(requested, settings.max_duration_limit) if requested else settings.default_max_duration
        s, created = await store.create_session(
            site=site.slug, user_id=body.user_id, instance_id=body.instance_id,
            course_id=body.course_id, lab_ref=body.lab_ref, max_duration=max_duration,
            student_key=key, resources=lab.resources, persistence=settings.lab_persistence,
        )
        if not created and not same(s):
            raise conflict(s)  # A concurrent POST for another activity won
        if created and body.client:
            await store.event(s["id"], None, "plugin_client", body.client.model_dump(exclude_none=True))
        services.orchestrator.wake()
        return JSONResponse(status_code=201 if created else 200, content=await present(s))

    @app.get("/api/v1/sessions/{session_id}")
    async def get_session(session_id: str, site=Depends(site_of)):
        return await present(await own_session(session_id, site))

    @app.post("/api/v1/sessions/{session_id}/stop")
    async def stop_session(session_id: str, body: StopRequest | None = None, site=Depends(site_of)):
        s = await own_session(session_id, site)
        if s["status"] not in ("stopped", "failed"):
            by = body.requested_by_user_id if body else None
            reason = "user" if by is None or by == s["user_id"] else "teacher"
            await store.request_stop(s["id"], site.slug, reason)
            if by is not None:
                await store.event(s["id"], s["slot_vmid"], "stop_requested_by", {"user_id": by})
            services.orchestrator.wake()
            s = await own_session(session_id, site)
            if s["status"] not in ("stopped", "failed"):
                # The orchestrator moves it to stopping on its next step; report the intent now.
                body = await present(s)
                body["status"] = "stopping"
                if s["status"] != "stopping":
                    body["status_message"] = "Остановка…"
                return body
        return await present(s)

    @app.post("/api/v1/sessions/{session_id}/console-tickets")
    async def console_ticket(session_id: str, body: TicketRequest, site=Depends(site_of)):
        s = await own_session(session_id, site)
        if s["status"] != "running" or not s["topology"]:
            raise ApiError(409, "session_not_running", "Session is not running")
        node_ids = {n["node_id"] for n in s["topology"]["topology"]["nodes"]}
        if body.node_id not in node_ids:
            raise ApiError(404, "node_not_found", "Node does not belong to this session")
        nodes, stale = await services.orchestrator.live_nodes(s)
        if not stale and nodes.get(body.node_id, {}).get("status") != "started":
            # Saves the plugin a closed WebSocket and a session re-read (П5).
            raise ApiError(409, "node_not_running", "Node is not started")
        slot = settings.slots.get(s["slot_vmid"])
        if slot is None:
            raise ApiError(503, "slot_unavailable", "Slot is not configured")
        who = f"{s['id']}:{'observer' if body.readonly else 'owner'}"
        try:
            ticket = await services.gateway.issue(slot=slot.address, project_id=str(s["project_id"]),
                                                  node_id=body.node_id, readonly=body.readonly, who=who)
        except SlotError as exc:
            raise ApiError(502, "gateway_unavailable", "Console gateway did not issue a ticket") from exc
        await store.event(s["id"], s["slot_vmid"], "console_ticket",
                          {"node_id": body.node_id, "readonly": body.readonly,
                           "issued_for_user_id": body.issued_for_user_id})
        return ticket

    return app
