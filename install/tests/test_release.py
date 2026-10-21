"""Local filesystem/model tests, not CephFS/LXC acceptance.

The executor is unprivileged. Fixtures emulate the trusted administrator UID/GID;
the production CLI has no override for root ownership or CephFS validation.
No product code, dependency or venv from a fixture is ever executed.
"""

import ctypes
import errno
import importlib.util
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lab_release", REPO / "install/lab-release.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
RAW_COMMAND = r.command
helper_spec = importlib.util.spec_from_file_location("release_builder", REPO / "install/release-builder.py")
h = importlib.util.module_from_spec(helper_spec)
helper_spec.loader.exec_module(h)


def modeled_runtime(component):
    return {"os_id": "debian" if component == "agent" else "ubuntu",
            "os_version": "13" if component == "agent" else "24.04",
            "python": "3.12", "python_full": "3.12.14", "architecture": "x86_64",
            "implementation": "cpython", "cache_tag": "cpython-312", "soabi": "cpython-312-x86_64-linux-gnu",
            "executable": "/usr/bin/python3.12", "executable_sha256": "a" * 64, "packages_sha256": "b" * 64}


def modeled_templates():
    return {name: {"template_vmid": 9701 + i, "config_sha256": "a" * 64,
                   "image_sha256": "b" * 64, "image_provenance": "maintainer_attested"}
            for i, name in enumerate(("ubuntu", "agent"))}


def chmod_tree(root, sealed):
    for directory, _, files in os.walk(root):
        Path(directory).chmod(0o555 if sealed else 0o755)
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                path.chmod(0o444 if sealed else 0o644)


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(r, "ADMIN_UID", os.getuid())
    monkeypatch.setattr(r, "ADMIN_GID", os.getgid())


def make_package(root, release_id="r1"):
    root.mkdir()
    ref = r.git(REPO, "rev-parse", "HEAD")
    source = r.export_source(REPO, ref, root)
    (root / "verification").mkdir()
    for component in r.COMPONENTS:
        prefix = root / "dependencies" / component
        (prefix / "wheels").mkdir(parents=True)
        wheel = prefix / "wheels/demo-1.0-py3-none-any.whl"
        wheel.write_bytes(b"synthetic wheel; never installed or executed")
        (prefix / "requirements.lock").write_text(f"demo==1.0 --hash=sha256:{r.digest(wheel.read_bytes())}\n")
        (root / "verification" / (component + ".json")).write_bytes(r.json_bytes({
            "ok": True, "component": component, "runtime": modeled_runtime(component), "installed": {"demo": "1.0"}}))
        if component != "agent":
            venv = root / component / "venv"
            (venv / "bin").mkdir(parents=True)
            (venv / "bin/python").symlink_to("/usr/bin/python3.12")
            (venv / "lib").mkdir()
            (venv / "lib64").symlink_to("lib")
            (venv / "lib/dependency.so").write_bytes(b"synthetic executable dependency")
    reseal(root, source, release_id)
    return root


def reseal(root, source=None, release_id="r1"):
    chmod_tree(root, False)
    if source is None:
        old = r.read_json(root / "manifest.json")
        source = old["source"]
        source["files"] = {name: r.digest((root / name).read_bytes()) for name in r.SOURCE_FILES}
    chmod_tree(root, True)
    entries = r.inventory(root)
    facts = r.source_facts(root)
    manifest = {"schema": r.SCHEMA, "release_id": release_id, "created_utc": "2026-10-21T00:00:00+00:00",
                "source": source,
                "builder": {"git_sha": "b" * 40, "version": r.VERSION, "templates": modeled_templates(),
                            "files": {"install/lab-release.py": "c" * 64, "install/release-builder.py": "d" * 64}},
                "slot_policy": r.POLICY, "slot_identity": r.slot_identity(root, entries),
                "facts": facts, "compatibility": {"components": facts["versions"], "slot_state": "unchanged",
                                                  "agent_state": "unchanged", "rollback": "not_assessed"},
                "dependencies": r.dependency_facts(root, entries), "files": entries}
    root.chmod(0o755)
    path = root / "manifest.json"
    if path.exists():
        path.chmod(0o644)
    path.write_bytes(r.json_bytes(manifest))
    path.chmod(0o444)
    root.chmod(0o555)


@pytest.fixture
def package(tmp_path, admin):
    root = make_package(tmp_path / "package")
    yield root
    chmod_tree(tmp_path, False)


def edit_manifest(root, mutate):
    path = root / "manifest.json"
    data = r.read_json(path)
    mutate(data)
    path.chmod(0o644)
    path.write_bytes(r.json_bytes(data))
    path.chmod(0o444)


def test_valid_package_separate_source_builder_identity(package):
    result = r.verify_package(package, "r1")
    assert result["ok"]
    assert result["source_git_sha"] != result["builder_git_sha"]
    assert result["manifest_sha256"] == r.digest((package / "manifest.json").read_bytes())
    assert r.read_json(package / "manifest.json")["facts"]["postgresql"]["minimum"] == 6


@pytest.mark.parametrize("name,action", [
    ("manager/run.py", "modify"), ("slot/api/lifecycle.py", "remove"),
    ("manager/venv/lib/dependency.so", "modify"), ("slot/venv/bin/entrypoint", "add"),
    ("agent/extra.unknown", "add"), ("dependencies/agent/wheels/demo-1.0-py3-none-any.whl", "modify"),
    ("verification/slot.json", "remove"), ("manager/venv/bin/python", "link"),
])
def test_mutation_removal_addition_and_dependency_integrity(package, name, action):
    path = package / name
    path.parent.chmod(0o755)
    if action == "remove":
        path.unlink()
    elif action == "link":
        path.unlink()
        path.symlink_to("/tmp/untrusted-python")
    else:
        if path.exists():
            path.chmod(0o644)
        path.write_bytes(b"changed")
        path.chmod(0o444)
    path.parent.chmod(0o555)
    with pytest.raises((r.ReleaseError, OSError)):
        r.verify_package(package)


def test_writable_files_rejected_even_if_inventory_resealed(package):
    (package / "agent/agent.py").chmod(0o644)
    with pytest.raises(r.ReleaseError, match="mode"):
        r.inventory(package)


def test_real_nonroot_owner_rejected_without_fixture_override(package, monkeypatch):
    if os.getuid() == 0:
        pytest.skip("Executor is root")
    monkeypatch.setattr(r, "ADMIN_UID", 0)
    monkeypatch.setattr(r, "ADMIN_GID", 0)
    with pytest.raises(r.ReleaseError, match="Unsealed directory"):
        r.verify_package(package)


@pytest.mark.parametrize("value", ["../r1", "/r1", "a/b", ".hidden", "a.b", "a" * 65, "x\n"])
def test_bad_release_ids(value):
    with pytest.raises(r.ReleaseError):
        r.valid_id(value)


@pytest.mark.parametrize("name", ["../file", "a/../b", "/absolute", "a//b", "./a", "a\\b", ""])
def test_bad_inventory_paths(name):
    with pytest.raises(r.ReleaseError):
        r.relative_name(name)


def test_hardlinks_and_special_files_not_followed(tmp_path):
    (tmp_path / "one").write_bytes(b"x")
    os.link(tmp_path / "one", tmp_path / "two")
    with pytest.raises(r.ReleaseError, match="hardlink"):
        r.inventory(tmp_path, sealed=False)
    (tmp_path / "two").unlink()
    os.mkfifo(tmp_path / "fifo")
    with pytest.raises(r.ReleaseError, match="Special"):
        r.inventory(tmp_path, sealed=False)


def test_manifest_duplicate_keys_and_unknown_policy(package):
    with pytest.raises(r.ReleaseError, match="Duplicate"):
        r.decode_json(b'{"a":1,"a":2}')
    edit_manifest(package, lambda data: data.update(slot_policy="from-payload.release.py"))
    with pytest.raises(r.ReleaseError, match="policy"):
        r.verify_package(package)


def test_nested_manifest_mismatch_rejected_even_with_consistent_outer_inventory(package):
    path = package / "slot/release.json"
    path.chmod(0o644)
    nested = r.read_json(path)
    nested["files"]["main.py"] = "0" * 64
    path.write_bytes(r.json_bytes(nested))
    path.chmod(0o444)
    edit_manifest(package, lambda data: data["files"].update(r.inventory(package)))
    with pytest.raises(r.ReleaseError, match="Nested Slot"):
        r.verify_package(package)


def test_verify_and_list_never_execute_payload(package, tmp_path, monkeypatch):
    sentinel = tmp_path / "EXECUTED"
    trap = f"\nopen({str(sentinel)!r}, 'w').write('executed')\n"
    chmod_tree(package, False)
    for name in ("slot/release.py", "agent/agent.py", "manager/lab_manager/__init__.py"):
        with (package / name).open("a") as stream:
            stream.write(trap)
    (package / "manager/venv/lib/trap.pth").write_text("import pathlib; pathlib.Path(" + repr(str(sentinel)) + ").touch()")
    entrypoint = package / "slot/venv/bin/entrypoint"
    entrypoint.write_text("#!/bin/sh\ntouch " + str(sentinel) + "\n")
    nested = r.read_json(package / "slot/release.json")
    nested["files"] = r.slot_projection(r.inventory(package, sealed=False))
    (package / "slot/release.json").write_bytes(r.json_bytes(nested))
    reseal(package)
    # No subprocess (including a payload interpreter) is needed for the actual verifier.
    monkeypatch.setattr(r, "command", lambda *a, **kw: pytest.fail("Unexpected executable policy"))
    assert r.verify_package(package)["ok"]
    parent = tmp_path
    package.rename(parent / "r1")
    monkeypatch.setattr(r, "safe_directory", lambda path: Path(path))
    assert r.list_releases(parent)["releases"][0]["status"] == "READY"
    assert not sentinel.exists()
    edit_manifest(parent / "r1", lambda data: data.update(hooks=[str(entrypoint)]))
    assert not r.list_releases(parent)["ok"]
    assert not sentinel.exists()


def test_hashes_do_not_claim_authenticity(package):
    # External pin changes when a self-consistent manifest changes; there is no signature claim.
    before = r.verify_package(package)["manifest_sha256"]
    edit_manifest(package, lambda data: data["compatibility"].update(rollback="reviewed separately"))
    after = r.verify_package(package)["manifest_sha256"]
    assert before != after


def test_publish_never_replaces_existing_even_empty(package, tmp_path):
    final = tmp_path / "r1"
    final.mkdir()
    with pytest.raises(FileExistsError):
        r.publish(package, tmp_path, "r1")
    assert final.is_dir() and not list(final.iterdir())
    assert package.exists()


def test_new_release_does_not_change_old(package, tmp_path):
    r.publish(package, tmp_path, "r1")
    old = r.verify_package(tmp_path / "r1")["manifest_sha256"]
    second = make_package(tmp_path / "second", "r2")
    r.publish(second, tmp_path, "r2")
    assert r.verify_package(tmp_path / "r1")["manifest_sha256"] == old


def race_publish(source, target, event, queue):
    event.wait()
    try:
        r.rename_noreplace(source, target)
        queue.put("ok")
    except FileExistsError:
        queue.put("exists")


def test_two_real_processes_publish_one_id_without_mixing(tmp_path):
    a, b, target = [tmp_path / name for name in ("a", "b", "r1")]
    for path in (a, b):
        path.mkdir()
        (path / "identity").write_text(path.name)
        (path / (path.name + ".only")).write_text(path.name)
    context = multiprocessing.get_context("fork")
    event, queue = context.Event(), context.Queue()
    processes = [context.Process(target=race_publish, args=(p, target, event, queue)) for p in (a, b)]
    for process in processes:
        process.start()
    event.set()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert sorted(queue.get(timeout=1) for _ in processes) == ["exists", "ok"]
    winner = (target / "identity").read_text()
    assert {p.name for p in target.iterdir()} == {"identity", winner + ".only"}


def test_unsupported_noreplace_never_falls_back(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw: object())
    with pytest.raises(r.ReleaseError, match="fallback"):
        r.rename_noreplace(stage, tmp_path / "final")
    assert stage.exists() and not (tmp_path / "final").exists()


def test_post_rename_fsync_failure_preserves_complete_final(package, tmp_path, monkeypatch):
    original = r.sync_dir
    def sync(path):
        if Path(path) == tmp_path:
            raise OSError(errno.EIO, "modelled durability failure")
        original(path)
    monkeypatch.setattr(r, "sync_dir", sync)
    with pytest.raises(OSError):
        r.publish(package, tmp_path, "r1")
    assert not package.exists()
    assert r.verify_package(tmp_path / "r1")["ok"]


def test_staging_after_sigkill_not_listed(tmp_path, monkeypatch):
    staging = tmp_path / ".staging"
    staging.mkdir()
    ready = multiprocessing.get_context("fork").Event()
    def prepare():
        partial = staging / "r1.attempt"
        partial.mkdir()
        (partial / "incomplete").write_text("partial")
        ready.set()
        signal.pause()
    process = multiprocessing.get_context("fork").Process(target=prepare)
    process.start()
    assert ready.wait(5)
    os.kill(process.pid, signal.SIGKILL)
    process.join(5)
    monkeypatch.setattr(r, "safe_directory", lambda path: Path(path))
    assert r.list_releases(tmp_path) == {"ok": True, "releases": []}


def test_dispatch_needs_no_production_config_or_pct(tmp_path):
    env = {"PATH": os.environ["PATH"], "LAB_CONF": str(tmp_path / "missing-production-config")}
    for action in ("build", "verify", "list"):
        result = subprocess.run(["bash", str(REPO / "install/lab-provision"), "release", action, "--help"],
                                capture_output=True, text=True, env=env)
        assert result.returncode == 0
        assert "--root" in result.stdout or "--config" in result.stdout
        assert "run as root" not in result.stderr and "pct not found" not in result.stderr


def test_export_uses_git_objects_not_dirty_checkout(tmp_path):
    repo = tmp_path / "source"
    subprocess.run(["git", "clone", "--shared", "--no-checkout", str(REPO), str(repo)], check=True, capture_output=True)
    ref = r.git(REPO, "rev-parse", "HEAD")
    (repo / "agent").mkdir()
    (repo / "agent/agent.py").write_text("dirty code")
    (repo / "agent/private.key").write_text("private untracked data")
    (repo / "state.sqlite3").write_text("student data")
    stage = tmp_path / "stage"
    stage.mkdir()
    source = r.export_source(repo, ref, stage)
    assert source["git_sha"] == ref
    assert (stage / "agent/agent.py").read_bytes() == r.git(REPO, "show", ref + ":agent/agent.py", binary=True)
    assert not (stage / "agent/private.key").exists() and not (stage / "state.sqlite3").exists()
    assert not any(path.name == ".git" for path in stage.rglob("*"))


def test_export_rejects_added_tracked_secret(tmp_path):
    repo = tmp_path / "source"
    subprocess.run(["git", "clone", "--shared", str(REPO), str(repo)], check=True, capture_output=True)
    (repo / "agent/private.key").write_text("secret")
    r.git(repo, "add", "-f", "agent/private.key")
    r.git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "bad source")
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(r.ReleaseError, match="Unreviewed source"):
        r.export_source(repo, r.git(repo, "rev-parse", "HEAD"), stage)


def test_dirty_builder_identity_is_rejected():
    # Deterministic check independent of this working tree's current status.
    original = r.git
    try:
        r.git = lambda *args, **kw: " M install/lab-release.py"
        with pytest.raises(r.ReleaseError, match="clean"):
            r.builder_identity()
    finally:
        r.git = original


def test_requirements_cannot_inject_pip_flags_urls_or_nested_files(tmp_path):
    path = tmp_path / "requirements.txt"
    for value in ("-r /etc/private", "--extra-index-url https://evil.invalid", "thing @ https://evil.invalid/x.whl"):
        path.write_text(value)
        with pytest.raises(ValueError):
            h.validate_requirements(path)
    for value in ("psycopg[binary,pool]>=3.2,<4", "fastapi>=0.115,<1"):
        path.write_text(value)
        h.validate_requirements(path)


def test_example_is_deliberately_unusable_until_operator_supplies_fingerprints():
    config = r.read_json(REPO / "install/lab-release-build.json.example")
    with pytest.raises(r.ReleaseError):
        r.validate_config(config)


def test_unknown_occupied_or_uncertain_builder_never_destroyed(tmp_path, monkeypatch):
    builder = r.Builders({}, "a" * 32, tmp_path / "stage", tmp_path / "scratch", tmp_path / "record.json")
    builder.resources["ubuntu"] = {"vmid": 9801, "state": "uncertain", "hostname": "ours", "rootfs": "local:vm-9801"}
    calls = []
    monkeypatch.setattr(r, "command", lambda args, **kw: calls.append(args))
    with pytest.raises(r.ReleaseError, match="cleanup incomplete"):
        builder.cleanup()
    assert calls == []
    builder.resources["ubuntu"]["state"] = "owned"
    monkeypatch.setattr(r, "pct_config", lambda vmid: ({"hostname": "someone-else", "rootfs": "local:vm-9801"}, ""))
    with pytest.raises(r.ReleaseError, match="identity changed"):
        builder.assert_owned("ubuntu")
    assert calls == []


def test_template_and_production_ct_never_pass_builder_role():
    base = {"unprivileged": "1", "onboot": "0", "tags": "labreleasebuilder"}
    r.isolated_ct(base)
    for key, value in (("tags", "labslot"), ("unprivileged", "0"), ("mp0", "/production"),
                       ("hookscript", "script"), ("features", "nesting=1"), ("lxc.idmap", "custom")):
        with pytest.raises(r.ReleaseError):
            r.isolated_ct(dict(base, **{key: value}))


def test_fresh_full_clones_and_destruction_on_sequential_attempts(tmp_path, monkeypatch):
    """Model PVE commands/state, not a real clone or rootfs compatibility result."""
    config = r.read_json(REPO / "install/lab-release-build.json.example")
    config["builders"]["ubuntu"]["template_config_sha256"] = "a" * 64
    config["builders"]["ubuntu"]["limits"]["io_max"] = "8:0 rbps=1024 wbps=1024 riops=10 wiops=10"
    profile = config["builders"]["ubuntu"]
    template = {"hostname": "clean-template", "template": "1", "unprivileged": "1", "onboot": "0",
                "tags": "labrelease-template", "rootfs": "local:template,size=4G",
                "lxc.cgroup2.io.max": profile["limits"]["io_max"]}
    states = {profile["template_vmid"]: template}
    history = []
    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", lambda p: True if str(p).startswith("/etc/pve/lxc/") else real_is_file(p))
    monkeypatch.setattr(r, "pct_config", lambda vmid: (dict(states[vmid]), "a" * 64))

    def fake_command(args, **kw):
        args = [str(a) for a in args]
        history.append(args)
        if args[:2] == ["pct", "status"]:
            return "status: stopped"
        if args[:2] == ["pct", "clone"]:
            assert args[args.index("--full") + 1] == "1"
            vmid = int(args[3])
            states[vmid] = {k: v for k, v in template.items() if k != "template"}
            states[vmid].update(hostname=args[args.index("--hostname") + 1], rootfs=f"local:vm-{vmid},size=4G")
            assert "leftover_venv" not in states[vmid]
        elif args[:2] == ["pct", "set"]:
            current = states[int(args[2])]
            for i in range(3, len(args), 2):
                current[args[i].removeprefix("--")] = args[i + 1]
        elif args[:2] == ["pct", "destroy"]:
            del states[int(args[2])]
        elif args[:2] == ["pvesm", "list"]:
            return "[]"
        return ""
    monkeypatch.setattr(r, "command", fake_command)
    hostnames = []
    for attempt in ("1" * 32, "2" * 32):
        builders = r.Builders(config, attempt, tmp_path / "stage", tmp_path / "scratch", tmp_path / (attempt + ".json"))
        builders.create("ubuntu")
        hostnames.append(states[profile["vmid"]]["hostname"])
        states[profile["vmid"]]["leftover_venv"] = "poison from this attempt"
        builders.cleanup()
        assert profile["vmid"] not in states
        assert builders.resources["ubuntu"]["state"] == "deleted"
    assert hostnames[0] != hostnames[1]
    assert states == {profile["template_vmid"]: template}
    assert len([args for args in history if args[:2] == ["pct", "clone"]]) == 2
    assert all(args[2] == str(profile["vmid"]) for args in history if args[:2] == ["pct", "destroy"])


def test_verification_failure_prevents_publication(package, tmp_path, monkeypatch):
    (package / "agent/agent.py").chmod(0o644)
    monkeypatch.setattr(r, "rename_noreplace", lambda *a: pytest.fail("Invalid staging must never be renamed"))
    with pytest.raises(r.ReleaseError):
        r.publish(package, tmp_path, "r1")
    assert not (tmp_path / "r1").exists()


def test_runtime_profile_mismatch_fails_closed():
    with pytest.raises(r.ReleaseError, match="runtime fingerprint"):
        r.match_runtime({"python": "3.12", "architecture": "aarch64"},
                        {"python": "3.12", "architecture": "x86_64"})


def test_missing_or_placeholder_compatibility_is_not_assumed(package):
    facts = r.read_json(package / "manifest.json")["facts"]
    with pytest.raises(r.ReleaseError, match="Missing compatibility"):
        r.validate_compatibility({"components": facts["versions"], "slot_state": "REPLACE_ME",
                                  "agent_state": "unchanged", "rollback": "not_assessed"}, facts)


@pytest.fixture
def model_host(tmp_path, admin, monkeypatch):
    """Exercise the real host orchestration, with synthetic CT/dependencies only."""
    ceph = tmp_path / "ceph"
    ceph.mkdir()
    source = tmp_path / "source"
    subprocess.run(["git", "clone", "--shared", "--no-checkout", str(REPO), str(source)],
                   check=True, capture_output=True)
    config = r.read_json(REPO / "install/lab-release-build.json.example")
    config.update(ceph_root=str(ceph), release_root=str(ceph / "releases"), source_repo=str(source),
                  isolated_network_confirmed=True)
    for profile in config["builders"].values():
        profile.update(template_image_sha256="a" * 64, template_config_sha256="b" * 64)
        profile["runtime"].update(executable_sha256="c" * 64, packages_sha256="d" * 64)
        profile["limits"]["io_max"] = "8:0 rbps=1024 wbps=1024 riops=10 wiops=10"
    config["builders"]["agent"]["runtime"].update(os_version="13", python="3.13", cache_tag="cpython-313",
                                                   soabi="cpython-313-x86_64-linux-gnu")
    config["compatibility"].update(slot_state="unchanged", agent_state="unchanged")
    path = tmp_path / "build.json"
    path.write_bytes(r.json_bytes(config))
    assert r.validate_config(config) is None
    monkeypatch.setattr(r.os, "geteuid", lambda: 0)
    monkeypatch.setattr(r, "safe_directory", lambda p: Path(p))
    # Config ownership is another production-only host check, emulated explicitly here.
    real_lstat = Path.lstat
    def lstat(p):
        original = real_lstat(p)
        if p == path:
            fields = list(original)
            fields[4] = 0
            return os.stat_result(fields)
        return original
    monkeypatch.setattr(Path, "lstat", lstat)
    original_admin_dir = r.admin_dir
    def admin_dir(p, mode=0o700):
        if str(p) == "/var/lib/lab-release-build":
            p = tmp_path / "records"
        return original_admin_dir(p, mode)
    monkeypatch.setattr(r, "admin_dir", admin_dir)
    monkeypatch.setattr(r, "check_ceph_root", lambda p, *a: Path(p))
    real_command = r.command
    def command(args, **kwargs):
        if args[0] == "findmnt":
            return "ceph " + str(ceph)
        assert args[0] == "git", "Host must not execute product, pct, services or migrations in this model"
        return real_command(args, **kwargs)
    monkeypatch.setattr(r, "command", command)
    monkeypatch.setattr(r, "builder_identity", lambda: {
        "git_sha": "f" * 40, "version": r.VERSION,
        "files": {"install/lab-release.py": "a" * 64, "install/release-builder.py": "b" * 64}})
    monkeypatch.setattr(r, "seal_tree", lambda p: chmod_tree(p, True))
    # Cross-parent directory rename needs write permission on the directory for an
    # unprivileged UID (updating '..'). Emulate root's DAC privilege only in this fixture.
    real_rename = r.rename_noreplace
    def root_rename(stage, target):
        stage.chmod(0o755)
        try:
            real_rename(stage, target)
        finally:
            (target if target.exists() else stage).chmod(0o555)
    monkeypatch.setattr(r, "rename_noreplace", root_rename)

    class ModelBuilders(r.Builders):
        cleanup_fails = False
        components = []

        def locked(self):
            pass

        def component(self, component):
            self.components.append(component)
            output = self.stage / "dependencies" / component
            (output / "wheels").mkdir(parents=True)
            wheel = output / "wheels/demo-1.0-py3-none-any.whl"
            wheel.write_bytes(b"synthetic, never executed")
            (output / "requirements.lock").write_text(f"demo==1.0 --hash=sha256:{r.digest(wheel.read_bytes())}\n")
            if component != "agent":
                (self.stage / component / "venv").mkdir()
            r.write_json(self.stage / "verification" / (component + ".json"), {
                "ok": True, "component": component, "runtime": modeled_runtime(component), "installed": {"demo": "1.0"}})

        def cleanup(self):
            if self.cleanup_fails:
                raise r.ReleaseError("model cleanup failed")

    monkeypatch.setattr(r, "Builders", ModelBuilders)
    yield path, config, ModelBuilders
    chmod_tree(tmp_path, False)


def test_host_build_pipeline_and_existing_release_unchanged(model_host):
    path, config, builders = model_host
    ref = r.git(REPO, "rev-parse", "HEAD")
    result = r.build_release("r1", ref, str(path))
    assert result["ok"] and builders.components == ["manager", "slot", "agent"]
    root = Path(config["release_root"])
    assert r.list_releases(root)["releases"][0]["status"] == "READY"
    before = r.verify_package(root / "r1")["manifest_sha256"]
    with pytest.raises(r.ReleaseError, match="already exists"):
        r.build_release("r1", ref, str(path))
    assert r.verify_package(root / "r1")["manifest_sha256"] == before
    assert builders.components == ["manager", "slot", "agent"]
    assert not list((root / ".locks").iterdir())
    assert not list((root / ".staging").iterdir())


def test_host_cleanup_failure_prevents_publish_and_keeps_recovery(model_host, monkeypatch):
    path, config, builders = model_host
    builders.cleanup_fails = True
    monkeypatch.setattr(r, "rename_noreplace", lambda *a: pytest.fail("Cleanup failure must block publication"))
    with pytest.raises(r.ReleaseError, match="model cleanup failed"):
        r.build_release("r1", r.git(REPO, "rev-parse", "HEAD"), str(path))
    root = Path(config["release_root"])
    assert not (root / "r1").exists()
    assert (root / ".locks/r1/owner.json").exists()
    assert list((root / ".staging").iterdir())
    assert r.list_releases(root) == {"ok": True, "releases": []}


def test_sigkill_after_rename_leaves_complete_final(package, tmp_path):
    """Linux process interruption model; makes no claim about CephFS durability."""
    context = multiprocessing.get_context("fork")
    renamed = context.Event()
    def publishing():
        original = r.sync_dir
        def sync(path):
            if Path(path) == tmp_path:
                renamed.set()
                signal.pause()
            original(path)
        r.sync_dir = sync
        r.publish(package, tmp_path, "r1")
    process = context.Process(target=publishing)
    process.start()
    assert renamed.wait(10)
    os.kill(process.pid, signal.SIGKILL)
    process.join(5)
    assert not package.exists()
    assert r.verify_package(tmp_path / "r1")["ok"]


def test_occupied_vmid_not_removed_or_reused(tmp_path, monkeypatch):
    builders = r.Builders({"builders": {"ubuntu": {"vmid": 9801, "template_vmid": 9701}}},
                          "a" * 32, tmp_path / "stage", tmp_path / "scratch", tmp_path / "record.json")
    original = Path.glob
    def glob(path, pattern):
        if str(path) == "/etc/pve/nodes":
            return iter([Path("/etc/pve/nodes/other/lxc/9801.conf")])
        return original(path, pattern)
    monkeypatch.setattr(Path, "glob", glob)
    monkeypatch.setattr(r, "command", lambda *a, **kw: pytest.fail("Occupied VMID must cause a read-only refusal"))
    with pytest.raises(r.ReleaseError, match="occupied"):
        builders.create("ubuntu")
    assert builders.resources == {}


def test_private_key_inside_allowlisted_source_is_rejected(tmp_path):
    repo = tmp_path / "source"
    subprocess.run(["git", "clone", "--shared", str(REPO), str(repo)], check=True, capture_output=True)
    (repo / "agent/manager-example.py").write_text("# -----BEGIN PRIVATE KEY-----\n")
    r.git(repo, "add", "agent/manager-example.py")
    r.git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "bad embedded key")
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(r.ReleaseError, match="Private key"):
        r.export_source(repo, r.git(repo, "rev-parse", "HEAD"), stage)


def test_payload_executables_in_path_are_never_selected(tmp_path, monkeypatch):
    sentinel = tmp_path / "EXECUTED"
    for name in ("findmnt", "python3", "dirname", "readlink"):
        path = tmp_path / name
        path.write_text("#!/bin/sh\n/usr/bin/touch " + str(sentinel) + "\nexit 99\n")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert "findmnt" in r.command(["findmnt", "--version"])
    result = subprocess.run(["/bin/bash", str(REPO / "install/lab-provision"), "release", "verify", "--help"],
                            capture_output=True, text=True)
    assert result.returncode == 0 and not sentinel.exists()


@pytest.mark.parametrize("action", ["verify", "list", "build"])
def test_direct_launcher_ignores_untrusted_bash_in_path(tmp_path, action):
    sentinel = tmp_path / "UNTRUSTED_BASH_EXECUTED"
    trap = tmp_path / "bash"
    trap.write_text("#!/bin/sh\n/usr/bin/touch " + str(sentinel) + "\nexit 99\n")
    trap.chmod(0o755)
    # Invoke the documented executable directly: the kernel must choose the trusted shebang.
    result = subprocess.run([str(REPO / "install/lab-provision"), "release", action, "--help"],
                            env={"PATH": str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode == 0 and not sentinel.exists()


def test_guest_and_host_diagnostic_catalogues_agree():
    assert r.ERROR_SCHEMA == h.ERROR_SCHEMA
    assert r.DIAGNOSTICS == h.DIAGNOSTICS


@pytest.mark.parametrize("stage", ["download", "install", "smoke"])
@pytest.mark.parametrize("timeout", [False, True])
def test_guest_emits_distinct_sanitized_stage_failures(stage, timeout, monkeypatch, capsys):
    secret = "https://alice:SECRET_CREDENTIAL@index.invalid/private"
    def process(args, **kwargs):
        if timeout:
            raise subprocess.TimeoutExpired(args, 1200, output=secret.encode(), stderr=secret.encode())
        raise subprocess.CalledProcessError(2, args, output=secret.encode(), stderr=secret.encode())
    monkeypatch.setattr(h.subprocess, "run", process)
    def failed_step(component, *args):
        return h.checked(component, stage, h.run, ["/usr/bin/pip", "--index-url", secret])
    action = "smoke" if stage == "smoke" else "build"
    monkeypatch.setattr(h, action, failed_step)
    assert h.main([action, "--component", "slot"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "SECRET" not in captured.err and secret not in captured.err
    data = r.decode_json(captured.err)
    assert data["component"] == "slot" and data["stage"] == stage
    assert data["code"] == r.DIAGNOSTICS[stage][0]
    assert data["reason"] == ("timeout" if timeout else "command_failed")
    assert data["returncode"] == (None if timeout else 2)
    assert data["detail"] == r.DIAGNOSTICS[stage][1]
    assert len(captured.err) < 512


@pytest.mark.parametrize("stage", ["download", "install", "smoke"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_host_preserves_primary_diagnostic_before_and_after_cleanup(
        model_host, stage, cleanup_fails, monkeypatch, capsys):
    path, config, builders = model_host
    secret = "SECRET_GUEST_LOG_TOKEN"
    original = h.failure("manager", stage, subprocess.CalledProcessError(2, [secret], stderr=secret.encode()))
    envelope = dict(original, detail=secret)  # Even supported guest prose is never trusted for logging.
    helper_stderr = r.json_bytes(envelope)
    host_command, real_process = r.command, subprocess.run
    observed = []

    def process(args, **kwargs):
        if str(args[0]) == "/usr/sbin/pct":
            assert "/tmp/lab-release-builder.py" in args
            raise subprocess.CalledProcessError(1, args, output=secret.encode(), stderr=helper_stderr)
        return real_process(args, **kwargs)
    monkeypatch.setattr(r.subprocess, "run", process)
    monkeypatch.setattr(r, "command", lambda args, **kw: RAW_COMMAND(args, **kw) if args[0] == "pct" else host_command(args, **kw))
    monkeypatch.setattr(builders, "assert_owned", lambda *a, **kw: {})
    def component(self, component):
        self.resources["ubuntu"] = {"vmid": 9801, "state": "owned", "rootfs": "local:own-rootfs"}
        self.helper("ubuntu", "smoke" if stage == "smoke" else "build", component)
    def cleanup(self):
        # This check runs before even the simulated CT deletion.
        assert r.read_json(self.record)["failure"] == original
        observed.append(self.record)
        if cleanup_fails:
            raise r.ReleaseError(secret + " cleanup details must also stay private")
        self.resources["ubuntu"]["state"] = "deleted"
        self.save()
    monkeypatch.setattr(builders, "component", component)
    monkeypatch.setattr(builders, "cleanup", cleanup)
    with pytest.raises(r.BuilderFailure) as error:
        r.build_release("r1", r.git(REPO, "rev-parse", "HEAD"), str(path))
    assert observed
    persisted = r.read_json(observed[0])
    assert persisted["failure"] == error.value.diagnostic == original
    assert ("cleanup_failure" in persisted) == cleanup_fails
    if cleanup_fails:
        assert persisted["cleanup_failure"]["code"] == "BUILDER_CLEANUP_FAILED"
    else:
        assert persisted["resources"]["ubuntu"]["state"] == "deleted"
    captured = capsys.readouterr()
    assert secret not in captured.err + str(error.value) + r.json_bytes(persisted).decode()
    assert not (Path(config["release_root"]) / "r1").exists()


@pytest.mark.parametrize("bad", [b"SECRET_RAW_PIP_LOG", b"x" * 4097, b'{"schema":"unknown","detail":"SECRET"}',
                                  b'{"schema":1,"schema":2,"detail":"SECRET"}'])
def test_host_rejects_unbounded_or_unsupported_diagnostics(bad, monkeypatch):
    def process(args, **kwargs):
        raise subprocess.CalledProcessError(1, args, output=b"SECRET_STDOUT", stderr=bad)
    monkeypatch.setattr(r.subprocess, "run", process)
    with pytest.raises(r.BuilderFailure) as error:
        RAW_COMMAND(["pct", "exec", "9801"], builder_context={"component": "agent", "action": "build"})
    assert error.value.diagnostic == r.builder_diagnostic("agent", "protocol", "invalid_diagnostic")
    assert "SECRET" not in str(error.value)


def test_host_helper_timeout_has_safe_context(monkeypatch):
    def process(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 120, output=b"SECRET", stderr=b"SECRET")
    monkeypatch.setattr(r.subprocess, "run", process)
    with pytest.raises(r.BuilderFailure) as error:
        RAW_COMMAND(["pct", "exec", "9801"], builder_context={"component": "manager", "action": "smoke"})
    assert error.value.diagnostic == r.builder_diagnostic("manager", "smoke", "timeout")


def test_unsaved_primary_diagnostic_prevents_builder_deletion(tmp_path, monkeypatch, capsys):
    builders = r.Builders({}, "a" * 32, tmp_path / "stage", tmp_path / "scratch", tmp_path / "record.json")
    save = builders.save
    def failed_save():
        raise OSError("SECRET_IO_ERROR")
    monkeypatch.setattr(builders, "save", failed_save)
    diagnostic = r.builder_diagnostic("slot", "install", "command_failed", 1)
    builders.remember_failure(diagnostic)
    monkeypatch.setattr(r, "command", lambda *a, **kw: pytest.fail("Unsaved primary failure must block CT deletion"))
    with pytest.raises(r.ReleaseError, match="not durable"):
        builders.cleanup()
    assert "SECRET" not in capsys.readouterr().err
    monkeypatch.setattr(builders, "save", save)
    builders.remember_failure(diagnostic)
    assert builders.failure_persisted and r.read_json(builders.record)["failure"] == diagnostic


def test_nested_checked_step_keeps_primary_diagnostic():
    primary = h.BuilderFailure(h.failure("slot", "download", ValueError("SECRET")))
    def failed():
        raise primary
    with pytest.raises(h.BuilderFailure) as error:
        h.checked("slot", "build", failed)
    assert error.value is primary
