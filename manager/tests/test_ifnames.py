"""Random guest interface names: the names, the script against a fake guest root, the lab definition and the ticket."""

import copy
import re
import subprocess
import sys
from pathlib import Path

import pytest

from lab_manager import ifnames, netconfig
from lab_manager.config import SiteLimits
from lab_manager.images import Image, load_images
from lab_manager.labdef import LabInvalid, check, deploy_ticket

ROOT = Path(__file__).resolve().parents[1]
IMAGES = load_images(ROOT / "examples" / "images.json")
NICS = {"ens9": "0c:aa:00:00:00:00", "ens3": "0c:aa:00:00:00:01", "ens4": "0C:AA:00:00:00:02"}


# --- names -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("count", [1, 2, 3, 4, 8, 16])
def test_names_are_valid_distinct_and_stable(count):
    for seed in range(300):
        got = ifnames.names(str(seed), count)
        assert len(got) == count == len(set(got))
        assert all(n == ifnames.MAC_NAME or ifnames.NAME_RE.fullmatch(n) for n in got)
        assert not any(n.startswith("eth") for n in got)  # The kernel's own names: renaming into them races
        assert ifnames.names(str(seed), count) == got


def test_names_differ_between_projects_and_cover_the_families():
    seen = {tuple(ifnames.node_names(f"project-{i}", "PC1", 2)) for i in range(50)}
    assert len(seen) > 40
    names = {n for i in range(2000) for n in ifnames.names(str(i), 4)}
    kinds = {"enx" if n == ifnames.MAC_NAME else re.match("[a-z]+", n).group() for n in names}
    assert kinds == {"eno", "ens", "enp", "em", "p", "enx"}
    assert any(n.endswith("f3") for n in names) and any("np1" in n for n in names)  # Multi-port cards
    assert {"eno1", "ens18", "enp0s31f6"} & names


def test_names_reject_bad_counts():
    for count in (0, 17):
        with pytest.raises(ValueError):
            ifnames.names("x", count)


# --- script ----------------------------------------------------------------------------------------------------

def run(mechanism, adapters, tmp_path, *, active=(), files=None, nics=NICS, state=()):
    root = tmp_path / "root"
    for name, mac in nics.items():
        (root / "sys/class/net" / name).mkdir(parents=True)
        (root / "sys/class/net" / name / "address").write_text(mac + "\n")
        (root / "sys/class/net" / name / "type").write_text("1\n")
    for name, text in (files or {}).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stubs = {
        "systemctl": 'for u in $ACTIVE; do [ "$u" = "$3" ] && exit 0; done\nexit 3\n',
        # `ip link set dev OLD name NEW` moves the fake sysfs entry, so the guest sees the new name.
        "ip": 'echo "ip $*" >> "$LOG"\nif [ "$5" = name ]; then mv "$ROOT/sys/class/net/$4" "$ROOT/sys/class/net/$6"; fi\n',
        "ifquery": 'for i in $STATE; do [ "$i" = "$2" ] && exit 0; done\nexit 1\n',
    }
    for name in ("udevadm", "nmcli", "netplan", "networkctl", "ifup", "ifdown", "ifquery", "ip", "systemctl"):
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\n" + stubs.get(name, f'echo "{name} $*" >> "$LOG"\n'))
        stub.chmod(0o755)
    log = tmp_path / "calls.log"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "ROOT": str(root), "LOG": str(log), "ACTIVE": " ".join(active),
           "STATE": " ".join(state)}
    done = subprocess.run(["/bin/sh", "-c", ifnames.script(mechanism, adapters)], capture_output=True, text=True,
                          env=env)
    return done, root, log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("mechanism", netconfig.MECHANISMS)
def test_script_syntax_and_values(mechanism):
    text = ifnames.script(mechanism, {1: "enp3s0f1", 0: ifnames.MAC_NAME})
    assert subprocess.run(["/bin/sh", "-n", "-c", text]).returncode == 0
    assert text.endswith("rn 0 @mac\nrn 1 enp3s0f1\nrenamed\n") and len(text) < netconfig.MAX_SCRIPT


def test_script_takes_only_checked_names():
    for bad in ({0: "ens3; reboot"}, {0: "Eth0"}, {0: "a" * 16}, {16: "ens3"}):
        with pytest.raises(ValueError):
            ifnames.script("ifupdown", bad)
    with pytest.raises(ValueError):
        ifnames.script("routeros", {0: "ens3"})


def test_ifupdown_guest_gets_link_files_new_names_and_config(tmp_path):
    interfaces = "auto lo\niface lo inet loopback\n\nallow-hotplug ens9\niface ens9 inet dhcp\nauto ens3\niface ens3:1 inet manual\n"
    done, root, calls = run("ifupdown", {0: "ens3", 1: "ens9", 2: ifnames.MAC_NAME}, tmp_path,
                            files={"etc/network/interfaces": interfaces,
                                   "etc/systemd/network/10-gns3lab-0caa00000099.link": "stale\n",
                                   "etc/cloud/cloud.cfg.d/.keep": ""},
                            state=("ens9",))
    assert done.returncode == 0, done.stderr
    net = root / "sys/class/net"
    # Adapter 0 (lowest MAC, was ens9) and adapter 1 (was ens3) swap their names; adapter 2 is named by its MAC.
    assert sorted(p.name for p in net.iterdir()) == ["ens3", "ens9", "enx0caa00000002"]
    assert (net / "ens3/address").read_text() == "0c:aa:00:00:00:00\n"
    links = root / "etc/systemd/network"
    assert sorted(p.name for p in links.iterdir()) == [
        "10-gns3lab-0caa00000000.link", "10-gns3lab-0caa00000001.link", "10-gns3lab-0caa00000002.link"]
    assert (links / "10-gns3lab-0caa00000002.link").read_text() == (
        "[Match]\nMACAddress=0c:aa:00:00:00:02\n\n[Link]\nName=enx0caa00000002\n")
    assert (root / "etc/network/interfaces").read_text() == (
        "auto lo\niface lo inet loopback\n\nallow-hotplug ens3\niface ens3 inet dhcp\nauto ens9\niface ens9:1 inet manual\n")
    assert (root / "etc/cloud/cloud.cfg.d/99-gns3lab-network.cfg").exists()
    assert "ip link set dev ens9 name gns3lab0" in calls and "ip link set dev gns3lab0 name ens3" in calls
    assert calls[-1] == "ifup ens3"  # Only what ifupdown had up comes up again


def test_a_dhcp_client_of_the_boot_is_stopped_first(tmp_path):
    # Debian's ifup@ens9 of allow-hotplug asks DHCP for minutes in a lab without a server and holds the interface's
    # lock meanwhile: the script kills that dhclient instead of waiting for it (seen on the stand: provisioning timeout).
    client = subprocess.Popen(["dhclient", "-c", "import time; time.sleep(60)", "-1", "ens9"], executable=sys.executable)
    try:
        done, root, calls = run("ifupdown", {0: "eno1"}, tmp_path)
        assert done.returncode == 0, done.stderr
        assert client.wait(timeout=5) != 0  # Killed
    finally:
        client.kill()
    assert "ifdown --force ens9" in calls


def test_netplan_and_networkmanager_files_follow_the_new_name(tmp_path):
    plan = "network:\n  ethernets:\n    ens9:\n      match: {macaddress: '0c:aa:00:00:00:00'}\n      set-name: ens9\n"
    keyfile = "[connection]\nid=Wired\ninterface-name=ens9\n"
    done, root, calls = run("netplan", {0: "eno1"}, tmp_path, active=("systemd-networkd",),
                            files={"etc/netplan/50-cloud-init.yaml": plan,
                                   "etc/NetworkManager/system-connections/w.nmconnection": keyfile,
                                   "etc/netplan/other.yaml": "ens99: {}\n"})
    assert done.returncode == 0, done.stderr
    assert "ens9" not in (root / "etc/netplan/50-cloud-init.yaml").read_text()
    assert "set-name: eno1" in (root / "etc/netplan/50-cloud-init.yaml").read_text()
    assert (root / "etc/netplan/other.yaml").read_text() == "ens99: {}\n"  # Another name that merely starts so
    assert "interface-name=eno1" in (root / "etc/NetworkManager/system-connections/w.nmconnection").read_text()
    assert "netplan apply" in calls
    assert sorted(p.name for p in (root / "sys/class/net").iterdir()) == ["eno1", "ens3", "ens4"]


def test_script_stops_on_a_wrong_guest(tmp_path):
    done, _, _ = run("networkmanager", {0: "eno1"}, tmp_path)
    assert done.returncode != 0 and "net_config mismatch" in done.stderr
    done, _, _ = run("ifupdown", {5: "eno1"}, tmp_path / "b")
    assert done.returncode != 0 and "adapter 5 not found" in done.stderr


# --- catalog, definition, ticket -------------------------------------------------------------------------------

def test_catalog_flag_needs_a_shell_guest():
    base = {"id": "x", "title": "X", "kind": "qemu", "file": "x.qcow2", "ram_mb": {"default": 512, "min": 256, "max": 1024},
            "adapters": {"default": 1, "min": 1, "max": 4}, "random_ifnames": True}
    assert Image.model_validate({**base, "net_config": "netplan"}).public()["random_ifnames"] is True
    for bad in ({}, {"net_config": "routeros"}):
        with pytest.raises(ValueError):
            Image.model_validate({**base, **bad})
    assert "random_ifnames" not in IMAGES["mikrotik-chr7"].public()
    assert IMAGES["debian12"].public()["random_ifnames"] is True


LAB = {"format": "gns3lab.lab/1",
       "nodes": [{"name": "PC1", "image": "debian12", "adapters": 3, "random_ifnames": True},
                 {"name": "PC2", "image": "debian12", "random_ifnames": False},
                 {"name": "R1", "image": "mikrotik-chr7"}],
       "links": [{"a": {"node": "PC1"}, "b": {"node": "R1"}}]}


def test_definition_keeps_only_true_and_checks_the_image():
    checked = check(copy.deepcopy(LAB), IMAGES, SiteLimits())
    nodes = {n["name"]: n for n in checked.definition["nodes"]}
    assert nodes["PC1"]["random_ifnames"] is True and "random_ifnames" not in nodes["PC2"]
    without = copy.deepcopy(LAB)
    del without["nodes"][0]["random_ifnames"]
    assert check(without, IMAGES, SiteLimits()).lab_def != checked.lab_def
    del without["nodes"][1]["random_ifnames"]  # `false` is as good as nothing
    assert check(without, IMAGES, SiteLimits()).definition == {
        **checked.definition, "nodes": [{k: v for k, v in n.items() if k != "random_ifnames"}
                                        for n in checked.definition["nodes"]]}
    bad = copy.deepcopy(LAB)
    bad["nodes"][2]["random_ifnames"] = True
    bad["nodes"][1]["random_ifnames"] = "yes"
    with pytest.raises(LabInvalid) as exc:
        check(bad, IMAGES, SiteLimits())
    assert [(p["path"], p["code"]) for p in exc.value.problems] == [
        ("nodes[1].random_ifnames", "bad_type"), ("nodes[2].random_ifnames", "unsupported_image")]


def test_ticket_renames_before_the_segment_network_and_follows_the_project():
    lab = {**copy.deepcopy(LAB), "format": "gns3lab.lab/2",
           "segments": [{"name": "LAN", "subnet": {"fixed": "10.0.0.0/24"}, "members": [{"node": "PC1"}]}]}
    definition = check(lab, IMAGES, SiteLimits()).definition
    one = deploy_ticket(definition, IMAGES, project_id="p-1", project_name="a")
    pc1 = next(n for n in one["nodes"] if n["name"] == "PC1")
    pc2 = next(n for n in one["nodes"] if n["name"] == "PC2")
    rename, network = pc1["provisioning"]
    assert rename["command"].endswith("renamed\n") and rename["timeout_seconds"] == ifnames.TIMEOUT_SECONDS
    assert network["command"].endswith("finish\n")
    names = ifnames.node_names("p-1", "PC1", 3)
    assert "".join(f"rn {a} {n}\n" for a, n in enumerate(names)) in rename["command"]
    assert "provisioning" not in pc2
    assert deploy_ticket(definition, IMAGES, project_id="p-1", project_name="a") == one
    other = deploy_ticket(definition, IMAGES, project_id="p-2", project_name="a")
    assert any(n != one["nodes"][i] for i, n in enumerate(other["nodes"]))
