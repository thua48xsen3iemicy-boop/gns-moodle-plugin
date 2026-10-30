#!/usr/bin/env python3
"""Install the component into a dedicated guest during image preparation; never touches live networking.

    install_guest.py prepare  [--root /] --pppoe-user USER --pppoe-password PASSWORD
    install_guest.py activate [--root /]

prepare copies the files and writes the PPPoE credentials (root, 0600). activate enables the units and masks
the other network managers; it needs systemctl and runs inside the guest. The password never appears in
the output.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
UNITS = ("lab-uplink-setup.service", "lab-uplink-nft.service", "lab-uplink-pppoe.service", "lab-uplink-dnsmasq.service")
MASKED = ("ifupdown-pre.service", "networking.service", "NetworkManager.service", "systemd-networkd-wait-online.service",
          "dhcpcd.service", "isc-dhcp-client.service")
CREDENTIALS = "etc/lab-uplink/pppoe.options"


def install(source, target, mode=0o644):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)


def quote(value):
    if not value or any(c in value for c in "\r\n\0"):
        raise ValueError("PPPoE user and password must be non-empty single-line values")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def prepare(root, user, password, source=SOURCE):
    credentials = f"user {quote(user)}\npassword {quote(password)}\n"
    for path in (source / "uplink_net").glob("*.py"):
        install(path, root / "opt/lab-uplink/uplink_net" / path.name)
    for unit in UNITS:
        install(source / "system" / unit, root / "etc/systemd/system" / unit)
    install(source / "sysctl/90-lab-uplink.conf", root / "etc/sysctl.d/90-lab-uplink.conf")
    target = root / CREDENTIALS
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(credentials)
    target.chmod(0o600)
    print("Prepared lab-uplink. Credentials are in", target)
    print("Next: verify QGA and the console, then run activate and shut the guest down.")


def activate(root, run=subprocess.run):
    for unit in UNITS:
        if not (root / "etc/systemd/system" / unit).is_file():
            raise RuntimeError(f"Missing {unit}; run prepare first")
    if not (root / CREDENTIALS).is_file():
        raise RuntimeError("Missing PPPoE credentials; run prepare first")
    run(["systemctl", "enable", "systemd-networkd.service", "qemu-guest-agent.service", "serial-getty@ttyS0.service",
         *UNITS], check=True)
    run(["systemctl", "mask", *MASKED], check=False)
    print("Activated. Reboot or shut the guest down for the image.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("prepare", "activate"))
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--pppoe-user")
    parser.add_argument("--pppoe-password")
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            if not args.pppoe_user or not args.pppoe_password:
                parser.error("prepare needs --pppoe-user and --pppoe-password")
            prepare(args.root, args.pppoe_user, args.pppoe_password)
        else:
            activate(args.root)
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
