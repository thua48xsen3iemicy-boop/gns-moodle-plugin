"""Install into a temporary root; never invoke the real systemctl."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

RECIPE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("lab_uplink_installer", RECIPE / "tools/install_guest.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def prepare(tmp_path, *extra):
    root = tmp_path / "guest"
    return root, subprocess.run(
        [sys.executable, str(RECIPE / "tools/install_guest.py"), "prepare", "--root", str(root), *extra],
        cwd=tmp_path, capture_output=True, text=True, check=False)


def test_prepare_installs_files_and_writes_private_credentials(tmp_path):
    root, result = prepare(tmp_path, "--pppoe-user", "lab", "--pppoe-password", 'S3cret"Pw\\x')
    assert result.returncode == 0, result.stderr
    assert "S3cret" not in result.stdout + result.stderr
    creds = root / "etc/lab-uplink/pppoe.options"
    assert creds.read_text() == 'user "lab"\npassword "S3cret\\"Pw\\\\x"\n'
    assert creds.stat().st_mode & 0o777 == 0o600
    assert (root / "etc/lab-uplink").stat().st_mode & 0o777 == 0o700
    for unit in installer.UNITS:
        assert (root / "etc/systemd/system" / unit).is_file()
    assert (root / "opt/lab-uplink/uplink_net/setup.py").is_file()
    assert (root / "etc/sysctl.d/90-lab-uplink.conf").is_file()
    assert not (root / "etc/systemd/system/multi-user.target.wants").exists()  # nothing is enabled by prepare


def test_prepare_needs_credentials(tmp_path):
    _, result = prepare(tmp_path)
    assert result.returncode != 0 and "--pppoe-user" in result.stderr


def test_prepare_refuses_a_multiline_password(tmp_path):
    root, result = prepare(tmp_path, "--pppoe-user", "lab", "--pppoe-password", "a\nb")
    assert result.returncode != 0 and not (root / "etc/lab-uplink").exists()


def test_activate_needs_prepare_first(tmp_path):
    with pytest.raises(RuntimeError):
        installer.activate(tmp_path / "guest", run=lambda *a, **k: None)


def test_activate_enables_the_units(tmp_path):
    root, result = prepare(tmp_path, "--pppoe-user", "lab", "--pppoe-password", "x")
    assert result.returncode == 0
    calls = []
    installer.activate(root, run=lambda cmd, **kw: calls.append(cmd))
    enabled = calls[0]
    assert enabled[:2] == ["systemctl", "enable"]
    assert set(installer.UNITS) <= set(enabled) and "qemu-guest-agent.service" in enabled


def test_the_repository_example_has_only_placeholders():
    text = (RECIPE / "ppp/pppoe.options.example").read_text()
    assert "REPLACE_WITH_PPPOE_USER" in text and "REPLACE_WITH_PPPOE_PASSWORD" in text


def test_units_reference_the_installed_paths():
    for unit in installer.UNITS:
        text = (RECIPE / "system" / unit).read_text()
        assert "[Unit]" in text and "[Service]" in text and "[Install]" in text
    assert "/opt/lab-uplink" in (RECIPE / "system/lab-uplink-setup.service").read_text()
    assert "/run/lab-uplink/pppd.options" in (RECIPE / "system/lab-uplink-pppoe.service").read_text()
