"""Network setup scripts of the lab constructor: reference text, sh -n and a run against a fake guest root."""

import os
import shutil
import stat
import subprocess
from ipaddress import IPv4Address, IPv4Interface
from pathlib import Path

import pytest

from lab_manager import netconfig
from lab_manager.netconfig import Iface, ScriptTooLarge, script

ONE = [Iface(0, IPv4Interface("192.168.10.1/24"), IPv4Address("192.168.10.254"))]
TWO = [Iface(0, IPv4Interface("192.168.10.1/24"), IPv4Address("192.168.10.254")),
       Iface(1, IPv4Interface("10.0.0.5/30"))]
# Interface names in the order that does NOT follow the MACs: the adapter is found by MAC only.
NICS = {"ens9": "0c:aa:00:00:00:00", "ens3": "0c:aa:00:00:00:01", "ens4": "0C:AA:00:00:00:02"}
STUBS = ("systemctl", "nmcli", "netplan", "networkctl", "ifup", "ifdown")


def run(mechanism, ifaces, tmp_path, *, stubs, active=(), nics=NICS, files=None):
    root = tmp_path / "root"
    for name, mac in nics.items():
        (root / "sys/class/net" / name).mkdir(parents=True)
        (root / "sys/class/net" / name / "address").write_text(mac + "\n")
        (root / "sys/class/net" / name / "type").write_text("1\n")
    (root / "sys/class/net/lo").mkdir(parents=True)
    (root / "sys/class/net/lo/address").write_text("00:00:00:00:00:00\n")
    (root / "sys/class/net/lo/type").write_text("772\n")
    for name, text in (files or {}).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in stubs:
        stub = bin_dir / name
        if name == "systemctl":
            stub.write_text('#!/bin/sh\necho "systemctl $*" >> "$LOG"\n'
                            'for u in $ACTIVE; do [ "$u" = "$3" ] && exit 0; done\nexit 3\n')
        else:
            stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "$LOG"\n')
        stub.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "ROOT": str(root), "LOG": str(log), "ACTIVE": " ".join(active)}
    done = subprocess.run(["/bin/sh", "-c", script(mechanism, ifaces)], capture_output=True, text=True, env=env)
    calls = log.read_text().splitlines() if log.exists() else []
    return done, root, [c for c in calls if not c.startswith("systemctl")]


@pytest.mark.parametrize("mechanism", netconfig.MECHANISMS)
@pytest.mark.parametrize("ifaces", [ONE, TWO])
def test_syntax_and_size(mechanism, ifaces):
    text = script(mechanism, ifaces)
    assert len(text) < netconfig.MAX_SCRIPT and "\r" not in text
    assert subprocess.run(["/bin/sh", "-n", "-c", text]).returncode == 0
    assert script(mechanism, ifaces) == text  # Deterministic
    assert script(mechanism, list(reversed(ifaces))) == text  # By adapter, not by the order given


def test_script_has_only_validated_values():
    text = script("netplan", ONE)
    assert text.endswith('cfg 0 192.168.10.1/24 255.255.255.0 192.168.10.254\nfinish\n')
    with pytest.raises(ValueError):
        script("template", ONE)
    with pytest.raises(ScriptTooLarge):
        script("ifupdown", [Iface(n, IPv4Interface(f"10.{n}.0.1/24")) for n in range(16)] * 12)


def test_ifupdown(tmp_path):
    old = ("auto lo\niface lo inet loopback\n\nallow-hotplug ens3 ens4\niface ens3 inet dhcp\n    metric 10\n"
           "iface ens4 inet dhcp\n\nauto ens9\niface ens9 inet dhcp\n")
    done, root, calls = run("ifupdown", TWO, tmp_path, stubs=("systemctl", "ifup", "ifdown"),
                            files={"etc/network/interfaces": old, "etc/network/interfaces.d/extra": "auto ens3\n"})
    assert done.returncode == 0, done.stderr
    # Adapter 0 is the lowest MAC (ens9), adapter 1 the next (ens3).
    assert (root / "etc/network/interfaces.d/gns3lab").read_text() == (
        "auto ens9\niface ens9 inet static\n    address 192.168.10.1\n    netmask 255.255.255.0\n"
        "    gateway 192.168.10.254\nauto ens3\niface ens3 inet static\n    address 10.0.0.5\n"
        "    netmask 255.255.255.252\n")
    interfaces = (root / "etc/network/interfaces").read_text()
    assert "iface ens3 inet dhcp" not in interfaces.replace("# gns3lab: iface ens3 inet dhcp", "")
    assert "# gns3lab: iface ens3 inet dhcp\n# gns3lab:     metric 10\n" in interfaces
    assert "allow-hotplug ens4\n" in interfaces and "iface ens4 inet dhcp" in interfaces  # ens4 is not ours
    assert "# gns3lab: auto ens9\n# gns3lab: iface ens9 inet dhcp" in interfaces
    assert "iface lo inet loopback" in interfaces and interfaces.endswith("source /etc/network/interfaces.d/*\n")
    assert (root / "etc/network/interfaces.d/extra").read_text() == "# gns3lab: auto ens3\n"
    assert calls == ["ifdown --force ens9", "ifup ens9", "ifdown --force ens3", "ifup ens3"]
    assert not list(root.glob("etc/network/**/*.gns3lab-tmp"))


def test_ifupdown_keeps_an_existing_source_line(tmp_path):
    done, root, _ = run("ifupdown", ONE, tmp_path, stubs=("systemctl", "ifup", "ifdown"),
                        files={"etc/network/interfaces": "source /etc/network/interfaces.d/*\n"})
    assert done.returncode == 0, done.stderr
    assert (root / "etc/network/interfaces").read_text().count("source") == 1


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_networkmanager(tmp_path):
    done, root, calls = run("networkmanager", TWO, tmp_path, stubs=("systemctl", "nmcli"), active=("NetworkManager",))
    assert done.returncode == 0, done.stderr
    d = root / "etc/NetworkManager"
    assert (d / "conf.d/90-gns3lab.conf").read_text() == "[main]\nno-auto-default=*\n"
    first, second = (d / f"system-connections/gns3lab-0caa0000000{n}.nmconnection" for n in (0, 1))
    assert first.read_text() == (
        "[connection]\nid=gns3lab-0caa00000000\ntype=ethernet\nuuid=00000000-0000-4000-8000-0caa00000000\n"
        "autoconnect-priority=100\n\n[ethernet]\nmac-address=0c:aa:00:00:00:00\n\n"
        "[ipv4]\nmethod=manual\naddress1=192.168.10.1/24,192.168.10.254\n")
    assert "address1=10.0.0.5/30\n" in second.read_text() and "mac-address=0c:aa:00:00:00:01" in second.read_text()
    assert mode(first) == 0o600 and mode(second) == 0o600
    assert calls == ["nmcli connection reload", "nmcli connection up gns3lab-0caa00000000",
                     "nmcli connection up gns3lab-0caa00000001"]


def test_netplan(tmp_path):
    done, root, calls = run("netplan", TWO, tmp_path, stubs=("systemctl", "netplan"), active=("systemd-networkd",), files={
        "etc/netplan/50-cloud-init.yaml": "network: {}\n", "etc/netplan/01-x.yaml": "network: {}\n",
        "etc/cloud/cloud.cfg.d/90-dpkg.cfg": "x\n"})
    assert done.returncode == 0, done.stderr
    f = root / "etc/netplan/90-gns3lab.yaml"
    assert f.read_text() == (
        'network:\n  version: 2\n  renderer: networkd\n  ethernets:\n    gns3lab0:\n      match:\n        macaddress: "0c:aa:00:00:00:00"\n'
        '      dhcp4: false\n      addresses: [192.168.10.1/24]\n      routes:\n        - to: 0.0.0.0/0\n'
        '          via: 192.168.10.254\n    gns3lab1:\n      match:\n        macaddress: "0c:aa:00:00:00:01"\n'
        '      dhcp4: false\n      addresses: [10.0.0.5/30]\n')
    assert mode(f) == 0o600
    assert sorted(p.name for p in (root / "etc/netplan").glob("*.yaml")) == ["90-gns3lab.yaml"]
    assert sorted(p.name for p in (root / "etc/netplan/gns3lab-disabled").iterdir()) == [
        "01-x.yaml", "50-cloud-init.yaml"]
    assert (root / "etc/cloud/cloud.cfg.d/99-gns3lab-network.cfg").read_text() == "network: {config: disabled}\n"
    assert calls == ["netplan apply"]


def test_netplan_without_cloud_init_leaves_etc_cloud_alone(tmp_path):
    done, root, _ = run("netplan", ONE, tmp_path, stubs=("systemctl", "netplan"), active=("systemd-networkd",))
    assert done.returncode == 0, done.stderr
    assert not (root / "etc/cloud").exists()


def test_networkd(tmp_path):
    done, root, calls = run("networkd", TWO, tmp_path, stubs=("systemctl", "networkctl"), active=("systemd-networkd",))
    assert done.returncode == 0, done.stderr
    d = root / "etc/systemd/network"
    assert (d / "10-gns3lab-0caa00000000.network").read_text() == (
        "[Match]\nMACAddress=0c:aa:00:00:00:00\n\n[Network]\nDHCP=no\nAddress=192.168.10.1/24\n"
        "Gateway=192.168.10.254\n")
    assert "Gateway" not in (d / "10-gns3lab-0caa00000001.network").read_text()
    assert calls == ["networkctl reload", "networkctl reconfigure ens9", "networkctl reconfigure ens3"]


@pytest.mark.parametrize("nics", [
    {"ens3": "0c:aa:00:00:00:00", "ens4": "0c:aa:00:00:00:01"},
    {"ens4": "0c:aa:00:00:00:00", "ens3": "0c:aa:00:00:00:01"},
    {"enp0s9": "0c:aa:00:00:00:01", "a1": "0c:aa:00:00:00:00"},
])
def test_interface_is_found_by_mac_for_any_names(tmp_path, nics):
    done, root, calls = run("networkd", ONE[:1] + [Iface(1, IPv4Interface("10.0.0.5/30"))], tmp_path,
                            stubs=("systemctl", "networkctl"), active=("systemd-networkd",), nics=nics)
    assert done.returncode == 0, done.stderr
    by_mac = {mac: name for name, mac in nics.items()}
    assert calls[1:] == [f"networkctl reconfigure {by_mac['0c:aa:00:00:00:00']}",
                         f"networkctl reconfigure {by_mac['0c:aa:00:00:00:01']}"]


def test_a_missing_adapter_fails(tmp_path):
    done, root, calls = run("networkd", TWO, tmp_path, stubs=("systemctl", "networkctl"),
                            active=("systemd-networkd",), nics={"ens3": "0c:aa:00:00:00:00"})
    assert done.returncode != 0 and "adapter 1 not found" in done.stderr


@pytest.mark.parametrize("mechanism,stubs,active,found", [
    ("networkmanager", ("systemctl", "ifup"), (), "ifupdown"),
    ("networkd", ("systemctl", "nmcli"), ("NetworkManager",), "networkmanager"),
    ("netplan", ("systemctl",), ("systemd-networkd",), "networkd"),
    ("netplan", ("systemctl", "netplan"), ("NetworkManager",), "networkmanager"),
    ("netplan", ("systemctl", "netplan"), (), "none"),
    ("ifupdown", ("systemctl", "ifup"), ("NetworkManager",), "networkmanager"),
    ("ifupdown", ("systemctl", "ifup"), ("systemd-networkd",), "networkd"),
    ("networkd", ("systemctl",), (), "none"),
])
def test_wrong_mechanism_fails_before_any_change(tmp_path, mechanism, stubs, active, found):
    done, root, calls = run(mechanism, ONE, tmp_path, stubs=stubs, active=active)
    assert done.returncode != 0
    assert done.stderr.strip() == f"net_config mismatch: declared {mechanism}, found {found}"
    assert calls == [] and not (root / "etc").exists()


MECHANISM_SETUP = {
    "ifupdown": (("systemctl", "ifup", "ifdown"), ()),
    "networkmanager": (("systemctl", "nmcli"), ("NetworkManager",)),
    "netplan": (("systemctl", "netplan"), ("systemd-networkd",)),
    "networkd": (("systemctl", "networkctl"), ("systemd-networkd",)),
}


@pytest.mark.parametrize("mechanism", netconfig.MECHANISMS)
def test_cloud_init_network_is_switched_off_by_every_mechanism(tmp_path, mechanism):
    stubs, active = MECHANISM_SETUP[mechanism]
    done, root, _ = run(mechanism, ONE, tmp_path, stubs=stubs, active=active,
                        files={"etc/cloud/cloud.cfg.d/90-dpkg.cfg": "x\n"})
    assert done.returncode == 0, done.stderr
    assert (root / "etc/cloud/cloud.cfg.d/99-gns3lab-network.cfg").read_text() == "network: {config: disabled}\n"
    assert (root / "etc/cloud/cloud.cfg.d/90-dpkg.cfg").read_text() == "x\n"


@pytest.mark.parametrize("mechanism", netconfig.MECHANISMS)
def test_cloud_init_is_left_alone_when_the_guest_has_none(tmp_path, mechanism):
    stubs, active = MECHANISM_SETUP[mechanism]
    done, root, _ = run(mechanism, ONE, tmp_path, stubs=stubs, active=active)
    assert done.returncode == 0, done.stderr
    assert not (root / "etc/cloud").exists()


def test_ifupdown_comments_out_the_cloud_init_stanza(tmp_path):
    done, root, _ = run("ifupdown", TWO, tmp_path, stubs=("systemctl", "ifup", "ifdown"), files={
        "etc/network/interfaces": "source /etc/network/interfaces.d/*\n",
        "etc/network/interfaces.d/50-cloud-init": "auto ens9\niface ens9 inet dhcp\n\nauto ens4\niface ens4 inet dhcp\n",
        "etc/cloud/cloud.cfg.d/90-dpkg.cfg": "x\n"})
    assert done.returncode == 0, done.stderr
    assert (root / "etc/network/interfaces.d/50-cloud-init").read_text() == (
        "# gns3lab: auto ens9\n# gns3lab: iface ens9 inet dhcp\n# gns3lab: \nauto ens4\niface ens4 inet dhcp\n")
    assert (root / "etc/cloud/cloud.cfg.d/99-gns3lab-network.cfg").exists()


def test_netplan_on_networkmanager_is_refused_before_any_change(tmp_path):
    done, root, calls = run("netplan", ONE, tmp_path, stubs=("systemctl", "netplan", "nmcli"),
                            active=("NetworkManager",), files={
                                "etc/netplan/01-network-manager-all.yaml": "network: {renderer: NetworkManager}\n",
                                "etc/cloud/cloud.cfg.d/90-dpkg.cfg": "x\n"})
    assert done.returncode != 0
    assert done.stderr.strip() == "net_config mismatch: declared netplan, found networkmanager"
    assert calls == []
    assert (root / "etc/netplan/01-network-manager-all.yaml").exists()
    assert not (root / "etc/netplan/gns3lab-disabled").exists()
    assert not (root / "etc/cloud/cloud.cfg.d/99-gns3lab-network.cfg").exists()
