"""Session state machine: placement, CT preparation, start via the node agent, deploy, stop.

Slots are interchangeable executors. A session gets any free slot whose Proxmox node has
room for the lab (best fit, so slots pack densely onto nodes); the next session of the same
student may run on another slot and another node.

Every stage is re-entrant: it first reads the real state (node agent, Proxmox config, Lab
Slot) and only then acts, so after a manager restart the loop continues from the stage
stored in the database. Nothing is deleted or recreated "just in case".

    plugin status  stage            what happens
    queued         wait_slot        placement over free slots and node memory, allocation
    starting       ct_prepare       CT stopped (agent), sized to the lab (Proxmox config)
    starting       student_dir      agent: PUT /students/<key> (idempotent, mapped gns3 owner)
    starting       ct_start         agent: mounts (code mp0, student mp1) + pct start + guest actions;
                                    both mounts then verified exactly from the Proxmox config
    starting       slot_ready       /healthz and /v1/readyz of Lab Slot
    starting       lab_submit       deploy with Idempotency-Key (same project_id when resuming)
    starting       lab_wait         operation polling: DEPLOYING/STARTING/PROVISIONING
    running        ready            slot READY; maxduration and slot health watched
    stopping       lab_stop         slot stop with Idempotency-Key
    stopping       lab_stop_wait    operation polling: STOPPING -> STOPPED
    stopping       ct_stop          agent: pct shutdown (pct stop only as last resort)
    stopping       ct_park          agent: placeholder mounted read-only instead of the student dir;
                                    verified, otherwise the slot goes to maintenance (never free)
    stopped/failed                  terminal; failed keeps the slot 'failed' for the operator

A failure while the CT may hold the student's directory (ct_start .. lab_stop_wait) does not
end the session at once: the lab is stopped if it may exist, the CT is stopped and parked,
and only then the session is failed (the slot stays 'failed' for the operator, but with the
placeholder mounted). Until the CT is confirmed stopped, slots.student_key names the student,
and a new session of that student fails with STUDENT_DIR_BUSY instead of mounting the same
directory read-write in a second slot.
"""

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import psycopg

from .agent_client import (AgentBusy, AgentCommandFailed, AgentError, AgentNotStopped, AgentRejected,
                           AgentSourceMissing, AgentUncertain, AgentWrongNode)
from . import labdef
from .labs import LabError, deploy_ticket
from .proxmox import PveApiError, PveError
from .slot_client import SlotError, SlotUnavailable
from .store import Fenced

log = logging.getLogger("lab_manager.orchestrator")
MIB = 1024 * 1024

NODE_KEYS = ("node_id", "name", "node_type", "x", "y", "z", "width", "height", "symbol",
             "label", "console_type", "status", "ports", "locked")
LINK_KEYS = ("link_id", "nodes", "suspend", "filters", "link_type", "link_style")
CONSOLE_TYPES = {"telnet", "vnc"}
NODE_STATUSES = {"started", "stopped", "suspended"}

# The CT may be running with the student's directory mounted read-write.
MOUNTED_STAGES = ("ct_start", "slot_ready", "lab_submit", "lab_wait", "ready", "lab_stop", "lab_stop_wait")
# The lab may exist in the slot: stop it before the CT.
LAB_STAGES = ("lab_submit", "lab_wait", "ready")

SLOT_STAGE_MESSAGES = {
    "DEPLOYING": "Создание или открытие проекта…",
    "STARTING": "Запуск устройств…",
    "PROVISIONING": "Проверка устройств…",
    "READY": "Лаба запущена",
    "STOPPING": "Остановка устройств…",
    "STOPPED": "Лаба остановлена",
}


class Failure(Exception):
    """Unrecoverable for this session: it goes to failed, the slot to failed (after the CT is
    stopped and parked when the failure happens in a MOUNTED_STAGES stage)."""

    def __init__(self, message, code="FAILED", slot_state="failed"):
        super().__init__(message)
        self.message = message
        self.code = code
        self.slot_state = slot_state


class Wait(Exception):
    """Nothing to do right now; try again on the next tick (optional message and progress)."""

    def __init__(self, message=None, progress=None):
        super().__init__(message or "")
        self.message = message
        self.progress = progress


def now():
    return datetime.now(timezone.utc)


def session_topology(name: str, raw: dict, reference=None) -> dict:
    """Live topology for the plugin; reference is the lab's own topology (lab.gns3 or alike)."""
    ref = (reference or {}).get("topology") or {}
    # The slot creates nodes without a symbol and GNS3 2.2 draws them all as computers
    # (slot ALIGNMENT §3.4): the symbol of the reference node with the same name wins.
    symbols = {n.get("name"): n["symbol"] for n in ref.get("nodes", []) if n.get("symbol")}
    nodes = []
    for n in raw.get("nodes", []):
        node = {k: n[k] for k in NODE_KEYS if k in n}
        if node.get("name") in symbols:
            node["symbol"] = symbols[node["name"]]
        node["properties"] = {}  # QEMU options, paths and sockets are internal to the slot
        nodes.append(node)
    links = [{k: link[k] for k in LINK_KEYS if k in link} for link in raw.get("links", [])]
    # Lab Slot does not return drawings; they reference no node_id, so the reference ones fit.
    return {"name": name, "topology": {"nodes": nodes, "links": links,
                                       "drawings": list(ref.get("drawings") or [])}}


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
    def __init__(self, settings, store, pve, agent, slot_clients, catalog, *, sleep=asyncio.sleep):
        self.settings = settings
        self.store = store
        self.pve = pve
        self.agent = agent
        self.slot_clients = slot_clients  # vmid -> SlotClient
        self.catalog = catalog
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
            changes = {}
            if exc.message and exc.message != s["status_message"] and s["status"] != "queued":
                changes["status_message"] = exc.message
            if exc.progress is not None and exc.progress != s["progress"]:
                changes["progress"] = max(0, min(100, int(exc.progress)))
            if changes:
                await self.store.update_session(s, **changes)
            return False
        except PveApiError as exc:
            if 400 <= exc.status < 500:
                await self._fail(s, Failure(f"Proxmox отклонил запрос: {exc}", "PVE_REJECTED"))
                return False
            log.warning("Session %s stage %s: %s", s["id"], s["stage"], exc)
            await self._check_stage_timeout(s)
            return False
        except AgentRejected as exc:
            await self._fail(s, Failure(f"Агент узла отклонил запрос: {exc.message}", "AGENT_REJECTED"))
            return False
        except AgentWrongNode:
            self.pve.forget_ct_node(s["slot_vmid"])  # CT migrated: look the node up again
            return False
        except (PveError, SlotUnavailable, AgentError) as exc:
            log.warning("Session %s stage %s: %s", s["id"], s["stage"], exc)
            await self._check_stage_timeout(s)
            return False
        except LabError as exc:
            await self._fail(s, Failure(f"Лаба недоступна: {exc}", "LAB_UNAVAILABLE"))
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
            "ct_prepare": st.agent_stop_timeout * 3,
            "student_dir": 120,
            "ct_start": st.agent_start_timeout * 2,
            "ct_stop": st.agent_stop_timeout * 3,
            "slot_ready": st.slot_ready_timeout,
            "lab_wait": st.lab_op_timeout,
            "lab_stop_wait": st.lab_op_timeout,
        }.get(stage, 600)

    async def _check_stage_timeout(self, s) -> bool:
        limit = self._stage_limit(s["stage"])
        if limit is not None and (now() - s["stage_started_at"]).total_seconds() > limit:
            # Before ct_start the CT was not touched for this session: the slot stays usable.
            slot_state = "free" if s["stage"] == "student_dir" else "failed"
            await self._fail(s, Failure(f"Этап {s['stage']} не завершился за {int(limit)} с", "TIMEOUT",
                                        slot_state=slot_state))
            return True
        return False

    async def _fail(self, s, failure: Failure):
        log.error("Session %s failed at %s: %s", s["id"], s["stage"], failure.code)
        if s["slot_vmid"] is not None and s["stage"] in MOUNTED_STAGES:
            # Release the student's directory before giving up: stop the lab if it may exist,
            # then the CT, then park. The first error is kept and reported at the end.
            error = s["error"] or {"code": failure.code, "stage": s["stage"], "message": failure.message[:500],
                                   "slot_state": failure.slot_state}
            lab = s["stage"] in LAB_STAGES and await self.store.current_call(s, ("deploy",)) is not None
            stage, message = ("lab_stop", "Сбой, остановка лабы…") if lab else ("ct_stop", "Сбой, остановка слота…")
            await self.store.update_session(
                s, stage=stage, status="stopping", status_message=message, error=error, new_stage=True,
                progress=None, slot_state="releasing", event=("failure_cleanup", {"code": failure.code}),
            )
            return
        # A failure of the cleanup itself: the student still sees the first error.
        error = s["error"] or {"code": failure.code, "stage": s["stage"], "message": failure.message[:500]}
        codes = failure.code if error["code"] == failure.code else f"{error['code']}, then {failure.code}"
        await self.store.finish_session(
            s, status="failed", message=error["message"], error=error,
            slot_state=failure.slot_state if s["slot_vmid"] is not None else "free",
            slot_message=f"session {s['id']}: {codes}",
        )

    async def _goto(self, s, stage, message, *, status=None, slot_state=None, **fields):
        await self.store.update_session(
            s, stage=stage, status_message=message, new_stage=True, progress=None,
            **({"status": status} if status else {}), slot_state=slot_state,
            event=("stage", {"stage": stage}), **fields,
        )
        return True

    async def _goto_stop(self, s, *, lab_may_exist=False):
        """Stop requested while starting: stop the lab properly if it may have reached the slot."""
        if lab_may_exist:
            return await self._goto(s, "lab_stop", "Остановка лабы…", status="stopping", slot_state="releasing")
        return await self._goto(s, "ct_stop", "Остановка…", status="stopping", slot_state="releasing")

    # ------------------------------------------------------------------ helpers
    def _slot(self, s):
        client = self.slot_clients.get(s["slot_vmid"])
        if client is None:
            raise Failure("Слот отсутствует в конфигурации менеджера", "SLOT_NOT_CONFIGURED")
        return client

    async def _node(self, s) -> str:
        """Current Proxmox node of the slot CT (it may have migrated since allocation)."""
        return await self.pve.find_ct_node(s["slot_vmid"])

    async def _reference(self, s):
        """Reference topology of the session's lab: lab.gns3, or symbols from the image catalog."""
        if s["lab_def"]:
            ws = await self.store.workspace(s["workspace_id"])
            return labdef.reference_topology(ws["definition"], self.settings.images or {})
        try:
            return self.catalog.get(s["lab_ref"]).topology
        except LabError:
            return None

    @staticmethod
    def _need_mb(resources):
        return int((resources or {}).get("memory_mb") or 0)

    # ------------------------------------------------------------------ placement
    async def placement(self, s) -> list[tuple[int, str]]:
        """Free slots that fit the lab, best fit first (least memory left on the node).

        Node usage comes from /cluster/resources. For every slot already handed out, the part of
        its promised memory that the CT does not use yet is subtracted too, otherwise two labs
        could be placed on the same room while the first one is still starting.
        """
        free = await self.store.free_slots()
        if not free:
            return []
        resources = await self.pve.cluster_resources()
        nodes = {r["node"]: r for r in resources if r.get("type") == "node" and r.get("status") == "online"}
        cts = {r["vmid"]: r for r in resources if r.get("type") == "lxc"}
        reserved = defaultdict(int)
        for r in await self.store.reservations():
            ct = cts.get(r["slot_vmid"])
            if ct:
                # Promised memory not yet in use: a starting GNS3 grows to its limit later.
                promised = self._need_mb(r["resources"]) * MIB or int(ct.get("maxmem") or 0)
                reserved[ct["node"]] += max(0, promised - int(ct.get("mem") or 0))
        need = self._need_mb(s["resources"]) * MIB
        reserve = self.settings.node_memory_reserve_mb * MIB
        fitting = []
        for vmid in free:
            ct = cts.get(vmid)
            node = nodes.get(ct["node"]) if ct else None
            if node is None:
                continue  # CT unknown to the cluster or its node is offline
            want = need or int(ct.get("maxmem") or 0)
            room = int(node.get("maxmem", 0)) - int(node.get("mem", 0)) - reserved[ct["node"]] - reserve
            if room >= want:
                fitting.append((room - want, vmid, ct["node"]))
        fitting.sort()
        return [(vmid, node) for _, vmid, node in fitting]

    # ------------------------------------------------------------------ queued
    async def _stage_wait_slot(self, s):
        if s["stop_requested"]:
            await self.store.finish_session(s, status="stopped", message="Остановлено до запуска")
            return False
        if s["lab_ref"]:
            self.catalog.get(s["lab_ref"])  # Lab removed from the catalog -> failed
        else:
            ws = await self.store.workspace(s["workspace_id"])
            missing = sorted({n["image"] for n in ws["definition"]["nodes"]} - set(self.settings.images or {}))
            if missing:  # Removed by the administrator since the definition was checked
                raise LabError(f"Образы убраны из каталога: {', '.join(missing)}")
        held = await self.store.slot_holding_student(s["student_key"])
        if held is not None:
            # An earlier session failed and its CT could not be confirmed stopped: the student's
            # directory may still be writable there. Never mount it in a second slot.
            raise Failure(f"Каталог студента ещё подключён к слоту {held}; нужен оператор", "STUDENT_DIR_BUSY")
        if not await self.store.usable_slots():
            raise Failure("Нет ни одного рабочего слота", "NO_SLOTS")
        waiting = await self._try_allocate(s)
        if waiting is None:
            return True
        limit = self.settings.queue_timeout
        if limit and (now() - s["stage_started_at"]).total_seconds() > limit:
            raise Failure(f"Слот не освободился за {int(limit)} с", "QUEUE_TIMEOUT")
        await self.store.queued_message(s["id"], waiting)
        return False

    async def _try_allocate(self, s):
        """Allocate a slot. Returns None on success, otherwise the message to show while waiting."""
        if not await self.store.free_slots():
            return "Ожидание свободного слота…"
        try:
            candidates = await self.placement(s)
        except PveError:
            return "Proxmox недоступен, ожидание…"
        if await self.store.allocate_slot(s, candidates) is None:
            return "Недостаточно ресурсов на узлах, ожидание…" if not candidates else "Ожидание свободного слота…"
        return None

    # ------------------------------------------------------------------ starting
    async def _stage_ct_prepare(self, s):
        if s["stop_requested"]:
            return await self._goto_stop(s)
        vmid, node = s["slot_vmid"], await self._node(s)
        try:
            state = await self.agent.status(node, vmid)
        except AgentBusy:
            raise Wait() from None
        if state == "running":
            # A free slot should be stopped; mounts change only on a stopped CT.
            await self.agent.stop(node, vmid, force=False)
            raise Wait("Остановка слота перед запуском…")
        # A definition was sized when it was checked; a catalog lab follows its current meta.json.
        resources = self.catalog.get(s["lab_ref"]).resources if s["lab_ref"] else s["resources"]
        if resources:
            await self._size_ct(s, resources)
        return await self._goto(s, "student_dir", "Подготовка каталога студента…")

    async def _stage_student_dir(self, s):
        if s["stop_requested"]:
            return await self._goto_stop(s)
        try:
            body = await self.agent.ensure_student(await self._node(s), s["student_key"])
        except AgentCommandFailed:
            # 409: another owner, or a concurrent creation not finished yet. Retried until the
            # stage timeout; a wrong owner then fails the session (the agent never repairs it).
            raise Wait("Ожидание каталога студента…") from None
        except AgentRejected as exc:
            raise Failure(f"Агент не создал каталог студента: {exc.message}", "STUDENT_DIR_REJECTED",
                          slot_state="free") from exc
        await self.store.event(s["id"], s["slot_vmid"], "student_dir_ready", {"ready": body["ready"]})
        return await self._goto(s, "ct_start", "Запуск контейнера слота…")

    async def _size_ct(self, s, resources):
        vmid = s["slot_vmid"]
        wanted = {}
        if "cores" in resources:
            wanted["cores"] = str(resources["cores"])
        if "memory_mb" in resources:
            wanted["memory"] = str(resources["memory_mb"])
        config = await self.pve.ct_config(vmid)
        if all(str(config.get(k)) == v for k, v in wanted.items()):
            return
        upid = await self.pve.ct_set_config(vmid, wanted)
        if upid:
            await self.pve.wait_task(upid, timeout=60)
        config = await self.pve.ct_config(vmid)
        if not all(str(config.get(k)) == v for k, v in wanted.items()):
            raise Failure("Proxmox не применил ресурсы слота", "CT_SIZE_NOT_APPLIED")
        await self.store.event(s["id"], vmid, "ct_sized", wanted)

    @staticmethod
    def _mount(config, key):
        source, _, options = str(config.get(key, "")).partition(",")
        opts = dict(o.split("=", 1) for o in options.split(",") if "=" in o)
        return source.rstrip("/"), opts

    def mounts_ok(self, config, student_key, *, read_only):
        """Exact agent mount contract: code at /srv/gns3-ro/slot RO, student dir at
        /opt/gns3/projects. A matching suffix under another root is not accepted."""
        root = self.settings.agent_ceph_root
        rev = self.settings.slot_code_revision
        code_src, code = self._mount(config, "mp0")
        data_src, data = self._mount(config, "mp1")
        ro = "1" if read_only else "0"
        return (code_src == (f"{root}/slot/{rev}" if rev else f"{root}/slot")
                and code.get("mp") == "/srv/gns3-ro/slot" and code.get("ro") == "1"
                and data_src == f"{root}/students/{student_key}"
                and data.get("mp") == "/opt/gns3/projects" and data.get("ro", "0") == ro)

    async def _stage_ct_start(self, s):
        if s["stop_requested"]:
            return await self._goto_stop(s)
        vmid, node, key = s["slot_vmid"], await self._node(s), s["student_key"]
        try:
            state = await self.agent.status(node, vmid)
        except AgentBusy:
            raise Wait() from None
        if state == "stopped":
            await self.store.set_slot_student(s, key)  # Before the mount can exist
            try:
                result = await self.agent.start(
                    node, vmid, student_key=key, code_revision=self.settings.slot_code_revision,
                    actions=self.settings.agent_start_actions)
            except (AgentBusy, AgentNotStopped, AgentUncertain):
                raise Wait() from None  # Re-read the real state on the next tick
            except AgentSourceMissing as exc:
                # The agent re-checks sources after `pct set`: mounts may already be changed, so
                # the slot is released only through stop + verified park, never directly.
                error = {"code": "STUDENT_DIR_MISSING", "stage": "ct_start",
                         "message": "Каталог студента на CephFS не найден; обратитесь к администратору"}
                await self.store.update_session(s, error=error, event=("start_source_missing",
                                                                        {"detail": exc.message[:200]}))
                return await self._goto(s, "ct_stop", "Остановка…", status="stopping", slot_state="releasing")
            except AgentCommandFailed as exc:
                raise Failure(f"Агент не смог запустить слот: {exc.message}", "SLOT_START_FAILED") from exc
            await self.store.event(s["id"], vmid, "ct_started",
                                   {"node": node, "actions": result.get("completed_actions")})
        # Running: our own start (the slot is ours since allocation) or one interrupted by a
        # restart. Either way confirm the student mount from the Proxmox config first.
        if not self.mounts_ok(await self.pve.ct_config(vmid), key, read_only=False):
            raise Failure("У запущенного слота точки монтирования не совпадают с ожидаемыми",
                          "WRONG_STUDENT_MOUNT")
        return await self._goto(s, "slot_ready", "Ожидание готовности слота…")

    async def _stage_slot_ready(self, s):
        if s["stop_requested"]:
            return await self._goto_stop(s)
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
        call = await self.store.current_call(s, (action,))
        if call is None or call["status"] == "failed":
            call = await self.store.new_call(s, action, body_factory() if body_factory else None)
        if call["status"] == "accepted" and call["operation_id"]:
            return str(call["operation_id"])
        try:
            if action == "deploy":
                resp = await client.deploy(call["body"], call["idempotency_key"])
            else:
                resp = await client.stop(str(s["project_id"]), call["idempotency_key"])
        except SlotUnavailable:
            raise Wait("Слот недоступен, повтор…") from None
        except SlotError as exc:
            await self._handle_submit_conflict(s, call, action, exc)
            raise Wait() from None
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
            status = await self._slot(s).lab_status(str(s["project_id"]))
            state = (status or {}).get("state")
            goal = "STOPPED" if action == "stop" else "READY"
            if state == goal:
                await self.store.update_call(call["id"], status="succeeded", result={"already": state})
                return
            if state == "FAILED":
                raise Failure("Лаба в слоте в состоянии FAILED, нужен оператор", "SLOT_LAB_FAILED")
            return  # Transitional state: wait for it
        if exc.status == 404 and action == "stop":
            raise Failure("Слот не знает проект студента; нужен оператор", "SLOT_PROJECT_UNKNOWN")
        # The slot's own code goes to the message; the session code stays from the fixed list.
        raise Failure(f"Слот отклонил {action}: {exc.code or exc.status}: {exc.message}"[:500], "SLOT_REJECTED")

    async def _ticket(self, s):
        """DeployTicket of this session: the exact ticket of the workspace's first deploy (the slot
        refuses another manifest for an existing project), otherwise built from the lab."""
        ws = await self.store.workspace(s["workspace_id"])
        if ws["ticket"]:
            return ws["ticket"], ws
        if s["lab_def"]:
            try:
                return labdef.deploy_ticket(ws["definition"], self.settings.images or {},
                                            project_id=str(s["project_id"]), project_name=s["project_name"]), ws
            except KeyError as exc:
                raise LabError(f"Образ {exc} убран из каталога образов") from exc
        lab = self.catalog.get(s["lab_ref"])
        return deploy_ticket(lab, project_id=str(s["project_id"]), project_name=s["project_name"]), ws

    async def _stage_lab_submit(self, s):
        call = await self.store.current_call(s, ("deploy",))
        if s["stop_requested"]:
            return await self._goto_stop(s, lab_may_exist=call is not None)
        if call is None or call["status"] != "succeeded":
            ticket, _ = await self._ticket(s)
            await self._submit(s, "deploy", lambda: ticket)
        return await self._goto(s, "lab_wait", "Запуск лабы…")

    async def _stage_lab_wait(self, s):
        call = await self.store.current_call(s, ("deploy",))
        if call is None or (call["status"] != "succeeded" and not call["operation_id"]):
            return await self._goto(s, "lab_submit", "Запуск лабы…")
        if call["status"] != "succeeded":
            op = await self._operation(s, call)
            if op["status"] == "RUNNING":
                lab = op.get("laboratory") or {}
                msg = SLOT_STAGE_MESSAGES.get(lab.get("state"), "Запуск лабы…")
                progress = op.get("progress")
                if progress:
                    msg = f"{msg} {progress}%"
                raise Wait(msg, progress=progress if isinstance(progress, int) else None)
            if op["status"] != "SUCCEEDED":
                await self.store.update_call(call["id"], status="failed", result={"error": op.get("error")})
                err = op.get("error") or {}
                text = str(err.get("message", ""))
                # Stable codes of slot 0.4.1; the text is matched only for older slots. Both are about
                # the student's project, not the slot: the slot is released as usual.
                if err.get("code") == "PROVISIONING_UNKNOWN" or "Provisioning completion is unknown" in text:
                    raise Failure("Проект создан старым слотом или его первая подготовка оборвалась: "
                                  "продолжить его нельзя", "SLOT_COLD_RESUME_UNSUPPORTED", slot_state="free")
                if err.get("code") == "PROJECT_MISSING":
                    raise Failure("Проекта лабы нет в каталоге студента", "PROJECT_MISSING", slot_state="free")
                raise Failure(f"Слот: {err.get('code', op['status'])}: {text}"[:500], "SLOT_OPERATION_FAILED")
            await self.store.update_call(call["id"], status="succeeded")
        await self.store.mark_deployed(s["workspace_id"], call["body"])
        raw = await self._slot(s).topology(str(s["project_id"]))
        topology = session_topology(s["project_name"], raw, await self._reference(s))
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
        try:
            status = await self._slot(s).lab_status(str(s["project_id"]))
        except SlotUnavailable:
            return False  # Transient; the plugin still sees the last topology
        if status is None or status.get("state") == "FAILED":
            err = (status or {}).get("error") or {}
            raise Failure(f"Лаба в слоте перешла в FAILED: {err.get('code', 'UNKNOWN')}", "SLOT_LAB_FAILED")
        return False

    # ------------------------------------------------------------------ stopping
    async def _stage_lab_stop(self, s):
        call = await self.store.current_call(s, ("stop",))
        if call is None or call["status"] not in ("accepted", "succeeded"):
            try:
                status = await self._slot(s).lab_status(str(s["project_id"]))
            except SlotUnavailable:
                raise Wait("Слот недоступен, повтор остановки…") from None
            if status is None or status.get("state") == "STOPPED":
                return await self._goto(s, "ct_stop", "Остановка контейнера слота…")
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
        return await self._goto(s, "ct_stop", "Остановка контейнера слота…")

    async def _stage_ct_stop(self, s):
        vmid, node = s["slot_vmid"], await self._node(s)
        try:
            if await self.agent.status(node, vmid) == "running":
                try:
                    result = await self.agent.stop(node, vmid, force=False)
                except AgentCommandFailed:
                    # Re-read before escalating: the shutdown may have completed anyway.
                    result = {"state": await self.agent.status(node, vmid)}
                if result.get("state") != "stopped":
                    # Shutdown did not finish: pct stop is the last resort.
                    await self.store.event(s["id"], vmid, "ct_hard_stop", {"node": node})
                    log.warning("Session %s: CT %s did not shut down; hard stop", s["id"], vmid)
                    await self.agent.stop(node, vmid, force=True)
        except (AgentBusy, AgentUncertain):
            raise Wait("Остановка контейнера слота…") from None
        if await self.agent.status(node, vmid) != "stopped":
            raise Wait("Остановка контейнера слота…")
        await self.store.set_slot_student(s, None)
        return await self._goto(s, "ct_park", "Освобождение слота…")

    async def _stage_ct_park(self, s):
        """Replace the student mount, then release. The slot becomes free only when the
        placeholder is confirmed in the Proxmox config; otherwise it goes to maintenance."""
        key = self.settings.placeholder_student_key
        slot_state, slot_message = "free", ""
        if key:
            vmid, node = s["slot_vmid"], await self._node(s)
            try:
                await self.agent.set_mounts(node, vmid, student_key=key, data_mode="ro",
                                            code_revision=self.settings.slot_code_revision)
            except (AgentBusy, AgentUncertain, AgentNotStopped):
                raise Wait() from None
            except (AgentRejected, AgentCommandFailed) as exc:
                await self.store.event(s["id"], vmid, "park_failed", {"error": exc.message[:200]})
            if not self.mounts_ok(await self.pve.ct_config(vmid), key, read_only=True):
                # Last student's directory may still be configured: no automatic reuse.
                slot_state, slot_message = "maintenance", f"session {s['id']}: placeholder not confirmed"
                log.error("Session %s: slot %s not parked; maintenance", s["id"], vmid)
        if s["error"]:
            if slot_state == "free" and s["error"].get("slot_state", "free") != "free":
                # A slot-level failure: parked, but kept for the operator.
                slot_state = s["error"]["slot_state"]
                slot_message = f"session {s['id']}: {s['error']['code']}"
            await self.store.finish_session(s, status="failed", message=s["error"]["message"], error=s["error"],
                                            slot_state=slot_state, slot_message=slot_message)
        else:
            reason = s["stop_reason"] or "user"
            message = {"maxduration": "Время лабы истекло",
                       "teacher": "Лаба остановлена преподавателем"}.get(reason, "Лаба остановлена")
            await self.store.finish_session(s, status="stopped", message=message,
                                            slot_state=slot_state, slot_message=slot_message)
        self._health_checked.pop(s["id"], None)
        return False

    # ------------------------------------------------------------------ reads for the API
    async def live_nodes(self, s) -> tuple[dict, bool]:
        """Node statuses of a running session, cached a few seconds.

        Returns (nodes, stale): stale is True when the slot did not answer and the snapshot from
        the start of the session is returned instead (plugin request П6).
        """
        cached = self._live_cache.get(s["id"])
        if cached and time.monotonic() - cached[0] < 3:
            return cached[1], cached[2]
        nodes, stale = node_states(s["topology"]), True
        try:
            raw = await self._slot(s).topology(str(s["project_id"]))
            live = node_states(session_topology(s["project_name"], raw))
            nodes, stale = {k: live.get(k, v) for k, v in nodes.items()}, False
        except (SlotError, Failure):
            pass
        self._live_cache[s["id"]] = (time.monotonic(), nodes, stale)
        return nodes, stale
