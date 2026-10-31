"""Slot registry, workspaces and sessions in PostgreSQL.

Every change to a session that owns a slot is fenced: it only applies while
slots.generation still equals the generation the session got at allocation time.
"""

import asyncio
import json
from contextlib import asynccontextmanager
import secrets
import uuid

import psycopg
from psycopg.types.json import Jsonb

from .facts import differs

LIVE = ("queued", "starting", "running", "stopping")
TERMINAL = ("stopped", "failed")
SESSION_FIELDS = {
    "status", "stage", "status_message", "error", "pve_upid", "stop_requested", "stop_reason",
    "topology", "started_at", "expires_at", "finished_at", "progress",
}


class CaptureConflict(RuntimeError):
    pass


class Fenced(RuntimeError):
    """The slot was re-allocated or released: this worker's view is stale."""


def new_session_id() -> str:
    return "s-" + secrets.token_hex(8)


def _jsonable(fields: dict) -> dict:
    return {k: Jsonb(v) if isinstance(v, (dict, list)) else v for k, v in fields.items()}


class Store:
    def __init__(self, pool):
        self.pool = pool
        self._allocation_lock = asyncio.Lock()

    async def next_epoch(self):
        async with self.pool.connection() as conn:
            cur = await conn.execute("UPDATE command_owner SET epoch = nextval('command_epochs') RETURNING epoch")
            return (await cur.fetchone())["epoch"]

    async def current_epoch(self):
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT epoch FROM command_owner")
            epoch = (await cur.fetchone())["epoch"]
            if epoch < 1:
                raise Fenced("Start the manager once before enabling slots")
            return epoch

    @staticmethod
    async def _check_epoch(conn):
        from .ownership import current

        authority = current.get()
        if authority is not None:
            authority.headers()
            cur = await conn.execute("SELECT epoch FROM command_owner FOR SHARE")
            if (await cur.fetchone())["epoch"] != authority.epoch:
                raise Fenced("Manager leadership epoch changed")

    @asynccontextmanager
    async def allocation_guard(self):
        """Serialize snapshot + allocation across tasks/processes, before reading budgets.

        The local lock prevents waiting tasks from exhausting this process's pool.
        Other queries use pool connections while one transaction holds the cluster lock.
        """
        from .db import LOCK_KEY

        async with self._allocation_lock:
            async with self.pool.connection() as conn, conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY + 2,))
                yield

    # ------------------------------------------------------------------ slots
    async def sync_slots(self, slots):
        """Mirror the slots file. New slots start in maintenance until an operator enables them."""
        async with self.pool.connection() as conn, conn.transaction():
            for s in slots:
                await conn.execute(
                    "INSERT INTO slots (vmid, address, port, state, in_config)"
                    " VALUES (%s, %s, %s, 'maintenance', true)"
                    " ON CONFLICT (vmid) DO UPDATE SET address = EXCLUDED.address,"
                    " port = EXCLUDED.port, in_config = true",
                    (s.vmid, s.address, s.port),
                )
            await conn.execute(
                "UPDATE slots SET in_config = false WHERE NOT (vmid = ANY(%s))",
                ([s.vmid for s in slots],),
            )

    async def slots(self):
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM slots ORDER BY vmid")
            return await cur.fetchall()

    async def slot(self, vmid):
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM slots WHERE vmid = %s", (vmid,))
            return await cur.fetchone()

    async def set_slot_admin_state(self, vmid, state, message="", *, check_epoch=False):
        """Operator action: only between maintenance/failed/free, never on a slot in use."""
        async with self.pool.connection() as conn, conn.transaction():
            if check_epoch:
                await self._check_epoch(conn)
            cur = await conn.execute(
                "UPDATE slots SET state = %s, state_message = %s, generation = generation + 1,"
                " session_id = CASE WHEN %s = 'free' THEN NULL ELSE session_id END, updated_at = now()"
                " WHERE vmid = %s AND state IN ('free', 'failed', 'maintenance')"
                " AND NOT EXISTS (SELECT 1 FROM sessions WHERE slot_vmid = %s"
                "                 AND status NOT IN ('stopped', 'failed'))"
                " RETURNING vmid",
                (state, message, state, vmid, vmid),
            )
            if not await cur.fetchone():
                raise ValueError("Slot is in use or unknown")
            if state == "free":
                # `slot-enable` confirmed the CT is stopped: no student directory is writable there.
                # The operator has looked at the slot: its record of failed labs starts again.
                await conn.execute("UPDATE slots SET student_key = NULL, lab_failures = '[]'::jsonb"
                                   " WHERE vmid = %s", (vmid,))
            await self._event(conn, None, vmid, "slot_admin_state", {"state": state})

    # ------------------------------------------------------------------ sessions
    async def create_session(self, *, site, user_id, instance_id, course_id, lab_ref, max_duration,
                             student_key, resources, lab_def=None, definition=None, ticket_factory=None, mode=None, library_lab_id=None):
        """Returns (session, created). Idempotent: an existing live session is returned as is.

        The lab is either lab_ref (catalog) or lab_def with its normalized definition (Moodle).
        Every session of a workspace runs its one GNS3 project (same project_id, on any slot).
        """
        lab_key = lab_ref or lab_def
        label = lab_ref or "lab-" + lab_def.removeprefix("sha256:")[:8]  # Start of project names
        for _ in range(3):
            existing = await self.live_session(site, user_id, library_lab_id=library_lab_id)
            if existing:
                return existing, False  # The caller checks it is the same activity and lab
            try:
                async with self.pool.connection() as conn, conn.transaction():
                    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (student_key,))
                    active = await conn.execute("SELECT 1 FROM image_captures WHERE student_key = %s AND state = 'running'", (student_key,))
                    if await active.fetchone():
                        raise CaptureConflict("Workspace capture is running")
                    if library_lab_id is not None:
                        # Returning to an earlier definition must still create a fresh project.
                        previous = await conn.execute("SELECT lab_key FROM workspaces WHERE site = %s AND user_id = %s AND library_lab_id = %s ORDER BY id DESC LIMIT 1", (site, user_id, library_lab_id))
                        last = await previous.fetchone()
                        if last and last['lab_key'] != lab_key:
                            await conn.execute("UPDATE workspaces SET library_lab_id = NULL WHERE site = %s AND user_id = %s AND library_lab_id = %s", (site, user_id, library_lab_id))
                    cur = await conn.execute(
                        "INSERT INTO workspaces (site, user_id, instance_id, lab_ref, lab_def, definition, lab_key,"
                        " project_id, project_name, library_lab_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '', %s)"
                        " ON CONFLICT DO NOTHING RETURNING id",
                        (site, user_id, instance_id, lab_ref, lab_def,
                         Jsonb(definition) if definition is not None else None, lab_key, uuid.uuid4(), library_lab_id),
                    )
                    row = await cur.fetchone()
                    if row:
                        await conn.execute(
                            "UPDATE workspaces SET project_name = %s WHERE id = %s",
                            (f"{label}-w{row['id']}"[:120], row["id"]),
                        )
                    cur = await conn.execute(
                        "SELECT * FROM workspaces WHERE site = %s AND user_id = %s"
                        " AND instance_id IS NOT DISTINCT FROM %s AND lab_key = %s AND library_lab_id IS NOT DISTINCT FROM %s FOR UPDATE",
                        (site, user_id, instance_id, lab_key, library_lab_id),
                    )
                    ws = await cur.fetchone()
                    snapshot = ws["resource_snapshot"] or resources
                    ticket = ws["ticket"]
                    if ticket is None and ticket_factory is not None:
                        ticket = ticket_factory(str(ws["project_id"]), ws["project_name"])
                    await conn.execute("UPDATE workspaces SET resource_snapshot = %s, ticket = %s WHERE id = %s",
                                       (Jsonb(snapshot) if snapshot else None,
                                        Jsonb(ticket) if ticket else None, ws["id"]))
                    sid = new_session_id()
                    project_id, name = ws["project_id"], ws["project_name"]
                    await conn.execute(
                        "INSERT INTO sessions (id, site, user_id, instance_id, course_id, lab_ref, lab_def,"
                        " workspace_id, student_key, project_id, project_name, resources,"
                        " status, stage, status_message, max_duration, mode, library_lab_id)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued', 'wait_slot', %s, %s, %s, %s)",
                        (sid, site, user_id, instance_id, course_id, lab_ref, lab_def, ws["id"], student_key,
                         project_id, name, Jsonb(snapshot) if snapshot else None,
                         "Ожидание свободного слота…", max_duration, mode, library_lab_id),
                    )
                    await self._event(conn, sid, None, "session_created", {"lab_ref": lab_ref, "lab_def": lab_def})
                return await self.session(sid), True
            except psycopg.errors.UniqueViolation:
                continue  # A concurrent POST won; return its session
        raise RuntimeError("Could not create or find the session")

    async def live_session(self, site, user_id, *, library_lab_id=None):
        """A student has at most one live lab on a site, in any activity."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM sessions WHERE site = %s AND user_id = %s"
                " AND library_lab_id IS NOT DISTINCT FROM %s AND status NOT IN ('stopped', 'failed')",
                (site, user_id, library_lab_id),
            )
            return await cur.fetchone()

    async def latest_session(self, site, user_id, instance_id):
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM sessions WHERE site = %s AND user_id = %s AND instance_id = %s"
                " ORDER BY (status NOT IN ('stopped', 'failed')) DESC, created_at DESC LIMIT 1",
                (site, user_id, instance_id),
            )
            return await cur.fetchone()

    async def activity_sessions(self, site, instance_id):
        """Per student of an activity: the live session, otherwise the latest finished one."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT ON (user_id) * FROM sessions WHERE site = %s AND instance_id = %s"
                " ORDER BY user_id, (status NOT IN ('stopped', 'failed')) DESC, created_at DESC",
                (site, instance_id),
            )
            return await cur.fetchall()

    async def session(self, sid, site=None):
        async with self.pool.connection() as conn:
            if site is None:
                cur = await conn.execute("SELECT * FROM sessions WHERE id = %s", (sid,))
            else:
                cur = await conn.execute("SELECT * FROM sessions WHERE id = %s AND site = %s", (sid, site))
            return await cur.fetchone()

    async def workspace(self, wid):
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM workspaces WHERE id = %s", (wid,))
            return await cur.fetchone()

    async def live_sessions(self):
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM sessions WHERE status NOT IN ('stopped', 'failed') ORDER BY created_at"
            )
            return await cur.fetchall()

    async def request_stop(self, sid, site, reason):
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "UPDATE sessions SET stop_requested = true, stop_reason = coalesce(stop_reason, %s),"
                " updated_at = now() WHERE id = %s AND site = %s"
                " AND status NOT IN ('stopped', 'failed') RETURNING id",
                (reason, sid, site),
            )
            if await cur.fetchone():
                await self._event(conn, sid, None, "stop_requested", {"reason": reason})

    # ------------------------------------------------------------------ allocation
    async def allocate_slot(self, session, candidates):
        """Give a free slot to a queued session. Returns the slot vmid or None.

        candidates: [(vmid, node), ...] in preference order (placement decides; any slot can run
        any lab). SKIP LOCKED lets concurrent allocations pick different slots without waiting.
        """
        if not candidates:
            return None
        vmids = [v for v, _ in candidates]
        nodes = dict(candidates)
        async with self.pool.connection() as conn, conn.transaction():
            await self._check_epoch(conn)
            cur = await conn.execute(
                "SELECT id FROM sessions WHERE id = %s AND status = 'queued' FOR UPDATE", (session["id"],)
            )
            if not await cur.fetchone():
                return None
            cur = await conn.execute(
                "SELECT vmid FROM slots WHERE vmid = ANY(%s) AND state = 'free' AND in_config"
                " ORDER BY array_position(%s, vmid) LIMIT 1 FOR UPDATE SKIP LOCKED",
                (vmids, vmids),
            )
            row = await cur.fetchone()
            if not row:
                return None
            vmid = row["vmid"]
            cur = await conn.execute(
                "UPDATE slots SET state = 'preparing', generation = generation + 1, session_id = %s,"
                " state_message = '', updated_at = now() WHERE vmid = %s RETURNING generation",
                (session["id"], vmid),
            )
            generation = (await cur.fetchone())["generation"]
            await conn.execute(
                "UPDATE sessions SET status = 'starting', stage = 'ct_prepare', slot_vmid = %s,"
                " slot_generation = %s,"
                " slot_node = %s, status_message = %s, stage_started_at = now(),"
                " updated_at = now() WHERE id = %s",
                (vmid, generation, nodes[vmid], "Подготовка слота…", session["id"]),
            )
            await self._event(conn, session["id"], vmid, "slot_allocated",
                              {"generation": generation, "node": nodes[vmid]})
            return vmid

    async def usable_slots(self):
        """Slots that can be handed out now or later without the operator (П8 of the plugin)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT vmid FROM slots WHERE in_config AND state NOT IN ('failed', 'maintenance')"
            )
            return [r["vmid"] for r in await cur.fetchall()]

    async def slot_holding_student(self, student_key):
        """A slot whose CT may still have this student's directory mounted read-write."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT vmid FROM slots WHERE student_key = %s ORDER BY vmid LIMIT 1", (student_key,)
            )
            row = await cur.fetchone()
            return row["vmid"] if row else None

    async def set_slot_student(self, session, student_key):
        """Fenced: record (or clear, with None) the student mounted in the session's slot."""
        async with self.pool.connection() as conn, conn.transaction():
            await self._check_epoch(conn)
            cur = await conn.execute(
                "UPDATE slots SET student_key = %s, updated_at = now() WHERE vmid = %s AND generation = %s"
                " RETURNING vmid",
                (student_key, session["slot_vmid"], session["slot_generation"]),
            )
            if not await cur.fetchone():
                raise Fenced(f"Slot {session['slot_vmid']} generation changed")

    async def free_slots(self):
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT vmid FROM slots WHERE state = 'free' AND in_config ORDER BY vmid"
            )
            return [r["vmid"] for r in await cur.fetchall()]

    async def reservations(self):
        """Live sessions holding a slot: their memory may not show in node usage yet."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT s.slot_vmid, s.slot_node, s.resources FROM sessions s"
                " JOIN slots t ON t.session_id = s.id"
                " WHERE t.state <> 'free'"
            )
            return await cur.fetchall()

    async def queued_message(self, sid, message):
        async with self.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "UPDATE sessions SET status_message = %s, updated_at = now()"
                " WHERE id = %s AND status = 'queued' AND status_message <> %s",
                (message, sid, message),
            )

    async def update_session(self, session, *, slot_state=None, slot_message=None, event=None,
                             new_stage=False, **fields):
        """Fenced update of a session (and optionally its slot). Raises Fenced if stale."""
        unknown = set(fields) - SESSION_FIELDS
        if unknown:
            raise ValueError(f"Unknown session fields: {unknown}")
        values = _jsonable(fields)
        sets = [f"{k} = %({k})s" for k in values] + ["updated_at = now()"]
        if new_stage:
            sets.append("stage_started_at = now()")
        values.update(sid=session["id"], gen=session["slot_generation"], vmid=session["slot_vmid"])
        async with self.pool.connection() as conn, conn.transaction():
            await self._check_epoch(conn)
            if session["slot_vmid"] is not None:
                cur = await conn.execute(
                    "SELECT generation FROM slots WHERE vmid = %s FOR UPDATE", (session["slot_vmid"],)
                )
                row = await cur.fetchone()
                if not row or row["generation"] != session["slot_generation"]:
                    raise Fenced(f"Slot {session['slot_vmid']} generation changed")
            cur = await conn.execute(
                f"UPDATE sessions SET {', '.join(sets)} WHERE id = %(sid)s"
                " AND slot_generation IS NOT DISTINCT FROM %(gen)s"
                " AND status NOT IN ('stopped', 'failed') RETURNING *",
                values,
            )
            row = await cur.fetchone()
            if not row:
                raise Fenced("Session already finished or re-fenced")
            if slot_state is not None or slot_message is not None:
                await conn.execute(
                    "UPDATE slots SET state = coalesce(%s, state), state_message = coalesce(%s, state_message),"
                    " updated_at = now() WHERE vmid = %s",
                    (slot_state, slot_message, session["slot_vmid"]),
                )
            if event:
                await self._event(conn, session["id"], session["slot_vmid"], event[0], event[1])
            return row

    async def finish_session(self, session, *, status, message, error=None, slot_state="free",
                             slot_message="", lab_failures=None):
        """Terminal transition. slot_state='free' releases the slot (generation bump).

        lab_failures: the slot's new list of students whose labs failed in a row; None leaves it."""
        async with self.pool.connection() as conn, conn.transaction():
            await self._check_epoch(conn)
            vmid, gen = session["slot_vmid"], session["slot_generation"]
            if vmid is not None:
                cur = await conn.execute("SELECT generation FROM slots WHERE vmid = %s FOR UPDATE", (vmid,))
                row = await cur.fetchone()
                if not row or row["generation"] != gen:
                    raise Fenced(f"Slot {vmid} generation changed")
            cur = await conn.execute(
                "UPDATE sessions SET status = %s, status_message = %s, error = %s, stage = %s,"
                " pve_upid = NULL, finished_at = now(), updated_at = now(), stage_started_at = now()"
                " WHERE id = %s AND status NOT IN ('stopped', 'failed') RETURNING *",
                (status, message, Jsonb(error) if error else None, status, session["id"]),
            )
            row = await cur.fetchone()
            if not row:
                raise Fenced("Session already finished")
            if vmid is not None:
                failures = Jsonb(lab_failures) if lab_failures is not None else None
                if slot_state == "free":
                    await conn.execute(
                        "UPDATE slots SET state = 'free', generation = generation + 1, session_id = NULL,"
                        " state_message = '', lab_failures = coalesce(%s, lab_failures), updated_at = now()"
                        " WHERE vmid = %s",
                        (failures, vmid),
                    )
                else:
                    # failed/maintenance keep ownership visible for the operator.
                    await conn.execute(
                        "UPDATE slots SET state = %s, state_message = %s, lab_failures = coalesce(%s, lab_failures),"
                        " updated_at = now() WHERE vmid = %s",
                        (slot_state, slot_message[:500], failures, vmid),
                    )
            await self._event(conn, session["id"], vmid, "session_" + status,
                              {"message": message, "error": error} if error else {"message": message})
            return row

    async def mark_deployed(self, workspace_id, ticket):
        async with self.pool.connection() as conn, conn.transaction():
            await self._check_epoch(conn)
            await conn.execute(
                "UPDATE workspaces SET deployed = true, ticket = coalesce(ticket, %s) WHERE id = %s",
                (Jsonb(ticket) if ticket else None, workspace_id),
            )

    # ------------------------------------------------------------------ slot calls
    async def current_call(self, session, actions):
        """The latest non-abandoned call for these actions in this slot generation."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM slot_calls WHERE session_id = %s AND action = ANY(%s) AND slot_generation = %s"
                " AND status <> 'abandoned' ORDER BY id DESC LIMIT 1",
                (session["id"], list(actions), session["slot_generation"]),
            )
            return await cur.fetchone()

    async def new_call(self, session, action, body):
        key = f"lm-{session['id']}-{action}-{secrets.token_hex(6)}"
        async with self.pool.connection() as conn, conn.transaction():
            await self._check_epoch(conn)
            cur = await conn.execute(
                "INSERT INTO slot_calls (session_id, slot_vmid, slot_generation, action, idempotency_key, body)"
                " VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
                (session["id"], session["slot_vmid"], session["slot_generation"], action, key,
                 Jsonb(body) if body is not None else None),
            )
            return await cur.fetchone()

    async def update_call(self, call_id, *, status, operation_id=None, result=None):
        async with self.pool.connection() as conn, conn.transaction():
            await self._check_epoch(conn)
            await conn.execute(
                "UPDATE slot_calls SET status = %s, operation_id = coalesce(%s, operation_id),"
                " result = coalesce(%s, result), updated_at = now() WHERE id = %s",
                (status, operation_id, Jsonb(result) if result is not None else None, call_id),
            )

    # ------------------------------------------------------------------ facts (DESIGN 6.7)
    async def facts_rows(self, sid):
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM session_facts WHERE session_id = %s", (sid,))
            return await cur.fetchall()

    async def record_facts(self, sid, records):
        """Store what the slot reported. records: facts.parse_slot_facts().

        The latest result of every node and collector is always updated. A journal row is added only
        when something changed (new data or schema, a collection that started or stopped failing), and
        then the session's facts_rev grows once for the whole read. Returns the rev, None if the session
        is gone. Raises Fenced if the manager is no longer the leader.
        """
        async with self.pool.connection() as conn, conn.transaction():
            await self._check_epoch(conn)
            cur = await conn.execute("SELECT facts_rev FROM sessions WHERE id = %s FOR UPDATE", (sid,))
            session = await cur.fetchone()
            if session is None:
                return None
            cur = await conn.execute("SELECT * FROM session_facts WHERE session_id = %s", (sid,))
            known = {(r["node_id"], r["collector"]): r for r in await cur.fetchall()}
            rev, changed = session["facts_rev"], False
            for node_id, node_name, collector, record in records:
                old = known.get((node_id, collector))
                fresh = old is None or differs(old, record)
                if fresh and not changed:
                    rev, changed = rev + 1, True
                values = dict(
                    sid=sid, node_id=node_id, node_name=node_name, collector=collector, schema=record["schema"],
                    observed_at=record["observed_at"], attempted_at=record["attempted_at"], ok=record["ok"],
                    error=Jsonb(record["error"]) if record["error"] is not None else None,
                    data=Jsonb(record["data"]) if record["data"] is not None else None,
                )
                await conn.execute(
                    "INSERT INTO session_facts (session_id, node_id, node_name, collector, schema, observed_at,"
                    " attempted_at, ok, error, data) VALUES (%(sid)s, %(node_id)s, %(node_name)s, %(collector)s,"
                    " %(schema)s, %(observed_at)s, %(attempted_at)s, %(ok)s, %(error)s, %(data)s)"
                    " ON CONFLICT (session_id, node_id, collector) DO UPDATE SET node_name = excluded.node_name,"
                    " schema = excluded.schema, observed_at = excluded.observed_at,"
                    " attempted_at = excluded.attempted_at, ok = excluded.ok, error = excluded.error,"
                    " data = excluded.data",
                    values,
                )
                if fresh:
                    await conn.execute(
                        "INSERT INTO session_facts_log (session_id, rev, node_id, node_name, collector, schema,"
                        " observed_at, attempted_at, ok, error, data) VALUES (%(sid)s, %(rev)s, %(node_id)s,"
                        " %(node_name)s, %(collector)s, %(schema)s, %(observed_at)s, %(attempted_at)s, %(ok)s,"
                        " %(error)s, %(data)s)",
                        dict(values, rev=rev),
                    )
            await conn.execute(
                "UPDATE sessions SET facts_rev = %s, facts_polled_at = now() WHERE id = %s", (rev, sid)
            )
            return rev

    async def request_facts_refresh(self, sid, min_gap=10):
        """The plugin asked for a collection now. A request within min_gap seconds of the last is merged."""
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE sessions SET facts_refresh_at = now() WHERE id = %s AND (facts_refresh_at IS NULL"
                " OR facts_refresh_at < now() - make_interval(secs => %s))",
                (sid, min_gap),
            )

    # ------------------------------------------------------------------ events
    async def event(self, session_id, vmid, kind, detail=None):
        async with self.pool.connection() as conn, conn.transaction():
            await self._event(conn, session_id, vmid, kind, detail or {})

    @staticmethod
    async def _event(conn, session_id, vmid, kind, detail):
        await conn.execute(
            "INSERT INTO events (session_id, slot_vmid, kind, detail) VALUES (%s, %s, %s, %s)",
            (session_id, vmid, kind, Jsonb(json.loads(json.dumps(detail, default=str)))),
        )
