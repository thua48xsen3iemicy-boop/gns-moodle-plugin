"""The console gateway (gateway/, taken over from the plugin) and its generated env file."""

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


def test_gateway_env_refuses_an_incomplete_config(tmp_path):
    with pytest.raises(SystemExit, match="127.0.0.1"):
        gateway_env(make_settings(tmp_path))  # Gateway on another host: not the agreed layout
    settings = make_settings(tmp_path, gateway_internal_url="http://127.0.0.1:8080")
    settings.slots[201] = settings.slots[201].model_copy(update={"console_token": ""})
    with pytest.raises(SystemExit, match="console_token"):
        gateway_env(settings)
