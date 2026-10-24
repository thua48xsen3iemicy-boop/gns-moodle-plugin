"""The console gateway (gateway/, taken over from the plugin) and its generated env file."""

import base64
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import make_settings

from lab_manager.admin import gateway_env

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(importlib.util.find_spec("aiohttp") is None, reason="aiohttp is not installed")
def test_gateway_against_a_fake_slot():
    run = subprocess.run([sys.executable, str(ROOT / "gateway" / "tests" / "check_gateway.py")],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and "ALL OK" in run.stdout, run.stdout + run.stderr


def parse(text):
    """What systemd makes of EnvironmentFile lines with single-quoted values."""
    return dict((k, v[1:-1]) for k, v in (line.split("=", 1) for line in text.splitlines() if line[:1] != "#"))


def test_gateway_env_from_the_manager_config(tmp_path):
    settings = make_settings(tmp_path, gateway_internal_url="http://127.0.0.1:8080")
    env = parse(gateway_env(settings))
    assert env["GW_HOST"] == "127.0.0.1" and env["GW_PORT"] == "8080"
    assert env["GW_INTERNAL_KEY"] == "gw-key"
    assert env["GW_ALLOWED_ORIGINS"] == "https://moodle.example.edu,https://other.example.edu"
    assert env["GW_SLOTS"] == '{"10.0.0.1":"' + "c" * 40 + '","10.0.0.2":"' + "c" * 40 + '"}'


GATEWAY_URL = "http://127.0.0.1:8080"
BROKEN_PEM = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"


def demo_ca(tmp_path):
    subprocess.run(["bash", str(ROOT.parent / "agent" / "make-demo-pki.sh"), "localhost=127.0.0.1"],
                   cwd=tmp_path, capture_output=True, check=True, timeout=30)
    return tmp_path / "pki" / "node-server-ca.crt"


def load_gateway(monkeypatch, **env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("gateway_under_test", ROOT / "gateway" / "console_gateway.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gateway_env_carries_the_slot_ca_inline(tmp_path):
    # The unit hides /etc/lab-manager from the gateway's dynamic user: a path there cannot be read.
    ca = demo_ca(tmp_path)
    env = parse(gateway_env(make_settings(tmp_path, gateway_internal_url=GATEWAY_URL, slot_ca_file=ca)))
    assert "GW_SLOT_CA_FILE" not in env
    assert base64.b64decode(env["GW_SLOT_CA_B64"]).decode() == ca.read_text()
    env = parse(gateway_env(make_settings(tmp_path, gateway_internal_url=GATEWAY_URL)))
    assert env["GW_SLOT_CA_B64"] == ""


def test_gateway_env_refuses_an_unusable_slot_ca(tmp_path):
    junk = tmp_path / "junk.crt"
    junk.write_text("not a certificate")
    broken = tmp_path / "broken.crt"
    broken.write_text(BROKEN_PEM)
    for path in (tmp_path / "missing.crt", junk, broken):
        with pytest.raises(SystemExit, match="SLOT_CA_FILE"):
            gateway_env(make_settings(tmp_path, gateway_internal_url=GATEWAY_URL, slot_ca_file=path))


@pytest.mark.skipif(importlib.util.find_spec("aiohttp") is None, reason="aiohttp is not installed")
def test_gateway_tls_context_from_the_inline_ca(tmp_path, monkeypatch):
    ca = demo_ca(tmp_path)
    env = parse(gateway_env(make_settings(tmp_path, gateway_internal_url=GATEWAY_URL, slot_ca_file=ca)))
    gateway = load_gateway(monkeypatch, GW_SLOT_CA_B64=env["GW_SLOT_CA_B64"])
    assert len(gateway.slot_tls_context().get_ca_certs()) == 1


@pytest.mark.skipif(importlib.util.find_spec("aiohttp") is None, reason="aiohttp is not installed")
def test_gateway_names_the_unusable_slot_ca(tmp_path, monkeypatch):
    gateway = load_gateway(monkeypatch, GW_SLOT_CA_FILE=str(tmp_path / "missing.crt"))
    with pytest.raises(RuntimeError, match="GW_SLOT_CA_FILE"):
        gateway.slot_tls_context()
    gateway = load_gateway(monkeypatch, GW_SLOT_CA_FILE="", GW_SLOT_CA_B64="not base64!")
    with pytest.raises(RuntimeError, match="GW_SLOT_CA_B64"):
        gateway.slot_tls_context()
    gateway = load_gateway(monkeypatch, GW_SLOT_CA_B64=base64.b64encode(BROKEN_PEM.encode()).decode())
    with pytest.raises(RuntimeError, match="valid PEM"):
        gateway.slot_tls_context()


def test_gateway_env_refuses_an_incomplete_config(tmp_path):
    with pytest.raises(SystemExit, match="127.0.0.1"):
        gateway_env(make_settings(tmp_path))  # Gateway on another host: not the agreed layout
    settings = make_settings(tmp_path, gateway_internal_url="http://127.0.0.1:8080")
    settings.slots[201] = settings.slots[201].model_copy(update={"console_token": ""})
    with pytest.raises(SystemExit, match="console_token"):
        gateway_env(settings)
