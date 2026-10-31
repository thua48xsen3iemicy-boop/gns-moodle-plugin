"""HTTP API for the Moodle plugin (plugin/docs/SPEC.md §3) plus health endpoints."""

import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager
from datetime import timezone

import psycopg
from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import VERSION, labdef
from .derived import DerivedImages, register_routes
from .agent_client import AgentClient, start_budget_warnings
from .db import latest_version, make_pool, schema_version
from .errors import public_error
from .facts import build_state
from .labs import Catalog, LabError, LabNotFound, deploy_ticket
from .orchestrator import Orchestrator
from .proxmox import PveClient
from .slot_client import GatewayClient, SlotClient, SlotError
from .storage import StorageError, student_key, authoring_key
from .store import Store, CaptureConflict

log = logging.getLogger("lab_manager.api")


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str, **extra):
        self.status, self.error, self.message, self.extra = status, error, message, extra


class Strict(BaseModel):
    # Unknown fields are refused (CONTRACTS §1.2): the plugin learns it was not understood.
    model_config = ConfigDict(extra="forbid")


class SessionOptions(Strict):
    mode: str | None = None
    max_duration: int | None = Field(default=None, ge=0, le=7 * 24 * 60)  # Minutes; 0 = default


class ClientInfo(Strict):
    plugin_version: str | None = Field(default=None, max_length=64)
    moodle_release: str | None = Field(default=None, max_length=64)


class SessionCreate(Strict):
    user_id: int = Field(gt=0)
    instance_id: int | None = Field(default=None, gt=0)
    library_lab_id: int | None = Field(default=None, gt=0)
    lab_ref: str | None = Field(default=None, min_length=1, max_length=64)
    lab: dict | None = None  # gns3lab.lab/1 or /2 from Moodle, checked by labdef.check (exactly one of the two)
    course_id: int | None = Field(default=None, ge=0)
    options: SessionOptions | None = None
    client: ClientInfo | None = None


class LabValidate(Strict):
    lab: dict


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
                             scheme=settings.slot_scheme, ca_file=settings.slot_ca_file,
                             transport=slot_transport)
            for vmid, s in settings.slots.items()
        }
        self.gateway = GatewayClient(settings.gateway_internal_url, settings.gateway_internal_key,
                                     settings.gateway_public_url, transport=gateway_transport)
        self.derived = DerivedImages(self)
        self.orchestrator = Orchestrator(settings, self.store, self.pve, self.agent, self.slots,
                                         self.catalog)
        self.orchestrator.derived = self.derived

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
    async def warn_start_budget():
        """The agents report how long a start may take (1.2.1); a shorter AGENT_START_TIMEOUT loses starts."""
        try:
            for line in await start_budget_warnings(services.agent, await services.pve.online_nodes(),
                                                    services.settings.agent_start_timeout):
                log.warning(line)
        except Exception as exc:  # noqa: BLE001 - a diagnostic must never stop the service
            log.warning("Start budget of the agents not checked: %s", type(exc).__name__)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await services.pool.open(wait=True, timeout=10)
        async with services.pool.connection() as conn:
            version = await schema_version(conn)
        if version != latest_version():
            raise RuntimeError(f"DB schema version {version}, expected {latest_version()}: run admin migrate")
        await services.store.sync_slots(list(services.settings.slots.values()))
        budget_task = None
        if run_orchestrator:
            budget_task = asyncio.create_task(warn_start_budget())
        task = None
        if run_orchestrator:
            task = asyncio.create_task(services.orchestrator.run_forever(services.leader_connection))
        app.state.orchestrator_task = task
        try:
            yield
        finally:
            for t in (task, budget_task):
                if t:
                    t.cancel()
                    await asyncio.gather(t, return_exceptions=True)
            await services.close()

    app = FastAPI(title="Lab Manager", version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    settings = services.settings
    store = services.store

    @app.exception_handler(ApiError)
    async def api_error(request, exc: ApiError):
        return JSONResponse(status_code=exc.status,
                            content={"error": exc.error, "message": exc.message, **exc.extra})

    @app.exception_handler(CaptureConflict)
    async def capture_conflict(request, exc):
        return JSONResponse(status_code=409, content={"error": "capture_in_progress", "message": str(exc)})

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
            "lab_def": s["lab_def"],
            "status": s["status"],
            "stage": s["stage"],
            "status_message": s["status_message"],
            "error": public_error(s["error"]) if s["status"] == "failed" else None,
            "started_at": iso_or_none(s["started_at"]),
            "expires_at": iso_or_none(s["expires_at"]),
        }
        if s["progress"] is not None:
            body["progress"] = s["progress"]
        if s.get("mode"):
            body["mode"] = s["mode"]
            body["capture_nodes"] = [n["name"] for n in (s.get("topology") or {}).get("topology", {}).get("nodes", [])
                                     if n.get("node_type") == "qemu" and n.get("status") == "started"]
        return body

    async def present(s):
        body = summary(s)
        body["features"] = ["consoles"] if s["status"] == "running" else []
        if s["status"] == "running" and s["facts_polled_at"] is not None:
            body["features"].append("facts")  # The slot of this session serves facts (CONTRACTS 2.7)
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
        checks["orchestrator"] = {"ok": alive and orch.leader and fresh,
                                   "leader": orch.leader, "fresh": fresh}
        try:
            checks["labs"] = {"ok": settings.labs_dir.is_dir()}
        except OSError:
            checks["labs"] = {"ok": False}
        ready = all(c["ok"] for c in checks.values())
        return JSONResponse(status_code=200 if ready else 503,
                            content={"status": "ready" if ready else "not_ready", "version": VERSION,
                                     "checks": checks})

    # ------------------------------------------------------------------ SPEC §3
    async def site_images(site):
        return {k: v for k, v in (await services.derived.catalog(site.slug)).items()
                if site.limits.allows(v.derived["base"] if v.derived else k)}

    async def check_lab(lab, site):
        if settings.images is None:
            raise ApiError(400, "unsupported_field", "Lab definitions are not enabled (no image catalog)")
        try:
            return labdef.check(lab, await services.derived.catalog(site.slug), site.limits, overhead_mb=settings.lab_memory_overhead_mb)
        except labdef.LabInvalid as exc:
            raise ApiError(422, "lab_invalid", "The lab definition is invalid", problems=exc.problems) from exc

    @app.get("/api/v1/info")
    async def info(site=Depends(site_of)):
        features = ["consoles", "observer", "resume", "facts"]  # resume: the next session continues the project
        body = {"schema": "gns3lab.info/1", "version": VERSION, "session_schemas": ["gns3lab.session/1"],
                "options": list(OPTIONS), "features": features}
        if settings.images is not None:
            features += ["images", "lab_definitions", "lab_constructor"]
            body["lab_formats"] = list(labdef.FORMATS)
            body["limits"] = site.limits.public()
        if await services.derived.enabled():
            features.append("image_capture")
            body["options"].append("mode")
        return body

    @app.get("/api/v1/images")
    async def images(site=Depends(site_of)):
        if settings.images is None:
            raise ApiError(404, "not_found", "No image catalog")
        return {"images": [image.public() for image in (await site_images(site)).values()]}

    @app.post("/api/v1/labs/validate")
    async def validate_lab(body: LabValidate, site=Depends(site_of)):
        checked = await check_lab(body.lab, site)
        return {"lab_def": checked.lab_def, "resources": checked.resources, "warnings": list(checked.warnings)}

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
    async def find_session(instance_id: int | None = None, user_id: int | None = None, library_lab_id: int | None = None, site=Depends(site_of)):
        if library_lab_id is not None:
            if instance_id is not None or user_id is None or library_lab_id <= 0:
                raise ApiError(400, "invalid_request", "Invalid authoring lookup")
            async with store.pool.connection() as conn:
                cur = await conn.execute("SELECT * FROM sessions WHERE site = %s AND user_id = %s AND library_lab_id = %s ORDER BY created_at DESC LIMIT 1", (site.slug, user_id, library_lab_id))
                session = await cur.fetchone()
            if session is None:
                raise ApiError(404, "session_not_found", "No authoring session")
            return await present(session)
        if instance_id is None:
            raise ApiError(400, "invalid_request", "instance_id is required")
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
        mode = body.options.mode if body.options else None
        authoring = mode == "authoring"
        if (mode not in (None, "authoring") or
                (authoring and (body.instance_id is not None or body.library_lab_id is None or body.lab is None or body.lab_ref is not None)) or
                (not authoring and (body.instance_id is None or body.library_lab_id is not None))):
            raise ApiError(400, "invalid_request", "Invalid authoring identity or mode")
        if authoring and not await services.derived.enabled():
            raise ApiError(400, "unsupported_field", "Authoring is unavailable")
        if (body.lab_ref is None) == (body.lab is None):
            raise ApiError(400, "invalid_request", "Exactly one of lab_ref and lab is required")
        # A definition is checked (and hashed) even for a repeated POST: lab_def identifies the lab.
        checked = await check_lab(body.lab, site) if body.lab is not None else None
        lab_def = checked.lab_def if checked else None

        def same(s):
            return s["instance_id"] == body.instance_id and s.get("library_lab_id") == body.library_lab_id and s["lab_ref"] == body.lab_ref and s["lab_def"] == lab_def

        def conflict(s):
            # The student's own other lab, so the plugin can link to it (П3).
            return ApiError(409, "session_conflict", "У студента уже запущена другая лаба; сначала остановите её",
                            conflict={"instance_id": s["instance_id"], "course_id": s["course_id"],
                                      "lab_ref": s["lab_ref"], "lab_def": s["lab_def"], "status": s["status"]})

        existing = await store.live_session(site.slug, body.user_id, library_lab_id=body.library_lab_id)
        if existing:
            if not same(existing):
                raise conflict(existing)
            return JSONResponse(status_code=200, content=await present(existing))  # options not re-applied
        try:
            lab = None if checked else await asyncio.to_thread(services.catalog.get, body.lab_ref)
            resources = checked.resources if checked else lab.resources
            if not resources or not all(resources.get(k, 0) > 0 for k in ("cores", "memory_mb")):
                raise LabError("Explicit cores and memory_mb are required for resource reservations")
            key = authoring_key(site.slug, body.user_id, body.library_lab_id) if authoring else student_key(site.slug, body.user_id)
        except LabNotFound as exc:
            raise ApiError(404, "lab_not_found", "Unknown lab_ref") from exc
        except LabError as exc:
            raise ApiError(409, "lab_invalid", str(exc)) from exc
        except StorageError as exc:
            raise ApiError(400, "invalid_request", str(exc)) from exc
        nodes = (checked.definition["nodes"] if checked else lab.ticket["nodes"])
        if any(n.get("uplink_port") is not None for n in nodes) and not any(
                "uplink" in slot.features for slot in settings.slots.values()):
            raise ApiError(409, "unsupported_lab", "The lab has a node with an internet uplink, no slot offers it")
        requested = body.options.max_duration if body.options else None
        max_duration = min(requested, settings.max_duration_limit) if requested else settings.default_max_duration
        if authoring:
            max_duration = min(requested or settings.authoring_max_duration, settings.authoring_max_duration)
        image_map = await site_images(site) if checked else {}
        s, created = await store.create_session(
            site=site.slug, user_id=body.user_id, instance_id=body.instance_id,
            course_id=body.course_id, lab_ref=body.lab_ref, max_duration=max_duration,
            student_key=key, resources=resources, mode=mode, library_lab_id=body.library_lab_id,
            lab_def=lab_def, definition=checked.definition if checked else None,
            ticket_factory=lambda pid, name: (
                labdef.deploy_ticket(checked.definition, image_map, project_id=pid, project_name=name)
                if checked else deploy_ticket(lab, project_id=pid, project_name=name)),
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

    @app.get("/api/v1/sessions/{session_id}/state")
    async def session_state(session_id: str, since_rev: int | None = Query(default=None, ge=0),
                            site=Depends(site_of)):
        """Facts of the lab (CONTRACTS 2.7): the last stored state, also for a finished session."""
        s = await own_session(session_id, site)
        if since_rev is not None and since_rev == s["facts_rev"]:
            return Response(status_code=304)
        statuses = {}
        if s["status"] == "running" and s["topology"]:
            nodes, _stale = await services.orchestrator.live_nodes(s)
            statuses = {node_id: n["status"] for node_id, n in nodes.items()}
        return build_state(s["facts_rev"], await store.facts_rows(s["id"]), statuses, settings.facts_ttl)

    @app.post("/api/v1/sessions/{session_id}/state/refresh", status_code=202)
    async def refresh_state(session_id: str, site=Depends(site_of)):
        s = await own_session(session_id, site)
        if s["status"] != "running":
            raise ApiError(409, "session_not_running", "Session is not running")
        await store.request_facts_refresh(s["id"])
        services.orchestrator.wake()
        return Response(status_code=202)

    @app.post("/api/v1/sessions/{session_id}/console-tickets")
    async def console_ticket(session_id: str, body: TicketRequest, site=Depends(site_of)):
        s = await own_session(session_id, site)
        if s["status"] != "running" or not s["topology"]:
            raise ApiError(409, "session_not_running", "Session is not running")
        node_ids = {n["node_id"] for n in s["topology"]["topology"]["nodes"]}
        if body.node_id not in node_ids:
            raise ApiError(404, "node_not_found", "Node does not belong to this session")
        try:
            no_console = await services.orchestrator.consoleless(s)
        except (LabError, KeyError) as exc:  # Fail closed: not knowing is not a reason to hand out a console
            raise ApiError(503, "console_unavailable", "Cannot tell whether this node has a console") from exc
        if body.node_id in no_console:  # For everybody, a read-only observer too
            raise ApiError(403, "console_disabled", "The image of this node has no console for users")
        nodes, stale = await services.orchestrator.live_nodes(s)
        node = nodes.get(body.node_id, {})
        console = node.get("console_type")
        if console == "vnc" and body.readonly:  # A permission, so it goes before the state check below
            raise ApiError(403, "console_disabled", "The screen of this node is visible to the session owner only")
        if not stale and node.get("status") != "started":
            # Saves the plugin a closed WebSocket and a session re-read (П5).
            raise ApiError(409, "node_not_running", "Node is not started")
        slot = settings.slots.get(s["slot_vmid"])
        if slot is None:
            raise ApiError(503, "slot_unavailable", "Slot is not configured")
        who = f"{s['id']}:{'observer' if body.readonly else 'owner'}"
        try:
            ticket = await services.gateway.issue(slot=slot.address, project_id=str(s["project_id"]),
                                                  node_id=body.node_id, readonly=body.readonly, who=who,
                                                  console="vnc" if console == "vnc" else "telnet")
        except SlotError as exc:
            raise ApiError(502, "gateway_unavailable", "Console gateway did not issue a ticket") from exc
        await store.event(s["id"], s["slot_vmid"], "console_ticket",
                          {"node_id": body.node_id, "readonly": body.readonly,
                           "issued_for_user_id": body.issued_for_user_id})
        return ticket

    if not hasattr(services, "derived"):
        services.derived = DerivedImages(services)
    register_routes(app, services, site_of, own_session, ApiError)
    return app
