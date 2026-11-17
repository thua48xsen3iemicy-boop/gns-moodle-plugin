"""lab-provision deploy --node: adding nodes to a working stand touches only what is new."""
import subprocess
from pathlib import Path

PROVISION = Path(__file__).resolve().parents[1] / "lab-provision"

# Sourced lab-provision with a two-node stand: 700-702 on pve1 (the manager's node), 710-712 on pve2.
PRELUDE = r'''
. "$PROVISION"
set -u
log() { echo "LOG $*"; }
warn() { echo "WARN $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }
CONF=/lab.conf NODE=pve1 MANAGER_NODE=pve1 MANAGER_VMID=201 TEMPLATE_VMID=2000 TEMPLATE_NODE=pve1
AGENT_DOMAIN=lab.local CLEAN_SNAPSHOT=lab-clean
declare -A NODE_IPS=([pve1]=10.0.0.1 [pve2]=10.0.0.2 [pve3]=10.0.0.3) SLOT_NODE=()
NODE_NAMES=(pve1 pve2 pve3)
SLOT_IDS=(700 701 702 710 711 712)
for id in 700 701 702; do SLOT_NODE[$id]=pve1; done
for id in 710 711 712; do SLOT_NODE[$id]=pve2; done
LOCAL_SLOTS=(700 701 702)
need_cluster() { :; }; need_secrets() { :; }; need_node() { :; }
reach() { echo "reach $*"; }
run_on() { echo "run $*"; }
ct_node() { echo pve1; }
ct_local() { :; }
ct_ip() { echo 10.0.0.50; }
has_clean() { [[ " ${CLEAN:-} " == *" $1 "* ]]; }
agent_check() { echo "agent_check $NODE"; }
ct_sh() { echo "ct_sh $1 ${*:3}"; }
manager_slot_states() { printf '%s\n' "$STATES" | tr , '\n'; }
'''


def bash(script, **env):
    r = subprocess.run(["bash", "-c", PRELUDE + script], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "PROVISION": str(PROVISION), **env})
    return r.returncode, r.stdout.splitlines(), r.stderr


def test_node_args_split_lists_and_drop_repeats():
    rc, out, err = bash('node_args --node pve1,pve2 x --node=pve2 --node pve3 y; echo "${NODE_ARGS[*]}|${REST_ARGS[*]}"')
    assert rc == 0, err
    assert out == ["pve1 pve2 pve3|x y"]


def test_node_args_refuse_an_unknown_node():
    rc, _, err = bash("node_args --node pve9")
    assert rc != 0
    assert "pve9: not in NODES" in err


def test_deploy_node_prepares_only_the_slots_without_the_clean_snapshot():
    rc, out, err = bash("cmd_deploy --node pve2", CLEAN="700 701 702 710")
    assert rc == 0, err
    assert [line for line in out if line.startswith("run ")] == [
        "run pve1 template",
        "run pve1 create",
        "run pve1 secrets",
        "run pve2 slot 711 712",
        "run pve2 agent",
        "run pve1 manager",
        "run pve1 _agents-ok pve2",
        "run pve2 check",
        "run pve1 check --node pve2",
        "run pve1 enable --node pve2",
    ]


def test_deploy_node_with_every_slot_prepared_skips_the_slot_step():
    rc, out, err = bash("cmd_deploy --node pve2,pve1", CLEAN="700 701 702 710 711 712")
    assert rc == 0, err
    runs = [line for line in out if line.startswith("run ")]
    assert not any(" slot " in line for line in runs)
    assert "run pve2 agent" in runs and "run pve1 agent" in runs
    assert "run pve1 check" not in runs  # The manager's node is checked once, with --node
    assert runs[-2:] == ["run pve1 check --node pve2 --node pve1", "run pve1 enable --node pve2 --node pve1"]


def test_deploy_node_without_slots_is_refused():
    rc, out, err = bash("cmd_deploy --node pve3")
    assert rc != 0
    assert "pve3: no slot of SLOTS" in err
    assert not any(line.startswith("run ") for line in out)


def test_enable_node_takes_only_the_slots_in_maintenance():
    rc, out, err = bash("cmd_enable --node pve2",
                        STATES="700 maintenance,710 maintenance,711 free,712 failed")
    assert rc == 0, err
    assert [line for line in out if line.startswith("ct_sh")] == ["ct_sh 201 VMID=710"]
    assert any("slot 712 is 'failed'" in line for line in out)


def test_enable_node_with_nothing_to_enable_is_not_an_error():
    rc, out, err = bash("cmd_enable --node pve2", STATES="710 free,711 busy,712 ready")
    assert rc == 0, err
    assert not any(line.startswith("ct_sh") for line in out)


def test_enable_without_arguments_still_takes_every_slot():
    rc, out, err = bash("cmd_enable", STATES="")
    assert rc == 0, err
    assert len([line for line in out if line.startswith("ct_sh")]) == 6


def test_check_node_asks_the_manager_about_these_nodes_only():
    rc, out, err = bash("cmd_check --node pve2")
    assert rc == 0, err
    assert "agent_check pve1" not in out  # This node is not among them
    check = next(line for line in out if line.startswith("ct_sh 201"))
    assert "AGENTS=pve2.lab.local" in check
    assert "SLOT_AGENTS=710=pve2.lab.local 711=pve2.lab.local 712=pve2.lab.local" in check


def test_check_without_arguments_covers_everything():
    rc, out, err = bash("cmd_check")
    assert rc == 0, err
    assert "agent_check pve1" in out
    check = next(line for line in out if line.startswith("ct_sh 201"))
    assert "AGENTS=pve1.lab.local pve2.lab.local pve3.lab.local" in check
    assert "700=pve1.lab.local" in check and "712=pve2.lab.local" in check
