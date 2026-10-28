import json
import os

import pytest

from lab_manager.config import ConfigError, load

SECRET = "z" * 48


def write(path, data, mode=0o600):
    path.write_text(json.dumps(data))
    os.chmod(path, mode)
    return str(path)


@pytest.fixture
def env(tmp_path):
    return {
        "DATABASE_URL": "postgresql:///lab_manager",
        "MANAGER_SITES_FILE": write(tmp_path / "sites.json",
                                    [{"url": "https://m.example/", "slug": "main", "secret": SECRET}]),
        "MANAGER_SLOTS_FILE": write(tmp_path / "slots.json",
                                    [{"vmid": 201, "address": "10.0.0.1", "api_token": SECRET}]),
        "PVE_NODES": "pve1:8006, pve2:8006",
        "PVE_TOKEN_ID": "lab-manager@pve!manager",
        "PVE_TOKEN_SECRET": SECRET,
        "PVE_CA_FILE": "/etc/lab-manager/pve-root-ca.pem",
        "AGENT_URL_TEMPLATE": "https://{node}.mgmt:8443",
        "AGENT_CA_FILE": "/etc/lab-manager/node-server-ca.crt",
        "AGENT_CERT_FILE": "/etc/lab-manager/manager.crt",
        "AGENT_KEY_FILE": "/etc/lab-manager/manager.key",
        "GATEWAY_INTERNAL_URL": "http://127.0.0.1:8080",
        "GATEWAY_INTERNAL_KEY": SECRET,
        "GATEWAY_PUBLIC_URL": "wss://gw.example",
    }


def test_valid_config(env):
    s = load(env)
    assert s.site("https://m.example").slug == "main"  # Trailing slash normalized
    assert s.pve_nodes == ("pve1:8006", "pve2:8006")
    assert s.placeholder_student_key == "placeholder"
    assert s.agent_start_actions == ("init_slot",)
    assert s.node_guest_memory_mb == 0 and s.node_cpu_overcommit == 0  # No node limits by default
    env.update(NODE_GUEST_MEMORY_MB="118784", NODE_CPU_OVERCOMMIT="1.5")
    assert (load(env).node_guest_memory_mb, load(env).node_cpu_overcommit) == (118784, 1.5)
    env["AGENT_START_ACTIONS"] = ""
    assert load(env).agent_start_actions == ()
    assert s.slot_lab_failure_limit == 3
    env["SLOT_LAB_FAILURE_LIMIT"] = "0"
    assert load(env).slot_lab_failure_limit == 0  # Off
    assert SECRET not in repr(s)


def test_start_timeout_covers_the_agent_start_budget(env):
    s = load(env)
    assert s.agent_start_timeout == 720  # 480 s of the agent's default budget + 60 s of reserve + the mount edit
    env["AGENT_START_TIMEOUT"] = "540"
    assert load(env).agent_start_timeout == 540
    for value in ("420", "539"):
        env["AGENT_START_TIMEOUT"] = value
        with pytest.raises(ConfigError, match="AGENT_START_TIMEOUT"):
            load(env)


def test_ceph_layout(env):
    s = load(env)
    assert (s.agent_ceph_root, s.agent_code_dir, s.agent_students_dir) == (
        "/mnt/pve/cfs", "gns3-ro/slot", "gns3-rw/students")
    env.update(LAB_CEPH_ROOT="/mnt/pve/data/", LAB_CODE_DIR="/slot/", LAB_STUDENTS_DIR="students")
    s = load(env)
    assert (s.agent_ceph_root, s.agent_code_dir, s.agent_students_dir) == (
        "/mnt/pve/data", "slot", "students")


@pytest.mark.parametrize("name", ["DATABASE_URL", "PVE_TOKEN_SECRET", "GATEWAY_INTERNAL_KEY",
                                  "MANAGER_SITES_FILE", "PVE_NODES", "AGENT_URL_TEMPLATE",
                                  "AGENT_CERT_FILE", "AGENT_KEY_FILE", "AGENT_CA_FILE"])
def test_missing_required_value_fails_closed(env, name):
    env[name] = ""
    with pytest.raises(ConfigError, match=name):
        load(env)


def test_group_readable_secrets_file_is_refused(env, tmp_path):
    os.chmod(env["MANAGER_SLOTS_FILE"], 0o640)
    with pytest.raises(ConfigError, match="chmod 600"):
        load(env)


def test_short_secret_rejected_without_leaking_it(env, tmp_path):
    write(tmp_path / "sites.json", [{"url": "https://m", "slug": "main", "secret": "short-secret-value"}])
    with pytest.raises(ConfigError) as exc:
        load(env)
    assert "short-secret-value" not in str(exc.value)


@pytest.mark.parametrize("key,value", [("PVE_TOKEN_ID", "root@pam"), ("PVE_NODES", "https://pve1"),
                                       ("GATEWAY_PUBLIC_URL", "https://gw"),
                                       ("AGENT_URL_TEMPLATE", "http://{node}:8443"),
                                       ("AGENT_URL_TEMPLATE", "https://agent:8443"),
                                       ("AGENT_START_ACTIONS", "rm_rf"), ("SLOT_CODE_REVISION", "../x"),
                                       ("PLACEHOLDER_STUDENT_KEY", "a/b"), ("LAB_PERSISTENCE", "fresh"),
                                       ("LAB_CEPH_ROOT", "/mnt/../etc"), ("AGENT_START_TIMEOUT", "330"),
                                       ("LAB_CODE_DIR", "../slot"), ("LAB_CODE_DIR", "a//b"),
                                       ("LAB_STUDENTS_DIR", ""), ("LAB_STUDENTS_DIR", ".hidden/students"),
                                       ("NODE_GUEST_MEMORY_MB", "116G"), ("NODE_CPU_OVERCOMMIT", "-1"),
                                       ("NODE_CPU_OVERCOMMIT", "two"), ("SLOT_LAB_FAILURE_LIMIT", "-1"),
                                       ("SLOT_LAB_FAILURE_LIMIT", "few")])
def test_invalid_values(env, key, value):
    env[key] = value
    with pytest.raises(ConfigError):
        load(env)


def test_slot_tokens_must_be_unique(env, tmp_path):
    env["MANAGER_SLOTS_FILE"] = write(tmp_path / "slots2.json", [
        {"vmid": 201, "address": "10.0.0.1", "api_token": SECRET},
        {"vmid": 202, "address": "10.0.0.2", "api_token": SECRET},
    ])
    with pytest.raises(ConfigError, match="unique") as exc:
        load(env)
    assert SECRET not in str(exc.value)


def test_images_file_and_site_limits(env, tmp_path):
    from pathlib import Path

    env["IMAGES_FILE"] = str(Path(__file__).resolve().parents[1] / "examples" / "images.json")
    env["MANAGER_SITES_FILE"] = write(tmp_path / "sites2.json", [
        {"url": "https://m.example/", "slug": "main", "secret": SECRET,
         "limits": {"max_nodes": 30, "max_memory_mb": 16384, "images": ["debian12"]}}])
    s = load(env)
    assert sorted(s.images) == ["cisco-iou-l2", "debian12", "linux-switch", "vpcs"]
    limits = s.site("https://m.example").limits
    assert limits.allows("debian12") and not limits.allows("vpcs") and limits.max_cpus is None

    env["MANAGER_SITES_FILE"] = write(tmp_path / "sites3.json", [
        {"url": "https://m.example/", "slug": "main", "secret": SECRET, "limits": {"images": ["cisco-asa"]}}])
    with pytest.raises(ConfigError, match="unknown images"):
        load(env)
    env["IMAGES_FILE"] = str(tmp_path / "missing.json")
    with pytest.raises(ConfigError):
        load(env)


def test_sites_without_images_file_keep_working(env):
    s = load(env)
    assert s.images is None and s.site("https://m.example").limits.allows("anything")
