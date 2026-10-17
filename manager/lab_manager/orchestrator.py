"""Session state machine: slot allocation, CT preparation, mount, start, deploy, stop, release.

Every stage is re-entrant: it first reads the real state (Proxmox, filesystem, slot) and
only then acts, so after a manager restart the loop simply continues from the stage
stored in the database. Nothing is deleted or recreated "just in case".

    plugin status  stage            what happens
    queued         wait_slot        waiting for a free slot (or the student's pinned slot)
    starting       ct_prepare       CT must be stopped before the mount changes
    starting       mount            student work/ -> slot bind source (or mpN via Proxmox)
    starting       ct_start         Proxmox start, UPID awaited
    starting       slot_ready       /healthz and /v1/readyz of Lab Slot
    starting       lab_submit       deploy (first time) or resume, with Idempotency-Key
    starting       lab_wait         operation polling: DEPLOYING/STARTING/PROVISIONING
    running        ready            slot READY; maxduration and slot health watched
    stopping       lab_stop         slot stop with Idempotency-Key
    stopping       lab_stop_wait    operation polling: STOPPING -> STOPPED
    stopping       ct_shutdown      Proxmox shutdown (hard stop only as last resort)
    stopping       unmount          student work/ back home; slot placeholder restored
    stopped/failed                  terminal; failed keeps the slot 'failed' for the operator
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import psycopg

from .labs import LabError, deploy_ticket
from .proxmox import PveApiError, PveError, PveTaskFailed, PveTaskUnknown, PveUncertain
from .slot_client import SlotError, SlotUnavailable
from .storage import StorageError
from .store import Fenced

log = logging.getLogger("lab_manager.orchestrator")

NODE_KEYS = ("node_id", "name", "node_type", "x", "y", "z", "width", "height", "symbol",
             "label", "console_type", "status", "ports", "locked")
LINK_KEYS = ("link_id", "nodes", "suspend", "filters", "link_type", "link_style")
CONSOLE_TYPES = {"telnet", "vnc"}
NODE_STATUSES = {"started", "stopped", "suspended"}

SLOT_STAGE_MESSAGES = {
    "DEPLOYING": "Создание или открытие проекта…",
    "STARTING": "Запуск устройств…",
    "PROVISIONING": "Проверка устройств…",
    "READY": "Лаба запущена",
    "STOPPING": "Остановка устройств…",
    "STOPPED": "Лаба остановлена",
}


class Failure(Exception):
    """Unrecoverable for this session: it goes to failed, the slot to failed."""

    def __init__(self, message, code="FAILED", slot_state="failed"):
        super().__init__(message)
        self.message = message
        self.code = code
        self.slot_state = slot_state


class Wait(Exception):
    """Nothing to do right now; try again on the next tick (with an optional message)."""

    def __init__(self, message=None):
        super().__init__(message or "")
        self.message = message


def now():
    return datetime.now(timezone.utc)


def session_topology(name: str, raw: dict, drawings=None) -> dict:
    nodes = []
    for n in raw.get("nodes", []):
        node = {k: n[k] for k in NODE_KEYS if k in n}
        node["properties"] = {}  # QEMU options, paths and sockets are internal to the slot
        nodes.append(node)
    links = [{k: link[k] for k in LINK_KEYS if k in link} for link in raw.get("links", [])]
    # Lab Slot does not return drawings; they reference no node_id, so the reference ones fit.
    return {"name": name, "topology": {"nodes": nodes, "links": links, "drawings": list(drawings or [])}}


def node_states(topology: dict) -> dict:
    result = {}
    for n in (topology or {}).get("topology", {}).get("nodes", []):
        status = n.get("status")
        console = n.get("console_type")
        result[n["node_id"]] = {
            "status": status if status in NODE_STATUSES else "stopped",
            "console_type": console if console in CONSOLE_TYPES else "none",
        }
    return result


class Orchestrator:
    def __init__(self, settings, store, pve, slot_clients, catalog, storage, *, sleep=asyncio.sleep):
        self.settings = settings
        self.store = store
        self.pve = pve
        self.slot_clients = slot_clients  # vmid -> SlotClient
        self.catalog = catalog
        self.storage = storage
        self.sleep = sleep
        self._wake = asyncio.Event()
        self._tasks: dict[str, asyncio.Task] = {}
        self._health_checked: dict[str, float] = {}
        self._live_cache: dict[str, tuple[float, dict]] = {}
        self.leader = False
        self.last_tick = None

    # ------------------------------------------------------------------ loop
    def wake(self):
        self._wake.set()

    async def run_forever(self, leader_conn_factory):
        """Only one manager process drives sessions (PostgreSQL advisory lock)."""
        from .db import LOCK_KEY

        while True:
            try:
                async with await leader_conn_factory() as conn:
                    while not self.leader:
                        cur = await conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK_KEY,))
                        self.leader = (await cur.fetchone())["ok"]
                        if not self.leader:
                            await self.sleep(5)
                    log.info("Orchestrator leader lock acquired")
                    while True:
                        await self.tick()
                        await conn.execute("SELECT 1")  # Lock lives as long as this connection
                        try:
                            await asyncio.wait_for(self._wake.wait(), self.settings.worker_interval)
                        except asyncio.TimeoutError:
                            pass
                        self._wake.clear()
            except asyncio.CancelledError:
                raise
            except (psycopg.Error, OSError) as exc:
                log.error("Database unavailable for the orchestrator: %s", type(exc).__name__)
            finally:
                self.leader = False
            await self.sleep(5)

    async def tick(self):
        self.last_tick = time.monotonic()
        for session in await self.store.live_sessions():
            task = self._tasks.get(session["id"])
            if task is None or task.done():
                self._tasks[session["id"]] = asyncio.create_task(self.drive(session["id"]))
        self._tasks = {k: t for k, t in self._tasks.items() if not t.done()}

    async def drain(self):
        """Tests: wait for the per-session tasks started by the last tick."""
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def drive(self, sid, max_steps=50):
        for _ in range(max_steps):
            session = await self.store.session(sid)
            if session is None or session["status"] in ("stopped", "failed"):
                return
            try:
                progressed = await self.step(session)
            except Fenced as exc:
                log.warning("Session %s: stale worker stopped (%s)", sid, exc)
                return
            if not progressed:
                return

    # ------------------------------------------------------------------ one step
    async def step(self, s) -> bool:
        handler = getattr(self, "_stage_" + s["stage"], None)
        if handler is None:
            await self._fail(s, Failure(f"Unknown stage {s['stage']}", "INTERNAL"))
            return False
        try:
            return await handler(s)
        except Fenced:
            raise
        except Failure as exc:
            await self._fail(s, exc)
            return False
        except Wait as exc:
            if await self._check_stage_timeout(s):
                return False
            if exc.message and exc.message != s["status_message"] and s["status"] != "queued":
                await self.store.update_session(s, status_message=exc.message)
            return False
        except PveApiError as exc:
            if 400 <= exc.status < 500:
                await self._fail(s, Failure(f"Proxmox отклонил запрос: {exc}", "PVE_REJECTED"))
                return False
            log.warning("Session %s stage %s: %s", s["id"], s["stage"], exc)
            await self._check_stage_timeout(s)
            return False
        except (PveError, SlotUnavailable) as exc:
            log.warning("Session %s stage %s: %s", s["id"], s["stage"], exc)
            await self._check_stage_timeout(s)
            return False
        except (StorageError, LabError) as exc:
            await self._fail(s, Failure(str(exc), type(exc).__name__))
            return False
        except OSError as exc:
            await self._fail(s, Failure(f"Ошибка файловой системы: {type(exc).__name__}", "FILESYSTEM"))
            return False
        except Exception as exc:  # noqa: BLE001 - a bug must not kill the loop; stage timeout bounds it
            log.exception("Session %s stage %s: unexpected %s", s["id"], s["stage"], type(exc).__name__)
            await self._check_stage_timeout(s)
            return False

    def _stage_limit(self, stage):
        st = self.settings
        return {
            "wait_slot": None,
            "ready": None,
            "ct_prepare": st.pve_task_timeout * 3,
            "ct_start": st.pve_task_timeout * 3,
            "ct_shutdown": st.pve_task_timeout * 3 + st.ct_shutdown_timeout,
            "slot_ready": st.slot_ready_timeout,
            "lab_wait": st.lab_op_timeout,
            "lab_stop_wait": st.lab_op_timeout,
        }.get(stage, 600)

    async def _check_stage_timeout(self, s) -> bool:
        limit = self._stage_limit(s["stage"])
        if limit is not None and (now() - s["stage_started_at"]).total_seconds() > limit:
            await self._fail(s, Failure(f"Этап {s['stage']} не завершился за {int(limit)} с", "TIMEOUT"))
            return True
        return False

    async def _fail(self, s, failure: Failure):
        log.error("Session %s failed at %s: %s", s["id"], s["stage"], failure.code)
        await self.store.finish_session(
            s, status="failed", message=failure.message[:500],
            error={"code": failure.code, "stage": s["stage"], "message": failure.message[:500]},
            slot_state=failure.slot_state if s["slot_vmid"] is not None else "free",
            slot_message=f"session {s['id']}: {failure.code}",
        )

    async def _goto(self, s, stage, message, *, status=None, slot_state=None, **fields):
        await self.store.update_session(
            s, stage=stage, status_message=message, new_stage=True,
            **({"status": status} if status else {}), slot_state=slot_state,
            event=("stage", {"stage": stage}), **fields,
        )
        return True

    # ------------------------------------------------------------------ helpers
    def _slot(self, s):
        client = self.slot_clients.get(s["slot_vmid"])
        if client is None:
            raise Failure("Слот отсутствует в конфигурации менеджера", "SLOT_NOT_CONFIGURED")
        return client

    @staticmethod
    def _site_slug(s):
        return s["site"]  # Sessions store the site slug, never the URL

    async def _ct_ensure(self, s, want: str):
        """Bring the CT to 'running' or 'stopped', surviving restarts and lost task nodes."""
        vmid = s["slot_vmid"]
        if s["pve_upid"]:
            try:
                await self.pve.wait_task(s["pve_upid"], timeout=self.settings.pve_task_timeout)
            except PveTaskFailed as exc:
                status = (await self.pve.ct_status(vmid)).get("status")
                await self.store.update_session(s, pve_upid=None, event=("pve_task_failed", {"exit": exc.exitstatus}))
                if status == want:
                    return
                if want == "stopped":
                    return await self._ct_last_resort_stop(s)
                raise Failure(f"Proxmox: задача завершилась ошибкой ({exc.exitstatus})", "PVE_TASK_FAILED")
            except PveTaskUnknown:
                # The task's node may be down: trust only the real CT state.
                status = (await self.pve.ct_status(vmid)).get("status")
                if status != want:
                    raise Wait()
            s = await self.store.update_session(s, pve_upid=None)
        status = (await self.pve.ct_status(vmid)).get("status")
        if status == want:
            return
        try:
            if want == "running":
                upid = await self.pve.ct_start(vmid)
            else:
                upid = await self.pve.ct_shutdown(vmid, self.settings.ct_shutdown_timeout)
        except PveUncertain:
            raise Wait()  # Next tick re-reads the CT status before acting again
        s = await self.store.update_session(s, pve_upid=upid, event=("pve_task", {"want": want}))
        await self._ct_ensure(s, want)

    async def _ct_last_resort_stop(self, s):
        await self.store.event(s["id"], s["slot_vmid"], "ct_hard_stop", {})
        log.warning("Session %s: CT %s did not shut down; hard stop", s["id"], s["slot_vmid"])
        upid = await self.pve.ct_stop(s["slot_vmid"])
        s = await self.store.update_session(s, pve_upid=upid)
        await self.pve.wait_task(upid, timeout=self.settings.pve_task_timeout)
        await self.store.update_session(s, pve_upid=None)

    def _reference_drawings(self, lab_ref):
        try:
            return self.catalog.get(lab_ref).topology.get("topology", {}).get("drawings", [])
        except LabError:
            return []

    async def _workspace(self, s):
        return await self.store.workspace(s["workspace_id"])

    # ------------------------------------------------------------------ queued
    async def _stage_wait_slot(self, s):
        if s["stop_requested"]:
            await self.store.finish_session(s, status="stopped", message="Остановлено до запуска")
            return False
        lab = self.catalog.get(s["lab_ref"])
        vmid = await self.store.allocate_slot(s, profile=lab.profile)
        if vmid is None:
            ws = await self._workspace(s)
            if ws["slot_vmid"] is not None:
                slot = await self.store.slot(ws["slot_vmid"])
                message = ("Ваш слот на обслуживании, ожидание оператора…"
                           if slot["state"] in ("failed", "maintenance") or not slot["in_config"]
                           else "Ожидание освобождения вашего слота…")
            else:
                message = "Ожидание свободного слота…"
            await self.store.queued_message(s["id"], message)
            return False
        return True

    # ------------------------------------------------------------------ starting
    async def _stage_ct_prepare(self, s):
        if s["stop_requested"]:
            return await self._goto(s, "ct_shutdown", "Остановка…", status="stopping", slot_state="releasing")
        if self.storage.mode != "none":
            await self._ct_ensure(s, "stopped")  # Mount points change only on a stopped CT
        return await self._goto(s, "mount", "Подключение каталога студента…")

    async def _stage_mount(self, s):
        if s["stop_requested"]:
            return await self._goto(s, "ct_shutdown", "Остановка…", status="stopping", slot_state="releasing")
        vmid, slug, uid = s["slot_vmid"], self._site_slug(s), s["user_id"]
        if self.storage.mode != "none":
            if (await self.pve.ct_status(vmid)).get("status") != "stopped":
                return await self._goto(s, "ct_prepare", "Подготовка слота…")
            self.storage.ensure_student(slug, uid)
        if self.storage.mode == "handover":
            self.storage.attach(vmid, slug, uid)
        elif self.storage.mode == "pve_config":
            wanted = self.storage.pve_mount(slug, uid)
            key = self.storage.mp_key
            if (await self.pve.ct_config(vmid)).get(key) != wanted[key]:
                try:
                    await self.pve.ct_set_config(vmid, wanted)
                except PveUncertain:
                    raise Wait() from None
                if (await self.pve.ct_config(vmid)).get(key) != wanted[key]:
                    raise Failure("Proxmox не применил точку монтирования", "MOUNT_NOT_APPLIED")
        await self.store.event(s["id"], vmid, "student_mounted", {"mode": self.storage.mode})
        return await self._goto(s, "ct_start", "Запуск контейнера слота…")

    async def _stage_ct_start(self, s):
        if s["stop_requested"]:
            return await self._goto(s, "ct_shutdown", "Остановка…", status="stopping", slot_state="releasing")
        await self._ct_ensure(s, "running")
        return await self._goto(s, "slot_ready", "Ожидание готовности слота…")

    async def _stage_slot_ready(self, s):
        if s["stop_requested"]:
            return await self._goto(s, "ct_shutdown", "Остановка…", status="stopping", slot_state="releasing")
        client = self._slot(s)
        if not await client.healthz():
            raise Wait("Ожидание запуска службы слота…")
        ready, body = await client.readyz()
        if not ready:
            failed = sorted(k for k, v in (body.get("checks") or {}).items() if not v.get("ok"))
            raise Wait("Слот не готов: " + (", ".join(failed) or "ожидание"))
        return await self._goto(s, "lab_submit", "Запуск лабы…", slot_state="ready")

    async def _submit(self, s, action, body_factory):
        """Mutating slot call with a stored Idempotency-Key. Returns the operation id."""
        client = self._slot(s)
        ws = await self._workspace(s)
        call = await self.store.current_call(s, (action,))
        if call is None or call["status"] == "failed":
            call = await self.store.new_call(s, action, body_factory() if body_factory else None)
        if call["status"] == "accepted" and call["operation_id"]:
            return str(call["operation_id"])
        pid = str(ws["project_id"])
        try:
            if action == "deploy":
                resp = await client.deploy(call["body"], call["idempotency_key"])
            elif action == "resume":
                resp = await client.resume(pid, call["idempotency_key"])
            else:
                resp = await client.stop(pid, call["idempotency_key"])
        except SlotUnavailable:
            raise Wait("Слот недоступен, повтор…") from None
        except SlotError as exc:
            await self._handle_submit_conflict(s, call, action, exc)
            raise Wait()
        await self.store.update_call(call["id"], status="accepted", operation_id=resp["operation_id"],
                                     result={"replayed": resp.get("replayed"), "status": resp.get("status")})
        return resp["operation_id"]

    async def _handle_submit_conflict(self, s, call, action, exc: SlotError):
        """409 handling per slot/README.md. Raises Failure, or returns to retry later."""
        lab_state = (exc.detail.get("laboratory") or {}).get("state")
        if exc.code == "STALE_IDEMPOTENCY_KEY":
            # An earlier success no longer holds. A FAILED lab needs the operator; otherwise
            # the intent is still valid and a new key starts a new intentional operation.
            if lab_state == "FAILED":
                raise Failure("Лаба в слоте в состоянии FAILED, нужен оператор", "SLOT_LAB_FAILED")
            await self.store.update_call(call["id"], status="abandoned", result={"stale": lab_state})
            return
        if exc.code == "OPERATION_IN_PROGRESS":
            return  # Slot busy with another operation: wait
        if exc.code == "INVALID_TRANSITION":
            status = await self._slot(s).lab_status(str((await self._workspace(s))["project_id"]))
            state = (status or {}).get("state")
            goal = "STOPPED" if action == "stop" else "READY"
            if state == goal:
                await self.store.update_call(call["id"], status="succeeded", result={"already": state})
                return
            if state == "FAILED":
                raise Failure("Лаба в слоте в состоянии FAILED, нужен оператор", "SLOT_LAB_FAILED")
            return  # Transitional state: wait for it
        if exc.status == 404 and action in ("resume", "stop"):
            raise Failure("Слот не знает проект студента; нужен оператор", "SLOT_PROJECT_UNKNOWN")
        raise Failure(f"Слот отклонил {action}: {exc.message or exc.code}", exc.code or f"HTTP_{exc.status}")

    async def _stage_lab_submit(self, s):
        ws = await self._workspace(s)
        call = await self.store.current_call(s, ("deploy", "resume"))
        if s["stop_requested"]:
            # If a deploy/resume may have reached the slot, stop the lab properly first.
            if call:
                return await self._goto(s, "lab_stop", "Остановка лабы…", status="stopping",
                                        slot_state="releasing")
            return await self._goto(s, "ct_shutdown", "Остановка…", status="stopping", slot_state="releasing")
        if call is None or call["status"] != "succeeded":
            action = call["action"] if call else ("resume" if ws["deployed"] else "deploy")
            lab = self.catalog.get(s["lab_ref"]) if action == "deploy" else None

            def body():
                return deploy_ticket(lab, project_id=str(ws["project_id"]), project_name=ws["project_name"])

            await self._submit(s, action, body if action == "deploy" else None)
        return await self._goto(s, "lab_wait", "Запуск лабы…")

    async def _stage_lab_wait(self, s):
        ws = await self._workspace(s)
        call = await self.store.current_call(s, ("deploy", "resume"))
        if call is None:
            return await self._goto(s, "lab_submit", "Запуск лабы…")
        if call["status"] != "succeeded":
            if not call["operation_id"]:
                return await self._goto(s, "lab_submit", "Запуск лабы…")
            op = await self._operation(s, call)
            if op["status"] == "RUNNING":
                lab = op.get("laboratory") or {}
                msg = SLOT_STAGE_MESSAGES.get(lab.get("state"), "Запуск лабы…")
                if op.get("progress"):
                    msg = f"{msg} {op['progress']}%"
                raise Wait(msg)
            if op["status"] != "SUCCEEDED":
                await self.store.update_call(call["id"], status="failed", result={"error": op.get("error")})
                err = op.get("error") or {}
                raise Failure(f"Слот: {err.get('code', op['status'])}: {err.get('message', '')}"[:500],
                              "SLOT_OPERATION_FAILED")
            await self.store.update_call(call["id"], status="succeeded")
        if not ws["deployed"]:
            await self.store.mark_deployed(ws["id"])
        raw = await self._slot(s).topology(str(ws["project_id"]))
        topology = session_topology(ws["project_name"], raw, self._reference_drawings(s["lab_ref"]))
        if s["stop_requested"]:
            return await self._goto(s, "lab_stop", "Остановка лабы…", status="stopping",
                                    slot_state="releasing", topology=topology)
        started = now()
        return await self._goto(
            s, "ready", "Лаба запущена", status="running", slot_state="busy", topology=topology,
            started_at=started, expires_at=started + timedelta(minutes=s["max_duration"]),
        )

    async def _operation(self, s, call):
        try:
            return await self._slot(s).operation(str(call["operation_id"]))
        except SlotUnavailable:
            raise Wait("Слот недоступен, повтор…") from None
        except SlotError as exc:
            if exc.status == 404:
                raise Failure("Слот не знает операцию (состояние слота потеряно?)", "SLOT_OPERATION_UNKNOWN") from exc
            raise

    # ------------------------------------------------------------------ running
    async def _stage_ready(self, s):
        if s["stop_requested"] or (s["expires_at"] and now() >= s["expires_at"]):
            reason = "maxduration" if not s["stop_requested"] else (s["stop_reason"] or "user")
            msg = "Время лабы истекло, остановка…" if reason == "maxduration" else "Остановка лабы…"
            return await self._goto(s, "lab_stop", msg, status="stopping", slot_state="releasing",
                                    stop_requested=True, stop_reason=reason)
        last = self._health_checked.get(s["id"], 0)
        if time.monotonic() - last < 30:
            return False
        self._health_checked[s["id"]] = time.monotonic()
        ws = await self._workspace(s)
        try:
            status = await self._slot(s).lab_status(str(ws["project_id"]))
        except SlotUnavailable:
            return False  # Transient; the plugin still sees the last topology
        if status is None or status.get("state") == "FAILED":
            err = (status or {}).get("error") or {}
            raise Failure(f"Лаба в слоте перешла в FAILED: {err.get('code', 'UNKNOWN')}", "SLOT_LAB_FAILED")
        return False

    # ------------------------------------------------------------------ stopping
    async def _stage_lab_stop(self, s):
        ws = await self._workspace(s)
        call = await self.store.current_call(s, ("stop",))
        if call is None or call["status"] not in ("accepted", "succeeded"):
            try:
                status = await self._slot(s).lab_status(str(ws["project_id"]))
            except SlotUnavailable:
                raise Wait("Слот недоступен, повтор остановки…") from None
            if status is None or status.get("state") == "STOPPED":
                return await self._goto(s, "ct_shutdown", "Остановка контейнера слота…")
        if call is None or call["status"] != "succeeded":
            await self._submit(s, "stop", None)
        return await self._goto(s, "lab_stop_wait", "Остановка устройств…")

    async def _stage_lab_stop_wait(self, s):
        call = await self.store.current_call(s, ("stop",))
        if call is None or (call["status"] != "succeeded" and not call["operation_id"]):
            return await self._goto(s, "lab_stop", "Остановка лабы…")
        if call["status"] != "succeeded":
            op = await self._operation(s, call)
            if op["status"] == "RUNNING":
                raise Wait("Остановка устройств…")
            if op["status"] != "SUCCEEDED":
                await self.store.update_call(call["id"], status="failed", result={"error": op.get("error")})
                err = op.get("error") or {}
                raise Failure(f"Слот не смог остановить лабу: {err.get('code', op['status'])}",
                              "SLOT_STOP_FAILED")
            await self.store.update_call(call["id"], status="succeeded")
        return await self._goto(s, "ct_shutdown", "Остановка контейнера слота…")

    async def _stage_ct_shutdown(self, s):
        await self._ct_ensure(s, "stopped")
        return await self._goto(s, "unmount", "Отключение каталога студента…")

    async def _stage_unmount(self, s):
        vmid = s["slot_vmid"]
        if self.storage.mode != "none":
            if (await self.pve.ct_status(vmid)).get("status") != "stopped":
                return await self._goto(s, "ct_shutdown", "Остановка контейнера слота…")
        if self.storage.mode == "handover":
            self.storage.detach(vmid, self._site_slug(s), s["user_id"])
            self.storage.check_slot_placeholder(vmid)
        elif self.storage.mode == "pve_config":
            key = self.storage.mp_key
            if key in await self.pve.ct_config(vmid):
                try:
                    await self.pve.ct_set_config(vmid, {}, delete=[key])
                except PveUncertain:
                    raise Wait() from None
                if key in await self.pve.ct_config(vmid):
                    raise Failure("Proxmox не снял точку монтирования", "UNMOUNT_NOT_APPLIED")
        reason = s["stop_reason"] or "user"
        message = "Время лабы истекло" if reason == "maxduration" else "Лаба остановлена"
        await self.store.finish_session(s, status="stopped", message=message)
        self._health_checked.pop(s["id"], None)
        return False

    # ------------------------------------------------------------------ reads for the API
    async def live_nodes(self, s) -> dict:
        """Node statuses of a running session (cached a few seconds; snapshot as fallback)."""
        cached = self._live_cache.get(s["id"])
        if cached and time.monotonic() - cached[0] < 3:
            return cached[1]
        nodes = node_states(s["topology"])
        try:
            ws = await self._workspace(s)
            raw = await self._slot(s).topology(str(ws["project_id"]))
            live = node_states(session_topology(ws["project_name"], raw))
            nodes = {k: live.get(k, v) for k, v in nodes.items()}
        except (SlotError, Failure):
            pass
        self._live_cache[s["id"]] = (time.monotonic(), nodes)
        return nodes
