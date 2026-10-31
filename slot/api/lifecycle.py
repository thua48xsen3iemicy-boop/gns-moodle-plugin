"""Persistent single-slot operation coordinator. Never replays guest commands after a crash."""

import asyncio
import fcntl
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from api import uplink
from api.client import call, get_lifecycle
from api.facts import Facts
from api.l1 import Keepalives
from api.models import DeployTicket
from api.qga import Priority, QgaBroker, QgaError

TRANSITIONS = {
    None: {"DEPLOYING"},
    "DEPLOYING": {"STARTING", "FAILED"},
    "STARTING": {"PROVISIONING", "FAILED"},
    "PROVISIONING": {"PROVISIONING", "READY", "FAILED"},
    "READY": {"DEPLOYING", "STOPPING", "WIPING", "FAILED"},
    "STOPPING": {"STOPPED", "FAILED"},
    "STOPPED": {"DEPLOYING", "STOPPING", "FAILED"},
    "WIPING": {"READY", "FAILED"},
    "FAILED": {"DEPLOYING", "STOPPING", "FAILED"},
}

router = APIRouter(prefix="/v1/operations", tags=["operations"])


class LabError(RuntimeError):
    """Operation failure with a stable error.code; the manager decides by the code, not the text."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

LAB_STATE_DIR = "/opt/gns3/projects/.lab-slot"


class LabStore:
    """Lab records in the student's directory, so a project continues in any slot.

    Operations and idempotency keys stay in the slot's SQLite: they live for one session.
    """

    def __init__(self, root: Path):
        self.root = root

    def path(self, pid):
        return self.root / (pid + ".json")

    @staticmethod
    def decode(data):
        try:
            return json.loads(data)
        except ValueError as exc:
            raise OSError("Corrupt lab record") from exc

    def get(self, pid):
        try:
            return self.decode(self.path(pid).read_bytes())
        except FileNotFoundError:
            return None

    def all(self):
        try:
            paths = sorted(self.root.iterdir())
        except FileNotFoundError:
            return []
        return [
            self.decode(p.read_bytes())
            for p in paths
            if p.suffix == ".json" and not p.name.startswith(".")
        ]

    def write(self, pid, obj):
        # Atomic replace: a power loss leaves the old or the new record, never a mix.
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.root / f".{pid}.{uuid4().hex}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(json.dumps(obj).encode())
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path(pid))
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        self.sync()

    def delete(self, pid):
        self.path(pid).unlink(missing_ok=True)
        self.sync()

    def sync(self):
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@router.get("/{operation_id}")
async def operation(request: Request, operation_id: str):
    return get_lifecycle(request).operation(operation_id)


class Admission:
    """A long command accepted under command_lock; claim() waits for it to finish.

    Ownership must not move while a command is still running in a guest, and a guest
    command cannot be killed, so claim() waits instead of cancelling.
    """

    def __init__(self, lifecycle):
        self.attached = False
        self.future = asyncio.get_running_loop().create_future()
        lifecycle.commands.add(self.future)
        self.future.add_done_callback(lifecycle.commands.discard)

    def release(self):
        if not self.future.done():
            self.future.set_result(None)

    def start(self, work):
        """Attach synchronously before an asynchronous HTTP response releases admission."""
        self.attached = True
        task = asyncio.ensure_future(work)
        task.add_done_callback(self._finished)
        return task

    async def run(self, work):
        """Run `work` to the end even if the HTTP client goes away."""
        return await asyncio.shield(self.start(work))

    def _finished(self, task):
        if not task.cancelled():
            task.exception()  # mark retrieved: the caller may be gone
        self.release()


class Lifecycle:
    def __init__(self, app):
        self.request = SimpleNamespace(app=app)
        root = Path(os.getenv("SLOT_STATE_DIR", "/var/lib/lab-slot"))
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (root / "service.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise
        try:
            self.db = sqlite3.connect(root / "state.sqlite3", timeout=1)
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id))"
            )
            self.tasks = set()
            self.commands = set()  # see Admission
            self.command_lock = asyncio.Lock()
            self.claimed = False
            self.boot_requires_new_owner = True
            self.project_locks = {}
            self.qga = QgaBroker(self.qga_socket)
            self.labs = LabStore(
                Path(os.getenv("SLOT_LAB_STATE_DIR", LAB_STATE_DIR)) / "labs"
            )
            try:
                self.labs.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            except OSError:
                pass  # Reported by /v1/readyz as LAB_STATE_UNAVAILABLE
            for op in self.all("operation"):
                if op["status"] == "RUNNING":
                    op.update(
                        status="FAILED",
                        error={
                            "code": "SERVICE_RESTART",
                            "message": "Interrupted; inspect GNS3 and explicitly resume",
                        },
                    )
                    self.save("operation", op["operation_id"], op)
                    lab = self.get("lab", op["project_id"])
                    if lab:
                        self.update(lab, "FAILED", "interrupted", error=op["error"])
            for lab in self.all("lab"):
                if lab["state"] not in ("STOPPED", "FAILED"):
                    self.update(
                        lab,
                        "FAILED",
                        "restart_requires_resume",
                        error={
                            "code": "SERVICE_RESTART",
                            "message": "Explicit resume required to reacquire lock and recheck readiness",
                        },
                    )
            self.l1 = Keepalives(self)
            self.monitor = asyncio.create_task(self.watch())
            self.facts = Facts(self)
        except BaseException:
            for task in (getattr(self, "monitor", None),
                         getattr(getattr(self, "facts", None), "task", None),
                         getattr(getattr(self, "l1", None), "task", None)):
                if task is not None:
                    task.cancel()
            if hasattr(self, "db"):
                self.db.close()
            self.lock.close()
            raise

    @staticmethod
    def qga_socket(node_id):
        from api import labs

        return labs.QGA_DIR / (node_id + ".sock")

    def get(self, kind, ident):
        if kind == "lab":
            return self.labs.get(ident)
        row = self.db.execute(
            "SELECT data FROM records WHERE kind=? AND id=?", (kind, ident)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, kind):
        if kind == "lab":
            return self.labs.all()
        return [
            json.loads(r[0])
            for r in self.db.execute("SELECT data FROM records WHERE kind=?", (kind,))
        ]

    def save(self, kind, ident, obj):
        obj["updated_at"] = time.time()
        if kind == "lab":
            self.labs.write(ident, obj)
            return
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO records VALUES (?,?,?)",
                (kind, ident, json.dumps(obj)),
            )

    def update(self, lab, state, stage, **values):
        if state not in TRANSITIONS.get(lab.get("state"), set()):
            raise RuntimeError(
                f"Invalid lifecycle transition: {lab.get('state')} -> {state}"
            )
        lab.update(state=state, stage=stage, **values)
        self.save("lab", lab["project_id"], lab)
        if state != "READY" and hasattr(self, "l1"):
            self.l1.reset()

    def status(self, pid):
        lab = self.get("lab", pid)
        if not lab:
            raise HTTPException(404, "Unknown managed laboratory")
        # Do not expose trusted provisioning commands in status.
        return {k: v for k, v in lab.items() if k != "ticket"}

    @staticmethod
    def node_summary(nodes):
        fields = ("node_id", "name", "node_type", "status")
        return [{k: n[k] for k in fields if k in n} for n in nodes]

    def present(self, op, *, replayed=False, compact=False):
        lab = self.get("lab", op["project_id"])
        current = (
            {
                "state": lab["state"],
                "stage": lab.get("stage"),
                "updated_at": lab.get("updated_at"),
            }
            if lab
            else None
        )
        result = {
            k: op.get(k) for k in ("operation_id", "project_id", "status", "action")
        }
        result.update(replayed=replayed, laboratory=current)
        if not compact:
            source = lab if lab and op["status"] == "RUNNING" and op.get("action") != "shutdown" else op
            result.update(
                stage=source.get("stage"),
                progress=source.get("progress", 0),
                error=op.get("error"),
                nodes=self.node_summary(source.get("nodes", [])),
                updated_at=op.get("updated_at"),
            )
        if op.get("action") == "shutdown":
            result["result"] = op.get("result")
        return result

    def operation(self, oid):
        op = self.get("operation", oid)
        if not op:
            raise HTTPException(404, "Unknown operation")
        return self.present(op)

    def submit(self, action, pid=None, ticket=None, node_id=None, key=None):
        ticket_data = ticket.model_dump(mode="json") if ticket else None
        fingerprint = hashlib.sha256(
            json.dumps([action, pid, ticket_data, node_id], sort_keys=True).encode()
        ).hexdigest()
        dedup = key or fingerprint
        for op in self.all("operation"):
            if op["key"] == dedup:
                if op["fingerprint"] != fingerprint:
                    raise HTTPException(
                        409,
                        {
                            "code": "IDEMPOTENCY_KEY_CONFLICT",
                            "message": "Idempotency-Key reused with different request",
                        },
                    )
                # Explicit keys persist; implicit retries deduplicate running operations only.
                if key or op["status"] == "RUNNING":
                    lab_now = self.get("lab", op["project_id"])
                    expected = {
                        "deploy": "READY",
                        "resume": "READY",
                        "stop": "STOPPED",
                    }.get(op["action"])
                    if (
                        op["status"] == "SUCCEEDED"
                        and expected
                        and (not lab_now or lab_now["state"] != expected)
                    ):
                        raise HTTPException(
                            409,
                            {
                                "code": "STALE_IDEMPOTENCY_KEY",
                                "message": "Historical success no longer satisfies its postcondition; use a new key for an intentional operation",
                                **self.present(op, replayed=True, compact=True),
                            },
                        )
                    return self.present(op, replayed=True, compact=True)
        if any(op["status"] == "RUNNING" for op in self.all("operation")):
            raise HTTPException(
                409,
                {
                    "code": "OPERATION_IN_PROGRESS",
                    "message": "Slot operation in progress",
                },
            )
        pid = pid or str(ticket.project_id or uuid4())
        lab = self.get("lab", pid)
        # Ownership is the project lock held by this process. After a restart no lock is held:
        # labs left FAILED by an earlier run (often another student's mount) do not block the
        # slot, and run() still refuses to start while another GNS3 project has started nodes.
        for other in self.all("lab"):
            if (
                other["project_id"] != pid
                and other["state"] != "STOPPED"
                and other["project_id"] in self.project_locks
            ):
                raise HTTPException(
                    409,
                    {
                        "code": "SLOT_OCCUPIED",
                        "message": "Another laboratory owns this slot; stop it first",
                        "project_id": other["project_id"],
                    },
                )
        if action != "deploy" and not lab:
            raise HTTPException(404, "Unknown managed laboratory")
        if action == "wipe" and lab["state"] != "READY":
            raise HTTPException(409, "Wipe requires a READY laboratory")
        if lab and ticket_data:
            ticket_data["project_id"] = pid
            if ticket_data != lab["ticket"]:
                raise HTTPException(409, "Existing project manifest differs")
        if not lab:
            ticket_data["project_id"] = pid
            lab = {
                "project_id": pid,
                "ticket": ticket_data,
                "nodes": [],
                "progress": 0,
                "error": None,
            }
        next_state = {
            "deploy": "DEPLOYING",
            "resume": "DEPLOYING",
            "stop": "STOPPING",
            "wipe": "WIPING",
        }[action]
        if next_state not in TRANSITIONS.get(lab.get("state"), set()):
            raise HTTPException(
                409,
                {
                    "code": "INVALID_TRANSITION",
                    "message": "Current laboratory state does not allow this operation",
                },
            )
        # Shared project directory lock coordinates cooperating Slot services on CephFS.
        newly_locked = pid not in self.project_locks
        if newly_locked:
            root = Path(
                os.getenv("SLOT_PROJECT_LOCK_DIR", "/opt/gns3/projects/.slot-locks")
            )
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            handle = (root / (pid + ".lock")).open("a")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                handle.close()
                if isinstance(exc, BlockingIOError):
                    raise HTTPException(
                        409,
                        {
                            "code": "PROJECT_LOCKED",
                            "message": "Project locked by another slot",
                        },
                    ) from exc
                raise
            self.project_locks[pid] = handle
        previous_state = lab.get("state", "FAILED")
        lab["generation"] = lab.get("generation", 0) + 1
        lab.update(state=next_state, stage=action, error=None, updated_at=time.time())
        op = {
            "operation_id": str(uuid4()),
            "project_id": pid,
            "action": action,
            "node_id": node_id,
            "status": "RUNNING",
            "key": dedup,
            "fingerprint": fingerprint,
            "error": None,
            "previous_state": previous_state,
        }
        op["updated_at"] = time.time()
        # The lab record goes first: a crash before the operation is stored leaves a
        # non-final state, which the next start turns into FAILED / SERVICE_RESTART.
        try:
            previous = self.get("lab", pid)
            self.labs.write(pid, lab)
            try:
                with self.db:
                    self.db.execute(
                        "INSERT OR REPLACE INTO records VALUES (?,?,?)",
                        ("operation", op["operation_id"], json.dumps(op)),
                    )
            except BaseException:
                if previous is None:
                    self.labs.delete(pid)
                else:
                    self.labs.write(pid, previous)
                raise
        except BaseException:
            if newly_locked:
                self.project_locks.pop(pid).close()
            raise
        task = asyncio.create_task(self.run(op, lab))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return self.present(op, compact=True)

    def submit_shutdown(self, pid, node_id, timeout, key, admission):
        fingerprint = hashlib.sha256(json.dumps(["shutdown", pid, node_id, timeout]).encode()).hexdigest()
        dedup = key or fingerprint
        for op in self.all("operation"):
            if op["key"] == dedup:
                if op["fingerprint"] != fingerprint:
                    raise HTTPException(409, {"code": "IDEMPOTENCY_KEY_CONFLICT", "message": "Shutdown key reused"})
                if key or op["status"] == "RUNNING":
                    return self.present(op, replayed=True, compact=True)
            if op["status"] == "RUNNING" and (op.get("action") != "shutdown" or op["project_id"] != pid or op["node_id"] == node_id):
                raise HTTPException(409, {"code": "OPERATION_IN_PROGRESS", "message": "Conflicting slot operation"})
        lab = self.get("lab", pid)
        lab["generation"] = lab.get("generation", 0) + 1
        self.save("lab", pid, lab)  # Invalidate a monitor snapshot taken before admission.
        op = {"operation_id": str(uuid4()), "project_id": pid, "node_id": node_id,
              "action": "shutdown", "status": "RUNNING", "key": dedup,
              "fingerprint": fingerprint, "updated_at": time.time(), "stage": "guest_shutdown",
              "progress": 0, "error": None, "nodes": []}
        self.save("operation", op["operation_id"], op)
        task = admission.start(self.run_shutdown(op, timeout))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return self.present(op, compact=True)

    async def run_shutdown(self, op, timeout):
        pid, node_id = op["project_id"], op["node_id"]
        accepted = False
        node = None
        try:
            async with asyncio.timeout(timeout):
                node = await self.api("GET", f"projects/{pid}/nodes/{node_id}")
                if node.get("status") != "stopped":
                    try:
                        await self.qga.call(node_id, "guest-shutdown", {"mode": "powerdown"},
                                            priority=Priority.LIFECYCLE, timeout=min(10, timeout), max_wait=timeout)
                    except (QgaError, OSError, ValueError) as exc:
                        raise LabError("GUEST_AGENT_UNAVAILABLE", "Guest agent is unavailable") from exc
                    accepted = True
                    while True:
                        node = await self.api("GET", f"projects/{pid}/nodes/{node_id}")
                        if node.get("status") == "stopped":
                            break
                        await asyncio.sleep(0.25)
                op.update(status="SUCCEEDED", result="stopped", progress=100)
        except TimeoutError:
            op.update(status="FAILED", error={"code": "GUEST_SHUTDOWN_TIMEOUT" if accepted else "GUEST_AGENT_UNAVAILABLE",
                                              "message": "Guest did not power down within the deadline"})
        except asyncio.CancelledError:
            op.update(status="FAILED", error={"code": "SERVICE_RESTART", "message": "Shutdown interrupted"})
            raise
        except Exception as exc:
            op.update(status="FAILED", error={"code": exc.code if isinstance(exc, LabError) else "GUEST_AGENT_UNAVAILABLE",
                                              "message": "Guest shutdown failed"})
        finally:
            # This is a node operation, not a failed laboratory lifecycle. A guest
            # refusing shutdown must leave the other devices and lab running.
            lab = self.get("lab", pid)
            if lab:
                lab["generation"] = lab.get("generation", 0) + 1
                if node:
                    lab["nodes"] = [node if n.get("node_id") == node_id else n for n in lab.get("nodes", [])]
                self.save("lab", pid, lab)
            op.update(updated_at=time.time(), nodes=[node] if node else [])
            self.save("operation", op["operation_id"], op)

    async def claim(self, epoch, generation):
        """Called under command_lock. Drain accepted work before handing ownership over.

        Persist the high-water mark first. A timed-out claimant can retry, but no
        previous owner is admitted even if draining takes longer than its HTTP timeout.
        """
        old = self.get("authority", "slot") or {"epoch": 0, "generation": 0}
        if ((epoch, generation) < (old["epoch"], old["generation"])
                or (self.boot_requires_new_owner and (epoch, generation) == (old["epoch"], old["generation"]))):
            raise HTTPException(409, {"code": "STALE_OWNER", "message": "Command owner superseded"})
        self.claimed = False
        self.l1.reset()
        self.facts.reset()  # nothing collected for the previous owner is served to this one
        self.save("authority", "slot", {"epoch": epoch, "generation": generation})
        self.boot_requires_new_owner = False
        pending = [*self.tasks, *self.commands]
        if pending:
            await asyncio.shield(asyncio.gather(*pending, return_exceptions=True))
        self.claimed = True
        return {"epoch": epoch, "generation": generation}

    def admit(self):
        """Register a long command. Call under command_lock right after the owner check:
        the lock may then be released and claim() still waits for the command."""
        return Admission(self)

    def owns(self, headers):
        owner = self.get("authority", "slot")
        return self.claimed and owner is not None and (headers.get("X-Lab-Epoch"), headers.get("X-Lab-Generation")) == (
            str(owner["epoch"]), str(owner["generation"]))

    async def api(self, method, path, payload=None):
        return await call(self.request, method, path, payload)

    async def wait_nodes(self, lab, expected):
        deadline = time.monotonic() + float(os.getenv("SLOT_READY_TIMEOUT", "180"))
        while True:
            nodes = uplink.visible_nodes(
                lab["ticket"], await self.api("GET", f"projects/{lab['project_id']}/nodes")
            )
            lab["nodes"] = nodes
            self.save("lab", lab["project_id"], lab)
            names = {n["name"] for n in nodes}
            if names != {n["name"] for n in lab["ticket"]["nodes"]}:
                raise RuntimeError(
                    "Project node inventory differs from manifest; no automatic repair"
                )
            if all(n.get("status") == expected for n in nodes):
                return nodes
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Devices did not reach {expected}")
            await asyncio.sleep(1)

    async def validate_topology(self, lab):
        pid = lab["project_id"]
        all_nodes = await self.api("GET", f"projects/{pid}/nodes")
        all_links = await self.api("GET", f"projects/{pid}/links")
        # The cloud node of the uplink and its link are the slot's own: not part of the inventory, but
        # they must be there (a resumed project is never patched).
        nodes = uplink.visible_nodes(lab["ticket"], all_nodes)
        specs = {s["name"]: s for s in lab["ticket"]["nodes"]}
        if len(nodes) != len(specs) or {n["name"] for n in nodes} != set(specs):
            raise RuntimeError(
                "Incomplete or foreign project inventory; operator reconciliation required"
            )
        if any(n["node_type"] != specs[n["name"]]["kind"] for n in nodes):
            raise RuntimeError("Device types differ from manifest")
        names = {n["node_id"]: n["name"] for n in nodes}

        def endpoints(link, actual):
            if actual:
                return tuple(
                    sorted(
                        (
                            names.get(e["node_id"], "UNKNOWN"),
                            e["adapter_number"],
                            e["port_number"],
                        )
                        for e in link["nodes"]
                    )
                )
            return tuple(
                sorted(
                    (link[k]["node"], link[k]["adapter"], link[k]["port"])
                    for k in ("a", "b")
                )
            )

        links = uplink.visible_links(lab["ticket"], all_nodes, all_links)
        if sorted(endpoints(l, True) for l in links) != sorted(
            endpoints(l, False) for l in lab["ticket"]["links"]
        ):
            raise RuntimeError(
                "Project links differ from manifest; refusing automatic rebuild"
            )
        uplink.check_attached(lab["ticket"], all_nodes, all_links)

    async def provision(self, lab, node, spec, initial):
        deadline = time.monotonic() + float(os.getenv("SLOT_READY_TIMEOUT", "180"))
        # A VNC node has no serial console: neither serial-getty nor a Telnet port exist, and
        # readiness is the VNC port accepting a TCP connection. QGA does not depend on the
        # console; it is awaited here only when the initial provisioning commands need it.
        # Tickets stored before NodeSpec.console existed have no such key: they are telnet.
        vnc = spec.get("console") == "vnc"
        qga_needed = spec["kind"] == "qemu" and (
            not vnc or bool(initial and spec.get("provisioning"))
        )
        while True:
            try:
                if qga_needed:
                    await self.qga.call(
                        node["node_id"],
                        "guest-ping",
                        timeout=2,
                        priority=Priority.LIFECYCLE,
                    )
                if spec["kind"] == "qemu" and not vnc:
                    command = (
                        "systemctl enable --now serial-getty@ttyS0.service && systemctl is-active --quiet serial-getty@ttyS0.service"
                        if initial
                        else "systemctl is-active --quiet serial-getty@ttyS0.service"
                    )
                    result = await self.qga.exec(
                        node["node_id"], command, timeout=20, priority=Priority.LIFECYCLE
                    )
                    if result["exit_code"] != 0:
                        raise RuntimeError("serial-getty readiness failed")
                port = node.get("console")
                if (
                    node.get("console_type") != ("vnc" if vnc else "telnet")
                    or type(port) is not int
                    or not 1 <= port <= 65535
                ):
                    raise RuntimeError(
                        "Missing local VNC console" if vnc else "Missing local Telnet console"
                    )
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port), 3
                )
                writer.close()
                await writer.wait_closed()
                break
            except (OSError, RuntimeError, ValueError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(1)
        if initial and spec["kind"] == "iou" and spec.get("provisioning"):
            from api.cli import run_commands

            await run_commands(port, spec["provisioning"], spec["cli_prompt"])
            return
        if initial:
            for cmd in spec.get("provisioning", []):
                if spec["kind"] != "qemu":
                    raise RuntimeError(
                        "NOT_SUPPORTED: Cisco CLI provisioning requires a verified prompt/command adapter"
                    )
                result = await self.qga.exec(
                    node["node_id"],
                    cmd["command"],
                    timeout=cmd["timeout_seconds"],
                    priority=Priority.LIFECYCLE,
                )
                if result["exit_code"] != 0:
                    raise RuntimeError("Mandatory provisioning command failed")

    async def run(self, op, lab):
        pid = lab["project_id"]
        try:
            version = await self.api("GET", "version")
            if version.get("version") != "2.2.61":
                raise RuntimeError("This lifecycle requires GNS3 2.2.61")
            if op["action"] == "wipe":
                nodes = await self.api("GET", f"projects/{pid}/nodes")
                if not any(n["node_id"] == op["node_id"] for n in nodes):
                    raise RuntimeError("Node does not belong to project")
                op.update(
                    status="NOT_SUPPORTED",
                    error={
                        "code": "NOT_SUPPORTED",
                        "message": "No verified atomic reset adapter for QEMU overlays / IOL NVRAM; no devices or files modified",
                    },
                )
                self.update(
                    lab, op["previous_state"], "wipe_not_supported", error=op["error"]
                )
                return
            if op["action"] == "stop":
                await self.api("POST", f"projects/{pid}/nodes/stop", {})
                nodes = await self.wait_nodes(lab, "stopped")
                for node in nodes:
                    await self.qga.forget(node["node_id"])
                self.update(lab, "STOPPED", "stopped", progress=100)
                handle = self.project_locks.pop(pid, None)
                if handle:
                    handle.close()
            else:
                from api.labs import create_project

                # Existing projects are opened, never rebuilt. Interrupted partial creations fail inventory validation.
                projects = await self.api("GET", "projects")
                if (
                    op["action"] == "resume" or lab.get("creation_complete")
                ) and not any(p["project_id"] == pid for p in projects):
                    raise LabError(
                        "PROJECT_MISSING",
                        "Saved project is missing; refusing to recreate student disks",
                    )
                for project in projects:
                    if (
                        project["project_id"] != pid
                        and project.get("status") != "closed"
                    ):
                        other_nodes = await self.api(
                            "GET", f"projects/{project['project_id']}/nodes"
                        )
                        if any(n.get("status") == "started" for n in other_nodes):
                            raise RuntimeError(
                                "Another GNS3 project is running in this slot"
                            )
                result = await create_project(
                    self.request, DeployTicket.model_validate(lab["ticket"])
                )
                initial = result["result"] == "created"
                if initial:
                    lab["creation_complete"] = True
                    self.save("lab", pid, lab)
                if not initial and not lab.get("provisioned"):
                    raise LabError(
                        "PROVISIONING_UNKNOWN",
                        "Provisioning completion is unknown; refusing to skip or replay commands. Operator reconciliation required",
                    )
                await self.validate_topology(lab)
                self.update(lab, "STARTING", "start_devices", progress=20)
                await self.api("POST", f"projects/{pid}/nodes/start", {})
                nodes = await self.wait_nodes(lab, "started")
                self.update(lab, "PROVISIONING", "service_readiness", progress=50)
                specs = {n["name"]: n for n in lab["ticket"]["nodes"]}
                for i, node in enumerate(nodes):
                    self.update(
                        lab,
                        "PROVISIONING",
                        node["name"],
                        active_node_id=node["node_id"],
                    )
                    await self.provision(lab, node, specs[node["name"]], initial)
                    self.update(
                        lab,
                        "PROVISIONING",
                        node["name"],
                        progress=50 + int(49 * (i + 1) / len(nodes)),
                    )
                await self.wait_nodes(lab, "started")
                lab["provisioned"] = True
                self.update(lab, "READY", "ready", progress=100, active_node_id=None)
            op["status"] = "SUCCEEDED"
        except Exception as exc:  # noqa: BLE001 - persist failures at the background-task boundary.
            code = exc.code if isinstance(exc, LabError) else type(exc).__name__
            error = {"code": code, "message": str(exc)}
            self.update(lab, "FAILED", "operation_failed", error=error)
            op.update(status="FAILED", error=error)
        finally:
            op.update(
                stage=lab["stage"],
                progress=lab.get("progress", 0),
                nodes=self.node_summary(lab.get("nodes", [])),
            )
            self.save("operation", op["operation_id"], op)

    async def watch(self):
        while True:
            await asyncio.sleep(float(os.getenv("SLOT_MONITOR_INTERVAL", "10")))
            for lab in self.all("lab"):
                if lab["state"] != "READY":
                    continue
                if any(op.get("action") == "shutdown" and op["status"] == "RUNNING" and op["project_id"] == lab["project_id"]
                       for op in self.all("operation")):
                    continue
                try:
                    nodes = uplink.visible_nodes(
                        lab["ticket"], await self.api("GET", f"projects/{lab['project_id']}/nodes")
                    )
                    if {n["name"] for n in nodes} != {
                        n["name"] for n in lab["ticket"]["nodes"]
                    } or any(n.get("status") != "started" and n.get("node_type") != "qemu" for n in nodes):
                        raise RuntimeError("Required node stopped or missing")
                    specs = {s["name"]: s for s in lab["ticket"]["nodes"]}
                    for node in nodes:
                        if node.get("node_type") == "qemu" and node.get("status") != "started":
                            continue  # A guest may power itself off; its disk remains usable.
                        await self.provision(lab, node, specs[node["name"]], False)
                    # A request may have started an operation while checks were awaiting IO.
                    current = self.get("lab", lab["project_id"])
                    if current["state"] == "READY" and current.get(
                        "generation"
                    ) == lab.get("generation"):
                        lab["nodes"] = nodes
                        self.save("lab", lab["project_id"], lab)
                except Exception as exc:  # noqa: BLE001 - persist failures at the background-task boundary.
                    current = self.get("lab", lab["project_id"])
                    if current["state"] == "READY" and current.get(
                        "generation"
                    ) == lab.get("generation"):
                        self.update(
                            lab,
                            "FAILED",
                            "monitor",
                            error={"code": type(exc).__name__, "message": str(exc)},
                        )

    async def close(self):
        self.claimed = False
        self.l1.reset()
        self.monitor.cancel()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(self.monitor, *self.tasks, return_exceptions=True)
        await self.l1.stop()
        await self.facts.stop()
        await self.qga.close()
        for handle in self.project_locks.values():
            handle.close()
        self.db.close()
        self.lock.close()
