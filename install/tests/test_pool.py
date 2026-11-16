"""lab-provision keeps the pool lab-slots equal to SLOTS: adds the missing slots, removes leftovers."""
import json
import subprocess
from pathlib import Path

PROVISION = Path(__file__).resolve().parents[1] / "lab-provision"

# Sourced lab-provision with a fake Proxmox: $WORK/members is the pool, $WORK/foreign the VMIDs of another pool.
PRELUDE = r'''
. "$PROVISION"
set -u
log() { echo "LOG $*"; }
warn() { echo "WARN $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }
pvesh() { cat "$WORK/pool.json"; }
pveum() {
  echo "pveum $*" >>"$WORK/calls"
  local vms=$5 id
  if [[ ${6:-} == -delete ]]; then return 0; fi
  for id in ${vms//,/ }; do
    if grep -qx "$id" "$WORK/members" "$WORK/foreign" 2>/dev/null; then
      echo "update pools failed: VM $id is already a pool member" >&2; return 1
    fi
  done
}
'''


def run(tmp_path, slots, members, foreign=()):
    pool = {"members": [{"id": f"lxc/{m}", "vmid": m, "type": "lxc"} for m in members]
            + [{"id": "storage/pve1/rbd", "type": "storage", "storage": "rbd"}]}
    (tmp_path / "pool.json").write_text(json.dumps(pool))
    (tmp_path / "members").write_text("".join(f"{m}\n" for m in members))
    (tmp_path / "foreign").write_text("".join(f"{m}\n" for m in foreign))
    (tmp_path / "calls").write_text("")
    script = PRELUDE + f"SLOT_IDS=({' '.join(map(str, slots))}); pool_sync"
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "PROVISION": str(PROVISION), "WORK": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    return r.stdout, (tmp_path / "calls").read_text().splitlines()


def test_adds_only_the_missing_slots(tmp_path):
    out, calls = run(tmp_path, [700, 701, 702, 705, 706], [700, 701, 702])
    assert calls == ["pveum pool modify lab-slots -vms 705,706"]
    assert "WARN" not in out


def test_nothing_to_do_when_the_pool_matches(tmp_path):
    _, calls = run(tmp_path, [700, 701], [700, 701])
    assert calls == []


def test_removes_members_not_in_slots(tmp_path):
    out, calls = run(tmp_path, [700, 701], [181, 700, 701])
    assert calls == ["pveum pool modify lab-slots -vms 181 -delete 1"]
    assert "removing 181" in out


def test_slot_of_another_pool_is_named_and_the_rest_are_added(tmp_path):
    out, calls = run(tmp_path, [700, 705, 706], [700], foreign=[706])
    assert calls == ["pveum pool modify lab-slots -vms 705,706",
                     "pveum pool modify lab-slots -vms 705",
                     "pveum pool modify lab-slots -vms 706"]
    assert "slot 706" in out and "-allow-move 1" in out
