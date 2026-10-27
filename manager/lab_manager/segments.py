"""Segments of a gns3lab.lab/2 definition: checking, addresses, compilation (lab constructor README §3, §4, §13).

A segment says "these ports are one broadcast domain with this subnet, this VLAN and this gateway"; the
teacher never writes device commands. Stage 2 knows fixed values only and presets Linux (QGA script,
netconfig.py) and VPCS (`ip` line of the startup script) members; switches and routers stay the student's job.

check() is called by labdef.check and reports problems with a path the editor can highlight
(`segments[i].members[j]`). Everything here is pure: the same definition gives the same text.
"""

import ipaddress
import re
from dataclasses import dataclass

from . import netconfig
from .images import PORT_NAMES

SEGMENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
SUBNET_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")
SEGMENT_KEYS = {"name", "vlan", "subnet", "gateway", "members"}
MEMBER_KEYS = {"node", "adapter", "port", "host"}
MAX_SEGMENTS = 100
MAX_MEMBERS = 100
MAX_VLAN = 4094
RESERVED_VLANS = range(1002, 1006)  # Token ring / FDDI of Cisco


def is_int(value):
    return type(value) is int


@dataclass(frozen=True)
class Assignment:
    """The address of one member port."""
    segment: str
    node: str
    adapter: int
    port: int
    address: ipaddress.IPv4Interface
    gateway: ipaddress.IPv4Address | None  # Of its segment, if it has one


def usable(network) -> int:
    """Working host addresses: without the network and broadcast address."""
    return max(0, network.num_addresses - 2)


def parse_subnet(text):
    """IPv4Network from "A.B.C.D/N" or None: a network (no host bits), unicast, not loopback or link-local."""
    if not isinstance(text, str) or not SUBNET_RE.fullmatch(text):
        return None
    try:
        network = ipaddress.IPv4Network(text, strict=True)
    except ValueError:
        return None
    first = network.network_address
    if network.prefixlen < 8 or first.packed[0] == 0 or first.is_loopback or first.is_link_local \
            or first.is_multicast or first.is_reserved:
        return None
    return network


def plan(network, gateway: bool, gateway_host, hosts):
    """Addresses of a segment: (gateway address | None, [member addresses], [(where, code)]).

    `hosts`: the `host` of every member or None. The gateway takes `gateway_host` or the last working
    address; explicit hosts are placed first, the others get the lowest free numbers from .1. `where`
    of a problem is "gateway", a member index or "subnet". Used by check() and, with no problems, by compile.
    """
    problems, taken = [], {}
    size = usable(network)
    count = len(hosts) + (1 if gateway else 0)
    if size < 1 or count > size:
        return None, [None] * len(hosts), [("subnet", "subnet_too_small")]
    if gateway:
        host = gateway_host if gateway_host is not None else size
        if is_int(host) and 1 <= host <= size:
            taken[host] = "gateway"
        else:
            problems.append(("gateway", "host_out_of_range"))
    result = [None] * len(hosts)
    for j, host in enumerate(hosts):
        if host is None:
            continue
        if not (is_int(host) and 1 <= host <= size):
            problems.append((j, "host_out_of_range"))
        elif host in taken:
            problems.append((j, "host_in_use"))
        else:
            taken[host] = j
            result[j] = host
    number = 1
    for j, host in enumerate(hosts):
        if host is not None:
            continue
        while number in taken:
            number += 1
        taken[number] = j
        result[j] = number
    base = network.network_address
    addresses = [None if n is None else ipaddress.IPv4Interface((base + n, network.prefixlen)) for n in result]
    gw = next((base + n for n, who in taken.items() if who == "gateway"), None)
    return gw, addresses, problems


class Fabric:
    """Layer 2 of a lab as links make it: switches (category `switch`) flood, everything else ends a path."""

    def __init__(self, nodes: dict, links: list, images: dict):
        self.peer = {}
        for link in links:
            a, b = (link["a"]["node"], link["a"]["adapter"], link["a"]["port"]), \
                   (link["b"]["node"], link["b"]["adapter"], link["b"]["port"])
            self.peer[a], self.peer[b] = b, a
        self.switches = {name for name, node in nodes.items() if images[node["image"]].category == "switch"}
        self.ports = {}
        for port in self.peer:
            self.ports.setdefault(port[0], []).append(port)

    def reach(self, start):
        """(end ports reachable from `start` port, switches on the way)."""
        ends, passed = set(), set()
        stack = [self.peer[start]] if start in self.peer else []
        while stack:
            port = stack.pop()
            if port[0] in self.switches:
                if port[0] in passed:
                    continue
                passed.add(port[0])
                stack.extend(self.peer[other] for other in self.ports[port[0]] if other != port)
            else:
                ends.add(port)
        return ends, passed


def check(raw, *, nodes: dict, known: set, images: dict, links: list, port_error, problem, warn, connected: bool):
    """Validate `segments`; returns the normalized list (only meaningful when `problem` was not called).

    nodes: name -> normalized node; known: every node name in the document (a node with its own problem is
    reported there, not again here); port_error(node, adapter, port) -> message | None; problem/warn(path,
    code, message); connected: the nodes and links are sound, so paths through them can be traced.
    """
    if not isinstance(raw, list):
        problem("segments", "bad_type", "list")
        return []
    if len(raw) > MAX_SEGMENTS:
        problem("segments", "too_large", f"max {MAX_SEGMENTS} segments")
        raw = raw[:MAX_SEGMENTS]
    fabric = Fabric(nodes, links, images) if connected else None
    out, names, owner, gateway_of_node = [], {}, {}, {}
    traced = []  # (index, vlan, switches passed) of the segments whose members are all connected

    def endpoint(item, path, keys):
        """{node, adapter, port[, host]} of a member or gateway, or None after reporting."""
        if not isinstance(item, dict):
            problem(path, "required" if item is None else "bad_type", "object expected")
            return None
        for key in sorted(set(item) - keys):
            problem(f"{path}.{key}", "unknown_field")
        name = item.get("node")
        if name is None:
            problem(f"{path}.node", "required")
            return None
        if not isinstance(name, str) or name not in nodes:
            if not isinstance(name, str) or name not in known:
                problem(f"{path}.node", "unknown_node", str(name)[:80])
            return None
        adapter, port = item.get("adapter", 0), item.get("port", 0)
        message = port_error(name, adapter, port)
        if message:
            problem(path, "bad_port", message)
            return None
        result = {"node": name, "adapter": adapter, "port": port}
        if "host" in item:
            result["host"] = item["host"]
        return result

    for i, seg in enumerate(raw):
        path = f"segments[{i}]"
        if not isinstance(seg, dict):
            problem(path, "bad_type", "object expected")
            continue
        for key in sorted(set(seg) - SEGMENT_KEYS):
            problem(f"{path}.{key}", "unknown_field")
        norm, sound = {}, True
        name = seg.get("name")
        if not isinstance(name, str) or not SEGMENT_NAME_RE.fullmatch(name):
            problem(f"{path}.name", "required" if name is None else "bad_name", SEGMENT_NAME_RE.pattern)
            sound = False
        elif name in names:
            problem(f"{path}.name", "duplicate_segment", name)
            sound = False
        else:
            names[name] = i
            norm["name"] = name

        if "vlan" in seg:
            vlan = seg["vlan"]
            if not isinstance(vlan, dict):
                problem(f"{path}.vlan", "bad_type", '{"fixed": N}')
            else:
                for key in sorted(set(vlan) - {"fixed"}):
                    problem(f"{path}.vlan.{key}", "unknown_field")
                number = vlan.get("fixed")
                if number is None:
                    problem(f"{path}.vlan.fixed", "required")
                elif not is_int(number):
                    problem(f"{path}.vlan.fixed", "bad_type", "integer")
                elif not 2 <= number <= MAX_VLAN or number in RESERVED_VLANS:
                    problem(f"{path}.vlan.fixed", "vlan_out_of_range", f"2..{MAX_VLAN}, not 1002-1005")
                else:
                    norm["vlan"] = {"fixed": number}

        network = None
        subnet = seg.get("subnet")
        if not isinstance(subnet, dict):
            problem(f"{path}.subnet", "required" if subnet is None else "bad_type", '{"fixed": "A.B.C.D/N"}')
        else:
            for key in sorted(set(subnet) - {"fixed"}):
                problem(f"{path}.subnet.{key}", "unknown_field")
            if "fixed" not in subnet:
                problem(f"{path}.subnet.fixed", "required")
            else:
                network = parse_subnet(subnet["fixed"])
                if network is None:
                    problem(f"{path}.subnet.fixed", "bad_subnet", "IPv4 network such as 192.168.10.0/24, /8 or longer")
                else:
                    norm["subnet"] = {"fixed": str(network)}
                    if network.prefixlen > 30:
                        problem(f"{path}.subnet.fixed", "subnet_too_small", "/30 or larger")
                        network = None

        gateway = None
        if "gateway" in seg:
            gateway = endpoint(seg["gateway"], f"{path}.gateway", MEMBER_KEYS)
            if gateway is None:
                sound = False
            else:
                if images[nodes[gateway["node"]]["image"]].category != "router":
                    problem(f"{path}.gateway", "gateway_not_router", gateway["node"])
                    sound = False
                if "host" in gateway and not is_int(gateway["host"]):
                    problem(f"{path}.gateway.host", "bad_type", "integer")
                    gateway.pop("host")
                    sound = False
                port = (gateway["node"], gateway["adapter"], gateway["port"])
                if port in owner:
                    problem(f"{path}.gateway", "member_in_two_segments", f"{port[0]} {port[1]}/{port[2]}")
                    sound = False
                else:
                    owner[port] = path
                norm["gateway"] = gateway

        members_raw = seg.get("members")
        members = []
        if not isinstance(members_raw, list) or not members_raw:
            problem(f"{path}.members", "required" if members_raw is None else "bad_type", "non-empty list")
            sound = False
        else:
            if len(members_raw) > MAX_MEMBERS:
                problem(f"{path}.members", "too_large", f"max {MAX_MEMBERS} members")
                members_raw = members_raw[:MAX_MEMBERS]
            for j, item in enumerate(members_raw):
                mpath = f"{path}.members[{j}]"
                member = endpoint(item, mpath, MEMBER_KEYS)
                if member is None:
                    sound = False
                    members.append(None)
                    continue
                if "host" in member and not is_int(member["host"]):
                    problem(f"{mpath}.host", "bad_type", "integer")
                    member.pop("host")
                    sound = False
                if not images[nodes[member["node"]]["image"]].preconfig:
                    problem(mpath, "unsupported_image", images[nodes[member["node"]]["image"]].id)
                    sound = False
                port = (member["node"], member["adapter"], member["port"])
                if port in owner:
                    problem(mpath, "member_in_two_segments", f"{port[0]} {port[1]}/{port[2]}")
                    sound = False
                else:
                    owner[port] = mpath
                if gateway is not None and member["node"] in gateway_of_node:
                    problem(mpath, "multiple_gateways", member["node"])
                    sound = False
                members.append(member)
            norm["members"] = [m for m in members if m is not None]
        if gateway is not None:
            for member in members:
                if member is not None:
                    gateway_of_node.setdefault(member["node"], i)

        if network is not None and members_raw and isinstance(members_raw, list):
            hosts = [m.get("host") if m else None for m in members]
            _, _, found = plan(network, gateway is not None, gateway["host"] if gateway and "host" in gateway else None,
                               hosts)
            for where, code in found:
                if where == "subnet":
                    problem(f"{path}.members", code, f"{len(hosts) + (gateway is not None)} addresses in {network}")
                elif where == "gateway":
                    problem(f"{path}.gateway", code, f"1..{usable(network)}")
                else:
                    problem(f"{path}.members[{where}]", code, f"1..{usable(network)}" if code == "host_out_of_range"
                            else f"host {hosts[where]}")
        if gateway is not None:
            warn(f"{path}.gateway", "gateway_not_configured", "the router is set by the student")

        if fabric is not None and sound and "subnet" in norm and "name" in norm:
            ports = ([("gateway", gateway)] if gateway else []) + [(j, m) for j, m in enumerate(members)]
            keys = [(label, (m["node"], m["adapter"], m["port"])) for label, m in ports]
            ends, passed = fabric.reach(keys[0][1]) if keys else (set(), set())
            lost = False
            for label, key in keys[1:]:
                if key not in ends:
                    where = f"{path}.gateway" if label == "gateway" else f"{path}.members[{label}]"
                    problem(where, "segment_not_connected", f"{key[0]} is not on the same L2 path as {keys[0][1][0]}")
                    lost = True
            if not lost:
                traced.append((i, norm.get("vlan"), passed))
                if norm.get("vlan") and passed:
                    warn(f"{path}.vlan", "switch_not_configured", "the switches on the path are set by the student")
        if sound and "subnet" in norm and "name" in norm and "members" in norm:
            out.append({"name": norm["name"], **({"vlan": norm["vlan"]} if "vlan" in norm else {}),
                        "subnet": norm["subnet"], **({"gateway": norm["gateway"]} if "gateway" in norm else {}),
                        "members": norm["members"]})

    for n, (i, vlan, passed) in enumerate(traced):
        for k, other_vlan, other_passed in traced[:n]:
            if passed & other_passed and vlan == other_vlan:
                problem(f"segments[{i}]", "segment_overlap",
                        f"shares {', '.join(sorted(passed & other_passed))} with segments[{k}]"
                        + (f" in VLAN {vlan['fixed']}" if vlan else " without VLAN"))
                break
    return out


def assignments(definition: dict) -> list[Assignment]:
    """Addresses of all members of a checked definition (lab/1: none), in segment and member order."""
    result = []
    for seg in definition.get("segments", []):
        network = ipaddress.IPv4Network(seg["subnet"]["fixed"])
        gateway = seg.get("gateway")
        hosts = [m.get("host") for m in seg["members"]]
        gw, addresses, _ = plan(network, gateway is not None, gateway.get("host") if gateway else None, hosts)
        for member, address in zip(seg["members"], addresses):
            result.append(Assignment(seg["name"], member["node"], member["adapter"], member["port"], address, gw))
    return result


def setup(definition: dict, images: dict) -> dict:
    """What the members get: {node name: {"command": sh script}} for qemu, {"startup_line": "ip A/N [G]"} for vpcs."""
    by_node = {}
    for item in assignments(definition):
        by_node.setdefault(item.node, []).append(item)
    kinds = {n["name"]: images[n["image"]] for n in definition["nodes"]}
    result = {}
    for name, items in by_node.items():
        image = kinds[name]
        if image.kind == "vpcs":
            item = items[0]
            gw = f" {item.gateway}" if item.gateway else ""
            result[name] = {"startup_line": f"ip {item.address}{gw}"}
        else:
            ifaces = [netconfig.Iface(i.adapter, i.address, i.gateway) for i in items]
            result[name] = {"command": netconfig.script(image.net_config, ifaces)}
    return result


def expected_facts(definition: dict, images: dict) -> dict:
    """The `gns3lab.facts/1` snapshot (live-state.md §2) the members will show right after the preconfig.

    Keyed by node name (no GNS3 node ids exist yet) and without `rev`/`generated_at`. The interface name is the
    GNS3 port name: the guest's own name is not known to the manager. Only what the preconfig sets is there.
    """
    by_node = {}
    for item in assignments(definition):
        by_node.setdefault(item.node, []).append(item)
    kinds = {n["name"]: images[n["image"]] for n in definition["nodes"]}
    nodes = {}
    for name, items in by_node.items():
        image = kinds[name]
        interfaces = []
        for item in sorted(items, key=lambda i: (i.adapter, i.port)):
            iface = {"name": PORT_NAMES[image.kind].format(adapter=item.adapter, port=item.port), "kind": "physical",
                     "port": {"adapter": item.adapter, "port": item.port}, "admin": "up", "oper": "up",
                     "ipv4": [str(item.address)]}
            if item.gateway:
                iface["gateway"] = str(item.gateway)
            interfaces.append(iface)
        nodes[name] = {"name": name, "status": "started", "interfaces": interfaces}
    return {"schema": "gns3lab.facts/1", "nodes": nodes}
