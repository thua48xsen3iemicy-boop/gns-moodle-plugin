"""Text of the configuration files, from the list of Ethernet ports. Pure functions, no I/O.

Port roles follow the adapter numbers of the node (CONTRACTS 4.2): the adapter with the lowest MAC is
adapter 0 and is the outer port (`uplink_port: 0`), adapters 1..N are LAN ports. The slot gives the
adapters ascending MACs (GNS3 numbers them upwards from the base MAC), and manager/lab_manager/netconfig.py
orders guest interfaces the same way.
"""

from dataclasses import dataclass

PPP = "ppp0"
MAX_LAN = 15  # one /24 of 198.18.0.0/15 per port, 198.18.<N>.0/24
LAN_PREFIX = 24
RUN = "/run/lab-uplink"
CREDENTIALS = "/etc/lab-uplink/pppoe.options"
RESOLV = "/etc/ppp/resolv.conf"  # written by pppd with usepeerdns


@dataclass(frozen=True)
class Port:
    name: str
    mac: str  # lower case, colon separated


def split(ports):
    """(outer port, [LAN ports]) in adapter order."""
    ordered = sorted(ports, key=lambda p: int(p.mac.replace(":", ""), 16))
    if not ordered:
        raise ValueError("The node has no Ethernet adapter")
    if len(ordered) - 1 > MAX_LAN:
        raise ValueError(f"At most {MAX_LAN} LAN ports are supported")
    return ordered[0], ordered[1:]


def lan_address(number):
    return f"198.18.{number}.1"


def networkd(ports):
    """{file name: text} for systemd-networkd: no address on the outer port, 198.18.N.1/24 on LAN port N."""
    outer, lan = split(ports)
    common = ("[Link]\nActivationPolicy=always-up\nRequiredForOnline=no\n\n[Network]\n"
              "DHCP=no\nLinkLocalAddressing=no\nIPv6AcceptRA=no\nConfigureWithoutCarrier=yes\n"
              "LLDP=no\nEmitLLDP=no\nMulticastDNS=no\nLLMNR=no\n")
    files = {"10-lab-uplink-outer.network": f"[Match]\nMACAddress={outer.mac}\n\n{common}"}
    for number, port in enumerate(lan, start=1):
        files[f"20-lab-uplink-lan{number}.network"] = (
            f"[Match]\nMACAddress={port.mac}\n\n{common}Address={lan_address(number)}/{LAN_PREFIX}\n")
    return files


def pppd(ports):
    """Options of pppd (`pppd file ...`): the user and password come from the build-time file."""
    outer, _ = split(ports)
    return "\n".join([
        "# Generated at boot by lab-uplink-setup. Credentials: " + CREDENTIALS,
        "plugin rp-pppoe.so",
        f"nic-{outer.name}",
        f"ifname {PPP}",
        "noauth",
        "hide-password",
        "noipdefault",
        "defaultroute",
        "usepeerdns",
        "persist",
        "maxfail 0",
        "holdoff 5",
        "mtu 1492",
        "mru 1492",
        "lcp-echo-interval 20",
        "lcp-echo-failure 3",
        "nodetach",
        f"file {CREDENTIALS}",
        "",
    ])


def dnsmasq(ports):
    """DHCP with the router and DNS set to the port's own address; DNS is forwarded to the PPPoE servers."""
    _, lan = split(ports)
    lines = [
        "# Generated at boot by lab-uplink-setup",
        "keep-in-foreground",
        "bind-interfaces",
        "domain-needed",
        "bogus-priv",
        "no-hosts",
        "dhcp-authoritative",
        f"dhcp-leasefile={RUN}/dnsmasq.leases",
        f"resolv-file={RESOLV}",
        "no-dhcp-interface=" + PPP,
    ]
    for number, port in enumerate(lan, start=1):
        address = lan_address(number)
        net = f"lan{number}"
        lines += [
            f"interface={port.name}",
            f"listen-address={address}",
            f"dhcp-range=set:{net},198.18.{number}.100,198.18.{number}.250,255.255.255.0,12h",
            f"dhcp-option=tag:{net},option:router,{address}",
            f"dhcp-option=tag:{net},option:dns-server,{address}",
        ]
    return "\n".join(lines) + "\n"


def nftables(ports):
    """NAT into ppp0; forwarding only LAN -> ppp0 and replies; the node itself answers DHCP and DNS in the LAN."""
    _, lan = split(ports)
    names = ", ".join(f'"{p.name}"' for p in lan) or '"none"'  # an empty set literal is not valid nft
    return f"""# Generated at boot by lab-uplink-setup
flush ruleset
table inet lab_uplink {{
	set lan {{
		type ifname
		elements = {{ {names} }}
	}}
	chain input {{
		type filter hook input priority 0; policy drop;
		iif "lo" accept
		ct state established,related accept
		iifname @lan udp dport {{ 53, 67 }} accept
		iifname @lan tcp dport 53 accept
	}}
	chain forward {{
		type filter hook forward priority 0; policy drop;
		oifname "{PPP}" tcp flags syn tcp option maxseg size set rt mtu
		iifname "{PPP}" tcp flags syn tcp option maxseg size set rt mtu
		ct state established,related accept
		iifname @lan oifname "{PPP}" accept
	}}
	chain output {{
		type filter hook output priority 0; policy accept;
	}}
}}
table ip lab_uplink_nat {{
	chain postrouting {{
		type nat hook postrouting priority srcnat; policy accept;
		oifname "{PPP}" masquerade
	}}
}}
"""
