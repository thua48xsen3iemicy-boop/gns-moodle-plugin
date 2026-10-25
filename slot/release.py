"""Release identity and checksum verification; usable before importing the application."""

import argparse
import hashlib
import importlib
import json
import os
import re
import sys
from importlib.metadata import version
from pathlib import Path

VERSION = "0.7.0"
ROOT = Path(__file__).resolve().parent


def files(root):
    # Prune environments before traversal: readiness must not walk site-packages on CephFS.
    selected = []
    ignored = {"venv", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".git"}
    for directory, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if name not in ignored]
        for name in names:
            path = Path(directory) / name
            if (
                path.suffix in (".py", ".service", ".md", ".json")
                and name != "release.json"
            ) or name in ("requirements.txt", "slot.env.example"):
                selected.append(path)
    return sorted(selected)


def verify(root=None):
    root = root or ROOT
    try:
        manifest = json.loads((root / "release.json").read_text())
        expected = manifest["files"]
        required = {
            "main.py",
            "release.py",
            "run.py",
            "api/lifecycle.py",
            "api/health.py",
            "requirements.txt",
            "lab-slot.service",
        }
        if (
            not isinstance(expected, dict)
            or not required <= expected.keys()
            or not isinstance(manifest["build_id"], str)
        ):
            raise ValueError("Incomplete release manifest")
        actual = {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files(root)
        }
        if manifest["version"] != VERSION or actual != expected:
            raise ValueError("Release files or version differ from sealed manifest")
        return {"ok": True, "version": VERSION, "build_id": manifest["build_id"]}
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "ok": False,
            "code": "RELEASE_INTEGRITY_ERROR",
            "version": VERSION,
            "build_id": None,
        }


def preflight():
    result = verify()
    if not result["ok"]:
        return result
    try:
        if sys.version_info[:2] != (3, 12):
            raise RuntimeError("Python 3.12 required")
        for package, minimum, maximum in (
            ("fastapi", (0, 115), (1, 0)),
            ("uvicorn", (0, 34), (1, 0)),
            ("websockets", (13, 0), (18, 0)),  # WS console; without it uvicorn answers 404
            ("httpx", (0, 28), (1, 0)),
            ("pydantic", (2, 7), (3, 0)),
        ):
            parts = re.match(r"^(\d+)\.(\d+)", version(package))
            if not parts or not minimum <= tuple(map(int, parts.groups())) < maximum:
                raise RuntimeError("Unsupported dependency version: " + package)
        for name in (
            "main",
            "api.lifecycle",
            "api.labs",
            "api.nodes",
            "api.console",
            "api.health",
            "api.facts",
        ):
            module = importlib.import_module(name)
            if not Path(module.__file__).resolve().is_relative_to(ROOT):
                raise RuntimeError("Module loaded outside release")
        from api.lifecycle import Lifecycle

        if not callable(Lifecycle):
            raise TypeError("Lifecycle unavailable")
    except (ImportError, AttributeError, RuntimeError, SyntaxError, TypeError) as exc:
        return {
            **result,
            "ok": False,
            "code": "RELEASE_IMPORT_ERROR",
            "error_type": type(exc).__name__,
        }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seal", "check"))
    parser.add_argument("--build-id")
    args = parser.parse_args()
    if args.action == "seal":
        hashes = {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files(ROOT)
        }
        identity = (
            args.build_id
            or "sha256:"
            + hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
        )
        (ROOT / "release.json").write_text(
            json.dumps(
                {"version": VERSION, "build_id": identity, "files": hashes}, indent=2
            )
            + "\n"
        )
    result = preflight()
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
