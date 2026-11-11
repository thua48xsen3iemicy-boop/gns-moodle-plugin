"""Maintenance mode (CONTRACTS §2.11): announce, stop every lab at a deadline, then let the operator work.

Phases, derived from the single row of the `maintenance` table and the live work:
  off       - no row; normal operation;
  announced - before starts_at: new sessions are refused, running labs keep going and see the deadline;
  draining  - starts_at passed: every live session is asked to stop (reason `maintenance`); image captures
              already running are finished, not cut;
  active    - nothing runs any more: no live session, no running capture. The operator may work.
"""

import re
from datetime import datetime, timedelta, timezone

from .store import Store

PHASES = ("off", "announced", "draining", "active")
STOP_MESSAGE = "Лаба остановлена на время технического обслуживания"
CANCEL_MESSAGE = "Запуск отменён: техническое обслуживание"
STOPPING_MESSAGE = "Техническое обслуживание, остановка лабы…"
MAX_MESSAGE = 500
_DELAY = re.compile(r"^(\d+)([smh]?)$")


def now():
    return datetime.now(timezone.utc)


def parse_delay(text: str) -> timedelta:
    """`5m`, `90s`, `1h`, `0` (now); a bare number means minutes."""
    m = _DELAY.match((text or "").strip().lower())
    if not m:
        raise ValueError(f"Invalid delay {text!r}: use e.g. 5m, 90s, 1h or 0")
    value, unit = int(m.group(1)), m.group(2) or "m"
    delay = timedelta(seconds=value * {"s": 1, "m": 60, "h": 3600}[unit])
    if delay > timedelta(days=7):
        raise ValueError("Delay longer than 7 days")
    return delay


class Maintenance:
    def __init__(self, pool):
        self.pool = pool

    async def row(self, conn=None):
        if conn is None:
            async with self.pool.connection() as conn:
                return await self.row(conn)
        cur = await conn.execute("SELECT starts_at, announced_at, message FROM maintenance")
        return await cur.fetchone()

    async def switch_on(self, delay: timedelta, message: str = ""):
        """Announce (or re-announce) the maintenance. Returns the stored row."""
        message = (message or "").strip()[:MAX_MESSAGE]
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "INSERT INTO maintenance (id, starts_at, announced_at, message) VALUES (true, %s, now(), %s)"
                " ON CONFLICT (id) DO UPDATE SET starts_at = EXCLUDED.starts_at, message = EXCLUDED.message"
                " RETURNING starts_at, announced_at, message",
                (now() + delay, message))
            row = await cur.fetchone()
            await Store._event(conn, None, None, "maintenance_on",
                               {"starts_at": iso(row["starts_at"]), "message": message})
            return row

    async def switch_off(self) -> bool:
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute("DELETE FROM maintenance RETURNING starts_at")
            removed = await cur.fetchone() is not None
            if removed:
                await Store._event(conn, None, None, "maintenance_off", {})
            return removed

    async def busy(self, conn):
        """What still runs: live sessions and running image captures."""
        cur = await conn.execute(
            "SELECT id, site, user_id, status, stage, mode, slot_vmid FROM sessions"
            " WHERE status NOT IN ('stopped', 'failed') ORDER BY created_at")
        sessions = await cur.fetchall()
        cur = await conn.execute("SELECT session_id, phase FROM image_captures WHERE state = 'running'")
        captures = await cur.fetchall()
        return sessions, captures

    async def state(self):
        """The public object (CONTRACTS §2.11) plus what still runs, for the operator."""
        async with self.pool.connection() as conn:
            row = await self.row(conn)
            if row is None:
                return {"state": "off"}, [], []
            sessions, captures = await self.busy(conn)
        if now() < row["starts_at"]:
            phase = "announced"
        else:
            phase = "draining" if sessions or captures else "active"
        return public(row, phase), sessions, captures

    async def public_state(self):
        return (await self.state())[0]

    async def stop_sessions(self, *, queued_only=False):
        """Ask live sessions to stop with reason `maintenance`. Returns their ids.

        queued_only: only sessions still waiting for a slot (no lab to lose), used while announced.
        """
        where = " AND stage = 'wait_slot'" if queued_only else ""
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "UPDATE sessions SET stop_requested = true, stop_reason = coalesce(stop_reason, 'maintenance'),"
                " updated_at = now() WHERE status NOT IN ('stopped', 'failed') AND NOT stop_requested" + where +
                " RETURNING id, slot_vmid")
            rows = await cur.fetchall()
            for r in rows:
                await Store._event(conn, r["id"], r["slot_vmid"], "stop_requested", {"reason": "maintenance"})
            return [r["id"] for r in rows]


def iso(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def public(row, phase):
    return {"state": phase, "starts_at": iso(row["starts_at"]), "announced_at": iso(row["announced_at"]),
            "message": row["message"]}
