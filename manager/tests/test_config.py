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
    (tmp_path / "students").mkdir()
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
        "STUDENT_ROOT": str(tmp_path / "students"),
        "AGENT_URL_TEMPLATE": "https://{node}.mgmt:8443",
        "AGENT_CA_FILE": "/etc/lab-manager/agent-ca.pem",
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
    assert s.lab_persistence == "fresh" and s.placeholder_student_key == "placeholder"
    assert s.agent_start_actions == ()
    assert SECRET not in repr(s)


@pytest.mark.parametrize("name", ["DATABASE_URL", "PVE_TOKEN_SECRET", "GATEWAY_INTERNAL_KEY",
                                  "MANAGER_SITES_FILE", "PVE_NODES", "STUDENT_ROOT", "AGENT_URL_TEMPLATE",
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
                                       ("STUDENT_ROOT", "/srv/../etc"), ("GATEWAY_PUBLIC_URL", "https://gw"),
                                       ("AGENT_URL_TEMPLATE", "http://{node}:8443"),
                                       ("AGENT_URL_TEMPLATE", "https://agent:8443"),
                                       ("AGENT_START_ACTIONS", "rm_rf"), ("SLOT_CODE_REVISION", "../x"),
                                       ("PLACEHOLDER_STUDENT_KEY", "a/b"), ("LAB_PERSISTENCE", "pinned")])
def test_invalid_values(env, key, value):
    env[key] = value
    with pytest.raises(ConfigError):
        load(env)
