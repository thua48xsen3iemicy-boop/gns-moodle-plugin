#!/usr/bin/python3 -I -B
"""Clear the `uncertain` mark of a slot on the agent's node, after the operator has checked the slot.

The agent writes <fence dir>/<VMID>.json ({"epoch", "generation"} plus "inflight"/"uncertain"/"command")
before and after every changing `pct` command; a command that timed out leaves `uncertain` and the agent
refuses every change and claim until an operator reconciles the slot (agent/README.md). This is what the
operator did by hand, with the checks that were done by eye: nothing is changed unless the CT is stopped, no
pct/lxc process works on it, the agent is not busy with it and the manager holds the slot in maintenance
or failed. epoch and generation are kept: the agent's ownership protocol goes on from them.

    lab-provision reconcile <VMID> [--yes]     (runs this file; without --yes it only shows)
"""

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

FENCE_DIR = Path("/var/lib/lab-node-agent")  # LAB_FENCE_DIR of the agent
LOCK_DIR = Path("/run/lab-node-agent")  # The agent's per-slot flock
BACKUP_DIR = Path("/root")
MARKS = ("inflight", "uncertain", "command")
HELD_STATES = ("maintenance", "failed")


class Refused(Exception):
    """The slot is not in a state where the mark may be cleared; nothing was changed."""


def read_record(fence_dir: Path, vmid: int) -> dict:
    path = fence_dir / f"{vmid}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Refused(f"{path} does not exist: the agent has no record of slot {vmid}, there is nothing to reconcile")
    except (OSError, ValueError) as exc:
        raise Refused(f"{path} cannot be read ({type(exc).__name__}); not touching a record that looks damaged")
    ok = (isinstance(data, dict) and type(data.get("epoch")) is int and type(data.get("generation")) is int
          and data["epoch"] >= 1 and data["generation"] >= 1)
    if not ok:
        raise Refused(f"{path} has no valid epoch and generation; not touching it")
    return data


def pct_lxc_processes(vmid: int, proc: Path = Path("/proc")) -> list[tuple[int, str]]:
    """Processes of `pct` or `lxc-*` commands that name this VMID. The container monitor of a stopped CT
    ('[lxc monitor] /var/lib/lxc 501') is neither and does not count."""
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            argv = [a for a in (entry / "cmdline").read_bytes().decode("utf-8", "replace").split("\0") if a]
        except OSError:
            continue  # Gone meanwhile
        if not argv:
            continue
        names = [os.path.basename(a) for a in argv[:2]]  # pct is a perl script: ['perl', 'pct', ...]
        if (names[0] == "pct" or names[0].startswith("lxc-") or (len(names) > 1 and names[1] == "pct")) \
                and str(vmid) in argv[1:]:
            found.append((int(entry.name), " ".join(argv)[:200]))
    return found


def check(vmid: int, *, fence_dir: Path, ct_state: str, manager_state: str, proc: Path) -> dict:
    """The record to clear, or Refused. Order: what is cheapest to fix comes first."""
    if ct_state != "stopped":
        raise Refused(f"CT {vmid} is {ct_state or 'in an unknown state'}: stop it first "
                      f"(pct shutdown {vmid} --timeout 120), then run this again")
    busy = pct_lxc_processes(vmid, proc)
    if busy:
        listing = "; ".join(f"{pid}: {cmd}" for pid, cmd in busy)
        raise Refused(f"pct/lxc processes still work on {vmid} ({listing}): wait until they end or are "
                      "proven dead; the mark cannot be cleared under a running command")
    record = read_record(fence_dir, vmid)
    if not record.get("uncertain") and not record.get("inflight"):
        raise Refused(f"the record of slot {vmid} has no uncertain/inflight mark: there is nothing to reconcile")
    if manager_state not in HELD_STATES:
        raise Refused(f"the manager reports slot {vmid} as '{manager_state or 'unknown'}', expected "
                      f"{' or '.join(HELD_STATES)}: a slot of a living session is not reconciled")
    return record


def cleared(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in MARKS}  # epoch and generation stay


def write_atomic(path: Path, data: dict, mode: int = 0o600) -> None:
    """Temporary file in the same directory, fsync, rename, fsync of the directory."""
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), mode)
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def backup(vmid: int, record_text: str, backup_dir: Path, stamp: str) -> Path:
    path = backup_dir / f"lab-fence-{vmid}-{stamp}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(record_text)
        f.flush()
        os.fsync(f.fileno())
    return path


def reconcile(vmid: int, *, yes: bool, ct_state: str, manager_state: str, fence_dir: Path = FENCE_DIR,
              lock_dir: Path = LOCK_DIR, backup_dir: Path = BACKUP_DIR, proc: Path = Path("/proc"),
              stamp: str | None = None, out=print) -> bool:
    """Show the record and, with yes, clear its marks. Returns True when the file was changed."""
    record = check(vmid, fence_dir=fence_dir, ct_state=ct_state, manager_state=manager_state, proc=proc)
    path = fence_dir / f"{vmid}.json"
    out(f"{path}: {json.dumps(record, sort_keys=True)}")
    out(f"CT {vmid} is stopped, no pct/lxc process names it, the manager holds the slot as {manager_state}.")
    new = cleared(record)
    if not yes:
        out(f"Would save a copy to {backup_dir}/lab-fence-{vmid}-<time>.json and write "
            f"{json.dumps(new, sort_keys=True)}. Nothing was changed; add --yes to do it.")
        return False
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(lock_dir / f"{vmid}.lock", "a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused(f"the agent is working on slot {vmid} right now (its lock is held): try again later")
        record = read_record(fence_dir, vmid)  # Again, under the agent's lock
        text = path.read_text(encoding="utf-8")
        if not record.get("uncertain") and not record.get("inflight"):
            raise Refused(f"the mark of slot {vmid} was cleared meanwhile")
        saved = backup(vmid, text, backup_dir, stamp or time.strftime("%Y%m%dT%H%M%S"))
        write_atomic(path, cleared(record))
    out(f"Copy of the old record: {saved}")
    out(f"Cleared {', '.join(k for k in MARKS if k in record)}; epoch {record['epoch']} and generation "
        f"{record['generation']} are kept.")
    out(f"Next: lab-provision enable {vmid}")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="lab-provision reconcile")
    parser.add_argument("vmid", type=int)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--ct-state", required=True, help="`pct status` of the CT: running or stopped")
    parser.add_argument("--manager-state", required=True, help="state of the slot in the manager")
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("ERROR: run as root on the node of the slot", file=sys.stderr)
        return 1
    if not re.fullmatch(r"[0-9]{3,9}", str(args.vmid)):
        print("ERROR: invalid VMID", file=sys.stderr)
        return 1
    try:
        reconcile(args.vmid, yes=args.yes, ct_state=args.ct_state, manager_state=args.manager_state)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
