"""Deleting student work (CONTRACTS §2.10).

Moodle asks to purge the workspaces of an activity, of a user, of a library lab, or one workspace (a student's own
"Wipe"). The manager marks them, stops their live sessions, and as soon as none is live deletes them from the
database in one transaction each: sessions, slot calls, captures, facts and check runs go with them. From then on
the next start of that lab is a fresh project. The project directory on CephFS is queued in project_purges: the
manager cannot write there, the node agent deletes it (DELETE /students/{key}/projects/{id}, agent 1.4.0) once no
slot holds the student's directory. A failed attempt is retried with a growing delay; nothing is lost on a restart.
"""

import logging
import time

import psycopg

from .agent_client import AgentError

log = logging.getLogger("lab_manager.purge")

LIVE = "status NOT IN ('stopped', 'failed')"
BATCH = 50
RETRY_MIN, RETRY_MAX = 30, 3600  # Seconds between attempts to delete one project directory
FILES_INTERVAL = 30  # Seconds between two passes over the directory queue of the leader


class Purges:
    def __init__(self, services):
        self.services = services
        self.store = services.store
        self._caps = {}
        self._last_files = 0.0
        self._warned = False

    # ------------------------------------------------------------------ requests
    async def request(self, site, *, instance_id=None, user_id=None, library_lab_id=None) -> dict:
        """Mark the matching workspaces and stop their live sessions; delete at once what is not live."""
        where, args = ["site = %s"], [site]
        for column, value in (("instance_id", instance_id), ("user_id", user_id), ("library_lab_id", library_lab_id)):
            if value is not None:
                where.append(f"{column} = %s")
                args.append(value)
        async with self.store.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "UPDATE workspaces SET purge_requested_at = coalesce(purge_requested_at, now())"
                f" WHERE {' AND '.join(where)} RETURNING id", args)
            ids = [r["id"] for r in await cur.fetchall()]
            await self._stop_live(conn, ids)
        await self.collect(ids)
        return {"workspaces": len(ids), "pending": await self._remaining(ids)}

    async def reconcile(self, site, instance_ids) -> dict:
        """Safety net: workspaces of activities Moodle no longer has (a lost request, a restored Moodle database).

        Only ids up to the largest one Moodle listed are judged: Moodle never reuses an id, so an activity created
        after Moodle took its list (and a workspace started in it meanwhile) is never mistaken for a deleted one."""
        async with self.store.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "UPDATE workspaces SET purge_requested_at = coalesce(purge_requested_at, now())"
                " WHERE site = %s AND instance_id IS NOT NULL AND instance_id <= %s"
                " AND NOT (instance_id = ANY(%s)) RETURNING id",
                (site, max(instance_ids), list(instance_ids)))
            ids = [r["id"] for r in await cur.fetchall()]
            await self._stop_live(conn, ids)
        await self.collect(ids)
        return {"workspaces": len(ids), "pending": await self._remaining(ids)}

    async def _stop_live(self, conn, ids):
        if not ids:
            return
        cur = await conn.execute(
            "UPDATE sessions SET stop_requested = true, stop_reason = 'purge', updated_at = now()"
            f" WHERE workspace_id = ANY(%s) AND {LIVE} RETURNING id", (ids,))
        for row in await cur.fetchall():
            await self.store._event(conn, row["id"], None, "stop_requested", {"reason": "purge"})
        if cur.rowcount:
            self.services.orchestrator.wake()

    async def _remaining(self, ids):
        if not ids:
            return 0
        async with self.store.pool.connection() as conn:
            cur = await conn.execute("SELECT count(*) AS n FROM workspaces WHERE id = ANY(%s)", (ids,))
            return (await cur.fetchone())["n"]

    # ------------------------------------------------------------------ database
    async def collect(self, ids=None) -> int:
        """Delete marked workspaces none of whose sessions is live. Returns how many went."""
        async with self.store.pool.connection() as conn:
            if ids is None:
                cur = await conn.execute("SELECT id FROM workspaces WHERE purge_requested_at IS NOT NULL"
                                         " ORDER BY purge_requested_at LIMIT %s", (BATCH,))
                ids = [r["id"] for r in await cur.fetchall()]
        done = 0
        for wid in ids:
            try:
                done += await self._delete_workspace(wid)
            except psycopg.Error as exc:
                log.warning("Workspace %s not purged yet: %s", wid, type(exc).__name__)
        return done

    async def _delete_workspace(self, wid) -> int:
        async with self.store.pool.connection() as conn, conn.transaction():
            cur = await conn.execute("SELECT DISTINCT student_key FROM sessions WHERE workspace_id = %s ORDER BY 1",
                                     (wid,))
            keys = [r["student_key"] for r in await cur.fetchall()]
            # The same lock as create_session, taken before the row lock as there: no start races the delete.
            for key in keys:
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,))
            cur = await conn.execute("SELECT id, project_id FROM workspaces WHERE id = %s"
                                     " AND purge_requested_at IS NOT NULL FOR UPDATE", (wid,))
            ws = await cur.fetchone()
            if ws is None:
                return 0
            cur = await conn.execute(
                f"SELECT 1 FROM sessions WHERE workspace_id = %s AND {LIVE}"
                " UNION ALL SELECT 1 FROM image_captures c JOIN sessions s ON s.id = c.session_id"
                " WHERE s.workspace_id = %s AND c.state = 'running' LIMIT 1", (wid, wid))
            if await cur.fetchone():
                return 0  # The stop (or the capture) finishes first; the leader comes back
            cur = await conn.execute("SELECT DISTINCT student_key FROM sessions WHERE workspace_id = %s", (wid,))
            if sorted(r["student_key"] for r in await cur.fetchall()) != keys:
                return 0  # A session appeared between the two reads: next pass
            sids = [r["id"] for r in await (await conn.execute(
                "SELECT id FROM sessions WHERE workspace_id = %s", (wid,))).fetchall()]
            await conn.execute("DELETE FROM slot_calls WHERE session_id = ANY(%s)", (sids,))
            await conn.execute("DELETE FROM image_captures WHERE session_id = ANY(%s)", (sids,))
            await conn.execute("DELETE FROM sessions WHERE id = ANY(%s)", (sids,))  # Facts and check runs cascade
            await conn.execute("DELETE FROM workspaces WHERE id = %s", (wid,))
            for key in keys:
                await conn.execute("INSERT INTO project_purges (project_id, student_key) VALUES (%s, %s)"
                                   " ON CONFLICT DO NOTHING", (ws["project_id"], key))
            await self.store._event(conn, None, None, "workspace_purged",
                                    {"workspace_id": wid, "project_id": str(ws["project_id"]), "sessions": len(sids)})
            return 1

    # ------------------------------------------------------------------ files
    async def _capable(self, node) -> bool:
        cached = self._caps.get(node)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        body = await self.services.agent.capture_capabilities(node)
        ok = body.get("projects_delete") is True
        self._caps[node] = (time.monotonic(), ok)
        return ok

    async def _node(self):
        try:
            nodes = await self.services.pve.online_nodes()
        except Exception as exc:  # noqa: BLE001 - Proxmox unavailable: try again on the next pass
            log.warning("No node for project deletion: %s", type(exc).__name__)
            return None
        for node in nodes:
            if await self._capable(node):
                return node
        return None

    async def _postpone(self, row, error):
        delay = min(RETRY_MAX, RETRY_MIN * 2 ** min(row["attempts"], 10))
        async with self.store.pool.connection() as conn:
            await conn.execute(
                "UPDATE project_purges SET attempts = attempts + 1, last_error = %s,"
                " next_attempt_at = now() + make_interval(secs => %s) WHERE project_id = %s AND student_key = %s",
                (error[:200], delay, row["project_id"], row["student_key"]))

    async def delete_files(self) -> int:
        async with self.store.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM project_purges WHERE next_attempt_at <= now()"
                                     " ORDER BY next_attempt_at LIMIT %s", (BATCH,))
            rows = await cur.fetchall()
        if not rows:
            return 0
        node = await self._node()
        if node is None:
            if not self._warned:
                log.warning("%d project directories wait for deletion: no online node agent has projects_delete"
                            " (agent 1.4.0)", len(rows))
                self._warned = True
            for row in rows:
                await self._postpone(row, "no agent with projects_delete")
            return 0
        self._warned = False
        done = 0
        for row in rows:
            if await self.store.slot_holding_student(row["student_key"]) is not None:
                await self._postpone(row, "student directory is held by a slot")
                continue
            try:
                await self.services.agent.delete_project(node, row["student_key"], str(row["project_id"]))
            except AgentError as exc:
                log.warning("Project directory not deleted yet: %s", exc.code or exc.status or "unreachable")
                await self._postpone(row, exc.code or str(exc.status or "unreachable"))
                continue
            async with self.store.pool.connection() as conn:
                await conn.execute("DELETE FROM project_purges WHERE project_id = %s AND student_key = %s",
                                   (row["project_id"], row["student_key"]))
            done += 1
        return done

    async def step(self, *, force=False):
        """One pass of the leader: workspaces whose sessions have stopped, then due project directories."""
        await self.collect()
        if force or time.monotonic() - self._last_files >= FILES_INTERVAL:
            self._last_files = time.monotonic()
            await self.delete_files()
