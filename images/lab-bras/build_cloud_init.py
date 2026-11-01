#!/usr/bin/env python3
"""Render Proxmox cloud-init snippets for a dedicated Ubuntu 24.04 BRAS VM.

No packages are installed. The output contains a secret and must not enter Git.
JSON is used as a dependency-free YAML serialization (accepted by cloud-init).
"""

import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re


def quoted(value):
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Credentials must be non-empty and contain no control characters")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(*, user, password, wan="ens18", access="ens19", pool="10.254.0.0/24",
           dns=("1.1.1.1", "9.9.9.9"), binary="/usr/sbin/accel-pppd"):
    for iface in (wan, access):
        if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,14}", iface) or iface.startswith("ppp"):
            raise ValueError("Use plain Ethernet interface names, not PPP interfaces")
    if wan == access:
        raise ValueError("WAN and PPPoE access must be different interfaces")
    subnet = ipaddress.IPv4Network(pool)
    if not 16 <= subnet.prefixlen <= 29:
        raise ValueError("The dedicated PPP address pool must be between /16 and /29")
    if subnet.overlaps(ipaddress.IPv4Network("198.18.0.0/15")) or not subnet.is_private:
        raise ValueError("Use a private pool that does not overlap the lab LANs")
    if len(dns) != 2:
        raise ValueError("Exactly two IPv4 DNS servers are required")
    dns = [str(ipaddress.IPv4Address(address)) for address in dns]
    if not re.fullmatch(r"/(?:[a-zA-Z0-9_-]+/)*accel-pppd", binary):
        raise ValueError("Provide an absolute path to accel-pppd")
    gateway = subnet.network_address + 1
    first, last = subnet.network_address + 2, subnet.broadcast_address - 1
    # Accel-PPP accepts CIDRs or x.x.x.x-y, not two full IPv4 endpoints.
    ranges = "\n".join(str(block) for block in ipaddress.summarize_address_range(first, last))
    secrets = f"{quoted(user)} * {quoted(password)} *\n"
    config = f"""[modules]
log_file
pppoe
auth_pap
auth_chap_md5
auth_mschap_v2
chap-secrets
ippool

[core]
thread-count=2

[ppp]
mtu=1492
mru=1492
ipv4=require
ipv6=deny
lcp-echo-interval=20
lcp-echo-failure=3

[pppoe]
interface={access}
ac-name=lab-bras

[chap-secrets]
chap-secrets=/etc/ppp/lab-bras-secrets

[ip-pool]
gw-ip-address={gateway}
{ranges}

[dns]
dns1={dns[0]}
dns2={dns[1]}

[log]
log-file=/var/log/accel-ppp/lab-bras.log
level=3
"""
    firewall = f"""# Own table only; loading this file is atomic and repeatable.
add table inet lab_bras
flush table inet lab_bras
table inet lab_bras {{
    chain input {{
        type filter hook input priority 0; policy accept;
        iifname "ppp*" ct state established,related accept
        iifname "ppp*" ip protocol icmp accept
        iifname "ppp*" drop
    }}
    chain forward {{
        type filter hook forward priority 0; policy drop;
        ct state invalid drop
        iifname "{wan}" oifname "ppp*" ip daddr {subnet} ct state established,related accept
        iifname "ppp*" oifname "{wan}" ip saddr {subnet} accept
    }}
    chain postrouting {{
        type nat hook postrouting priority srcnat; policy accept;
        oifname "{wan}" ip saddr {subnet} masquerade
    }}
}}
"""
    firewall_unit = """[Unit]
Description=Lab BRAS forwarding and NAT
Wants=network-online.target
After=network-online.target
Before=lab-bras.service

[Service]
Type=oneshot
ExecStart=/usr/sbin/nft -f /etc/lab-bras.nft
ExecReload=/usr/sbin/nft -f /etc/lab-bras.nft
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""
    server_unit = f"""[Unit]
Description=Lab PPPoE access concentrator
Wants=network-online.target
Requires=lab-bras-firewall.service
After=network-online.target lab-bras-firewall.service
Conflicts=accel-ppp.service
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStartPre=/usr/bin/test -d /sys/class/net/{access}
ExecStart={binary} -c /etc/accel-ppp.conf
Restart=on-failure
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
"""
    files = []

    def put(path, content, permissions="0644"):
        files.append(dict(path=path, content=content, owner="root:root", permissions=permissions))

    put("/etc/accel-ppp.conf", config, "0600")
    put("/etc/ppp/lab-bras-secrets", secrets, "0600")
    put("/etc/sysctl.d/90-lab-bras.conf", "net.ipv4.ip_forward=1\n")
    put("/etc/lab-bras.nft", firewall)
    put("/etc/systemd/system/lab-bras-firewall.service", firewall_unit)
    put("/etc/systemd/system/lab-bras.service", server_unit)
    # Fail before starting services if the prepared image is missing a prerequisite.
    init = f"""#!/bin/sh
set -eu
test -x {binary}
command -v nft >/dev/null
command -v sysctl >/dev/null
test -d /sys/class/net/{wan}
test -d /sys/class/net/{access}
modprobe pppoe
install -d -m 0750 /var/log/accel-ppp
if systemctl cat accel-ppp.service >/dev/null 2>&1; then
    systemctl disable --now accel-ppp.service
fi
sysctl -p /etc/sysctl.d/90-lab-bras.conf
nft --check -f /etc/lab-bras.nft
systemctl daemon-reload
systemctl enable --now lab-bras-firewall.service
systemctl reload lab-bras-firewall.service
systemctl enable lab-bras.service
systemctl restart lab-bras.service
systemctl enable --now qemu-guest-agent.service
"""
    put("/usr/local/sbin/lab-bras-init", init, "0700")
    cloud = {"write_files": files, "runcmd": [["/usr/local/sbin/lab-bras-init"]]}
    network = {"version": 2, "ethernets": {
        wan: {"dhcp4": True, "dhcp6": False, "accept-ra": False, "link-local": []},
        access: {"dhcp4": False, "dhcp6": False, "accept-ra": False,
                 "link-local": [], "optional": True},
    }}
    return cloud, network


def write_private(path, text):
    # Do not follow symlinks or overwrite a previously generated secret.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--wan", default="ens18")
    parser.add_argument("--access", default="ens19")
    parser.add_argument("--pool", default="10.254.0.0/24")
    parser.add_argument("--dns", nargs=2, default=["1.1.1.1", "9.9.9.9"])
    parser.add_argument("--binary", default="/usr/sbin/accel-pppd")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    password = getpass.getpass("PPPoE password: ")
    if password != getpass.getpass("Repeat PPPoE password: "):
        parser.error("Passwords do not match")
    try:
        cloud, network = render(user=args.user, password=password, wan=args.wan,
                                access=args.access, pool=args.pool, dns=args.dns, binary=args.binary)
        # A fresh directory prevents mixed output from different invocations.
        args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        write_private(args.output_dir / "user-data.yaml", "#cloud-config\n" + json.dumps(cloud, indent=2) + "\n")
        write_private(args.output_dir / "network-config.yaml", json.dumps(network, indent=2) + "\n")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"Created snippets in {args.output_dir}; user-data.yaml contains PPPoE credentials.")


if __name__ == "__main__":
    main()
