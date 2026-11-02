"""Find the node's Ethernet ports and write the configuration; runs once per boot, before networkd."""

import json
import os
import sys
from pathlib import Path

from . import config

SYS_NET = Path("/sys/class/net")
NETWORKD_RUN = Path("/run/systemd/network")


def discover(sys_net=SYS_NET):
    """Real Ethernet cards: type 1 (ARPHRD_ETHER) with a bus device. Loopback, ppp, bridges and VLANs have none."""
    ports = []
    for entry in sorted(sys_net.iterdir()):
        try:
            if (entry / "type").read_text().strip() != "1" or not (entry / "device").exists():
                continue
            mac = (entry / "address").read_text().strip().lower()
        except OSError:
            continue
        ports.append(config.Port(entry.name, mac))
    return ports


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.chmod(mode)
    os.replace(tmp, path)


def read_routing(path):
    """Old images without a routing file retain LAN isolation."""
    try:
        settings = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    if not isinstance(settings, dict) or set(settings) != {"routed_lan_ports"}:
        raise ValueError("routing.json must contain only routed_lan_ports")
    return config.routed_ports(settings["routed_lan_ports"])


def main(sys_net=SYS_NET, run=Path(config.RUN), networkd_dir=NETWORKD_RUN, routing_path=Path(config.ROUTING)):
    ports = discover(sys_net)
    outer, lan = config.split(ports)
    selected = read_routing(routing_path)
    firewall = config.nftables(ports, selected)  # validate before writing any generated files
    for name, text in config.networkd(ports).items():
        write(networkd_dir / name, text)
    write(run / "pppd.options", config.pppd(ports))
    write(run / "nftables.nft", firewall)
    dnsmasq = run / "dnsmasq.conf"
    if lan:
        write(dnsmasq, config.dnsmasq(ports))
    else:
        dnsmasq.unlink(missing_ok=True)  # the dnsmasq unit is conditional on this file
    print(f"lab-uplink: outer {outer.name}, LAN {', '.join(p.name for p in lan) or 'none'}, routed LAN ports {selected}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f"lab-uplink-setup: {exc}", file=sys.stderr)
        sys.exit(1)
