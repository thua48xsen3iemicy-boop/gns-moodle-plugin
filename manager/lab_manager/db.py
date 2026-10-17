"""PostgreSQL access: a small async pool and numbered SQL migrations.

Migrations are plain files migrations/NNN_name.sql, applied in order inside one
transaction each and recorded in schema_migrations. The service never migrates by itself:
an administrator runs `python -m lab_manager.admin migrate`, startup only checks the version.
"""

import re
from importlib import resources

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

MIGRATION_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
LOCK_KEY = 0x4C61624D  # "LabM": advisory lock id for migrations and the orchestrator leader


def migrations():
    files = []
    for entry in resources.files("lab_manager.migrations").iterdir():
        m = MIGRATION_RE.match(entry.name)
        if m:
            files.append((int(m.group(1)), entry.name, entry.read_text()))
    files.sort()
    if [v for v, _, _ in files] != list(range(1, len(files) + 1)):
        raise RuntimeError("Migrations must be numbered 001, 002, ... without gaps")
    return files


def latest_version() -> int:
    return migrations()[-1][0]


def migrate(dsn: str) -> list[str]:
    """Apply missing migrations. Safe to run concurrently (advisory lock)."""
    applied = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY + 1,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                " version integer PRIMARY KEY, name text NOT NULL,"
                " applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
            for version, name, sql in migrations():
                if version in done:
                    continue
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                        (version, name),
                    )
                applied.append(name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY + 1,))
    return applied


async def schema_version(conn) -> int:
    cur = await conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL AS ok")
    if not (await cur.fetchone())["ok"]:
        return 0
    cur = await conn.execute("SELECT coalesce(max(version), 0) AS v FROM schema_migrations")
    return (await cur.fetchone())["v"]


def make_pool(dsn: str, *, min_size=1, max_size=10) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        kwargs={"row_factory": dict_row, "autocommit": False},
        open=False,
        timeout=10,
    )
