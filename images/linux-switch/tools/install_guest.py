#!/usr/bin/env python3
"""Two-phase installation inside a dedicated guest; never restart live networking."""

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("switch_preflight", SOURCE / "switch_api/preflight.py")
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def install(source, target, mode=0o644):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)


def prepare(root, source=SOURCE):
    preflight.check_network(source / "network")
    for path in (source / "switch_api").glob("*.py"):
        install(path, root / "opt/switch-api/switch_api" / path.name)
    install(source / "bin/switchctl", root / "usr/local/sbin/switchctl", 0o755)
    install(source / "system/switch-api.service", root / "etc/systemd/system/switch-api.service")
    state = root / "etc/switch-api"
    state.mkdir(parents=True, exist_ok=True)
    state.chmod(0o700)
    for name in preflight.NETWORK_FILES:
        install(source / "network" / name, root / "opt/switch-api/network-profile" / name)
    preflight.check_network(root / "opt/switch-api/network-profile")
    print("Prepared API and staged all three network files. Live networking and SSH unchanged.")
    print("Next: verify QGA and console access, then run activate and reboot.")


def activate(root, command=subprocess.run):
    staged = root / "opt/switch-api/network-profile"
    preflight.check_network(staged)
    for relative in ("opt/switch-api/switch_api/server.py", "opt/switch-api/switch_api/preflight.py",
                     "usr/local/sbin/switchctl", "etc/systemd/system/switch-api.service"):
        if not (root / relative).is_file():
            raise RuntimeError(f"Missing {relative}; run prepare first")
    network = root / "etc/systemd/network"
    network.mkdir(parents=True, exist_ok=True)
    backup = root / "var/backups/switch-network" / str(time.time_ns())
    backup.mkdir(parents=True)
    # Preserve old profiles, including symlinks, before replacing the dedicated guest's network.
    for path in list(network.iterdir()):
        if path.suffix in {".network", ".netdev"}:
            shutil.copy2(path, backup / path.name, follow_symlinks=False)
            path.unlink()
    for name in preflight.NETWORK_FILES:
        install(staged / name, network / name)
    preflight.check_network(network)
    for name in preflight.NETWORK_FILES:
        if (network / name).read_bytes() != (staged / name).read_bytes():
            raise RuntimeError(f"Installed profile verification failed: {name}")
    cloud = root / "etc/cloud"
    if cloud.is_dir():
        (cloud / "cloud-init.disabled").touch()
    command(["systemctl", "daemon-reload"], check=True)
    command(["systemctl", "enable", "systemd-networkd.service", "switch-api.service",
             "serial-getty@ttyS0.service"], check=True)
    command(["systemctl", "mask", "networking.service", "NetworkManager.service",
             "systemd-resolved.service"], check=True)
    # sshd.service is a packaged alias on Debian; masking it can fail with EEXIST.
    # Mask only the actual service and socket, last, without stopping either.
    command(["systemctl", "mask", "ssh.service", "ssh.socket"], check=True)
    print(f"Verified all three installed network files. Previous profiles: {backup}")
    print("Activation prepared. SSH is still running; run reboot when ready.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "activate"))
    parser.add_argument("--root", type=Path, default=Path("/"),
                        help="Offline staging root; only supported with prepare")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "activate" and root != Path("/"):
        parser.error("activate must run inside the guest, without --root")
    if root == Path("/") and os.geteuid() != 0:
        parser.error("root is required inside the guest")
    try:
        if args.action == "prepare":
            prepare(root)
        else:
            activate(root)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
