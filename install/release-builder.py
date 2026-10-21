#!/usr/bin/env python3
"""Trusted helper, copied into a disposable LXC. Never run by verify/list.

All product imports occur here in the builder, never on the Proxmox host.
The host checks this helper's digest and records its Git identity in the release.
"""

import argparse
import email
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile


def run(args):
    # No caller environment, user site, PYTHONPATH, production credentials or pip config.
    result = subprocess.run(args, check=True, capture_output=True, timeout=1200,
                            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                 "PYTHONDONTWRITEBYTECODE": "1", "PIP_CONFIG_FILE": "/dev/null",
                                 "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_CACHE_DIR": "1"})
    return result.stdout.decode().strip()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def runtime():
    os_release = platform.freedesktop_os_release()
    packages = run(["/usr/bin/dpkg-query", "-W", "-f=${binary:Package}=${Version}\n"])
    return {"os_id": os_release["ID"], "os_version": os_release["VERSION_ID"],
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "python_full": platform.python_version(), "architecture": platform.machine(),
            "implementation": sys.implementation.name, "cache_tag": sys.implementation.cache_tag,
            "soabi": sysconfig.get_config_var("SOABI"), "executable": str(Path(sys.executable).resolve()),
            "executable_sha256": sha(Path(sys.executable).resolve()),
            "packages_sha256": hashlib.sha256(("\n".join(sorted(packages.splitlines())) + "\n").encode()).hexdigest()}


def normal(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def validate_requirements(path, constraints=False):
    # Requirements from Git are data: no URLs, credentials, nested -r, editable installs or pip flags.
    pattern = (r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]+" if constraints else
               r"[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?(?:[<>=!~]+[A-Za-z0-9_.+*-]+)(?:,[<>=!~]+[A-Za-z0-9_.+*-]+)*")
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not re.fullmatch(pattern, line):
            raise ValueError("Unsupported requirement syntax")


def build(component, code, output, inputs):
    code, output, inputs = Path(code), Path(output), Path(inputs)
    expected_path = "/opt/lab-node-agent" if component == "agent" else "/srv/gns3-ro/" + component
    if str(code) != expected_path or Path(sys.executable).resolve().parent != Path("/usr/bin"):
        raise ValueError("Unexpected build path/interpreter")
    if list(output.iterdir()) or list((code / "venv").iterdir()):
        raise ValueError("Refusing to reuse dependencies/venv")
    before = runtime()
    constraints = inputs / "constraints-tested.txt"
    reqs = [code / "requirements.txt"]
    if component == "manager":
        reqs.append(code / "gateway/requirements.txt")
    for path in reqs:
        validate_requirements(path)
    validate_requirements(constraints, True)
    work = Path(tempfile.mkdtemp(prefix="lab-release-resolver-"))
    try:
        resolver = work / "venv"
        run([sys.executable, "-I", "-B", "-m", "venv", str(resolver)])
        wheels = output / "wheels"
        wheels.mkdir()
        args = [str(resolver / "bin/python"), "-I", "-B", "-m", "pip", "download",
                "--only-binary=:all:", "--index-url", json.loads((inputs / "index.json").read_text())["index_url"],
                "--dest", str(wheels), "-c", str(constraints)]
        for path in reqs:
            args += ["-r", str(path)]
        run(args)
        locks = {}
        for path in sorted(wheels.iterdir()):
            if not path.name.endswith(".whl") or not path.is_file() or path.is_symlink():
                raise ValueError("Only wheels allowed")
            with zipfile.ZipFile(path) as wheel:
                names = [n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")]
                if len(names) != 1 or wheel.getinfo(names[0]).file_size > 4 * 1024 * 1024:
                    raise ValueError("Invalid wheel metadata")
                metadata = email.message_from_bytes(wheel.read(names[0]))
                name, version = normal(metadata["Name"]), metadata["Version"]
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) or not re.fullmatch(r"[A-Za-z0-9_.+!-]+", version):
                    raise ValueError("Invalid distribution identity")
                if name in locks:
                    raise ValueError("Multiple wheels for distribution")
                locks[name] = (version, sha(path))
        if not locks:
            raise ValueError("Empty wheel closure")
        lock = output / "requirements.lock"
        lock.write_text("".join(f"{name}=={version} --hash=sha256:{checksum}\n"
                                for name, (version, checksum) in sorted(locks.items())))
        run([sys.executable, "-I", "-B", "-m", "venv", str(code / "venv")])
        python = str(code / "venv/bin/python")
        run([python, "-I", "-B", "-m", "pip", "install", "--no-compile", "--no-index",
             "--require-hashes", "--find-links", str(wheels), "-r", str(lock)])
        run([python, "-I", "-B", "-m", "pip", "check"])
        # Native libraries/system packages must not be installed implicitly during dependency work.
        if before != runtime():
            raise ValueError("Runtime changed during build")
        # venv seeding can contain bytecode; do not ship mutable interpreter caches.
        for directory in sorted((code / "venv").rglob("__pycache__"), reverse=True):
            shutil.rmtree(directory)
        for path in (code / "venv").rglob("*.pyc"):
            path.unlink()
        return {"ok": True, "component": component, "runtime": before}
    finally:
        shutil.rmtree(work)


SMOKE = r'''
import errno, hashlib, importlib, importlib.metadata, json, os, pathlib, sys
component, code = sys.argv[1:]
root = pathlib.Path(code)
assert str(root) == ("/opt/lab-node-agent" if component == "agent" else "/srv/gns3-ro/" + component)
assert pathlib.Path(sys.prefix) == root / "venv"
assert sys.dont_write_bytecode
sys.path.insert(0, code)
names = {"slot": ["release", "main", "api.lifecycle", "api.console"],
         "manager": ["lab_manager.app", "lab_manager.db", "lab_manager.orchestrator"],
         "agent": ["agent"]}[component]
for name in names:
    module = importlib.import_module(name)
    assert pathlib.Path(module.__file__).resolve().is_relative_to(root)
for name in {"slot": ["fastapi", "httpx", "uvicorn", "websockets", "pydantic"],
             "manager": ["fastapi", "httpx", "psycopg", "aiohttp"],
             "agent": ["fastapi", "uvicorn", "pydantic"]}[component]:
    module = importlib.import_module(name)
    assert pathlib.Path(module.__file__).resolve().is_relative_to(root / "venv")
if component == "manager":
    sys.path.insert(0, str(root / "gateway"))
    module = importlib.import_module("console_gateway")
    assert pathlib.Path(module.__file__).resolve().is_relative_to(root / "gateway")
if component == "slot":
    import release
    identity = release.preflight()
    assert identity["ok"], identity
    selected = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in release.files(root)}
else:
    identity, selected = None, None
for directory in (root, root / "venv"):
    test = directory / ".lab-release-write-test"
    try:
        test.write_bytes(b"unsafe")
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EROFS, errno.EPERM)
    else:
        test.unlink()
        raise RuntimeError("Published code/venv must be read-only")
installed = {}
for dist in importlib.metadata.distributions():
    import re
    name = re.sub(r"[-_.]+", "-", dist.metadata["Name"]).lower()
    assert name not in installed
    assert pathlib.Path(dist.locate_file("")).resolve().is_relative_to(root / "venv")
    installed[name] = dist.version
for path in (root / "venv/bin").iterdir():
    if path.is_file() and not path.is_symlink():
        first = path.open("rb").readline(4096)
        if first.startswith(b"#!") and b"python" in first:
            allowed = [root / "venv/bin" / name for name in
                       ("python", "python3", f"python3.{sys.version_info.minor}")]
            assert first.rstrip() in [("#!" + str(python)).encode() for python in allowed]
print(json.dumps({"ok": True, "component": component, "installed": installed,
                  "slot_identity": identity, "slot_files": selected}))
'''


def smoke(component, code):
    user = {"manager": "lab-manager", "slot": "gns3", "agent": "nobody"}[component]
    if pwd.getpwnam(user).pw_uid == 0:
        raise ValueError("Smoke user must be unprivileged")
    data = run(["/usr/sbin/runuser", "-u", user, "--", str(Path(code) / "venv/bin/python"),
                "-I", "-B", "-c", SMOKE, component, code])
    result = json.loads(data)
    result["runtime"] = runtime()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("runtime", "build", "smoke"))
    parser.add_argument("--component", choices=("manager", "slot", "agent"))
    args = parser.parse_args()
    if args.action == "runtime":
        result = runtime()
    else:
        if args.component is None:
            parser.error("--component required")
        code = "/opt/lab-node-agent" if args.component == "agent" else "/srv/gns3-ro/" + args.component
        result = (build(args.component, code, "/build/output", "/build/inputs")
                  if args.action == "build" else smoke(args.component, code))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Keep indexes/credentials and guest logs out of package reports.
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
