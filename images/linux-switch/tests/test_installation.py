"""Install the complete profile into a temporary root; never invoke real systemctl."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from switch_api.preflight import NETWORK_FILES, check_network

RECIPE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("guest_installer", RECIPE / "tools/install_guest.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_prepare_from_another_directory_keeps_network_untouched(tmp_path):
    root = tmp_path / "guest"
    old = root / "etc/systemd/network/old.network"
    old.parent.mkdir(parents=True)
    old.write_text("original SSH network")
    interfaces = root / "etc/network/interfaces"
    interfaces.parent.mkdir(parents=True)
    interfaces.write_text("allow-hotplug ens3\niface ens3 inet dhcp\n")
    result = subprocess.run(
        [sys.executable, str(RECIPE / "tools/install_guest.py"), "prepare", "--root", str(root)],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert old.read_text() == "original SSH network"
    assert "inet dhcp" in interfaces.read_text()
    assert not (root / "etc/sysctl.d/90-slot-switch.conf").exists()
    assert sorted(p.name for p in old.parent.iterdir()) == ["old.network"]
    check_network(root / "opt/switch-api/network-profile")
    assert (root / "usr/local/sbin/switchctl").stat().st_mode & 0o777 == 0o755
    assert (root / "opt/switch-api/switch_api/preflight.py").is_file()
    assert (root / "opt/switch-api/examples/access-trunk-5ports.json").is_file()
    assert not (root / "etc/systemd/system/multi-user.target.wants").exists()


@pytest.mark.parametrize("missing", NETWORK_FILES)
def test_incomplete_staging_cannot_disable_ssh(tmp_path, missing):
    installer.prepare(tmp_path)
    (tmp_path / "opt/switch-api/network-profile" / missing).unlink()
    commands = []
    with pytest.raises(RuntimeError, match="Incomplete switch network profile"):
        installer.activate(tmp_path, command=lambda *args, **kwargs: commands.append(args))
    assert not commands
    assert not (tmp_path / "etc/systemd/network").exists()


def test_activate_installs_profiles_before_enabling_and_masks_ssh_last(tmp_path):
    installer.prepare(tmp_path)
    old = tmp_path / "etc/systemd/network/01-previous.network"
    old.parent.mkdir(parents=True)
    old.write_text("old settings")
    cloud = tmp_path / "etc/cloud"
    cloud.mkdir()
    commands = []
    def command(args, **kwargs):
        check_network(tmp_path / "etc/systemd/network")
        commands.append(args)
        assert kwargs["check"] is True
    installer.activate(tmp_path, command=command)
    assert not old.exists()
    backups = list((tmp_path / "var/backups/switch-network").glob("*/01-previous.network"))
    assert len(backups) == 1 and backups[0].read_text() == "old settings"
    assert (cloud / "cloud-init.disabled").exists()
    assert commands[-1] == ["systemctl", "mask", "ssh.service", "ssh.socket"]
    assert not any(token in {"--now", "restart", "reboot", "sshd.service"}
                   for args in commands for token in args)
    for name in NETWORK_FILES:
        assert (tmp_path / "etc/systemd/network" / name).read_bytes() == (RECIPE / "network" / name).read_bytes()


def test_service_preflight_rejects_missing_and_malformed_profiles(tmp_path):
    with pytest.raises(RuntimeError):
        check_network(tmp_path)
    for name in NETWORK_FILES:
        (tmp_path / name).write_bytes((RECIPE / "network" / name).read_bytes())
    check_network(tmp_path)
    (tmp_path / "10-slot-br0.netdev").write_text("[NetDev]\nName=wrong\nKind=bridge\n")
    with pytest.raises(RuntimeError, match="Name=br0"):
        check_network(tmp_path)
    unit = (RECIPE / "system/switch-api.service").read_text()
    assert "ExecStartPre=/usr/bin/python3 -m switch_api.preflight" in unit


def test_offline_root_cannot_activate_host_services(tmp_path):
    result = subprocess.run(
        [sys.executable, str(RECIPE / "tools/install_guest.py"), "activate", "--root", str(tmp_path)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "activate must run inside the guest" in result.stderr


def test_activation_removes_ifupdown_hotplug_and_stages_no_address_policy(tmp_path):
    installer.prepare(tmp_path)
    legacy = tmp_path / "etc/network"
    (legacy / "interfaces.d").mkdir(parents=True)
    original = "source /etc/network/interfaces.d/*\nallow-hotplug ens3\niface ens3 inet dhcp\n"
    (legacy / "interfaces").write_text(original)
    (legacy / "interfaces.d/extra").write_text("auto ens4\niface ens4 inet6 auto\n")
    commands = []
    installer.activate(tmp_path, command=lambda args, **kwargs: commands.append(args))
    assert (legacy / "interfaces").read_text() == installer.LOOPBACK_ONLY
    assert not list((legacy / "interfaces.d").iterdir())
    backups = list((tmp_path / "var/backups/switch-network").glob("*/ifupdown/interfaces"))
    assert len(backups) == 1 and backups[0].read_text() == original
    assert (backups[0].parent / "interfaces.d/extra").read_text().startswith("auto ens4")
    assert (tmp_path / "etc/sysctl.d/90-slot-switch.conf").read_bytes() == (RECIPE / "sysctl/90-slot-switch.conf").read_bytes()
    masked = {unit for args in commands if args[:2] == ["systemctl", "mask"] for unit in args[2:]}
    assert {"ifup@.service", "dhcpcd.service", "dhclient.service", "avahi-autoipd.service"} <= masked
    assert not any("--now" in args or "sysctl" in args for args in commands)
    # Repeated activation preserves the original recovery copy and leaves no DHCP stanza.
    installer.activate(tmp_path, command=lambda *args, **kwargs: None)
    assert backups[0].read_text() == original
    assert "dhcp" not in (legacy / "interfaces").read_text()
