#!/usr/bin/env python3
"""Prepare releases; never apply them. verify/list use only this trusted stdlib code.

Invoke with python3 -I -B, from a trusted checkout outside the examined release.
Ceph administrators/root are trusted; checksums are not publisher signatures.
"""

import argparse
import ast
import ctypes
import datetime
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import uuid

VERSION = "1.0.0"
SCHEMA = "gns3lab.release/1"
POLICY = "slot-0.5-file-selection/1"
DEFAULT_ROOT = "/mnt/pve/cfs/gns3-releases"
COMPONENTS = ("manager", "slot", "agent")
HEX = re.compile(r"[a-f0-9]{64}\Z")
COMMIT = re.compile(r"[a-f0-9]{40}\Z")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
MAX_JSON = 32 * 1024 * 1024
ADMIN_UID = ADMIN_GID = 0
IGNORED_SLOT = {"venv", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".git"}
SLOT_REQUIRED = {"main.py", "release.py", "run.py", "api/lifecycle.py", "api/health.py",
                 "requirements.txt", "lab-slot.service"}
SECRET_SUFFIXES = (".key", ".pem", ".sqlite", ".sqlite3", ".db", ".log", ".env")
ERROR_SCHEMA = "gns3lab.builder-error/1"
DIAGNOSTICS = {
    "runtime": ("RUNTIME_CHECK_FAILED", "Unable to inspect or validate the builder runtime."),
    "build": ("BUILD_PREPARATION_FAILED", "Unable to prepare isolated build inputs or artifacts."),
    "requirements": ("REQUIREMENTS_INVALID", "Source requirements or constraints are unsupported."),
    "resolver": ("RESOLVER_VENV_FAILED", "Unable to create the isolated dependency resolver."),
    "download": ("WHEEL_DOWNLOAD_FAILED", "Unable to download a compatible complete wheel set from the configured index."),
    "lock": ("WHEEL_LOCK_FAILED", "Wheel metadata or dependency lock generation failed."),
    "venv": ("TARGET_VENV_FAILED", "Unable to create the venv at the required runtime path."),
    "install": ("WHEEL_INSTALL_FAILED", "Offline installation from the hashed wheel lock failed."),
    "pip_check": ("DEPENDENCY_CHECK_FAILED", "Installed dependencies failed pip check."),
    "smoke": ("SMOKE_CHECK_FAILED", "Import, module origin, interpreter path or read-only smoke check failed."),
    "resolver_cleanup": ("RESOLVER_CLEANUP_FAILED", "Unable to remove the disposable resolver workspace."),
    "protocol": ("BUILDER_PROTOCOL_ERROR", "Builder did not return a supported bounded diagnostic."),
    "cleanup": ("BUILDER_CLEANUP_FAILED", "Unable to confirm safe destruction of disposable builder resources."),
}


class ReleaseError(Exception):
    pass


class BuilderFailure(ReleaseError):
    def __init__(self, diagnostic):
        self.diagnostic = diagnostic
        super().__init__(json.dumps(diagnostic, sort_keys=True))


def builder_diagnostic(component, stage, reason, returncode=None):
    code, detail = DIAGNOSTICS[stage]
    return {"schema": ERROR_SCHEMA, "ok": False, "component": component, "stage": stage,
            "code": code, "reason": reason, "returncode": returncode, "detail": detail}


def decode_builder_failure(stderr, context):
    """Only a small closed vocabulary crosses the guest/host boundary; never guest prose."""
    try:
        require(isinstance(stderr, (bytes, str)) and len(stderr) <= 4096, "Invalid diagnostic size")
        data = decode_json(stderr)
        require(isinstance(data, dict) and set(data) == {
            "schema", "ok", "component", "stage", "code", "reason", "returncode", "detail"}, "Invalid diagnostic fields")
        require(data["schema"] == ERROR_SCHEMA and data["ok"] is False
                and data["component"] == context["component"], "Diagnostic context mismatch")
        allowed = {"runtime"} if context["action"] == "runtime" else (
            {"runtime", "smoke"} if context["action"] == "smoke" else
            set(DIAGNOSTICS) - {"protocol", "cleanup", "smoke"})
        require(data["stage"] in allowed and data["code"] == DIAGNOSTICS[data["stage"]][0], "Invalid diagnostic code")
        require(data["reason"] in {"command_failed", "timeout", "invalid_data", "io_error", "check_failed", "unexpected_error"}
                and (data["returncode"] is None or type(data["returncode"]) is int and -255 <= data["returncode"] <= 255),
                "Invalid diagnostic reason/status")
        # Reconstruct detail locally even for well-formed input: arbitrary text can contain credentials.
        return builder_diagnostic(context["component"], data["stage"], data["reason"], data["returncode"])
    except (ReleaseError, ValueError, TypeError, KeyError, RecursionError):
        return builder_diagnostic(context["component"], "protocol", "invalid_diagnostic")


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key: " + key)
        result[key] = value
    return result


def decode_json(data):
    require(len(data) <= MAX_JSON, "JSON too large")
    try:
        return json.loads(data, object_pairs_hook=no_duplicates,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              ReleaseError("Non-finite JSON number")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ReleaseError("Invalid JSON") from exc


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def read_regular(path, limit=MAX_JSON):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        st = os.fstat(stream.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_size <= limit, "Not a bounded regular file: " + str(path))
        data = stream.read(limit + 1)
        require(len(data) <= limit, "File too large: " + str(path))
        return data


def read_json(path):
    return decode_json(read_regular(path))


def write_json(path, value):
    """New files only, including recovery records: update via replace in admin-only dirs."""
    path = Path(path)
    tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        sync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def command(args, timeout=120, binary=False, builder_context=None):
    # Even a poisoned PATH pointing into an examined release must not select its executables.
    executables = {"git": "/usr/bin/git", "findmnt": "/usr/bin/findmnt",
                   "pct": "/usr/sbin/pct", "pvesm": "/usr/sbin/pvesm"}
    require(args[0] in executables, "Untrusted host command")
    try:
        result = subprocess.run([executables[args[0]], *[str(a) for a in args[1:]]], check=True, capture_output=True,
                                timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if builder_context is not None:
            raise BuilderFailure(builder_diagnostic(builder_context["component"], builder_context["action"], "timeout")) from None
        raise ReleaseError("Command outcome uncertain (timeout): " + str(args[0])) from exc
    except subprocess.CalledProcessError as exc:
        if builder_context is not None:
            raise BuilderFailure(decode_builder_failure(exc.stderr, builder_context)) from None
        # Do not echo command arguments, environment, pip output or possible credentials.
        raise ReleaseError("Command failed: " + str(args[0]) + " exit=" + str(exc.returncode)) from exc
    return result.stdout if binary else result.stdout.decode().strip()


def no_acl(path):
    try:
        names = os.listxattr(path, follow_symlinks=False)
    except OSError as exc:
        if exc.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP):
            raise
        names = []
    require(not {"system.posix_acl_access", "system.posix_acl_default"}.intersection(names),
            "Extended POSIX ACL is unsupported: " + str(path))


def safe_directory(path):
    """Open all ancestors without following links; only administrators may rename them."""
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts and str(path) == os.path.normpath(path),
            "Expected canonical absolute directory")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in (None, *path.parts[1:]):
            if part is not None:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            st = os.fstat(fd)
            require(st.st_uid == ADMIN_UID and st.st_gid == ADMIN_GID and not st.st_mode & 0o022,
                    "Directory/ancestor must be root-owned and not group/other writable: " + str(path))
            # fd xattrs do not follow links.
            require(not any(name in os.listxattr(fd) for name in
                            ("system.posix_acl_access", "system.posix_acl_default")),
                    "Extended ACL on directory/ancestor")
    finally:
        os.close(fd)
    return path


def check_ceph_root(root, ceph_root=None):
    root = safe_directory(root)
    fs = command(["findmnt", "-T", root, "-n", "-o", "FSTYPE,TARGET"]).split()
    require(len(fs) == 2 and fs[0] in ("ceph", "fuse.ceph", "fuse.ceph-fuse"),
            "Release root is not on CephFS")
    mount = safe_directory(fs[1])
    require(root != mount and root.is_relative_to(mount), "Release root must be below CephFS mount")
    if ceph_root is not None:
        require(str(mount) == ceph_root, "Unexpected CephFS mount")
    # Release storage is a new sibling tree, never a child of known active trees.
    forbidden = {"gns3-ro", "gns3-rw", "secrets", "students", "gns3-images"}
    require(not forbidden.intersection(root.relative_to(mount).parts), "Release root overlaps working data")
    return root


def valid_id(value):
    require(isinstance(value, str) and ID.fullmatch(value), "Invalid release ID")
    return value


def relative_name(value):
    require(isinstance(value, str) and value and "\\" not in value and "\x00" not in value,
            "Invalid inventory path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(part not in (".", "..") for part in path.parts)
            and str(path) == value, "Unsafe inventory path")
    return path


def allowed_link(name, target):
    """No arbitrary traversal: only stdlib venv links at the declared guest paths."""
    parts = PurePosixPath(name).parts
    if len(parts) != 4 or parts[0] not in ("manager", "slot") or parts[1:3] != ("venv", "bin"):
        return name in ("manager/venv/lib64", "slot/venv/lib64") and target == "lib"
    return parts[3] in ("python", "python3", "python3.12") and target in (
        "python", "python3", "python3.12", "/usr/bin/python3.12") and target != parts[3]


def inventory(root, sealed=True):
    """Exact tree inventory using directory FDs; never opens a payload symlink."""
    result = {}
    root = Path(root)
    rootfd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    device = os.fstat(rootfd).st_dev

    def walk(fd, prefix):
        before = os.fstat(fd)
        if sealed:
            require(before.st_uid == ADMIN_UID and before.st_gid == ADMIN_GID and stat.S_IMODE(before.st_mode) == 0o555,
                    "Unsealed directory: " + (prefix or "."))
        require(before.st_dev == device, "Nested mount/device in release")
        no_acl(root / prefix)
        with os.scandir(fd) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            key = prefix + "/" + name if prefix else name
            relative_name(key)
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            mode = stat.S_IMODE(st.st_mode)
            require(st.st_dev == device, "Nested device: " + key)
            if sealed:
                require(st.st_uid == ADMIN_UID and st.st_gid == ADMIN_GID, "Non-root owner: " + key)
            no_acl(root / key)
            if stat.S_ISDIR(st.st_mode):
                result[key] = {"type": "directory", "mode": mode}
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    walk(child, key)
                finally:
                    os.close(child)
            elif stat.S_ISLNK(st.st_mode):
                target = os.readlink(name, dir_fd=fd)
                require(allowed_link(key, target), "Forbidden symlink: " + key)
                result[key] = {"type": "symlink", "target": target, "mode": mode}
            else:
                require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, "Special file/hardlink: " + key)
                if sealed:
                    require(mode in (0o444, 0o555), "Writable/unsafe file mode: " + key)
                filefd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(filefd, "rb") as stream:
                    initial = os.fstat(stream.fileno())
                    require((initial.st_ino, initial.st_dev, initial.st_ctime_ns) ==
                            (st.st_ino, st.st_dev, st.st_ctime_ns), "File replaced while reading: " + key)
                    sha = hashlib.file_digest(stream, "sha256").hexdigest()
                    final = os.fstat(stream.fileno())
                    require((initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns) ==
                            (final.st_size, final.st_mtime_ns, final.st_ctime_ns), "File changed while reading")
                if key != "manifest.json":
                    result[key] = {"type": "file", "mode": mode, "size": st.st_size, "sha256": sha}
        after = os.fstat(fd)
        require(before.st_mtime_ns == after.st_mtime_ns and before.st_ctime_ns == after.st_ctime_ns,
                "Directory changed while reading")

    try:
        walk(rootfd, "")
    finally:
        os.close(rootfd)
    return result


def slot_projection(entries):
    selected = {}
    for name, entry in entries.items():
        parts = PurePosixPath(name).parts
        if parts[0] != "slot" or entry["type"] != "file" or IGNORED_SLOT.intersection(parts[1:-1]):
            continue
        leaf = parts[-1]
        if (PurePosixPath(leaf).suffix in (".py", ".service", ".md", ".json") and leaf != "release.json"
                or leaf in ("requirements.txt", "slot.env.example")):
            selected["/".join(parts[1:])] = entry["sha256"]
    return selected


def slot_identity(root, entries):
    nested = read_json(Path(root) / "slot/release.json")
    require(isinstance(nested, dict) and set(nested) == {"version", "build_id", "files"},
            "Unsupported Slot manifest")
    require(nested["version"] == "0.13.0" and isinstance(nested["build_id"], str)
            and nested["build_id"] and isinstance(nested["files"], dict)
            and SLOT_REQUIRED <= nested["files"].keys(), "Incomplete Slot identity")
    require(nested["files"] == slot_projection(entries), "Nested Slot manifest disagrees with payload")
    return {"version": nested["version"], "build_id": nested["build_id"]}


def source_facts(root):
    """Parse literals as data; never import release modules, including release.py."""
    root = Path(root)
    def literal_version(path, fastapi=False):
        tree = ast.parse(read_regular(root / path, 8 * 1024 * 1024))
        values = []
        for node in ast.walk(tree):
            if not fastapi and isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "VERSION" for t in node.targets):
                if isinstance(node.value, ast.Constant):
                    values.append(node.value.value)
            if fastapi and isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "FastAPI":
                values.extend(k.value.value for k in node.keywords if k.arg == "version"
                              and isinstance(k.value, ast.Constant))
        require(len(values) == 1 and isinstance(values[0], str) and re.fullmatch(r"\d+\.\d+\.\d+", values[0]),
                "Missing literal component version: " + path)
        return values[0]
    versions = {"manager": literal_version("manager/lab_manager/__init__.py"),
                "slot": literal_version("slot/release.py"), "agent": literal_version("agent/agent.py", True)}
    sql = sorted((root / "manager/lab_manager/migrations").glob("*.sql"))
    require(sql and all(re.fullmatch(r"\d{3}_[a-z0-9_]+\.sql", p.name) for p in sql)
            and [p.name[:3] for p in sql] == [f"{i:03}" for i in range(1, len(sql) + 1)],
            "Non-consecutive SQL migrations")
    migrations = {p.name: digest(read_regular(p)) for p in sql}
    return {"versions": versions, "postgresql": {"minimum": len(sql), "maximum": len(sql),
                                               "migrations": migrations}}


def dependency_facts(root, entries):
    result = {}
    lock_re = re.compile(r"([A-Za-z0-9_.-]+)==([^\s]+) --hash=sha256:([a-f0-9]{64})\Z")
    for component in COMPONENTS:
        prefix = "dependencies/" + component + "/"
        lock = prefix + "requirements.lock"
        require(lock in entries and entries[lock]["type"] == "file", "Missing lock: " + component)
        lines = read_regular(Path(root) / lock).decode().splitlines()
        parsed = [lock_re.fullmatch(line) for line in lines]
        require(parsed and all(parsed), "Invalid dependency lock")
        names = [re.sub(r"[-_.]+", "-", m[1]).lower() for m in parsed]
        require(len(names) == len(set(names)), "Duplicate lock distribution")
        wheels = {name: entry["sha256"] for name, entry in entries.items()
                  if name.startswith(prefix + "wheels/") and entry["type"] == "file"}
        require(len(wheels) == len(parsed) and all(name.endswith(".whl") for name in wheels),
                "Expected exactly one wheel for each locked distribution")
        require(sorted(wheels.values()) == sorted(m[3] for m in parsed), "Lock/wheel hashes disagree")
        report = read_json(Path(root) / ("verification/" + component + ".json"))
        require(isinstance(report, dict) and report.get("ok") is True and report.get("component") == component,
                "Missing build verification report")
        validate_recorded_runtime(report["runtime"], component)
        require(isinstance(report.get("installed"), dict), "Missing installed distribution inventory")
        expected = dict(zip(names, [m[2] for m in parsed]))
        require(all(report.get("installed", {}).get(name) == version for name, version in expected.items()),
                "Installed distributions disagree with lock")
        require(set(report["installed"]) <= set(expected) | {"pip", "setuptools", "wheel"},
                "Unaccounted installed distribution")
        result[component] = {"lock_sha256": entries[lock]["sha256"], "wheels": wheels,
                             "runtime": report["runtime"], "installed": report["installed"],
                             "code_path": "/opt/lab-node-agent" if component == "agent" else "/srv/gns3-ro/" + component,
                             "venv_shipped": component != "agent"}
    return result


def validate_recorded_runtime(value, component):
    fields = {"os_id", "os_version", "python", "python_full", "architecture", "implementation",
              "cache_tag", "soabi", "executable", "executable_sha256", "packages_sha256"}
    require(isinstance(value, dict) and set(value) == fields
            and all(isinstance(v, str) and v for v in value.values()), "Incomplete runtime report")
    require(re.fullmatch(r"3\.\d+", value["python"]) and value["python_full"].startswith(value["python"] + ".")
            and value["implementation"] == "cpython" and value["executable"] == "/usr/bin/python" + value["python"]
            and HEX.fullmatch(value["executable_sha256"]) and HEX.fullmatch(value["packages_sha256"]),
            "Invalid recorded interpreter profile")
    if component != "agent":
        require((value["os_id"], value["os_version"], value["python"]) == ("ubuntu", "24.04", "3.12"),
                "Unsupported Manager/Slot runtime")
    else:
        require(value["os_id"] == "debian" and int(value["python"].split(".")[1]) >= 10,
                "Unsupported Agent runtime")


def verify_package(root, expected_id=None):
    """Read-only verification. All executable policy belongs to this file, not payload."""
    root = Path(root)
    manifest_data = read_regular(root / "manifest.json")
    manifest = decode_json(manifest_data)
    require(isinstance(manifest, dict) and set(manifest) == {
        "schema", "release_id", "created_utc", "source", "builder", "slot_policy", "slot_identity",
        "facts", "compatibility", "dependencies", "files"}, "Unsupported release manifest fields")
    require(manifest["schema"] == SCHEMA and manifest["slot_policy"] == POLICY, "Unsupported manifest/policy")
    valid_id(manifest["release_id"])
    require(expected_id is None or manifest["release_id"] == expected_id, "Release ID mismatch")
    source, builder = manifest["source"], manifest["builder"]
    require(isinstance(manifest["created_utc"], str)
            and datetime.datetime.fromisoformat(manifest["created_utc"]).utcoffset() == datetime.timedelta(0),
            "UTC build time required")
    require(isinstance(source, dict) and set(source) == {"git_sha", "tree_sha", "files"}, "Invalid source identity")
    require(isinstance(builder, dict) and set(builder) == {"version", "git_sha", "files", "templates"},
            "Incomplete builder identity")
    require(COMMIT.fullmatch(source["git_sha"]) and COMMIT.fullmatch(source["tree_sha"]), "Invalid source SHA")
    require(COMMIT.fullmatch(builder["git_sha"]) and builder["version"] == VERSION
            and set(builder["files"]) == {"install/lab-release.py", "install/release-builder.py"}
            and all(HEX.fullmatch(value) for value in builder["files"].values()), "Invalid builder identity")
    require(isinstance(builder["templates"], dict) and set(builder["templates"]) == {"ubuntu", "agent"},
            "Missing clean template provenance")
    for template in builder["templates"].values():
        require(isinstance(template, dict) and set(template) == {
            "template_vmid", "config_sha256", "image_sha256", "image_provenance"}
            and type(template["template_vmid"]) is int and template["template_vmid"] >= 100
            and HEX.fullmatch(template["config_sha256"]) and HEX.fullmatch(template["image_sha256"])
            and template["image_provenance"] == "maintainer_attested", "Invalid template provenance")
    entries = inventory(root)
    require(isinstance(manifest["files"], dict), "Invalid file inventory")
    for name in manifest["files"]:
        relative_name(name)
    require(entries == manifest["files"], "Release tree changed: added, removed, modified or unsafe files")
    require(slot_identity(root, entries) == manifest["slot_identity"], "Slot identity mismatch")
    require(source_facts(root) == manifest["facts"], "Component/schema facts mismatch")
    require(dependency_facts(root, entries) == manifest["dependencies"], "Dependency facts mismatch")
    require(manifest["dependencies"]["manager"]["runtime"] == manifest["dependencies"]["slot"]["runtime"],
            "Manager/Slot builder runtimes differ")
    validate_compatibility(manifest["compatibility"], manifest["facts"])
    require(isinstance(source["files"], dict) and set(source["files"]) == set(SOURCE_FILES),
            "Unsupported source file set")
    require(all(entries[name]["type"] == "file" and entries[name]["sha256"] == sha
                for name, sha in source["files"].items()), "Source file hashes disagree")
    return {"ok": True, "release_id": manifest["release_id"], "source_git_sha": source["git_sha"],
            "manifest_sha256": digest(manifest_data), "builder_git_sha": builder["git_sha"]}


def validate_compatibility(value, facts):
    require(isinstance(value, dict) and set(value) == {"components", "slot_state", "agent_state", "rollback"},
            "Explicit maintainer compatibility declarations required")
    require(value["components"] == facts["versions"], "Maintainer component versions disagree")
    for field in ("slot_state", "agent_state", "rollback"):
        require(isinstance(value[field], str) and value[field].strip() and "REPLACE" not in value[field],
                "Missing compatibility: " + field)


def rename_noreplace(source, target):
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        rename = libc.renameat2
    except AttributeError as exc:
        raise ReleaseError("renameat2 unavailable; refusing overwrite fallback") from exc
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, "NOREPLACE publish failed; no overwrite fallback", str(target))


def publish(stage, root, release_id):
    verify_package(stage, release_id)
    sync_dir(stage)
    rename_noreplace(stage, Path(root) / release_id)
    # Failure here has an uncertain durable outcome. Never roll back an existing final.
    sync_dir(root)
    if Path(stage).parent != Path(root):
        sync_dir(Path(stage).parent)


def seal_tree(root):
    """Only called on the new, detached staging tree, never an existing release."""
    for directory, dirs, files in os.walk(root, followlinks=False, topdown=False):
        for name in files + dirs:
            path = Path(directory) / name
            st = path.lstat()
            os.chown(path, 0, 0, follow_symlinks=False)
            no_acl(path)
            if not path.is_symlink():
                os.chmod(path, 0o555 if path.is_dir() or st.st_mode & 0o111 else 0o444)
                if path.is_file():
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
        os.chown(directory, 0, 0)
        os.chmod(directory, 0o555)
        sync_dir(directory)


# Exact source allowlist is deliberately versioned with the trusted builder.
SOURCE_FILES = (
    'agent/AGENTS.md',
    'agent/CLAUDE.md',
    'agent/README.md',
    'agent/agent.py',
    'agent/docs/MANAGER_CONTRACT_REVIEW.md',
    'agent/docs/MANAGER_REVIEW_FOLLOWUP.md',
    'agent/guest/lab-slot-init',
    'agent/install-node.sh',
    'agent/lab-node-agent.service',
    'agent/make-demo-pki.sh',
    'agent/manager-example.py',
    'agent/requirements-test.txt',
    'agent/requirements.txt',
    'agent/tests/test_agent.py',
    'agent/tests/test_capture.py',
    'agent/tests/test_fencing.py',
    'agent/tests/test_guest_init.py',
    'agent/tests/test_tls.py',
    'constraints-tested.txt',
    'manager/AGENTS.md',
    'manager/CLAUDE.md',
    'manager/README.md',
    'manager/docs/AGENT_REQUESTS.md',
    'manager/docs/AGENT_REVIEW_ANSWERS.md',
    'manager/docs/CONTRACTS.md',
    'manager/docs/DESIGN.md',
    'manager/docs/PLUGIN_REQUESTS.md',
    'manager/docs/SLOT_REQUESTS.md',
    'manager/examples/images.json',
    'manager/examples/labs/cisco-3x2/checks/README.md',
    'manager/examples/labs/cisco-3x2/lab.gns3',
    'manager/examples/labs/cisco-3x2/meta.json',
    'manager/examples/labs/cisco-3x2/ticket.json',
    'manager/gateway/console_gateway.py',
    'manager/gateway/lab-console-gateway.service',
    'manager/gateway/nginx-console.conf',
    'manager/gateway/requirements.txt',
    'manager/gateway/tests/check_gateway.py',
    'manager/lab-manager-admin',
    'manager/lab-manager.service',
    'manager/lab_manager/__init__.py',
    'manager/lab_manager/__main__.py',
    'manager/lab_manager/admin.py',
    'manager/lab_manager/agent_client.py',
    'manager/lab_manager/app.py',
    'manager/lab_manager/config.py',
    'manager/lab_manager/db.py',
    'manager/lab_manager/derived.py',
    'manager/lab_manager/errors.py',
    'manager/lab_manager/facts.py',
    'manager/lab_manager/images.py',
    'manager/lab_manager/iosconfig.py',
    'manager/lab_manager/labdef.py',
    'manager/lab_manager/labs.py',
    'manager/lab_manager/migrations/001_init.sql',
    'manager/lab_manager/migrations/002_session_progress.sql',
    'manager/lab_manager/migrations/003_slot_student.sql',
    'manager/lab_manager/migrations/004_lab_definitions.sql',
    'manager/lab_manager/migrations/005_one_project_per_workspace.sql',
    'manager/lab_manager/migrations/006_command_ownership.sql',
    'manager/lab_manager/migrations/007_slot_lab_failures.sql',
    'manager/lab_manager/migrations/008_facts.sql',
    'manager/lab_manager/migrations/009_derived_images.sql',
    'manager/lab_manager/migrations/__init__.py',
    'manager/lab_manager/orchestrator.py',
    'manager/lab_manager/netconfig.py',
    'manager/lab_manager/ownership.py',
    'manager/lab_manager/preflight.py',
    'manager/lab_manager/proxmox.py',
    'manager/lab_manager/segments.py',
    'manager/lab_manager/slot_client.py',
    'manager/lab_manager/storage.py',
    'manager/lab_manager/store.py',
    'manager/manager.env.example',
    'manager/requirements.txt',
    'manager/run.py',
    'manager/sites.json.example',
    'manager/slots.json.example',
    'manager/tests/conftest.py',
    'manager/tests/fakes.py',
    'manager/tests/test_agent_client.py',
    'manager/tests/test_api.py',
    'manager/tests/test_config.py',
    'manager/tests/test_derived.py',
    'manager/tests/test_console_gateway.py',
    'manager/tests/test_facts.py',
    'manager/tests/test_ios_config.py',
    'manager/tests/test_lab_definitions.py',
    'manager/tests/test_labdef.py',
    'manager/tests/test_labs.py',
    'manager/tests/test_netconfig.py',
    'manager/tests/test_orchestrator.py',
    'manager/tests/test_pilot_safety.py',
    'manager/tests/test_proxmox.py',
    'manager/tests/test_segments.py',
    'manager/tests/test_slot_tls.py',
    'manager/tests/test_storage.py',
    'manager/tests/test_store.py',
    'manager/tests/test_uplink.py',
    'manager/tests/test_vnc.py',
    'slot/AGENTS.md',
    'slot/CLAUDE.md',
    'slot/README.md',
    'slot/api/__init__.py',
    'slot/api/auth.py',
    'slot/api/cli.py',
    'slot/api/client.py',
    'slot/api/console.py',
    'slot/api/facts.py',
    'slot/api/health.py',
    'slot/api/iol_nvram.py',
    'slot/api/ios_config.py',
    'slot/api/labs.py',
    'slot/api/l1.py',
    'slot/api/lifecycle.py',
    'slot/api/models.py',
    'slot/api/nodes.py',
    'slot/api/qga.py',
    'slot/api/uplink.py',
    'slot/docs/ALIGNMENT.md',
    'slot/examples/cisco-3x2.json',
    'slot/lab-slot.service',
    'slot/main.py',
    'slot/release.json',
    'slot/release.py',
    'slot/requirements.txt',
    'slot/run.py',
    'slot/slot.env.example',
    'slot/tests/conftest.py',
    'slot/tests/test_admission.py',
    'slot/tests/test_console.py',
    'slot/tests/test_facts.py',
    'slot/tests/test_fencing.py',
    'slot/tests/test_fixed_mount.py',
    'slot/tests/test_iol_nvram.py',
    'slot/tests/test_ios_config.py',
    'slot/tests/test_l1.py',
    'slot/tests/test_qga.py',
    'slot/tests/test_reliability.py',
    'slot/tests/test_slot.py',
    'slot/tests/test_shutdown.py',
    'slot/tests/test_student_state.py',
    'slot/tests/test_uplink.py',
    'slot/tests/test_vnc.py',
    'slot/tools/check_slot.py',
)


def git(repo, *args, binary=False):
    return command(["git", "--no-replace-objects", "-C", repo, *args], binary=binary)


def export_source(repo, ref, stage):
    require(COMMIT.fullmatch(ref), "--ref requires a full 40-hex commit SHA")
    require(git(repo, "cat-file", "-t", ref) == "commit", "Source ref must be a commit")
    rows = git(repo, "ls-tree", "-r", "-z", ref, "--", *COMPONENTS, "constraints-tested.txt", binary=True)
    selected = {}
    for row in rows.split(b"\0"):
        if not row:
            continue
        header, rawname = row.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        name = rawname.decode("utf-8")
        if PurePosixPath(name).name in (".gitignore", ".gitkeep"):
            continue
        require(name in SOURCE_FILES and kind == "blob" and mode in ("100644", "100755"),
                "Unreviewed source path/type: " + name)
        require(not name.endswith(SECRET_SUFFIXES), "State/secret file in source")
        content = git(repo, "cat-file", "blob", oid, binary=True)
        require(len(content) <= 8 * 1024 * 1024 and b"\x00" not in content, "Unexpected source binary/size")
        require(not re.search(rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----", content),
                "Private key in source")
        target = Path(stage) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(content)
        target.chmod(0o755 if mode == "100755" else 0o644)
        selected[name] = digest(content)
    require(set(selected) == set(SOURCE_FILES), "Source commit lacks reviewed delivery files")
    return {"git_sha": ref, "tree_sha": git(repo, "rev-parse", ref + "^{tree}"), "files": selected}


def builder_identity():
    repo = Path(__file__).resolve().parent.parent
    require(not git(repo, "status", "--porcelain", "--untracked-files=all"), "Builder checkout must be clean")
    sha = git(repo, "rev-parse", "HEAD")
    require(COMMIT.fullmatch(sha), "Unidentified builder revision")
    files = {}
    for name in ("install/lab-release.py", "install/release-builder.py"):
        content = read_regular(repo / name)
        require(content == git(repo, "show", sha + ":" + name, binary=True), "Builder differs from commit")
        files[name] = digest(content)
    return {"version": VERSION, "git_sha": sha, "files": files}


def admin_dir(path, mode=0o700):
    path = Path(path)
    safe_directory(path.parent)
    try:
        path.mkdir(mode=mode)
    except FileExistsError:
        pass
    return safe_directory(path)


def validate_config(config):
    require(isinstance(config, dict) and set(config) == {
        "schema", "ceph_root", "release_root", "source_repo", "index_url", "build_bridge",
        "isolated_network_confirmed", "builders", "compatibility"}, "Unsupported build config")
    require(config["schema"] == "gns3lab.release-build/1", "Unsupported build config schema")
    from urllib.parse import urlsplit
    url = urlsplit(config["index_url"])
    require(url.scheme == "https" and url.hostname and url.username is None and url.password is None
            and not url.query and not url.fragment, "Use an HTTPS index without embedded credentials")
    require(config["isolated_network_confirmed"] is True
            and re.fullmatch(r"[A-Za-z0-9_-]{1,15}", config["build_bridge"]),
            "An explicitly isolated builder bridge is required")
    require(set(config["builders"]) == {"ubuntu", "agent"}, "Two builder profiles required")
    ids = []
    for name, profile in config["builders"].items():
        require(set(profile) == {"vmid", "template_vmid", "template_image_sha256", "template_config_sha256", "runtime", "limits"},
                "Invalid builder profile")
        for key in ("vmid", "template_vmid"):
            require(type(profile[key]) is int and 100 <= profile[key] <= 999999999, "Invalid builder/template VMID")
            ids.append(profile[key])
        require(HEX.fullmatch(profile["template_config_sha256"]) and HEX.fullmatch(profile["template_image_sha256"]),
                "Template config and maintainer-attested image fingerprints required")
        runtime = profile["runtime"]
        require(set(runtime) == {"os_id", "os_version", "python", "architecture", "implementation",
                                 "cache_tag", "soabi", "executable", "executable_sha256", "packages_sha256"}
                and all(isinstance(v, str) and v for v in runtime.values()), "Full runtime profile required")
        require(runtime["implementation"] == "cpython" and re.fullmatch(r"/usr/bin/python3\.\d+", runtime["executable"])
                and HEX.fullmatch(runtime["executable_sha256"]) and HEX.fullmatch(runtime["packages_sha256"]),
                "Unsupported interpreter profile")
        if name == "ubuntu":
            require((runtime["os_id"], runtime["os_version"], runtime["python"], runtime["executable"]) ==
                    ("ubuntu", "24.04", "3.12", "/usr/bin/python3.12"), "Ubuntu 24.04/Python 3.12 required")
        else:
            require(runtime["os_id"] == "debian", "Agent needs a separate Debian/PVE profile")
        limits = profile["limits"]
        require(set(limits) == {"cores", "cpulimit", "memory_mb", "swap_mb", "rootfs_max_gib", "io_max"},
                "Explicit resource limits required")
        require(all(type(limits[k]) is int and limits[k] > 0 for k in
                    ("cores", "cpulimit", "memory_mb", "rootfs_max_gib"))
                and type(limits["swap_mb"]) is int and limits["swap_mb"] >= 0
                and limits["cpulimit"] <= limits["cores"], "Invalid builder resource limit")
        require(re.fullmatch(r"\d+:\d+ rbps=[1-9]\d* wbps=[1-9]\d* riops=[1-9]\d* wiops=[1-9]\d*", limits["io_max"]),
                "Finite cgroup2 IO limits required; configure them in the clean template")
    require(len(set(ids)) == len(ids), "Template and builder VMIDs must be distinct")


def pct_config(vmid):
    text = command(["pct", "config", vmid, "--current", "1"])
    config = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        key, value = line.split(": ", 1)
        require(key not in config, "Duplicate CT configuration key")
        config[key] = value
    return config, digest(text.encode())


def isolated_ct(config, template=False):
    require(config.get("unprivileged") == "1", "Builder/template must be unprivileged")
    require(config.get("onboot", "0") == "0", "Builder/template must have onboot=0")
    forbidden = [key for key in config if re.match(r"(?:mp\d+|dev\d+|hookscript|features|lxc\.)", key)
                 and key != "lxc.cgroup2.io.max"]
    require(not forbidden, "Unexpected passthrough, mounts or LXC overrides")
    expected_tag = "labrelease-template" if template else "labreleasebuilder"
    require(config.get("tags") == expected_tag, "Builder/template must have only its dedicated tag")
    require((config.get("template") == "1") == template, "Wrong CT/template role")
    require(not any(re.fullmatch(r"net[1-9]\d*", key) for key in config), "Only the isolated net0 is allowed")


def match_runtime(actual, expected):
    require(all(actual.get(key) == value for key, value in expected.items()), "Builder runtime fingerprint mismatch")


class Builders:
    """Only full clones owned by this attempt can be stopped, mounted or destroyed."""

    def __init__(self, config, attempt, stage, scratch, record):
        self.config, self.attempt = config, attempt
        self.stage, self.scratch, self.record = stage, scratch, record
        self.resources = {}
        self.locks = []
        self.failure_persisted = True
        self.metadata = {"schema": "gns3lab.release-recovery/1", "attempt": attempt,
                         "stage": str(stage), "scratch": str(scratch), "resources": self.resources}
        self.save()

    def save(self):
        write_json(self.record, self.metadata)

    def remember_failure(self, diagnostic):
        if "failure" in self.metadata and self.failure_persisted:
            return
        self.metadata.setdefault("failure", diagnostic)
        self.failure_persisted = False
        try:
            self.save()  # fsync + atomic replace before any CT cleanup is allowed.
            self.failure_persisted = True
        except OSError:
            print("Primary builder diagnostic could not be saved; retain builders for recovery", file=sys.stderr)

    def remember_cleanup_failure(self):
        component = self.metadata.get("failure", {}).get("component", "runtime")
        diagnostic = builder_diagnostic(component, "cleanup", "check_failed")
        self.metadata["cleanup_failure"] = diagnostic
        try:
            self.save()
        except OSError:
            pass
        print("Cleanup incomplete; original failure preserved. " + json.dumps(diagnostic, sort_keys=True), file=sys.stderr)

    def locked(self):
        lockroot = admin_dir("/run/lab-release-builders")
        # Reserve both VMIDs on this host, with a stable order; templates are not modified.
        for vmid in sorted(p["vmid"] for p in self.config["builders"].values()):
            fd = os.open(lockroot / (str(vmid) + ".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            self.locks.append(fd)
            st = os.fstat(fd)
            require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_nlink == 1
                    and not st.st_mode & 0o077, "Unsafe builder lock")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ReleaseError("Builder VMID is locked by another build") from exc

    def assert_owned(self, name, allow_mounts=False):
        resource = self.resources[name]
        require(resource["state"] not in ("cloning", "uncertain", "deleted"),
                "CT outcome uncertain; operator recovery required")
        current, _ = pct_config(resource["vmid"])
        require(current.get("hostname") == resource["hostname"]
                and current.get("rootfs") == resource["rootfs"], "Builder identity changed; refusing mutation")
        require(Path(f"/etc/pve/lxc/{resource['vmid']}.conf").is_file(), "Builder moved off this host; recover manually")
        basic = {k: v for k, v in current.items() if not (allow_mounts and re.fullmatch(r"mp[0-3]", k))}
        isolated_ct(basic)
        return current

    def ct_mutation(self, name, args, timeout=120):
        self.assert_owned(name, allow_mounts=True)
        resource = self.resources[name]
        resource["state"] = "uncertain"
        self.save()
        # Any timeout/failure requires operator inspection of PVE tasks. Do not race another pct.
        result = command(["pct", *args[:1], resource["vmid"], *args[1:]], timeout=timeout)
        resource["state"] = "owned"
        self.save()
        return result

    def create(self, name):
        profile = self.config["builders"][name]
        vmid, template = profile["vmid"], profile["template_vmid"]
        require(not list(Path("/etc/pve/nodes").glob(f"*/lxc/{vmid}.conf"))
                and not list(Path("/etc/pve/nodes").glob(f"*/qemu-server/{vmid}.conf")),
                "Reserved builder VMID is occupied; recover manually")
        source, sha = pct_config(template)
        require(Path(f"/etc/pve/lxc/{template}.conf").is_file(), "Template must be local to this builder host")
        isolated_ct(source, template=True)
        require(sha == profile["template_config_sha256"], "Clean template config fingerprint mismatch")
        require(command(["pct", "status", template]) == "status: stopped", "Template must be stopped")
        require(source.get("lxc.cgroup2.io.max") == profile["limits"]["io_max"], "Template IO limit mismatch")
        hostname = "labrel-" + self.attempt[:12] + "-" + name
        self.resources[name] = {"vmid": vmid, "template_vmid": template, "hostname": hostname,
                                "rootfs": None, "state": "cloning"}
        self.save()  # Persist ownership intent before creating any resources.
        command(["pct", "clone", template, vmid, "--full", "1", "--hostname", hostname], timeout=1800)
        current, _ = pct_config(vmid)
        require(current.get("hostname") == hostname and current.get("rootfs"), "Unexpected cloned CT")
        self.resources[name]["rootfs"] = current["rootfs"]
        self.resources[name]["state"] = "owned"
        # Clone inherits the dedicated template tag; this is the only role transition allowed.
        basic = dict(current, tags="labreleasebuilder")
        isolated_ct(basic)
        limits = profile["limits"]
        size = re.search(r"(?:^|,)size=([0-9]+(?:\.[0-9]+)?)([GM])(?:,|$)", current["rootfs"])
        require(size and float(size[1]) / (1024 if size[2] == "M" else 1) <= limits["rootfs_max_gib"],
                "Template rootfs exceeds configured disk cap")
        # Before the first role transition assert_owned expects the inherited tag.
        self.resources[name]["state"] = "uncertain"
        self.save()
        command(["pct", "set", vmid, "--tags", "labreleasebuilder", "--onboot", "0",
                 "--cores", limits["cores"], "--cpulimit", limits["cpulimit"],
                 "--memory", limits["memory_mb"], "--swap", limits["swap_mb"],
                 "--net0", "name=eth0,bridge=" + self.config["build_bridge"] + ",ip=dhcp,firewall=1"])
        self.resources[name]["state"] = "owned"
        self.save()
        configured = self.assert_owned(name)
        require(all(configured.get(key) == str(value) for key, value in {
            "cores": limits["cores"], "cpulimit": limits["cpulimit"], "memory": limits["memory_mb"],
            "swap": limits["swap_mb"]}.items()), "Configured resource limits disagree")

    def guest(self, name, *args, timeout=120, builder_context=None):
        resource = self.resources[name]
        self.assert_owned(name, allow_mounts=True)
        return command(["pct", "exec", resource["vmid"], "--", "/usr/bin/env", "-i", "PATH=/usr/bin:/bin",
                        "LANG=C.UTF-8", "PYTHONDONTWRITEBYTECODE=1", *args], timeout=timeout,
                       builder_context=builder_context)

    def helper(self, name, action, component=None, timeout=120):
        executable = self.config["builders"][name]["runtime"]["executable"]
        args = [executable, "-I", "-B", "/tmp/lab-release-builder.py", action]
        if component is not None:
            args += ["--component", component]
        context = {"component": component or "runtime", "action": action}
        try:
            output = self.guest(name, *args, timeout=timeout, builder_context=context)
            try:
                data = decode_json(output)
                require(isinstance(data, dict) and (action == "runtime" or data.get("ok") is True), "Builder check failed")
            except (ReleaseError, ValueError, TypeError, KeyError, RecursionError):
                raise BuilderFailure(builder_diagnostic(context["component"], "protocol", "invalid_diagnostic")) from None
            return data
        except BuilderFailure as exc:
            self.remember_failure(exc.diagnostic)
            raise

    def start(self, name, component):
        self.ct_mutation(name, ["start"])
        # pct start returns when init is available; any exec failure remains fail-closed.
        helper = Path(__file__).with_name("release-builder.py")
        self.ct_mutation(name, ["push", helper, "/tmp/lab-release-builder.py", "--perms", "0500", "--user", "0", "--group", "0"])
        remote = self.guest(name, "/usr/bin/sha256sum", "/tmp/lab-release-builder.py").split()[0]
        require(remote == digest(read_regular(helper)), "Builder helper hash mismatch")
        actual = self.helper(name, "runtime", component)
        match_runtime(actual, self.config["builders"][name]["runtime"])
        io = self.guest(name, "/bin/cat", "/sys/fs/cgroup/io.max")
        require(io == self.config["builders"][name]["limits"]["io_max"], "Effective IO limit mismatch")
        root_device = self.guest(name, "/usr/bin/findmnt", "-T", "/", "-n", "-o", "MAJ:MIN")
        require(root_device == io.split()[0], "IO cap does not cover builder rootfs device")
        return actual

    def stop_unmount(self, name):
        self.ct_mutation(name, ["stop"])
        current = self.assert_owned(name, allow_mounts=True)
        for key in ("mp0", "mp1", "mp2", "mp3"):
            if key in current:
                self.ct_mutation(name, ["set", "--delete", key])

    def component(self, component):
        name = "agent" if component == "agent" else "ubuntu"
        if name not in self.resources:
            self.create(name)
        code = self.stage / component
        guestcode = "/opt/lab-node-agent" if component == "agent" else "/srv/gns3-ro/" + component
        venv = self.scratch / "agent-venv" if component == "agent" else code / "venv"
        venv.mkdir()
        if component == "agent":
            # Nested bind target must exist even though Agent's test venv is not shipped.
            (code / "venv").mkdir()
        deps = self.stage / "dependencies" / component
        deps.mkdir(parents=True)
        for path in (venv, deps):
            os.chown(path, 100000, 100000)
            path.chmod(0o755)
        mounts = [(code, guestcode, 1), (venv, guestcode + "/venv", 0),
                  (deps, "/build/output", 0), (self.scratch / "inputs", "/build/inputs", 1)]
        for i, (host, guest, ro) in enumerate(mounts):
            self.ct_mutation(name, ["set", "--mp" + str(i), f"{host},mp={guest},ro={ro}"])
        runtime = self.start(name, component)
        built = self.helper(name, "build", component, timeout=3600)
        require(built["runtime"] == runtime, "Runtime changed during dependency build")
        self.stop_unmount(name)
        # Freeze code/dependencies before rechecking through read-only mounts.
        seal_tree(code)
        if component == "agent":
            seal_tree(venv)
        seal_tree(deps)
        for i, (host, guest, _) in enumerate(mounts):
            self.ct_mutation(name, ["set", "--mp" + str(i), f"{host},mp={guest},ro=1"])
        self.start(name, component)
        report = self.helper(name, "smoke", component)
        require(report["runtime"] == runtime, "Runtime changed before smoke checks")
        if component == "slot":
            entries = inventory(self.stage, sealed=False)
            require(report["slot_files"] == slot_projection(entries), "Slot executable policy differs from trusted policy")
            require(report["slot_identity"] == {"ok": True, **slot_identity(self.stage, entries)}, "Slot preflight identity differs")
        self.stop_unmount(name)
        if component == "agent":
            (code / "venv").rmdir()
        write_json(self.stage / "verification" / (component + ".json"), report)

    def cleanup(self):
        require(self.failure_persisted, "Primary diagnostic is not durable; retain builders for operator recovery")
        errors = []
        for name, resource in self.resources.items():
            if resource["state"] == "deleted":
                continue
            try:
                self.assert_owned(name, allow_mounts=True)
                status = command(["pct", "status", resource["vmid"]])
                if status != "status: stopped":
                    self.ct_mutation(name, ["stop"])
                current = self.assert_owned(name, allow_mounts=True)
                for key in ("mp0", "mp1", "mp2", "mp3"):
                    if key in current:
                        self.ct_mutation(name, ["set", "--delete", key])
                # Never use purge/destroy-unreferenced-disks: only this clone's rootfs.
                self.ct_mutation(name, ["destroy"], timeout=1800)
                require(not Path(f"/etc/pve/lxc/{resource['vmid']}.conf").exists(), "Builder CT still exists")
                volume = resource["rootfs"].split(",", 1)[0]
                storage = volume.split(":", 1)[0]
                volumes = decode_json(command(["pvesm", "list", storage, "--vmid", resource["vmid"], "--output-format", "json"]))
                require(not volumes, "Builder volumes remain after destruction; operator recovery required")
                resource["state"] = "deleted"
                self.save()
            except (ReleaseError, OSError, ValueError) as exc:
                errors.append(type(exc).__name__ + ": " + str(exc))
        require(not errors, "Builder cleanup incomplete; retain recovery record: " + str(self.record) + "; " + "; ".join(errors))

    def close(self):
        for fd in self.locks:
            os.close(fd)
        self.locks.clear()


def build_release(release_id, ref, config_path):
    valid_id(release_id)
    require(os.geteuid() == 0, "Build requires root on a Proxmox host")
    safe_directory(Path(config_path).absolute().parent)
    st = Path(config_path).lstat()
    require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022, "Unsafe build config")
    config = read_json(config_path)
    validate_config(config)
    identity = builder_identity()
    identity["templates"] = {name: {"template_vmid": profile["template_vmid"],
                                     "config_sha256": profile["template_config_sha256"],
                                     "image_sha256": profile["template_image_sha256"],
                                     "image_provenance": "maintainer_attested"}
                               for name, profile in config["builders"].items()}
    root = Path(config["release_root"])
    safe_directory(root.parent)
    fs = command(["findmnt", "-T", root.parent, "-n", "-o", "FSTYPE,TARGET"]).split()
    require(len(fs) == 2 and fs[0] in ("ceph", "fuse.ceph", "fuse.ceph-fuse")
            and fs[1] == config["ceph_root"], "Build storage must be on the declared CephFS")
    admin_dir(root, 0o755)
    check_ceph_root(root, config["ceph_root"])
    repo = safe_directory(config["source_repo"])
    require(not root.is_relative_to(repo) and not repo.is_relative_to(root), "Source and release trees overlap")
    lockroot, staging = admin_dir(root / ".locks"), admin_dir(root / ".staging")
    lock = lockroot / release_id
    require(not os.path.lexists(root / release_id), "Release ID already exists; never rebuild in place")
    lock.mkdir(mode=0o700)  # Atomic reservation across CephFS clients; no auto-stale takeover.
    attempt = uuid.uuid4().hex
    stage, scratch = staging / (release_id + "." + attempt), staging / (".work-" + attempt)
    write_json(lock / "owner.json", {"attempt": attempt, "host": os.uname().nodename,
                                    "stage": str(stage), "scratch": str(scratch), "source_git_sha": ref})
    sync_dir(lockroot)
    stage.mkdir(mode=0o700)
    scratch.mkdir(mode=0o700)
    records = admin_dir("/var/lib/lab-release-build")
    record = records / (attempt + ".json")
    builders = Builders(config, attempt, stage, scratch, record)
    error = None
    try:
        builders.locked()
        source = export_source(repo, ref, stage)
        facts = source_facts(stage)
        validate_compatibility(config["compatibility"], facts)
        slot_identity(stage, inventory(stage, sealed=False))
        (stage / "verification").mkdir()
        inputs = scratch / "inputs"
        inputs.mkdir(mode=0o755)
        shutil.copyfile(stage / "constraints-tested.txt", inputs / "constraints-tested.txt")
        (inputs / "constraints-tested.txt").chmod(0o444)
        write_json(inputs / "index.json", {"index_url": config["index_url"]})
        (inputs / "index.json").chmod(0o444)
        # Source code must be readable inside unprivileged RO bind mounts.
        for directory, _, files in os.walk(stage):
            Path(directory).chmod(0o755)
            for name in files:
                path = Path(directory) / name
                path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
        for component in COMPONENTS:
            builders.component(component)
        builders.cleanup()
        builders.metadata["state"] = "builders_removed"
        builders.save()
        # Scratch/Agent test venv is not part of the release. Builders no longer hold any mounts.
        shutil.rmtree(scratch)
        seal_tree(stage)
        entries = inventory(stage)
        manifest = {"schema": SCHEMA, "release_id": release_id,
                    "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "source": source, "builder": identity, "slot_policy": POLICY,
                    "slot_identity": slot_identity(stage, entries), "facts": facts,
                    "compatibility": config["compatibility"], "dependencies": dependency_facts(stage, entries),
                    "files": entries}
        # Root temporarily writable by trusted host root only; manifest is added before final verification.
        stage.chmod(0o755)
        write_json(stage / "manifest.json", manifest)
        (stage / "manifest.json").chmod(0o444)
        stage.chmod(0o555)
        fd = os.open(stage / "manifest.json", os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        builders.metadata["state"] = "publishing"
        builders.save()
        publish(stage, root, release_id)
        result = verify_package(root / release_id, release_id)
        builders.metadata["state"] = "published"
        builders.save()
        (lock / "owner.json").unlink()
        lock.rmdir()
        sync_dir(lockroot)
        return result
    except BaseException as exc:
        error = exc
        if isinstance(exc, BuilderFailure):
            builders.remember_failure(exc.diagnostic)
        raise
    finally:
        if error is not None:
            try:
                builders.cleanup()
            except (ReleaseError, OSError, ValueError):
                builders.remember_cleanup_failure()
            print("Recovery record: " + str(record) + "; keep staging/lock until inspected", file=sys.stderr)
        builders.close()


def list_releases(root):
    releases = []
    with os.scandir(root) as entries:
        names = sorted(entry.name for entry in entries if not entry.name.startswith("."))
    for name in names:
        try:
            valid_id(name)
            require(not (root / name).is_symlink(), "Release directory is a symlink")
            safe_directory(root / name)
            releases.append({"status": "READY", **verify_package(root / name, name)})
        except (ReleaseError, OSError, ValueError, TypeError, KeyError, SyntaxError, RecursionError) as exc:
            releases.append({"status": "INVALID", "release_id": name, "ok": False,
                             "error_type": type(exc).__name__})
    return {"ok": all(row["ok"] for row in releases), "releases": releases}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    build = sub.add_parser("build", help="prepare a new release; never switch the live deployment")
    build.add_argument("release_id")
    build.add_argument("--ref", required=True)
    build.add_argument("--config", required=True)
    verify = sub.add_parser("verify", help="offline, read-only; never execute release code")
    verify.add_argument("release_id")
    verify.add_argument("--root", default=DEFAULT_ROOT)
    listing = sub.add_parser("list", help="verify releases; hidden staging is excluded")
    listing.add_argument("--root", default=DEFAULT_ROOT)
    args = parser.parse_args(argv)
    try:
        if args.action == "build":
            def interrupted(number, _frame):
                raise ReleaseError("Build interrupted by signal " + str(number))
            signal.signal(signal.SIGTERM, interrupted)
            signal.signal(signal.SIGINT, interrupted)
            result = build_release(args.release_id, args.ref, args.config)
        else:
            root = check_ceph_root(args.root)
            if args.action == "verify":
                valid_id(args.release_id)
                safe_directory(root / args.release_id)
                result = verify_package(root / args.release_id, args.release_id)
            else:
                result = list_releases(root)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["ok"] else 1
    except BuilderFailure as exc:
        print(json.dumps(exc.diagnostic, sort_keys=True), file=sys.stderr)
        return 1
    except (ReleaseError, OSError, ValueError, TypeError, KeyError, SyntaxError, RecursionError) as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
