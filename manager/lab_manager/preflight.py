"""Checks before the service starts (ExecStartPre). Exit code != 0 keeps it from starting."""

import json
import os
import sys

import psycopg

from . import config
from .db import latest_version


def run(env=None) -> dict:
    checks = {}
    try:
        settings = config.load(env)
        checks["config"] = {"ok": True}
    except config.ConfigError as exc:
        return {"ok": False, "checks": {"config": {"ok": False, "error": str(exc)}}}

    checks["pve_ca"] = {"ok": os.access(settings.pve_ca_file, os.R_OK)}
    checks["labs"] = {"ok": settings.labs_dir.is_dir() and os.access(settings.labs_dir, os.R_OK | os.X_OK)}
    if settings.student_root is not None:
        checks["students"] = {"ok": settings.student_root.is_dir()
                              and os.access(settings.student_root, os.W_OK | os.X_OK)}
    if settings.slot_mount_root is not None:
        ok = settings.slot_mount_root.is_dir() and os.access(settings.slot_mount_root, os.W_OK | os.X_OK)
        same_fs = ok and settings.student_root is not None and (
            os.stat(settings.slot_mount_root).st_dev == os.stat(settings.student_root).st_dev)
        # rename(2) between the two trees only works inside one mount (otherwise EXDEV).
        checks["slot_mounts"] = {"ok": bool(ok and same_fs), "same_filesystem": bool(same_fs)}
    try:
        with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
            exists = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL").fetchone()[0]
            version = conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] if exists else 0
        checks["database"] = {"ok": version == latest_version(), "schema": version,
                              "expected": latest_version()}
    except psycopg.Error as exc:
        checks["database"] = {"ok": False, "error": type(exc).__name__}
    return {"ok": all(c["ok"] for c in checks.values()), "checks": checks}


def main():
    result = run()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
