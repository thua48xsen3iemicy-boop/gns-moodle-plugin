"""Auto-checks: the rules, the `gns3lab.checks/1` document and the grade (docs/autochecks/README.md, CONTRACTS.md §2.9).

Pure functions over the network model (netmodel.Model) and the expectations of the lab: segments of a `gns3lab.lab/2`
definition and groups of nodes. evaluate() gives the rules and findings, grade() the mark. The orchestrator only
feeds them and stores the result.

A finding is one mistake about one object. Its class comes from the strictness profile the teacher picked, the
`overrides` and the focus of the lab. A finding that a finding of a lower layer explains gets `caused_by` and does not
count: one wrong VLAN on a port must not cost three marks.
"""

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass, field

from . import segments as segments_mod
from .netmodel import Model

FORMAT = "gns3lab.checks/1"
CLASSES = ("critical", "major", "minor", "note")
PROFILES = ("lenient", "standard", "strict")
GROUPS = ("l1", "l2", "addressing", "gateways", "routing", "host")
GROUP_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
ROUTER_NAME_RE = re.compile(r"^(r|router)[-_]?\d*$", re.IGNORECASE)
SWITCH_NAME_RE = re.compile(r"^(sw|switch)[-_]?\d*$", re.IGNORECASE)
RFC1918 = tuple(ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
APIPA = ipaddress.IPv4Network("169.254.0.0/16")
# The LAN of adapter N of the ISP node (an image with uplink_port, images/lab-uplink/README.md): 198.18.N.0/24 by DHCP.
ISP_LAN = "198.18.{adapter}.0/24"
DEFAULT_FORBID_PREFIX = (0, 8, 32)
TRANSIENT = ("stale_facts", "collect_failed")  # A system reason a later run may not have: the run is incomplete
SKIP_REASONS = ("no_collector", "stale_facts", "collect_failed", "no_expectation", "unsupported_os", "port_unknown")
MAX_THRESHOLDS = 10

# code -> (group, classes for lenient / standard / strict, needs expectations)
RULES = {
    "node_stopped": ("l1", ("major", "critical", "critical"), False),
    "config_not_saved": ("l1", ("major", "major", "critical"), False),
    "link_suspended": ("l1", ("major", "critical", "critical"), True),
    "port_shutdown": ("l1", ("major", "major", "critical"), False),
    "link_down": ("l1", ("minor", "major", "major"), False),
    "unused_port_up": ("l1", ("note", "minor", "major"), True),
    "segments_merged": ("l2", ("major", "critical", "critical"), True),
    "segment_split": ("l2", ("major", "critical", "critical"), True),
    "member_isolated": ("l2", ("major", "major", "critical"), False),
    "vlan_mismatch": ("l2", ("minor", "major", "major"), False),
    "tag_not_allowed": ("l2", ("minor", "major", "major"), False),
    "vlan_wrong": ("l2", ("minor", "major", "major"), True),
    "vlan_name_wrong": ("l2", ("note", "minor", "minor"), True),
    "vlan_extra": ("l2", ("note", "minor", "minor"), True),
    "vlan_extra_used": ("l2", ("minor", "major", "major"), True),
    "trunk_not_pruned": ("l2", ("note", "minor", "major"), True),
    "mgmt_vlan_missing": ("l2", ("minor", "major", "major"), True),
    "address_missing": ("addressing", ("major", "major", "critical"), True),
    "address_wrong": ("addressing", ("major", "major", "critical"), True),
    "mask_wrong": ("addressing", ("minor", "major", "critical"), False),
    "address_misplaced": ("addressing", ("major", "major", "critical"), True),
    "address_extra": ("addressing", ("minor", "major", "major"), False),
    "address_apipa": ("addressing", ("minor", "major", "major"), False),
    "address_not_private": ("addressing", ("minor", "major", "critical"), False),
    "address_duplicate": ("addressing", ("major", "critical", "critical"), False),
    "subnet_overlap": ("addressing", ("major", "major", "critical"), False),
    "subnet_mixed": ("addressing", ("major", "major", "critical"), False),
    "address_not_saved": ("addressing", ("minor", "major", "critical"), True),
    "config_not_applied": ("addressing", ("minor", "major", "major"), True),
    "gateway_missing": ("gateways", ("major", "major", "critical"), True),
    "gateway_wrong": ("gateways", ("major", "critical", "critical"), False),
    "unreachable": ("routing", ("major", "critical", "critical"), True),
    "hostname_wrong": ("host", ("note", "minor", "major"), False),
    "forwarding_off": ("host", ("major", "critical", "critical"), False),
    "forwarding_on_host": ("host", ("minor", "major", "major"), False),
}
# Rules whose data no collector gives yet (stage 3): always skipped with no_collector.
NOT_COLLECTED: set = set()  # Every rule has its data since slot 0.17.0 (linux.sys, linux.config, hostname, IOS routes)
SAVED_MECHANISMS = ("ifupdown", "networkmanager", "netplan", "networkd")  # net_config of an image that saves addresses

# Layers for the root cause: a finding is explained only by a finding of a lower layer that shares an object with it
# and whose code is in CAUSES of the finding's code.
L1 = {"node_stopped", "config_not_saved", "link_suspended", "port_shutdown", "link_down"}
L2_PORT = {"vlan_mismatch", "tag_not_allowed", "vlan_wrong", "vlan_extra_used"}
L2_DOMAIN = {"segments_merged", "segment_split", "member_isolated"}
ADDRESS = {"address_missing", "address_wrong", "mask_wrong", "address_misplaced"}
CAUSES = {
    "link_down": {"node_stopped", "config_not_saved", "link_suspended", "port_shutdown"},
    "port_shutdown": {"node_stopped", "config_not_saved"},
    **{code: L1 for code in L2_PORT},
    **{code: L1 | L2_PORT for code in L2_DOMAIN},
    "member_isolated": L1 | L2_PORT | {"segments_merged", "segment_split"},
    **{code: {"node_stopped", "config_not_saved"} for code in ADDRESS | {"address_extra", "address_apipa", "address_not_private",
                                                    "unused_port_up", "vlan_extra", "vlan_name_wrong",
                                                    "trunk_not_pruned", "mgmt_vlan_missing"}},
    "subnet_mixed": {"node_stopped", "config_not_saved", "segments_merged"} | ADDRESS,
    "subnet_overlap": {"node_stopped", "config_not_saved", "mask_wrong", "address_wrong"},
    "address_duplicate": {"node_stopped", "config_not_saved", "address_wrong", "address_misplaced"},
    "gateway_missing": {"node_stopped", "config_not_saved"},
    "gateway_wrong": L1 | L2_PORT | L2_DOMAIN | ADDRESS,
    "unreachable": L1 | L2_PORT | L2_DOMAIN | ADDRESS | {"gateway_missing", "gateway_wrong", "subnet_mixed",
                                                        "forwarding_off"},
}
ORDER = list(RULES)  # Findings are reported in catalog order: lower layers first

DEFAULT_THRESHOLDS = (
    {"mark": 5, "critical": 0, "major": 0, "minor": 1},
    {"mark": 4, "critical": 0, "major": 1, "minor": 3},
    {"mark": 3, "critical": 0, "major": 3, "minor": None},
)
DEFAULT_GRADING = {"profile": "standard", "overrides": {}, "thresholds": [dict(t) for t in DEFAULT_THRESHOLDS],
                   "fail_mark": 2}


def is_int(value):
    return type(value) is int


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(checks: dict | None):
    return None if checks is None else "sha256:" + hashlib.sha256(canonical(checks).encode()).hexdigest()


# ---------------------------------------------------------------------- gns3lab.checks/1


def check_definition(raw, node_names: set, prefix="checks"):
    """Validate and normalize a `gns3lab.checks/1` document. Returns (normalized, problems).

    problems: [{"path", "code", "message"}], paths below `prefix`. Unknown fields at any level are refused.
    """
    problems = []

    def problem(path, code, message=""):
        problems.append({"path": f"{prefix}.{path}" if path else prefix, "code": code, "message": message})

    def keys(obj, allowed, path):
        for key in sorted(set(obj) - allowed):
            problem(f"{path}.{key}" if path else key, "unknown_field")

    if not isinstance(raw, dict):
        problem("", "bad_type", "object expected")
        return None, problems
    keys(raw, {"format", "focus", "persistence", "nodes", "addresses", "groups", "switches", "reachability", "rules"}, "")
    out = {"format": FORMAT}
    if raw.get("format") != FORMAT:
        problem("format", "required" if "format" not in raw else "bad_type", FORMAT)

    if "focus" in raw:
        focus = raw["focus"]
        if not isinstance(focus, list) or not all(isinstance(g, str) for g in focus):
            problem("focus", "bad_type", "list of " + ", ".join(GROUPS))
        else:
            bad = [g for g in focus if g not in GROUPS]
            for g in bad:
                problem("focus", "out_of_range", g)
            if not bad:
                out["focus"] = sorted(set(focus), key=GROUPS.index)

    if "persistence" in raw:
        if raw["persistence"] not in ("saved", "runtime"):
            problem("persistence", "out_of_range", "saved or runtime")
        else:
            out["persistence"] = raw["persistence"]
    if "nodes" in raw:
        nodes = raw["nodes"]
        if not isinstance(nodes, dict):
            problem("nodes", "bad_type", "object")
        else:
            norm = {}
            for name in sorted(nodes):
                item = nodes[name]
                if name not in node_names:
                    problem(f"nodes.{name}", "unknown_node", name[:80])
                    continue
                if not isinstance(item, dict):
                    problem(f"nodes.{name}", "bad_type", "object")
                    continue
                keys(item, {"persistence"}, f"nodes.{name}")
                if "persistence" in item:
                    if item["persistence"] not in ("saved", "runtime"):
                        problem(f"nodes.{name}.persistence", "out_of_range", "saved or runtime")
                    else:
                        norm[name] = {"persistence": item["persistence"]}
            if norm:
                out["nodes"] = norm

    if "addresses" in raw:
        addr = raw["addresses"]
        if not isinstance(addr, dict):
            problem("addresses", "bad_type", "object")
        else:
            keys(addr, {"allowed", "match", "forbid_prefix"}, "addresses")
            norm = {}
            if "allowed" in addr:
                allowed = addr["allowed"]
                if not isinstance(allowed, list) or not allowed:
                    problem("addresses.allowed", "bad_type", "non-empty list")
                else:
                    items = []
                    for k, item in enumerate(allowed[:64]):
                        if item == "rfc1918":
                            items.append(item)
                            continue
                        try:
                            items.append(str(ipaddress.IPv4Network(str(item), strict=True)) if isinstance(item, str) else None)
                        except ValueError:
                            items.append(None)
                        if items[-1] is None:
                            problem(f"addresses.allowed[{k}]", "bad_range", "rfc1918 or A.B.C.D/N")
                    if None not in items:
                        norm["allowed"] = sorted(set(items))
            if "match" in addr:
                if addr["match"] not in ("plan", "subnet"):
                    problem("addresses.match", "out_of_range", "plan or subnet")
                else:
                    norm["match"] = addr["match"]
            if "forbid_prefix" in addr:
                fp = addr["forbid_prefix"]
                if not isinstance(fp, list) or not all(is_int(x) for x in fp):
                    problem("addresses.forbid_prefix", "bad_type", "list of integers")
                elif not all(0 <= x <= 32 for x in fp):
                    problem("addresses.forbid_prefix", "out_of_range", "0..32")
                else:
                    norm["forbid_prefix"] = sorted(set(fp))
            if norm:
                out["addresses"] = norm

    if "groups" in raw:
        groups = raw["groups"]
        if not isinstance(groups, dict):
            problem("groups", "bad_type", "object")
        else:
            keys(groups, {"by_name", "explicit"}, "groups")
            norm = {}
            if "by_name" in groups:
                if type(groups["by_name"]) is not bool:
                    problem("groups.by_name", "bad_type", "boolean")
                else:
                    norm["by_name"] = groups["by_name"]
            if "explicit" in groups:
                explicit = groups["explicit"]
                if not isinstance(explicit, list):
                    problem("groups.explicit", "bad_type", "list")
                else:
                    items, seen, owner = [], set(), {}
                    for k, g in enumerate(explicit[:100]):
                        path = f"groups.explicit[{k}]"
                        if not isinstance(g, dict):
                            problem(path, "bad_type", "object")
                            continue
                        keys(g, {"name", "members", "vlan"}, path)
                        name = g.get("name")
                        if not isinstance(name, str) or not GROUP_NAME_RE.fullmatch(name):
                            problem(f"{path}.name", "required" if name is None else "bad_name", GROUP_NAME_RE.pattern)
                            continue
                        if name.lower() in seen:
                            problem(f"{path}.name", "duplicate_name", name)
                            continue
                        seen.add(name.lower())
                        members = g.get("members")
                        if not isinstance(members, list) or not members or not all(isinstance(m, str) for m in members):
                            problem(f"{path}.members", "required" if members is None else "bad_type", "non-empty list of node names")
                            continue
                        ok = True
                        for j, m in enumerate(members):
                            if m not in node_names:
                                problem(f"{path}.members[{j}]", "unknown_node", m[:80])
                                ok = False
                            elif m in owner and owner[m] != name:
                                problem(f"{path}.members[{j}]", "group_overlap", f"{m} is in {owner[m]}")
                                ok = False
                            else:
                                owner[m] = name
                        item = {"name": name, "members": sorted(set(members))}
                        if "vlan" in g:
                            if not is_int(g["vlan"]) or not 1 <= g["vlan"] <= 4094:
                                problem(f"{path}.vlan", "out_of_range", "1..4094")
                                ok = False
                            else:
                                item["vlan"] = g["vlan"]
                        if ok:
                            items.append(item)
                    norm["explicit"] = items
            if norm:
                out["groups"] = norm

    if "switches" in raw:
        sw = raw["switches"]
        if not isinstance(sw, dict):
            problem("switches", "bad_type", "object")
        else:
            keys(sw, {"unused_ports_shutdown", "trunk_prune", "vlan_names", "mgmt_vlan"}, "switches")
            norm = {}
            for key in ("unused_ports_shutdown", "trunk_prune"):
                if key in sw:
                    if type(sw[key]) is not bool:
                        problem(f"switches.{key}", "bad_type", "boolean")
                    elif sw[key]:
                        norm[key] = True
            if "vlan_names" in sw:
                if sw["vlan_names"] != "group":
                    problem("switches.vlan_names", "out_of_range", "group")
                else:
                    norm["vlan_names"] = "group"
            if "mgmt_vlan" in sw:
                if not is_int(sw["mgmt_vlan"]) or not 2 <= sw["mgmt_vlan"] <= 4094:
                    problem("switches.mgmt_vlan", "out_of_range", "2..4094")
                else:
                    norm["mgmt_vlan"] = sw["mgmt_vlan"]
            if norm:
                out["switches"] = norm

    if "reachability" in raw:
        reach = raw["reachability"]
        if reach == "segments":
            out["reachability"] = "segments"
        elif isinstance(reach, list) and reach:
            pairs, ok = [], True
            for k, pair in enumerate(reach[:200]):
                if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, str) for x in pair)):
                    problem(f"reachability[{k}]", "bad_type", "[node, node]")
                    ok = False
                    continue
                for j, name in enumerate(pair):
                    if name not in node_names:
                        problem(f"reachability[{k}][{j}]", "unknown_node", name[:80])
                        ok = False
                if pair[0] == pair[1]:
                    problem(f"reachability[{k}]", "bad_type", "two different nodes")
                    ok = False
                pairs.append(list(pair))
            if ok:
                out["reachability"] = pairs
        else:
            problem("reachability", "bad_type", '"segments" or a list of node pairs')

    if "rules" in raw:
        rules = raw["rules"]
        if not isinstance(rules, dict):
            problem("rules", "bad_type", "object")
        else:
            norm = {}
            for code in sorted(rules):
                if code not in RULES:
                    problem(f"rules.{code}", "unknown_rule", code[:64])
                elif rules[code] != "off":
                    problem(f"rules.{code}", "out_of_range", "off")
                else:
                    norm[code] = "off"
            if norm:
                out["rules"] = norm
    return (None if problems else out), problems


def normalize_grading(raw):
    """The grading settings of a check request with every default filled in. Raises ValueError on a bad value."""
    raw = raw or {}
    profile = raw.get("profile") or "standard"
    if profile not in PROFILES:
        raise ValueError(f"grading.profile: one of {', '.join(PROFILES)}")
    overrides = raw.get("overrides") or {}
    for code, cls in overrides.items():
        if code not in RULES:
            raise ValueError(f"grading.overrides: unknown rule {code[:64]}")
        if cls not in CLASSES:
            raise ValueError(f"grading.overrides.{code}: one of {', '.join(CLASSES)}")
    thresholds = raw.get("thresholds")
    if thresholds is None:
        thresholds = [dict(t) for t in DEFAULT_THRESHOLDS]
    else:
        if not 1 <= len(thresholds) <= MAX_THRESHOLDS:
            raise ValueError(f"grading.thresholds: 1..{MAX_THRESHOLDS} rows")
        rows = []
        for row in thresholds:
            mark = row.get("mark")
            if not is_int(mark) or not 0 <= mark <= 100:
                raise ValueError("grading.thresholds[].mark: integer 0..100")
            item = {"mark": mark}
            for cls in ("critical", "major", "minor"):
                value = row.get(cls)
                if value is not None and (not is_int(value) or value < 0):
                    raise ValueError(f"grading.thresholds[].{cls}: integer >= 0 or null")
                item[cls] = value
            rows.append(item)
        if any(a["mark"] <= b["mark"] for a, b in zip(rows, rows[1:])):
            raise ValueError("grading.thresholds: marks strictly decreasing")
        thresholds = rows
    fail_mark = raw.get("fail_mark", 2)
    if not is_int(fail_mark) or not 0 <= fail_mark <= 100 or fail_mark >= min(t["mark"] for t in thresholds):
        raise ValueError("grading.fail_mark: integer 0..100 below every mark")
    return {"profile": profile, "overrides": dict(sorted(overrides.items())), "thresholds": thresholds,
            "fail_mark": fail_mark}


# ---------------------------------------------------------------------- expectations


@dataclass(eq=False)
class Endpoint:
    set: "Expected"
    end: tuple  # (node_id, adapter, port)
    address: ipaddress.IPv4Interface | None
    gateway: bool = False


@dataclass(eq=False)
class Expected:
    """A segment of the definition or a group of nodes: what must be one broadcast domain."""
    kind: str  # segment | group
    name: str
    vlan: int | None
    network: ipaddress.IPv4Network | None
    gateway: ipaddress.IPv4Address | None
    endpoints: list = field(default_factory=list)
    objects: set = field(default_factory=set)  # Ports, links and switches of its L2 path


def node_roles(model: Model, definition: dict | None, images: dict) -> dict:
    """node_id -> switch | router | host. From the image category of a definition, else from the facts and names."""
    by_name = {}
    for n in (definition or {}).get("nodes", []):
        image = images.get(n.get("image"))
        if image is not None:
            by_name[n["name"]] = image.category
    roles = {}
    for nid, node in model.nodes.items():
        category = by_name.get(node["name"])
        ifs = model.ifaces.get(nid, [])
        if category in ("switch", "router"):
            roles[nid] = category
        elif category is not None:
            roles[nid] = "host"
        elif any(i.switchport is not None or i.mirror for i in ifs) or SWITCH_NAME_RE.match(node["name"]):
            roles[nid] = "switch"
        elif ROUTER_NAME_RE.match(node["name"]) or node["type"] == "iou":
            roles[nid] = "router"
        else:
            roles[nid] = "host"
    return roles


def l2_reach(model: Model, roles: dict, start):
    """(ends, switches, links) reachable from a port through switches."""
    ends, switches, links = set(), set(), set()
    stack = [model.peer[start]] if start in model.peer else []
    if start in model.link_of:
        links.add(model.link_of[start].id)
    while stack:
        port = stack.pop()
        if roles.get(port[0]) == "switch":
            if port[0] in switches:
                continue
            switches.add(port[0])
            for other, peer in model.peer.items():
                if other[0] == port[0] and other != port:
                    links.add(model.link_of[other].id)
                    stack.append(peer)
        else:
            ends.add(port)
    return ends, switches, links


def uplink_lans(model: Model, roles: dict, definition: dict | None, images: dict) -> dict:
    """(node_id, interface name) -> ISP LANs the interface is cabled to, directly or through switches.

    An address from such a LAN is where the ISP puts it, not outside the allowed ranges: it is checked against the LAN of
    the very ISP port the interface reaches, so 198.18.x anywhere else is still `address_not_private`.
    """
    lans = {}
    for node in (definition or {}).get("nodes", []):
        image = images.get(node.get("image"))
        isp = model.by_name.get(node.get("name"))
        if image is None or image.uplink_port is None or isp is None:
            continue
        for start in sorted(p for p in model.peer if p[0] == isp):
            adapter = start[1]
            if adapter == image.uplink_port or not is_int(adapter) or not 1 <= adapter <= 15:
                continue
            network = ipaddress.IPv4Network(ISP_LAN.format(adapter=adapter))
            ends, _, _ = l2_reach(model, roles, start)
            for end in ends:
                for i in model.ifaces.get(end[0], []):
                    if i.port == (end[1], end[2]):
                        lans.setdefault((i.node_id, i.name), set()).add(network)
    return lans


def path_objects(model: Model, roles: dict, ends: list) -> set:
    """Ports, links and switches on the L2 paths between the endpoints of a set (segments.Fabric.tree): a switch that
    no endpoint is cabled to and that has one cable at most inside the set (a spur) is not on a path."""
    objects = set()
    for end in ends:
        objects |= {f"port:{end}", f"node:{end[0]}"}
        if end in model.peer:
            objects |= {f"port:{model.peer[end]}", f"link:{model.link_of[end].id}"}
    if not ends:
        return objects
    _, switches, _ = l2_reach(model, roles, ends[0])
    cables = [link for link in model.links if link.a[0] in switches and link.b[0] in switches]
    attached = {model.peer[e][0] for e in ends if e in model.peer}
    keep = set(switches)
    while True:
        def degree(name):
            return sum(1 for link in cables if name in (link.a[0], link.b[0]) and link.a[0] in keep and link.b[0] in keep)
        spur = sorted(name for name in keep if name not in attached and degree(name) <= 1)
        if not spur:
            break
        keep.discard(spur[0])
    objects |= {f"node:{name}" for name in keep}
    for link in cables:
        if link.a[0] in keep and link.b[0] in keep:
            objects |= {f"link:{link.id}", f"port:{link.a}", f"port:{link.b}"}
    return objects


def build_expected(model: Model, roles: dict, definition: dict | None, checks: dict | None) -> list:
    out = []
    for seg in (definition or {}).get("segments", []):
        network = ipaddress.IPv4Network(seg["subnet"]["fixed"])
        gateway = seg.get("gateway")
        gw, addrs, _ = segments_mod.plan(network, gateway is not None, gateway.get("host") if gateway else None,
                                         [m.get("host") for m in seg["members"]])
        exp = Expected("segment", seg["name"], seg.get("vlan", {}).get("fixed"), network, gw)
        if gateway is not None and gateway["node"] in model.by_name:
            exp.endpoints.append(Endpoint(exp, (model.by_name[gateway["node"]], gateway.get("adapter", 0), gateway.get("port", 0)),
                                          ipaddress.IPv4Interface((gw, network.prefixlen)), gateway=True))
        for member, address in zip(seg["members"], addrs):
            if member["node"] in model.by_name:
                exp.endpoints.append(Endpoint(exp, (model.by_name[member["node"]], member.get("adapter", 0), member.get("port", 0)),
                                              address))
        out.append(exp)
    in_segments = {ep.end[0] for e in out for ep in e.endpoints}
    groups = (checks or {}).get("groups") or {}
    explicit = groups.get("explicit") or []
    taken = set()
    named = []
    for g in explicit:
        named.append((g["name"], g.get("vlan"), [model.by_name[m] for m in g["members"] if m in model.by_name]))
        taken |= set(named[-1][2])
    if groups.get("by_name"):
        by_prefix = {}
        for nid in sorted(model.nodes, key=model.name):
            if nid in taken or nid in in_segments or roles.get(nid) != "host":
                continue
            if not any(roles.get(model.peer[e][0]) == "switch" for e in model.peer if e[0] == nid):
                continue
            prefix = re.sub(r"\d+$", "", model.name(nid).strip()).rstrip("-_")
            if prefix:
                by_prefix.setdefault(prefix.lower(), (prefix, []))[1].append(nid)
        if len(by_prefix) >= 2:
            named += [(shown, None, nids) for _, (shown, nids) in sorted(by_prefix.items())]
    for name, vlan, nids in named:
        exp = Expected("group", name, vlan, None, None)
        for nid in nids:
            for end in sorted(e for e in model.peer if e[0] == nid and roles.get(model.peer[e][0]) == "switch"):
                exp.endpoints.append(Endpoint(exp, end, None))
        out.append(exp)
    for exp in out:
        exp.objects = path_objects(model, roles, [ep.end for ep in exp.endpoints])
    return out


# ---------------------------------------------------------------------- evaluation


@dataclass
class Finding:
    code: str
    message: str
    nodes: list
    ifaces: list
    objects: set
    id: str = ""
    caused_by: str | None = None


class Run:
    def __init__(self):
        self.findings = []
        self.skipped = {code: set() for code in RULES}  # code -> reasons
        self.applied = {code: False for code in RULES}

    def add(self, code, message, *, nodes=(), ifaces=(), objects=()):
        self.applied[code] = True
        self.findings.append(Finding(code, message, sorted(set(nodes)), list(ifaces), set(objects)))

    def skip(self, code, reason):
        self.skipped[code].add(reason)

    def ran(self, *codes):
        for code in codes:
            self.applied[code] = True


def evaluate(model: Model, *, roles: dict, expected: list, checks: dict | None, stale: set,
             unsaved: set = frozenset(), persistence: dict | None = None,
             isp_lans: dict | None = None) -> tuple[list, list]:
    """(rules, findings) of a lab: rules = [{"code", "group", "status"[, "reason"]}], findings as in the run document."""
    checks = checks or {}
    run = Run()
    names = model.name
    ignored = {nid for nid, n in model.nodes.items() if n["console"] == "none"}  # Service nodes: ISP, Linux switch
    has_expectations = bool(expected) or bool(checks)

    def availability(nid):
        """None if the node's facts can be used, else the reason of a skip ('' = a student matter, no skip)."""
        node = model.nodes[nid]
        if nid in stale:
            return "stale_facts"
        if node["facts"] is None or not node["has_ifaces"]:
            if node["status"] != "started":
                return ""
            if node["facts"] is not None and node["facts"].get("error"):
                return "collect_failed"
            return "no_collector"
        if node["facts"].get("error"):
            return "collect_failed"
        return None

    def iface_ref(i):
        return {"node": i.node, "iface": i.name}

    # ---------------------------------------------------------------- L1
    task_nodes = set(model.nodes)
    if expected:
        task_nodes = {o.split(":", 1)[1] for e in expected for o in e.objects if o.startswith("node:")}
    path_links = {o.split(":", 1)[1] for e in expected for o in e.objects if o.startswith("link:")}
    run.ran("node_stopped", "config_not_saved", "port_shutdown", "link_down")
    for nid in sorted(unsaved, key=names):
        ports = {f"port:{(nid, a, p)}" for (a, p) in model.nodes[nid]["ports"]}
        run.add("config_not_saved", f"{names(nid)}: конфигурация не сохранена (write memory) — после перезагрузки "
                                    f"устройство загрузится без настроек; проверка видит его таким.",
                nodes=[names(nid)], objects={f"node:{nid}"} | ports)
    for nid in sorted(task_nodes, key=names):
        if model.nodes[nid]["status"] != "started":
            run.add("node_stopped", f"{names(nid)}: узел {'на паузе' if model.nodes[nid]['status'] == 'suspended' else 'остановлен'}.",
                    nodes=[names(nid)], objects={f"node:{nid}"})
    if expected:
        run.ran("link_suspended")
        for link in model.links:
            if link.suspend and link.id in path_links:
                run.add("link_suspended", f"Линк {model.end_label(link.a)} — {model.end_label(link.b)} приостановлен.",
                        nodes=[names(link.a[0]), names(link.b[0])], objects={f"link:{link.id}"})
    else:
        run.skip("link_suspended", "no_expectation")
    for link in model.links:
        l1 = model.l1[link.id]
        if expected and link.id not in path_links:
            continue
        shut = [r for r in l1.reasons if r[0] == "shutdown"]
        for _, end, i in shut:
            if end[0] in ignored:
                continue
            run.add("port_shutdown", f"{model.end_label(end)}: порт выключен (shutdown), кабель к "
                                     f"{model.end_label(model.peer[end])}.",
                    nodes=[names(end[0])], ifaces=[iface_ref(i)], objects={f"port:{end}", f"link:{link.id}"})
        carrier = [r for r in l1.reasons if r[0] in ("no_carrier", "l1_down")]
        if carrier and not any(r[0] in ("suspended", "node_off") for r in l1.reasons):
            text = "; ".join(f"{model.end_label(end)}: {'нет несущей' if kind == 'no_carrier' else 'соединение недоступно'}"
                             for kind, end, _ in carrier)
            run.add("link_down", f"Линк {model.end_label(link.a)} — {model.end_label(link.b)} не поднят: {text}.",
                    nodes=[names(link.a[0]), names(link.b[0])],
                    objects={f"link:{link.id}", f"port:{link.a}", f"port:{link.b}"})

    switches_opt = checks.get("switches") or {}
    if switches_opt.get("unused_ports_shutdown"):
        run.ran("unused_port_up")
        for nid in sorted(model.nodes, key=names):
            if roles.get(nid) != "switch" or model.nodes[nid]["type"] != "iou":
                continue
            reason = availability(nid)
            if reason is not None:
                if reason:
                    run.skip("unused_port_up", reason)
                continue
            up = [i for i in model.ifaces[nid] if i.kind == "physical" and i.port is not None
                  and (nid, *i.port) not in model.peer and i.admin != "down"]
            if up:
                run.add("unused_port_up", f"{names(nid)}: неиспользуемые порты не выключены: {', '.join(i.name for i in up)}.",
                        nodes=[names(nid)], ifaces=[iface_ref(i) for i in up], objects={f"node:{nid}"})
    else:
        run.skip("unused_port_up", "no_expectation")

    # ---------------------------------------------------------------- L2: links
    run.ran("vlan_mismatch", "tag_not_allowed")
    for link in model.links:
        for issue in model.l2[link.id].issues:
            if issue["code"] == "tag_not_allowed":
                if any(e[0] in ignored and roles.get(e[0]) != "switch" for e in issue["ends"]):
                    continue
                # A switch trunk may carry VLANs the router or host behind it does not take: that is pruning
                # (trunk_not_pruned), not a broken link. A host, a router or a switch that the far switch refuses is.
                if roles.get(issue["from"][0]) == "switch" and not issue["to_switch"]:
                    continue
            run.add(issue["code"], issue["text"], nodes=[names(e[0]) for e in issue["ends"]],
                    objects={f"link:{link.id}"} | {f"port:{e}" for e in issue["ends"]})

    # ---------------------------------------------------------------- endpoints
    def endpoint_iface(ep):
        """The interface of an endpoint that holds (or should hold) its address; None if none can be told."""
        cands = model.on_port(ep.end)
        net = ep.set.network
        if net is not None:
            hit = next((i for i in cands if any(a in net or a.ip in net for a in i.addrs)), None)
            if hit is not None:
                return hit
        if ep.gateway and ep.set.vlan is not None:
            sub = next((i for i in cands if i.kind == "subif" and i.dot1q == ep.set.vlan), None)
            if sub is not None:
                return sub
        return model.by_port.get(ep.end)

    ep_domain = {}
    for exp in expected:
        for ep in exp.endpoints:
            i = endpoint_iface(ep) if availability(ep.end[0]) is None else None
            ep_domain[ep] = model.dom_of(i) if i is not None else None

    # ---------------------------------------------------------------- L2: domains
    if expected:
        run.ran("segments_merged", "segment_split", "vlan_wrong")
    else:
        for code in ("segments_merged", "segment_split", "vlan_wrong"):
            run.skip(code, "no_expectation")
    blind = {}  # set name -> reason: a switch of its path has no usable facts, its L2 cannot be told
    for exp in expected:
        for o in sorted(exp.objects):
            if o.startswith("node:") and roles.get(o[5:]) == "switch" and availability(o[5:]):
                blind[exp.name] = availability(o[5:])
                break
    for exp in expected:
        if exp.name in blind:
            run.skip("segment_split", blind[exp.name])
            run.skip("segments_merged", blind[exp.name])
            continue
        doms = {}
        for ep in exp.endpoints:
            reason = availability(ep.end[0])
            if reason:
                run.skip("segment_split", reason)
            d = ep_domain.get(ep)
            if d is not None:
                doms.setdefault(d.id, (d, []))[1].append(ep)
        if len(doms) > 1:
            parts = " | ".join(", ".join(model.end_label(ep.end) for ep in eps) + f" ({model.domain_label(d)})"
                               for d, eps in sorted(doms.values(), key=lambda x: x[0].id))
            what = f"Сегмент {exp.name} разорван" if exp.kind == "segment" else f"Группа {exp.name} разорвана"
            run.add("segment_split", f"{what} на L2: {parts}. Узлы не видят друг друга.",
                    nodes=[names(ep.end[0]) for ep in exp.endpoints], objects=exp.objects | {f"set:{exp.name}"})
    for n, a in enumerate(expected):
        if a.name in blind:
            continue
        da = {ep_domain[ep].id: ep_domain[ep] for ep in a.endpoints if not ep.gateway and ep_domain.get(ep)}
        for b in expected[:n]:
            if b.name in blind:
                continue
            db = {ep_domain[ep].id: ep_domain[ep] for ep in b.endpoints if not ep.gateway and ep_domain.get(ep)}
            common = sorted(set(da) & set(db))
            if common:
                run.add("segments_merged", f"{b.name} и {a.name} в одном L2-домене ({model.domain_label(da[common[0]])}): "
                                           f"они должны быть разделены.",
                        nodes=[names(ep.end[0]) for ep in a.endpoints + b.endpoints],
                        objects=a.objects | b.objects | {f"set:{a.name}", f"set:{b.name}"})
    endpoint_ports = {ep.end for e in expected for ep in e.endpoints}
    run.ran("member_isolated")
    for d in model.domains:
        if not d.switches or len(d.members) != 1:
            continue
        i = d.members[0]
        if not i.addrs or i.node_id in ignored or i.port is None or (i.node_id, *i.port) in endpoint_ports:
            continue
        if (i.node_id, i.name) in (isp_lans or {}):
            continue  # The ISP is there too: it has no facts, so it is no member of the domain
        end = (i.node_id, *i.port)
        run.add("member_isolated", f"{i.label} один в своём L2-домене ({model.domain_label(d)}): кроме него там никого нет.",
                nodes=[i.node], ifaces=[iface_ref(i)], objects={f"port:{end}", f"node:{i.node_id}"})
    for exp in expected:
        if exp.vlan is None:
            continue
        for ep in exp.endpoints:
            if ep.gateway or ep.end not in model.peer:
                continue
            peer = model.peer[ep.end]
            if roles.get(peer[0]) != "switch":
                continue
            reason = availability(peer[0])
            if reason is not None:
                if reason:
                    run.skip("vlan_wrong", reason)
                continue
            sw = model.by_port.get(peer)
            sp = (sw.switchport or {}) if sw else {}
            if sp.get("mode") == "access" and sp.get("vlan") != exp.vlan:
                run.add("vlan_wrong", f"{model.end_label(peer)} (к {model.end_label(ep.end)}): VLAN {sp.get('vlan')}, "
                                      f"должен быть {exp.vlan} ({exp.name}).",
                        nodes=[names(peer[0])], ifaces=[iface_ref(sw)], objects={f"port:{peer}", f"port:{ep.end}"})

    # ---------------------------------------------------------------- L2: switch configuration
    cisco = [nid for nid in sorted(model.nodes, key=names) if roles.get(nid) == "switch" and model.nodes[nid]["type"] == "iou"]
    mgmt = switches_opt.get("mgmt_vlan")
    expected_vlans = {1, *range(1002, 1006)} | ({mgmt} if mgmt else set())
    for exp in expected:
        if exp.vlan is not None:
            expected_vlans.add(exp.vlan)
        for ep in exp.endpoints:
            d = ep_domain.get(ep)
            if d is not None:
                expected_vlans |= d.vlans
    if has_expectations and expected:
        run.ran("vlan_extra", "vlan_extra_used")
        for nid in cisco:
            if availability(nid) is not None:
                continue
            created = {v.get("id") for v in (model.nodes[nid]["facts"].get("vlans") or []) if isinstance(v, dict)}
            extra = {v for v in created if is_int(v)} - expected_vlans
            used = set()
            for i in model.ifaces[nid]:
                sp = i.switchport or {}
                end = (nid, *i.port) if i.port else None
                if sp.get("mode") == "access" and sp.get("vlan") in extra and end in model.peer \
                        and roles.get(model.peer[end][0]) != "switch" and model.peer[end] not in endpoint_ports:
                    used.add(sp["vlan"])
                    run.add("vlan_extra_used", f"{names(nid)} {i.name}: порт узла {model.end_label(model.peer[end])} "
                                               f"в лишнем VLAN {sp['vlan']}.",
                            nodes=[names(nid)], ifaces=[iface_ref(i)], objects={f"port:{end}"})
            rest = sorted(extra - used)
            if rest:
                run.add("vlan_extra", f"{names(nid)}: лишние VLAN {', '.join(map(str, rest))} — их нет в задании.",
                        nodes=[names(nid)], objects={f"node:{nid}"})
    else:
        run.skip("vlan_extra", "no_expectation")
        run.skip("vlan_extra_used", "no_expectation")

    if switches_opt.get("vlan_names") and expected:
        run.ran("vlan_name_wrong")
        for exp in expected:
            vids = {exp.vlan} if exp.vlan is not None else set()
            if not vids:
                for ep in exp.endpoints:
                    peer = model.peer.get(ep.end)
                    sw = model.by_port.get(peer) if peer else None
                    if sw and (sw.switchport or {}).get("mode") == "access":
                        vids.add(sw.switchport.get("vlan"))
            for nid in cisco:
                if availability(nid) is not None or f"node:{nid}" not in exp.objects:
                    continue
                table = {v.get("id"): v.get("name") for v in model.nodes[nid]["facts"].get("vlans") or [] if isinstance(v, dict)}
                for vid in sorted(v for v in vids if v in table):
                    if str(table[vid] or "").lower() != exp.name.lower():
                        run.add("vlan_name_wrong", f"{names(nid)}: VLAN {vid} назван «{table[vid] or 'без имени'}», "
                                                   f"должен называться {exp.name}.",
                                nodes=[names(nid)], objects={f"node:{nid}"})
    else:
        run.skip("vlan_name_wrong", "no_expectation")

    if switches_opt.get("trunk_prune") and expected:
        run.ran("trunk_not_pruned")
        for link in model.links:
            if not all(roles.get(e[0]) == "switch" for e in (link.a, link.b)):
                continue
            needed = set()
            for exp in expected:
                if f"link:{link.id}" in exp.objects:
                    needed |= {exp.vlan} if exp.vlan is not None else \
                        {v for ep in exp.endpoints if ep_domain.get(ep) for v in ep_domain[ep].vlans}
            for end in (link.a, link.b):
                i = model.by_port.get(end)
                if i is None or availability(end[0]) is not None or (i.switchport or {}).get("mode") != "trunk":
                    continue
                allowed = i.switchport.get("allowed")
                nat = i.switchport.get("native", 1)
                if allowed is None or allowed == "all":
                    more = "все VLAN"
                else:
                    extra = sorted(set(allowed) - needed - {nat} - ({mgmt} if mgmt else set()))
                    more = ", ".join(map(str, extra)) if extra else None
                if more:
                    run.add("trunk_not_pruned", f"{model.end_label(end)}: на транке разрешены лишние VLAN ({more}).",
                            nodes=[names(end[0])], ifaces=[iface_ref(i)], objects={f"port:{end}"})
    else:
        run.skip("trunk_not_pruned", "no_expectation")

    if mgmt:
        run.ran("mgmt_vlan_missing")
        for nid in cisco:
            reason = availability(nid)
            if reason is not None:
                if reason:
                    run.skip("mgmt_vlan_missing", reason)
                continue
            created = {v.get("id") for v in model.nodes[nid]["facts"].get("vlans") or [] if isinstance(v, dict)}
            svi = next((i for i in model.ifaces[nid] if i.kind == "svi" and i.vlan == mgmt), None)
            if mgmt not in created and "vlans" in model.nodes[nid]["facts"]:
                why = f"нет VLAN {mgmt}"
            elif svi is None:
                why = f"нет интерфейса Vlan{mgmt}"
            elif not svi.addrs:
                why = f"у Vlan{mgmt} нет адреса"
            elif svi.admin == "down":
                why = f"Vlan{mgmt} выключен (shutdown)"
            else:
                continue
            run.add("mgmt_vlan_missing", f"{names(nid)}: менеджмент-VLAN не настроен: {why}.",
                    nodes=[names(nid)], objects={f"node:{nid}"})
    else:
        run.skip("mgmt_vlan_missing", "no_expectation")

    # ---------------------------------------------------------------- addresses of the endpoints
    addr = checks.get("addresses") or {}
    match = addr.get("match", "plan")
    checked_ifaces = set()
    if any(e.network for e in expected):
        run.ran("address_missing", "address_wrong", "address_misplaced", "mask_wrong")
    else:
        for code in ("address_missing", "address_wrong", "address_misplaced"):
            run.skip(code, "no_expectation")
    for exp in expected:
        if exp.network is None:
            continue
        for ep in exp.endpoints:
            nid = ep.end[0]
            reason = availability(nid)
            if reason is not None:
                if reason:
                    for code in ("address_missing", "address_wrong", "address_misplaced"):
                        run.skip(code, reason)
                continue
            cands = model.on_port(ep.end)
            if not cands:
                for code in ("address_missing", "address_wrong", "address_misplaced"):
                    run.skip(code, "port_unknown")
                continue
            checked_ifaces.update(cands)
            want = ep.address
            role = "шлюз" if ep.gateway else "узел"
            where = model.end_label(ep.end)
            objects = {f"port:{ep.end}", f"node:{nid}", f"set:{exp.name}"}
            have = [(i, a) for i in cands for a in i.addrs]
            exact_ok = any(a == want for _, a in have) if (match == "plan" or ep.gateway) else \
                any(a.network == exp.network and a.ip != a.network.network_address for _, a in have)
            if exact_ok:
                continue
            same_ip = [(i, a) for i, a in have if a.ip == want.ip]
            if same_ip and (match == "plan" or ep.gateway):
                i, a = same_ip[0]
                run.add("mask_wrong", f"{where}: адрес {a}, маска должна быть /{exp.network.prefixlen} ({exp.name}).",
                        nodes=[names(nid)], ifaces=[iface_ref(i)], objects=objects)
                continue
            elsewhere = [(i, a) for i in model.ifaces[nid] if i not in cands for a in i.addrs
                         if (a.ip == want.ip if (match == "plan" or ep.gateway) else a.network == exp.network)]
            if elsewhere:
                i, a = elsewhere[0]
                run.add("address_misplaced", f"{model.name(nid)}: адрес {a} стоит на {i.name}, а должен быть на "
                                             f"{model.end_text(ep.end)} (кабель к {model.end_label(model.peer[ep.end]) if ep.end in model.peer else 'нет'}).",
                        nodes=[names(nid)], ifaces=[iface_ref(i)], objects=objects)
                continue
            if have:
                text = ", ".join(str(a) for _, a in have)
                need = str(want) if (match == "plan" or ep.gateway) else f"адрес из {exp.network}"
                run.add("address_wrong", f"{where}: адрес {text}, а {role} {exp.name} должен иметь {need}.",
                        nodes=[names(nid)], ifaces=[iface_ref(i) for i, _ in have], objects=objects)
                continue
            need = str(want) if (match == "plan" or ep.gateway) else f"адрес из {exp.network}"
            run.add("address_missing", f"{where}: нет адреса; {role} {exp.name} должен иметь {need}.",
                    nodes=[names(nid)], objects=objects)

    # ---------------------------------------------------------------- addresses everywhere
    allowed_nets = []
    for item in addr.get("allowed", ["rfc1918"]):
        allowed_nets += list(RFC1918) if item == "rfc1918" else [ipaddress.IPv4Network(item)]
    allowed_nets += [e.network for e in expected if e.network is not None]
    forbid = set(addr.get("forbid_prefix", DEFAULT_FORBID_PREFIX))
    mgmt_svi = lambda i: i.kind == "svi" and (mgmt is None or i.vlan == mgmt)  # noqa: E731
    run.ran("mask_wrong", "address_extra", "address_apipa", "address_not_private", "address_duplicate",
            "subnet_overlap", "subnet_mixed")
    pairs = []
    for nid in sorted(model.nodes, key=names):
        if nid in ignored:
            continue
        reason = availability(nid)
        if reason is not None:
            if reason and reason != "no_collector":
                for code in ("address_extra", "address_apipa", "address_not_private", "mask_wrong"):
                    run.skip(code, reason)
            continue
        for i in model.ifaces[nid]:
            if i.kind == "loopback":
                continue
            for a in i.addrs:
                pairs.append((i, a))
                objects = {f"node:{nid}"} | ({f"port:{(nid, *i.port)}"} if i.port else set())
                if a.ip in APIPA:
                    run.add("address_apipa", f"{i.label}: адрес {a} из 169.254.0.0/16 — DHCP-клиент не получил ответа.",
                            nodes=[i.node], ifaces=[iface_ref(i)], objects=objects)
                    continue
                lans = (isp_lans or {}).get((i.node_id, i.name), ())
                if not any(a.ip in net for net in allowed_nets) and not any(a.ip in net for net in lans):
                    run.add("address_not_private", f"{i.label}: адрес {a} не из разрешённых диапазонов "
                                                   f"({', '.join(addr.get('allowed', ['rfc1918']))}).",
                            nodes=[i.node], ifaces=[iface_ref(i)], objects=objects)
                if i not in checked_ifaces and a.network.prefixlen in forbid:
                    run.add("mask_wrong", f"{i.label}: адрес {a} — маска /{a.network.prefixlen} недопустима.",
                            nodes=[i.node], ifaces=[iface_ref(i)], objects=objects)
            extra = []
            if len(i.addrs) > 1:
                extra.append(f"на интерфейсе несколько адресов ({', '.join(map(str, i.addrs))})")
            if i.addrs and i.port is not None and (nid, *i.port) not in model.peer:
                extra.append(f"адрес {i.addrs[0]} на интерфейсе без кабеля")
            if i.addrs and roles.get(nid) == "switch" and not mgmt_svi(i):
                extra.append(f"адрес {i.addrs[0]} на коммутаторе вне менеджмент-VLAN")
            if extra:
                run.add("address_extra", f"{i.label}: лишний адрес — {'; '.join(extra)}.",
                        nodes=[i.node], ifaces=[iface_ref(i)],
                        objects={f"node:{nid}"} | ({f"port:{(nid, *i.port)}"} if i.port else set()))
    by_ip = {}
    for i, a in pairs:
        if a.ip not in APIPA:
            by_ip.setdefault(a.ip, []).append(i)
    for ip in sorted(by_ip):
        holders = by_ip[ip]
        if len(holders) > 1:
            run.add("address_duplicate", f"Адрес {ip} назначен дважды: {' и '.join(i.label for i in holders)}.",
                    nodes=[i.node for i in holders], ifaces=[iface_ref(i) for i in holders],
                    objects={f"node:{i.node_id}" for i in holders})
    nets = sorted({a.network for _, a in pairs if a.ip not in APIPA}, key=lambda n: (int(n.network_address), n.prefixlen))
    for n, a in enumerate(nets):
        for b in nets[n + 1:]:
            if a.overlaps(b):
                who = [i for i, x in pairs if x.network in (a, b)]
                run.add("subnet_overlap", f"Подсети {a} и {b} пересекаются ({', '.join(sorted({i.label for i in who}))}) — "
                                          f"похоже на разные маски в одном сегменте.",
                        nodes=[i.node for i in who], ifaces=[iface_ref(i) for i in who],
                        objects={f"node:{i.node_id}" for i in who})
    for d in model.domains:
        subnets = sorted({a.network for i in d.members if i.node_id not in ignored for a in i.addrs if a.ip not in APIPA},
                         key=lambda n: (int(n.network_address), n.prefixlen))
        if len(subnets) > 1 and not any(x.overlaps(y) for k, x in enumerate(subnets) for y in subnets[k + 1:]):
            run.add("subnet_mixed", f"В одном L2-домене ({model.domain_label(d)}) несколько подсетей: "
                                    f"{', '.join(map(str, subnets))}.",
                    nodes=[i.node for i in d.members], objects={f"node:{i.node_id}" for i in d.members}
                    | {f"port:{(i.node_id, *i.port)}" for i in d.members if i.port}
                    | {f"set:{e.name}" for e in expected for ep in e.endpoints if ep_domain.get(ep) is d})

    # ---------------------------------------------------------------- gateways
    lab_ips = {a.ip for i, a in pairs}
    for nid in model.nodes:
        if nid in ignored:
            lab_ips |= {a.ip for i in model.ifaces[nid] for a in i.addrs}

    def default_via(nid):
        """(has routes, [via]) of the node's default routes."""
        routes = (model.nodes[nid]["facts"] or {}).get("routes")
        if not isinstance(routes, list):
            return False, []
        vias = []
        for r in routes:
            if isinstance(r, dict) and r.get("dst") in ("default", "0.0.0.0/0") and r.get("via"):
                try:
                    vias.append(ipaddress.IPv4Address(r["via"]))
                except ValueError:
                    continue
        return True, vias

    gw_sets = [e for e in expected if e.gateway is not None or e.kind == "group"]
    if gw_sets:
        run.ran("gateway_missing", "gateway_wrong")
    else:
        run.skip("gateway_missing", "no_expectation")
    run.ran("gateway_wrong")
    gw_checked = set()
    for exp in gw_sets:
        if exp.kind == "group":
            doms = {ep_domain[ep].id: ep_domain[ep] for ep in exp.endpoints if ep_domain.get(ep)}
            routers = {a.ip for d in doms.values() for i in d.members if roles.get(i.node_id) == "router" for a in i.addrs}
            reach_router = any(roles.get(e[0]) == "router" for ep in exp.endpoints[:1]
                               for e in l2_reach(model, roles, ep.end)[0])
            if not reach_router:
                continue
            want_text = "адрес маршрутизатора в домене группы"
        else:
            routers = {exp.gateway}
            want_text = str(exp.gateway)
        for ep in exp.endpoints:
            nid = ep.end[0]
            if ep.gateway or roles.get(nid) != "host" or nid in gw_checked:
                continue
            reason = availability(nid)
            if reason is not None:
                if reason:
                    run.skip("gateway_missing", reason)
                    run.skip("gateway_wrong", reason)
                continue
            has, vias = default_via(nid)
            if not has:
                run.skip("gateway_missing", "no_collector")
                continue
            gw_checked.add(nid)
            objects = {f"node:{nid}", f"port:{ep.end}", f"set:{exp.name}"} | exp.objects
            if not vias:
                run.add("gateway_missing", f"{names(nid)}: нет шлюза по умолчанию; должен быть {want_text} ({exp.name}).",
                        nodes=[names(nid)], objects={f"node:{nid}", f"port:{ep.end}"})
            elif not any(v in routers for v in vias):
                run.add("gateway_wrong", f"{names(nid)}: шлюз по умолчанию {', '.join(map(str, vias))}, а должен быть "
                                         f"{want_text} ({exp.name}).",
                        nodes=[names(nid)], objects=objects)
    for nid in sorted(model.nodes, key=names):
        if nid in gw_checked or nid in ignored or roles.get(nid) != "host" or availability(nid) is not None:
            continue
        has, vias = default_via(nid)
        own = [a.network for i in model.ifaces[nid] for a in i.addrs]
        # The ISP port of a LAN the node is cabled to is a gateway, though the ISP has no facts (images/lab-uplink).
        isp = {net.network_address + 1 for i in model.ifaces[nid] for net in (isp_lans or {}).get((nid, i.name), ())}
        for v in vias:
            if v in isp:
                continue
            if v not in lab_ips or not any(v in net for net in own):
                why = "такого адреса нет в лабе" if v not in lab_ips else "он не в подсети узла"
                run.add("gateway_wrong", f"{names(nid)}: шлюз по умолчанию {v} — {why}.",
                        nodes=[names(nid)], objects={f"node:{nid}"})
                break

    # ---------------------------------------------------------------- reachability
    reach = checks.get("reachability")
    if reach:
        run.ran("unreachable")
        owner = {}
        for i, a in pairs:
            owner.setdefault(a.ip, []).append(i)
        for nid in ignored:
            for i in model.ifaces.get(nid, []):
                for a in i.addrs:
                    owner.setdefault(a.ip, []).append(i)

        def trace(src, dst):
            """'ok' | ('fail', text, nodes) | ('skip', reason)."""
            nid, seen, path = src, set(), []
            for _ in range(32):
                if any(a.ip == dst for i in model.ifaces.get(nid, []) for a in i.addrs):
                    return "ok"
                if nid in seen:
                    return ("fail", f"петля маршрутизации через {' → '.join(names(x) for x in path)}", path)
                seen.add(nid)
                path.append(nid)
                reason = availability(nid)
                if reason is not None:
                    return ("skip", reason or "no_collector")
                routes = model.nodes[nid]["facts"].get("routes")
                if not isinstance(routes, list):
                    return ("skip", "no_collector")
                best = None
                for r in routes:
                    if not isinstance(r, dict):
                        continue
                    try:
                        net = ipaddress.IPv4Network("0.0.0.0/0" if r.get("dst") == "default" else str(r.get("dst")), strict=False)
                    except ValueError:
                        continue
                    if dst in net and (best is None or net.prefixlen > best[0].prefixlen):
                        best = (net, r)
                if best is None:
                    return ("fail", f"у {names(nid)} нет маршрута к {dst}", path)
                route = best[1]
                hop = dst
                if route.get("via"):
                    try:
                        hop = ipaddress.IPv4Address(route["via"])
                    except ValueError:
                        return ("fail", f"у {names(nid)} неверный маршрут к {dst}", path)
                egress = model.by_name_if.get(nid, {}).get(route.get("dev"))
                dom = model.dom_of(egress) if egress is not None else None
                holders = [i for i in owner.get(hop, []) if i.node_id != nid]
                near = [i for i in holders if dom is not None and model.dom_of(i) is dom] or \
                       ([] if dom is not None else holders)
                if not near:
                    if holders:
                        return ("fail", f"{names(nid)} → {hop}: {hop} не в одном L2-домене с {names(nid)}", path)
                    return ("fail", f"{names(nid)} → {hop}: узла с адресом {hop} нет в сегменте", path)
                nid = near[0].node_id
                if model.nodes[nid]["facts"].get("ip_forward") is False and \
                        not any(a.ip == dst for i in model.ifaces.get(nid, []) for a in i.addrs):
                    return ("fail", f"на {names(nid)} выключена пересылка пакетов (ip_forward)", path + [nid])
            return ("fail", "слишком длинный путь", path)

        def primary(nid, exp=None):
            if exp is not None:
                for ep in exp.endpoints:
                    if ep.end[0] == nid and ep.address is not None:
                        return ep.address.ip
            for i in model.ifaces.get(nid, []):
                if i.kind != "loopback" and i.addrs:
                    return i.addrs[0].ip
            return None

        pairs_to_check = []
        if reach == "segments":
            segs = [e for e in expected if e.kind == "segment" and e.gateway is not None]
            for n, a in enumerate(segs):
                for b in segs[n + 1:]:
                    sa = next((ep for ep in a.endpoints if not ep.gateway), None)
                    sb = next((ep for ep in b.endpoints if not ep.gateway), None)
                    if sa and sb:
                        pairs_to_check.append((f"{a.name} ↔ {b.name}", sa.end[0], sa.address.ip, sb.end[0], sb.address.ip,
                                               a.objects | b.objects | {f"set:{a.name}", f"set:{b.name}"}))
        else:
            for x, y in reach:
                nx, ny = model.by_name.get(x), model.by_name.get(y)
                if nx is None or ny is None:
                    continue
                objects = {f"node:{nx}", f"node:{ny}"}
                for e in expected:
                    if any(ep.end[0] in (nx, ny) for ep in e.endpoints):
                        objects |= e.objects | {f"set:{e.name}"}
                pairs_to_check.append((f"{x} ↔ {y}", nx, primary(nx), ny, primary(ny), objects))
        for title, na, ia, nb, ib, objects in pairs_to_check:
            if ia is None or ib is None:
                missing = names(na) if ia is None else names(nb)
                run.add("unreachable", f"Нет связи {title}: у {missing} нет адреса.", nodes=[names(na), names(nb)],
                        objects=objects)
                continue
            for src, dst in ((na, ib), (nb, ia)):
                result = trace(src, dst)
                if result == "ok":
                    continue
                if result[0] == "skip":
                    run.skip("unreachable", result[1])
                    break
                run.add("unreachable", f"Нет связи {title}: {result[1]}.",
                        nodes=[names(na), names(nb)] + [names(x) for x in result[2]],
                        objects=objects | {f"node:{x}" for x in result[2]})
                break
    else:
        run.skip("unreachable", "no_expectation")

    # ---------------------------------------------------------------- the node itself
    run.ran("hostname_wrong", "forwarding_off", "forwarding_on_host")
    for nid in sorted(model.nodes, key=names):
        if nid in ignored or nid in unsaved:
            continue
        reason = availability(nid)
        if reason is not None:
            if reason:
                for code in ("hostname_wrong", "forwarding_off", "forwarding_on_host"):
                    run.skip(code, reason)
            continue
        facts = model.nodes[nid]["facts"]
        hostname = facts.get("hostname")
        # ios.config of slot 0.17.0+ always gives `routes`; without a `hostname` line the device has no name of its own.
        if isinstance(hostname, str) or (model.nodes[nid]["type"] == "iou" and "routes" in facts):
            want = names(nid).lower()
            have = (hostname or "").strip()
            if have.lower() != want and not have.lower().startswith(want + "."):
                run.add("hostname_wrong", f"{names(nid)}: имя узла «{have}», должно быть {names(nid)}." if have
                        else f"{names(nid)}: имя узла не задано (hostname), должно быть {names(nid)}.",
                        nodes=[names(nid)], objects={f"node:{nid}"})
        else:
            run.skip("hostname_wrong", "no_collector")
        forward = facts.get("ip_forward")
        if not isinstance(forward, bool):
            if model.nodes[nid]["type"] == "qemu":
                run.skip("forwarding_off", "no_collector")
                run.skip("forwarding_on_host", "no_collector")
        elif roles.get(nid) == "router" and not forward:
            run.add("forwarding_off", f"{names(nid)}: маршрутизатор не пересылает пакеты (net.ipv4.ip_forward = 0).",
                    nodes=[names(nid)], objects={f"node:{nid}"})
        elif roles.get(nid) == "host" and forward:
            run.add("forwarding_on_host", f"{names(nid)}: на узле включена пересылка пакетов (net.ipv4.ip_forward = 1), "
                                          f"а это не маршрутизатор.", nodes=[names(nid)], objects={f"node:{nid}"})

    # ---------------------------------------------------------------- saved network configuration (Linux)
    run.ran("address_not_saved", "config_not_applied")
    for nid in sorted(model.nodes, key=names):
        mode = (persistence or {}).get(nid)
        if nid in ignored or mode == "runtime":
            continue
        reason = availability(nid)
        if reason is not None:
            if reason and mode == "saved":
                run.skip("address_not_saved", reason)
                run.skip("config_not_applied", reason)
            continue
        facts = model.nodes[nid]["facts"]
        saved = facts.get("net_saved")
        if not isinstance(saved, dict):
            if mode == "saved" and model.nodes[nid]["type"] == "qemu" and "interfaces" in facts and \
                    "switchport" not in str(facts.get("interfaces")):
                run.skip("address_not_saved", "no_collector")
                run.skip("config_not_applied", "no_collector")
            continue
        if mode is None:  # A lab of the catalog: the image is unknown, the guest tells whether it saves addresses
            mode = "runtime" if saved.get("mechanism") == "none" else "saved"
            if mode == "runtime":
                continue
        if saved.get("supported") is False:
            run.skip("address_not_saved", "unsupported_os")
            run.skip("config_not_applied", "unsupported_os")
            continue
        node_obj = {f"node:{nid}"}
        if saved.get("ok") is False:
            run.add("config_not_applied", f"{names(nid)}: сохранённая настройка сети не применится: "
                                          f"{str(saved.get('message') or 'ошибка в конфигурации')[:200]}.",
                    nodes=[names(nid)], objects=node_obj)
        stored = {}
        for item in saved.get("interfaces") or []:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                stored[item["name"]] = item
        mechanism = saved.get("mechanism") or "—"
        live_gw = set()
        routes = facts.get("routes") if isinstance(facts.get("routes"), list) else []
        for r in routes:
            if isinstance(r, dict) and r.get("dst") in ("default", "0.0.0.0/0") and r.get("via") and r.get("proto") != "dhcp":
                live_gw.add(str(r["via"]))
        saved_gw = {str(item["gateway"]) for item in stored.values() if item.get("gateway")}
        for i in model.ifaces[nid]:
            if i.kind == "loopback":
                continue
            entry = stored.get(i.name)
            dhcp = bool(entry and entry.get("method") == "dhcp")
            kept = {str(x) for x in (entry or {}).get("ipv4") or []}
            objects = node_obj | ({f"port:{(nid, *i.port)}"} if i.port else set())
            for a in i.addrs:
                if a.ip in APIPA or dhcp or str(a) in kept:
                    continue
                run.add("address_not_saved", f"{i.label}: адрес {a} задан только в работающей системе, в конфигурации "
                                             f"сети ({mechanism}) его нет — после перезагрузки он пропадёт.",
                        nodes=[i.node], ifaces=[iface_ref(i)], objects=objects)
            live = {str(a) for a in i.addrs}
            for text in sorted(kept - live):
                run.add("config_not_applied", f"{i.label}: адрес {text} есть в конфигурации сети ({mechanism}), "
                                              f"но в работающей системе его нет.",
                        nodes=[i.node], ifaces=[iface_ref(i)], objects=objects)
        dhcp_any = any(item.get("method") == "dhcp" for item in stored.values())
        for gw in sorted(live_gw - saved_gw):
            if not dhcp_any:
                run.add("address_not_saved", f"{names(nid)}: шлюз по умолчанию {gw} задан только в работающей системе, "
                                             f"в конфигурации сети ({mechanism}) его нет.", nodes=[names(nid)],
                        objects=node_obj)
        for gw in sorted(saved_gw - live_gw):
            run.add("config_not_applied", f"{names(nid)}: шлюз {gw} есть в конфигурации сети ({mechanism}), "
                                          f"но в работающей системе его нет.", nodes=[names(nid)], objects=node_obj)

    for code in NOT_COLLECTED:
        run.skip(code, "no_collector")

    return finish(run, checks)


def persistence_of(model: Model, definition: dict | None, images: dict, checks: dict | None) -> dict:
    """node_id -> saved | runtime | None (decide from the guest: a lab of the catalog) for the Linux guests (§4.4)."""
    checks = checks or {}
    images_of = {n["name"]: images.get(n.get("image")) for n in (definition or {}).get("nodes", [])}
    out = {}
    for nid, node in model.nodes.items():
        explicit = ((checks.get("nodes") or {}).get(node["name"]) or {}).get("persistence") or checks.get("persistence")
        if explicit:
            out[nid] = explicit
        elif node["name"] in images_of and images_of[node["name"]] is not None:
            out[nid] = "saved" if images_of[node["name"]].net_config in SAVED_MECHANISMS else "runtime"
        else:
            out[nid] = None
    return out


def finish(run: Run, checks: dict):
    off = set((checks.get("rules") or {}))
    findings = [f for f in run.findings if f.code not in off]
    findings.sort(key=lambda f: ORDER.index(f.code))
    for n, f in enumerate(findings, 1):
        f.id = f"f{n}"
    for f in findings:
        causes = CAUSES.get(f.code, set())
        for g in findings:
            if g is f or g.code not in causes:
                continue
            if f.objects & g.objects:
                f.caused_by = g.id
                break
    by_id = {f.id: f for f in findings}
    for f in findings:  # caused_by names the root cause, not the next link of the chain
        seen = {f.id}
        while f.caused_by and by_id[f.caused_by].caused_by and by_id[f.caused_by].caused_by not in seen:
            seen.add(f.caused_by)
            f.caused_by = by_id[f.caused_by].caused_by
    failed = {f.code for f in findings}
    rules = []
    for code, (group, _, _) in RULES.items():
        if code in off:
            rules.append({"code": code, "group": group, "status": "off"})
        elif code in failed:
            rules.append({"code": code, "group": group, "status": "fail"})
        elif run.skipped[code]:
            reasons = sorted(run.skipped[code], key=SKIP_REASONS.index)
            rules.append({"code": code, "group": group, "status": "skipped", "reason": reasons[0]})
        else:
            rules.append({"code": code, "group": group, "status": "pass"})
    return rules, findings


# ---------------------------------------------------------------------- grade

STEP = {"note": "minor", "minor": "major", "major": "critical", "critical": "critical"}


def grade(rules: list, findings: list, grading: dict, checks: dict | None):
    """The `result` of a run: outcome, mark, counts, rules, findings as JSON."""
    focus = set((checks or {}).get("focus") or [])
    profile = PROFILES.index(grading["profile"])
    counts = {cls: 0 for cls in CLASSES}
    out = []
    for f in findings:
        group = RULES[f.code][0]
        base = RULES[f.code][1][profile]
        cls = grading["overrides"].get(f.code) or (STEP[base] if group in focus else base)
        counted = f.caused_by is None and cls != "note"
        if f.caused_by is None:
            counts[cls] += 1
        out.append({"id": f.id, "code": f.code, "group": group, "class": cls, "base_class": base, "counted": counted,
                    "caused_by": f.caused_by, "nodes": f.nodes, "ifaces": f.ifaces, "message": f.message})
    watched = [r for r in rules if not focus or r["group"] in focus]
    incomplete = any(r["status"] == "skipped" and r.get("reason") in TRANSIENT for r in watched)
    mark = None
    if not incomplete:
        mark = grading["fail_mark"]
        for row in grading["thresholds"]:
            if all(row[cls] is None or counts[cls] <= row[cls] for cls in ("critical", "major", "minor")):
                mark = row["mark"]
                break
    return {"outcome": "incomplete" if incomplete else "graded", "mark": mark, "counts": counts,
            "rules": rules, "findings": out}


def run_checks(*, topology: dict, state: dict, statuses: dict, definition: dict | None, images: dict,
               checks: dict | None, grading: dict, stale: set, now=None, consoles=None) -> dict:
    """Everything a run needs, in one call: the `result` document of CONTRACTS.md §2.9.

    consoles: node_id -> console_type as the plugin sees it; nodes with `none` (ISP, Linux switch) are service nodes
    whose addresses are not the student's.
    """
    model = Model(topology, state, statuses, now, consoles)
    roles = node_roles(model, definition, images)
    state, unsaved = with_unsaved_iol(model, roles, state)
    if unsaved:
        model = Model(topology, state, statuses, now, consoles)
    expected = build_expected(model, roles, definition, checks)
    rules, findings = evaluate(model, roles=roles, expected=expected, checks=checks, stale=stale, unsaved=unsaved,
                               persistence=persistence_of(model, definition, images, checks),
                               isp_lans=uplink_lans(model, roles, definition, images))
    return grade(rules, findings, grading, checks)


def with_unsaved_iol(model: Model, roles: dict, state: dict):
    """An IOL node with no saved configuration has no facts (ios.config/1). The saved configuration is what counts,
    so it is checked as it would boot: a switch with every port in VLAN 1, a router with every port shut down.
    Returns (state, node ids treated so)."""
    unsaved = set()
    nodes = dict((state or {}).get("nodes") or {})
    for nid, node in model.nodes.items():
        facts = node["facts"] or {}
        if node["type"] != "iou" or node["has_ifaces"] or facts.get("error"):
            continue
        unsaved.add(nid)
        switch = roles.get(nid) == "switch"
        interfaces = [{"name": name or f"Ethernet{a}/{p}", "kind": "physical", "port": {"adapter": a, "port": p},
                       "admin": "up" if switch else "down", "ipv4": [],
                       **({"switchport": {"mode": "access", "vlan": 1}} if switch else {})}
                      for (a, p), name in sorted(node["ports"].items())]
        nodes[nid] = {**facts, "name": node["name"], "status": node["status"], "interfaces": interfaces}
    return ({**(state or {}), "nodes": nodes} if unsaved else state), unsaved
