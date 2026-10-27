"""Startup configurations of Cisco IOL nodes for lab segments (lab constructor README §4.2, §15.2).

segments.py reads the topology and decides what every IOL node has to do (SwitchPlan, RouterPlan); this module turns a
plan into the `startup-config` text that the slot puts into the node's NVRAM before its first start
(`NodeSpec.nvram_config`) and into the facts `ios.config/1` will report for it. Both come from the same plan, so
they cannot disagree. Everything is pure: the same plan gives the same text.

VLANs live in the configuration, not in `vlan.dat` (a closed format that is not generated): the switch runs
`vtp mode off`, so IOS keeps `vlan N` blocks in `startup-config` and reads them from there at boot. If a stand shows
that the image does not know `off`, change VTP_MODE to "transparent".
"""

import re
from dataclasses import dataclass, field
from ipaddress import IPv4Interface

from .images import PORT_NAMES

VTP_MODE = "off"
MAX_CONFIG = 65536  # NodeSpec.nvram_config of the slot
HOSTNAME_RE = re.compile(r"^[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")  # IOS: letter first, 63 characters at most
CONSOLE = ["line con 0", " exec-timeout 0 0", " logging synchronous"]
PORT = PORT_NAMES["iou"]


class ConfigTooLarge(ValueError):
    pass


@dataclass
class SwitchPort:
    """A port of a switch the plan sets: an access port of `vlan`, or a trunk allowing `allowed` (VLAN 1 is native)."""
    vlan: int | None = None
    allowed: frozenset | None = None


@dataclass
class SwitchPlan:
    vlans: dict = field(default_factory=dict)  # VLAN number (2..4094) -> segment name
    ports: dict = field(default_factory=dict)  # (adapter, port) -> SwitchPort


@dataclass
class RouterPlan:
    up: set = field(default_factory=set)  # (adapter, port) of the gateway ports: `no shutdown`
    addresses: dict = field(default_factory=dict)  # (adapter, port) -> IPv4Interface on the physical port
    subifs: dict = field(default_factory=dict)  # (adapter, port, vlan) -> IPv4Interface


def hostname_ok(name: str) -> bool:
    """Whether the node name is a valid IOS hostname as it is; if not, the configuration carries no `hostname`."""
    return HOSTNAME_RE.fullmatch(name) is not None


def vlan_list(numbers) -> str:
    """`1,10-12,20`: ascending, runs of three or more as ranges (IOS limits the length of a line)."""
    numbers, parts, i = sorted(numbers), [], 0
    while i < len(numbers):
        j = i
        while j + 1 < len(numbers) and numbers[j + 1] == numbers[j] + 1:
            j += 1
        parts.append(str(numbers[i]) if j - i < 2 else f"{numbers[i]}-{numbers[j]}")
        if j - i == 1:
            parts.append(str(numbers[j]))
        i = j + 1
    return ",".join(parts)


def _finish(lines):
    text = "\n".join([*lines, *CONSOLE, "end"]) + "\n"
    if len(text) > MAX_CONFIG:
        raise ConfigTooLarge(f"IOS configuration longer than {MAX_CONFIG} characters")
    return text


def _head(name):
    return ([f"hostname {name}"] if hostname_ok(name) else []) + ["no ip domain-lookup"]


def render_switch(name: str, plan: SwitchPlan) -> str:
    lines = [*_head(name), f"vtp mode {VTP_MODE}", "spanning-tree mode pvst"]
    for vlan in sorted(plan.vlans):
        lines += [f"vlan {vlan}", f" name {plan.vlans[vlan]}"]
    for (adapter, port), setting in sorted(plan.ports.items()):
        lines.append("interface " + PORT.format(adapter=adapter, port=port))
        if setting.allowed is None:
            lines += [" switchport mode access", f" switchport access vlan {setting.vlan}"]
        else:
            lines += [" switchport trunk encapsulation dot1q", " switchport mode trunk",
                      f" switchport trunk allowed vlan {vlan_list(setting.allowed)}"]
    return _finish(lines)


def _address(interface: IPv4Interface) -> str:
    return f" ip address {interface.ip} {interface.netmask}"


def render_router(name: str, plan: RouterPlan) -> str:
    lines = _head(name)
    ports = sorted(plan.up | set(plan.addresses) | {(a, p) for a, p, _ in plan.subifs})
    for adapter, port in ports:
        lines += ["interface " + PORT.format(adapter=adapter, port=port), " no shutdown"]
        if (adapter, port) in plan.addresses:
            lines.append(_address(plan.addresses[adapter, port]))
        for (a, p, vlan), interface in sorted(plan.subifs.items()):
            if (a, p) == (adapter, port):
                lines += [f"interface {PORT.format(adapter=a, port=p)}.{vlan}", f" encapsulation dot1Q {vlan}",
                          _address(interface)]
    return _finish(lines)


def facts_switch(plan: SwitchPlan) -> dict:
    """What `ios.config/1` reports for the configuration of render_switch (live-state.md 3.1, no `oper`)."""
    interfaces = []
    for (adapter, port), setting in sorted(plan.ports.items()):
        if setting.allowed is None:
            switchport = {"mode": "access", "vlan": setting.vlan}
        else:
            switchport = {"mode": "trunk", "allowed": sorted(setting.allowed), "native": 1}
        interfaces.append({"name": PORT.format(adapter=adapter, port=port), "kind": "physical",
                           "port": {"adapter": adapter, "port": port}, "admin": "up", "ipv4": [],
                           "switchport": switchport})
    return {"interfaces": interfaces,
            "vlans": [{"id": vlan, "name": plan.vlans[vlan]} for vlan in sorted(plan.vlans)]}


def facts_router(plan: RouterPlan) -> dict:
    interfaces = []
    for adapter, port in sorted(plan.up | set(plan.addresses) | {(a, p) for a, p, _ in plan.subifs}):
        name = PORT.format(adapter=adapter, port=port)
        interface = plan.addresses.get((adapter, port))
        interfaces.append({"name": name, "kind": "physical", "port": {"adapter": adapter, "port": port},
                           "admin": "up", "ipv4": [str(interface)] if interface else []})
        for (a, p, vlan), sub in sorted(plan.subifs.items()):
            if (a, p) == (adapter, port):
                interfaces.append({"name": f"{name}.{vlan}", "kind": "subif", "parent": name, "dot1q": vlan,
                                   "admin": "up", "ipv4": [str(sub)]})
    return {"interfaces": interfaces}


def render(name: str, plan) -> str:
    return render_switch(name, plan) if isinstance(plan, SwitchPlan) else render_router(name, plan)


def facts(plan) -> dict:
    return facts_switch(plan) if isinstance(plan, SwitchPlan) else facts_router(plan)
