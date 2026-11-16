"""VMID ranges in SLOTS and CREATE_SLOTS of lab.conf: lab-provision unfolds them into one entry per slot."""
import subprocess
from pathlib import Path

import pytest

PROVISION = Path(__file__).resolve().parents[1] / "lab-provision"


def expand(slots=None, create=None):
    env = {"PATH": "/usr/bin:/bin", "PROVISION": str(PROVISION)}
    if slots is not None:
        env["SLOTS"] = slots
    if create is not None:
        env["CREATE_SLOTS"] = create
    script = ('. "$PROVISION"; set -u; die() { echo "ERROR: $*" >&2; exit 1; }\n'
              'expand_slots; printf "%s\\n%s\\n" "$SLOTS" "$CREATE_SLOTS"')
    r = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
    if r.returncode:
        return None, r.stderr
    slots_out, create_out = r.stdout.split("\n")[:2]
    return (slots_out.split(), create_out.split()), r.stderr


def test_single_entries_stay_as_they_are():
    (slots, create), _ = expand("2001 2002", "2001=pve1:10.10.12.101 2002=pve2:10.10.12.102")
    assert slots == ["2001", "2002"]
    assert create == ["2001=pve1:10.10.12.101", "2002=pve2:10.10.12.102"]


def test_create_range_gets_consecutive_ips():
    (_, create), _ = expand("2001-2003 2101-2102", "2001-2003=pve1:10.10.12.101 2101-2102=pve2:10.10.12.126")
    assert create == ["2001=pve1:10.10.12.101", "2002=pve1:10.10.12.102", "2003=pve1:10.10.12.103",
                      "2101=pve2:10.10.12.126", "2102=pve2:10.10.12.127"]


def test_slots_range_unfolds_and_mixes_with_single_vmids():
    (slots, _), _ = expand("2001-2003 2101\n2102", "")
    assert slots == ["2001", "2002", "2003", "2101", "2102"]


@pytest.mark.parametrize("slots", [None, "", "  "])
def test_empty_slots_come_from_create_slots(slots):
    (slots, _), _ = expand(slots, "2001-2002=pve1:10.10.12.101 2101=pve2:10.10.12.126")
    assert slots == ["2001", "2002", "2101"]


def test_range_may_end_at_254():
    (_, create), _ = expand("", "2001-2004=pve1:10.10.12.251")
    assert create[-1] == "2004=pve1:10.10.12.254"


@pytest.mark.parametrize("create,message", [
    ("2001-2005=pve1:10.10.12.251", "run past .254"),
    ("2003-2001=pve1:10.10.12.101", "invalid VMID range"),
    ("2001-3001=pve1:10.10.12.1", "invalid VMID range"),
    ("2001-2002=pve1:dhcp", "IP of its first slot"),
    ("2001-2002=pve1", "IP of its first slot"),
    ("20a1=pve1:10.10.12.101", "expected <vmid>=<node>:<ip>"),
    ("2001-=pve1:10.10.12.101", "expected <vmid>=<node>:<ip>"),
])
def test_bad_create_ranges_are_refused(create, message):
    result, err = expand("", create)
    assert result is None
    assert message in err


@pytest.mark.parametrize("slots,message", [
    ("2005-2001", "invalid VMID range"),
    ("2001-9999", "invalid VMID range"),
    ("2001,2002", "expected VMIDs or <first>-<last>"),
])
def test_bad_slots_are_refused(slots, message):
    result, err = expand(slots, "")
    assert result is None
    assert message in err
