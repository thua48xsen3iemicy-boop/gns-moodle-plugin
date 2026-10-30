"""The internet uplink of the labs in install/: net1 of the slots, SLOT_UPLINK_MAC, the bridge filter."""
import subprocess
from pathlib import Path

import pytest

INSTALL = Path(__file__).resolve().parents[1]
PROVISION = INSTALL / "lab-provision"
FILTER = INSTALL / "lab-uplink-filter"

# Sourced lab-provision: only its functions run; the node commands are replaced by recorders.
PRELUDE = r'''
. "$PROVISION"
set +e
CALLS=$WORK/calls
: >"$CALLS"
log() { :; }
pct() { printf 'pct %s\n' "$*" >>"$CALLS"; if [[ $1 == config ]]; then cat "$WORK/template.conf" 2>/dev/null; fi; }
pvesh() { printf 'pvesh %s\n' "$*" >>"$CALLS"; }
ct_node() { return 1; }
ct_up() { :; }; ct_down() { :; }; ct_sh() { :; }; ct_dns() { CT_DNS=(); }
ostemplate() { echo local:vztmpl/ubuntu.tar.zst; }
have_kvm() { return 0; }
need_create_conf() { :; }
ct_ip() { echo 10.0.0.$(($1 % 100)); }
NODE=pve1 TEMPLATE_NODE=pve1 TEMPLATE_VMID=2000 SLOT_CLONE=full CT_STORAGE=ceph CT_BRIDGE=vmbr0 CT_CIDR=24
GNS3_VERSION=2.2.61 TEMPLATE_SCRIPT=: TEMPLATE_VERIFY=: TEMPLATE_CLEAN=:
declare -A PLAN_IP=([2000]=dhcp [2001]=10.0.0.101 [2077]=10.0.0.77) PLAN_NODE=([2000]=pve1 [2001]=pve1 [2077]=pve1)
die() { echo "ERROR: $*" >&2; exit 1; }
'''


def bash(tmp_path, script, env=None):
    env = {"PATH": "/usr/bin:/bin", "PROVISION": str(PROVISION), "WORK": str(tmp_path), **(env or {})}
    return subprocess.run(["bash", "-c", PRELUDE + script], env=env, capture_output=True, text=True)


def calls(tmp_path):
    return (tmp_path / "calls").read_text().splitlines()


NET1 = "name=eth1,bridge=vmbr1,tag=3000,ip=manual"


def test_provision_syntax():
    assert subprocess.run(["bash", "-n", str(PROVISION)]).returncode == 0
    assert subprocess.run(["bash", "-n", str(FILTER)]).returncode == 0


def test_template_gets_net1_without_firewall(tmp_path):
    r = bash(tmp_path, 'INET_BRIDGE=vmbr1 INET_VLAN=3000; cmd_template')
    assert r.returncode == 0, r.stderr
    create = next(c for c in calls(tmp_path) if c.startswith("pct create"))
    assert f"--net1 {NET1} " in create
    assert "firewall" not in create


def test_template_without_uplink_has_no_net1(tmp_path):
    r = bash(tmp_path, 'INET_BRIDGE= INET_VLAN=; cmd_template')
    assert r.returncode == 0, r.stderr
    assert "net1" not in " ".join(calls(tmp_path))


def test_clone_rewrites_net1_without_hwaddr(tmp_path):
    (tmp_path / "template.conf").write_text(f"template: 1\nnet1: {NET1},hwaddr=BC:24:11:00:00:01\n")
    r = bash(tmp_path, 'INET_BRIDGE=vmbr1 INET_VLAN=3000; cmd_clone_slot 2001')
    assert r.returncode == 0, r.stderr
    set_ = next(c for c in calls(tmp_path) if c.startswith("pvesh set"))
    assert f"--net1 {NET1} " in set_
    assert "hwaddr" not in set_
    assert "--net0 name=eth0,bridge=vmbr0,ip=10.0.0.101/24" in set_


def test_clone_from_a_template_without_net1_is_refused_with_a_hint(tmp_path):
    (tmp_path / "template.conf").write_text("template: 1\nnet0: name=eth0,bridge=vmbr0\n")
    r = bash(tmp_path, 'INET_BRIDGE=vmbr1 INET_VLAN=3000; cmd_clone_slot 2001')
    assert r.returncode != 0
    assert f"pct set 2000 -net1 {NET1}" in r.stderr
    assert not any(c.startswith(("pvesh", "pct clone")) for c in calls(tmp_path))


def test_clone_without_uplink_leaves_net1_alone(tmp_path):
    (tmp_path / "template.conf").write_text("template: 1\n")
    r = bash(tmp_path, 'INET_BRIDGE= INET_VLAN=; cmd_clone_slot 2001')
    assert r.returncode == 0, r.stderr
    assert "net1" not in " ".join(calls(tmp_path))


@pytest.mark.parametrize("vmid,mac", [
    (2001, "02:6c:61:00:07:d1"), (1, "02:6c:61:00:00:01"), (70000, "02:6c:61:01:11:70"), (16777215, "02:6c:61:ff:ff:ff"),
])
def test_uplink_mac_is_derived_from_the_vmid(tmp_path, vmid, mac):
    r = bash(tmp_path, f'uplink_mac {vmid}; uplink_mac {vmid}')
    assert r.stdout.split() == [mac, mac]


def test_uplink_mac_rejects_a_vmid_that_does_not_fit(tmp_path):
    assert bash(tmp_path, 'uplink_mac 16777216').returncode != 0
    assert bash(tmp_path, 'uplink_mac abc').returncode != 0


def render_env(tmp_path, inet):
    repo = tmp_path / "repo"
    (repo / "slot").mkdir(parents=True)
    (repo / "slot/slot.env.example").write_text(
        "SLOT_API_TOKEN=\nSLOT_CONSOLE_TOKEN=\nSLOT_LISTEN_HOST=127.0.0.1\nSLOT_UPLINK_MAC=\nSLOT_X=1\n")
    s = tmp_path / "secrets"
    (s / "slots/2001").mkdir(parents=True)
    (s / "slots/2001/api-token").write_text("api")
    (s / "slots/2001/console-token").write_text("con")
    r = bash(tmp_path, f'{inet}; S={s} REPO_DIR={repo}; render_slot_env 2001')
    assert r.returncode == 0, r.stderr
    return dict(line.split("=", 1) for line in (s / "slots/2001/slot.env").read_text().splitlines())


def test_slot_env_gets_its_own_uplink_mac(tmp_path):
    env = render_env(tmp_path, 'INET_BRIDGE=vmbr1 INET_VLAN=3000')
    assert env["SLOT_UPLINK_MAC"] == "02:6c:61:00:07:d1"
    assert env["SLOT_X"] == "1" and env["SLOT_API_TOKEN"] == "api"


def test_slot_env_without_uplink_has_no_mac(tmp_path):
    assert render_env(tmp_path, 'INET_BRIDGE= INET_VLAN=')["SLOT_UPLINK_MAC"] == ""


# ---- lab-uplink-filter

def run_filter(*args, **kw):
    return subprocess.run([str(FILTER), "--check", *args], capture_output=True, text=True, **kw)


def test_filter_rules_for_one_bras():
    r = run_filter("--bridge", "vmbr1", "--vlan", "3000", "--bras-macs", "AA:bb:cc:00:00:01")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert out.startswith("table bridge lab_uplink\ndelete table bridge lab_uplink\ntable bridge lab_uplink {\n")
    assert "elements = { aa:bb:cc:00:00:01 }" in out
    assert "elements = { ff:ff:ff:ff:ff:ff, aa:bb:cc:00:00:01 }" in out
    assert "from_slot {\n\t\tmeta protocol { 0x8863, 0x8864 } ether daddr @to_bras accept\n\t\tdrop\n" in out
    assert "to_slot {\n\t\tmeta protocol { 0x8863, 0x8864 } ether saddr @bras accept\n\t\tdrop\n" in out
    assert 'meta ibrname "vmbr1" iifname "veth*i1" jump from_slot' in out
    assert 'meta obrname "vmbr1" oifname "veth*i1" jump to_slot' in out
    # nothing but the slot ports is hooked: the node's uplink and the BRAS ports see no rule
    assert out.count("iifname") == 1 and out.count("oifname") == 1
    assert "policy accept" in out


def test_filter_rules_for_two_bras_macs():
    r = run_filter("--bridge", "vmbr1", "--vlan", "3000", "--bras-macs", "aa:bb:cc:00:00:01 02:00:00:00:00:02")
    assert "elements = { aa:bb:cc:00:00:01, 02:00:00:00:00:02 }" in r.stdout


def test_filter_reads_lab_conf(tmp_path):
    conf = tmp_path / "lab.conf"
    conf.write_text('INET_BRIDGE=vmbr9\nINET_VLAN=0042\nBRAS_MACS="aa:bb:cc:00:00:09"\n')
    r = run_filter(env={"PATH": "/usr/bin:/bin", "LAB_CONF": str(conf)})
    assert r.returncode == 0, r.stderr
    assert '"vmbr9"' in r.stdout and "(VLAN 42)" in r.stdout and "aa:bb:cc:00:00:09" in r.stdout


@pytest.mark.parametrize("args", [
    ["--vlan", "3000", "--bras-macs", "aa:bb:cc:00:00:01", "--bridge", "bad bridge"],
    ["--bridge", "vmbr1", "--vlan", "0", "--bras-macs", "aa:bb:cc:00:00:01"],
    ["--bridge", "vmbr1", "--vlan", "4095", "--bras-macs", "aa:bb:cc:00:00:01"],
    ["--bridge", "vmbr1", "--vlan", "3000", "--bras-macs", "aa:bb:cc:00:00"],
    ["--bridge", "vmbr1", "--vlan", "3000", "--bras-macs", "ff:ff:ff:ff:ff:ff"],
    ["--bridge", "vmbr1", "--vlan", "3000", "--bras-macs", "01:00:5e:00:00:01"],
    ["--bridge", "vmbr1", "--vlan", "3000", "--bras-macs", "  "],
])
def test_filter_refuses_bad_parameters(args, tmp_path):
    r = run_filter(*args, env={"PATH": "/usr/bin:/bin", "LAB_CONF": str(tmp_path / "none")})
    assert r.returncode != 0 and r.stdout == ""
