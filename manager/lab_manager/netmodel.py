"""The network of a running lab as its facts show it: L1 links, L2 broadcast domains, L3 addresses.

A Python port of the plugin's layers (plugin/plugin/amd/src/facts.js, plugin/docs/live-state.md §4) for the
auto-checks (docs/autochecks/README.md). Both implementations follow the same rules; the plugin draws them, the
manager grades them. Pure: no I/O, the same input gives the same model.

Input: the `.gns3` topology of the session (nodes with `node_id`, links with `nodes[].adapter_number/port_number`),
the `gns3lab.facts/1` snapshot (facts.build_state) and the live node statuses.
"""

import ipaddress
from dataclasses import dataclass, field
from datetime import datetime

NODE_OFF = {"stopped": "остановлен", "suspended": "на паузе"}


@dataclass(eq=False)
class Iface:
    node_id: str
    node: str  # Node name
    name: str
    kind: str
    port: tuple | None  # (adapter, port) of the GNS3 port the interface (or its parent) sits on
    parent: str | None
    admin: str | None
    oper: str | None
    mirror: bool
    switchport: dict | None
    dot1q: int | None
    vlan: int | None
    addrs: list  # [IPv4Interface]

    @property
    def label(self):
        return f"{self.node} {self.name}"


@dataclass(eq=False)
class Link:
    id: str
    a: tuple  # (node_id, adapter, port)
    b: tuple
    suspend: bool


@dataclass(eq=False)
class Domain:
    id: str
    vlans: set = field(default_factory=set)
    switches: set = field(default_factory=set)  # node ids
    members: list = field(default_factory=list)  # [Iface]


@dataclass(eq=False)
class LinkL1:
    down: bool
    unknown: bool
    reasons: list  # [(kind, end | None, Iface | None)]: kind in suspended, node_off, shutdown, no_carrier, l1_down


@dataclass(eq=False)
class LinkL2:
    known: bool
    trunk: bool
    vlans: list
    issues: list  # [{"code": "tag_not_allowed" | "vlan_mismatch", "text": ..., "ends": [end, ...]}]


def addresses(raw):
    out = []
    for text in raw if isinstance(raw, list) else []:
        try:
            out.append(ipaddress.IPv4Interface(str(text)))
        except ValueError:
            continue
    return out


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class Model:
    def __init__(self, topology: dict, state: dict, statuses: dict, now: datetime | None = None, consoles=None):
        topo = topology.get("topology", topology) if isinstance(topology, dict) else {}
        self.nodes = {}  # node_id -> {"id", "name", "type", "console", "status", "facts"}
        facts_nodes = (state or {}).get("nodes") or {}
        for n in topo.get("nodes") or []:
            nid = n.get("node_id")
            if not isinstance(nid, str):
                continue
            facts = facts_nodes.get(nid)
            status = statuses.get(nid) or (facts or {}).get("status") or n.get("status") or "stopped"
            self.nodes[nid] = {"id": nid, "name": str(n.get("name", nid)), "type": n.get("node_type"),
                               "console": (consoles or {}).get(nid, n.get("console_type")), "status": status,
                               "facts": facts if isinstance(facts, dict) else None,
                               "ports": {(p.get("adapter_number"), p.get("port_number")): p.get("name") or p.get("short_name")
                                         for p in n.get("ports") or [] if isinstance(p, dict)}}
        self.by_name = {n["name"]: nid for nid, n in self.nodes.items()}
        self.links, self.peer, self.link_of = [], {}, {}
        for raw in topo.get("links") or []:
            ends = raw.get("nodes") or []
            if len(ends) != 2 or not all(e.get("node_id") in self.nodes for e in ends):
                continue
            a, b = ((e["node_id"], e.get("adapter_number", 0), e.get("port_number", 0)) for e in ends)
            link = Link(str(raw.get("link_id")), a, b, bool(raw.get("suspend")))
            self.links.append(link)
            self.peer[a], self.peer[b] = b, a
            self.link_of[a] = self.link_of[b] = link
        self.ifaces, self.by_port, self.by_name_if = {}, {}, {}
        for nid, node in self.nodes.items():
            raw_ifs = (node["facts"] or {}).get("interfaces")
            node["has_ifaces"] = isinstance(raw_ifs, list)
            items = []
            for i in raw_ifs if isinstance(raw_ifs, list) else []:
                if not isinstance(i, dict) or not isinstance(i.get("name"), str):
                    continue
                port = i.get("port")
                port = (port.get("adapter"), port.get("port")) if isinstance(port, dict) else None
                items.append(Iface(nid, node["name"], i["name"], i.get("kind") or "physical", port, i.get("parent"),
                                   i.get("admin"), i.get("oper"), bool(i.get("mirror")),
                                   i.get("switchport") if isinstance(i.get("switchport"), dict) else None,
                                   _int(i.get("dot1q")), _int(i.get("vlan")), addresses(i.get("ipv4"))))
            names = {i.name: i for i in items}
            for i in items:  # A subinterface sits on the port of its parent
                if i.port is None and i.parent in names:
                    i.port = names[i.parent].port
            self.ifaces[nid] = items
            self.by_name_if[nid] = names
            for i in items:
                if i.kind == "physical" and i.port is not None:
                    self.by_port[(nid, *i.port)] = i
        self.now = now
        self.l1 = self._derive_l1()
        self._derive_l2()

    # ------------------------------------------------------------------ helpers
    def name(self, nid):
        return self.nodes[nid]["name"]

    def end_text(self, end):
        i = self.by_port.get(end)
        if i is not None:
            return i.name
        label = self.nodes[end[0]]["ports"].get((end[1], end[2]))
        return label or f"{end[1]}/{end[2]}"

    def end_label(self, end):
        return f"{self.name(end[0])} {self.end_text(end)}"

    def subs(self, nid, name):
        return [i for i in self.ifaces.get(nid, []) if i.parent == name]

    def on_port(self, end):
        """Interfaces on a GNS3 port: the physical one and the subinterfaces over it."""
        nid = end[0]
        return [i for i in self.ifaces.get(nid, []) if i.port == (end[1], end[2]) and i.kind in ("physical", "subif")]

    def started(self, nid):
        return self.nodes[nid]["status"] == "started"

    def _live_l1(self, end):
        """State of an IOL port from iol.l1 ('up' | 'down' | 'unknown') or None without a record."""
        live = (self.nodes[end[0]]["facts"] or {}).get("l1_ports")
        if not isinstance(live, dict) or not isinstance(live.get("ports"), list):
            return None
        for p in live["ports"]:
            if isinstance(p, dict) and isinstance(p.get("port"), dict) and \
                    (p["port"].get("adapter"), p["port"].get("port")) == (end[1], end[2]):
                fresh = live.get("ok") is True
                if fresh and self.now is not None:
                    try:
                        seen = datetime.fromisoformat(str(live.get("observed_at")).replace("Z", "+00:00"))
                        ttl = float(live.get("ttl"))
                        fresh = 0 <= (self.now - seen).total_seconds() <= ttl
                    except (TypeError, ValueError):
                        fresh = False
                return p.get("state") if fresh and p.get("state") in ("up", "down") else "unknown"
        return None

    # ------------------------------------------------------------------ L1
    def _derive_l1(self):
        out = {}
        for link in self.links:
            reasons, uncertain = [], False
            if link.suspend:
                reasons.append(("suspended", None, None))
            iou = [self.nodes[e[0]]["type"] == "iou" for e in (link.a, link.b)]
            for k, end in enumerate((link.a, link.b)):
                node = self.nodes[end[0]]
                i = self.by_port.get(end)
                live = self._live_l1(end) if iou[0] and iou[1] else None
                if node["status"] in NODE_OFF:
                    reasons.append(("node_off", end, None))
                elif live is not None:
                    uncertain = uncertain or live == "unknown"
                    if live == "down":
                        reasons.append(("l1_down", end, i))
                elif i is not None and not i.mirror and i.admin == "down":
                    reasons.append(("shutdown", end, i))
                elif i is not None and not i.mirror and i.oper == "down":
                    reasons.append(("no_carrier", end, i))
            out[link.id] = LinkL1(bool(reasons), not reasons and uncertain, reasons)
        return out

    # ------------------------------------------------------------------ L2
    @staticmethod
    def _wire(model, nid, i):
        sp = i.switchport
        if sp and sp.get("mode") == "access":
            vlan = _int(sp.get("vlan"))
            return {"sw": True, "sp": sp, "set": {"u"}, "any": False, "map": lambda t: vlan if t == "u" else None}
        if sp and sp.get("mode") == "trunk":
            nat = None if "native" in sp and sp["native"] is None else (_int(sp.get("native")) if sp.get("native") is not None else 1)
            allowed_raw = sp.get("allowed")
            every = allowed_raw is None or allowed_raw == "all"
            allowed = None if every else {v for v in (_int(x) for x in allowed_raw) if v is not None}
            tags = set() if nat is None else {"u"}
            if allowed:
                tags |= {v for v in allowed if v != nat}

            def mapping(t, nat=nat, every=every, allowed=allowed):
                if t == "u":
                    return nat
                return t if t != nat and (every or t in allowed) else None
            return {"sw": True, "sp": sp, "set": tags, "any": every, "nat": nat, "allowed": allowed, "map": mapping}
        if sp and sp.get("mode") == "none":
            return {"sw": True, "sp": sp, "set": set(), "any": False, "none": True, "map": lambda t: None}
        if sp:
            return None
        subs = model.subs(nid, i.name)
        tags = set()
        if i.addrs or not subs:
            tags.add("u")
        for s in subs:
            if s.dot1q is not None and s.admin != "down":
                tags.add(s.dot1q)
        return {"sw": False, "set": tags, "any": False, "subs": subs}

    @staticmethod
    def _vertex(nid, i, w, t):
        if w["sw"]:
            v = w["map"](t)
            return None if v is None else f"S|{nid}|{v}"
        if t not in w["set"]:
            return None
        if t == "u":
            return f"E|{nid}|{i.name}"
        s = next((x for x in w["subs"] if x.dot1q == t), None)
        return f"E|{nid}|{s.name}" if s else None

    @staticmethod
    def _sends(w, t):
        return t in w["set"] and not (t == "u" and w["sw"] and w["sp"].get("mode") == "trunk")

    def switch_vlans(self, nid):
        facts = self.nodes[nid]["facts"] or {}
        out = {v for v in (_int(x.get("id")) for x in facts.get("vlans") or [] if isinstance(x, dict)) if v is not None}
        for i in self.ifaces.get(nid, []):
            sp = i.switchport
            if sp and sp.get("mode") == "access" and _int(sp.get("vlan")) is not None:
                out.add(_int(sp.get("vlan")))
            if sp and sp.get("mode") == "trunk" and isinstance(sp.get("allowed"), list):
                out |= {v for v in (_int(x) for x in sp["allowed"]) if v is not None}
            if i.kind == "svi" and i.vlan is not None:
                out.add(i.vlan)
        return out

    def _tag_text(self, t, frm, to, w_to):
        f, d = self.end_label(frm), self.end_label(to)
        if t == "u":
            return (f"{f} шлёт кадры без тега, а на транке {d} native нет — такие кадры он отбросит." if w_to["sw"]
                    else f"{f} шлёт кадры без тега, а у {d} только сабинтерфейсы с dot1q — такие кадры он отбросит.")
        if not w_to["sw"]:
            why = f"у {d} нет сабинтерфейса с dot1q {t}"
        elif w_to["sp"].get("mode") == "access":
            why = f"{d} — access-порт в VLAN {w_to['sp'].get('vlan')}, кадры с тегом он не принимает"
        elif t == w_to.get("nat"):
            why = f"на транке {d} это native VLAN — его кадры должны идти без тега"
        else:
            allowed = w_to.get("allowed")
            why = f"на транке {d} он не разрешён (allowed {','.join(map(str, sorted(allowed))) if allowed else '—'})"
        return f"VLAN {t} не проходит: {f} шлёт его с тегом, а {why}."

    def _derive_l2(self):
        parent, members = {}, {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def unite(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for nid, items in self.ifaces.items():
            for i in items:
                if i.kind == "loopback" or i.admin == "down" or i.switchport:
                    continue
                if i.kind == "svi":
                    if i.vlan is not None:
                        v = f"S|{nid}|{i.vlan}"
                        find(v)
                        members.setdefault(v, []).append(i)
                    continue
                if i.addrs or i.kind == "subif" or (i.kind == "physical" and not self.subs(nid, i.name)):
                    v = f"E|{nid}|{i.name}"
                    find(v)
                    members.setdefault(v, []).append(i)

        self.l2 = {}
        for link in self.links:
            ia, ib = self.by_port.get(link.a), self.by_port.get(link.b)
            wa = self._wire(self, link.a[0], ia) if ia else None
            wb = self._wire(self, link.b[0], ib) if ib else None
            trunk = any(w and w["sw"] and w["sp"].get("mode") == "trunk" for w in (wa, wb))
            rec = LinkL2(bool(wa and wb), trunk, [], [])
            self.l2[link.id] = rec
            l1 = self.l1[link.id]
            if not rec.known or l1.down or l1.unknown or wa.get("none") or wb.get("none"):
                continue
            cand = set(wa["set"]) | set(wb["set"])
            if wa["any"] and wb["any"]:
                cand |= self.switch_vlans(link.a[0]) | self.switch_vlans(link.b[0])
            for t in sorted(cand, key=lambda x: (x != "u", x if x != "u" else 0)):
                va, vb = self._vertex(link.a[0], ia, wa, t), self._vertex(link.b[0], ib, wb, t)
                if va and vb:
                    unite(va, vb)
                    v = wa["map"](t) if wa["sw"] else wb["map"](t) if wb["sw"] else None
                    if v is not None and v not in rec.vlans:
                        rec.vlans.append(v)
                elif va and self._sends(wa, t):
                    rec.issues.append({"code": "tag_not_allowed", "text": self._tag_text(t, link.a, link.b, wb),
                                       "ends": [link.a, link.b], "from": link.a, "to_switch": wb["sw"]})
                elif vb and self._sends(wb, t):
                    rec.issues.append({"code": "tag_not_allowed", "text": self._tag_text(t, link.b, link.a, wa),
                                       "ends": [link.a, link.b], "from": link.b, "to_switch": wa["sw"]})
            if wa["sw"] and wb["sw"]:
                ua, ub = wa["map"]("u"), wb["map"]("u")
                if ua is not None and ub is not None and ua != ub:
                    rec.issues.append({"code": "vlan_mismatch", "ends": [link.a, link.b],
                                       "text": f"Кадры без тега склеивают VLAN {ua} на {self.end_label(link.a)} и VLAN {ub} "
                                               f"на {self.end_label(link.b)}: native или access VLAN на концах не совпадают."})
            rec.vlans.sort()

        comps = {}
        for v in list(parent):
            comps.setdefault(find(v), []).append(v)
        self.domains, self._dom_of = [], {}
        for root in sorted(comps):
            verts = sorted(comps[root])
            mem = [i for v in verts for i in members.get(v, [])]
            if not mem:
                continue
            d = Domain(f"d{len(self.domains)}")
            for v in verts:
                kind, nid, rest = v.split("|", 2)
                if kind == "S":
                    d.vlans.add(int(rest))
                    d.switches.add(nid)
            d.members = mem
            self.domains.append(d)
            for v in verts:
                self._dom_of[v] = d

    def dom_of(self, i: Iface):
        key = f"S|{i.node_id}|{i.vlan}" if i.kind == "svi" else f"E|{i.node_id}|{i.name}"
        return self._dom_of.get(key)

    def domain_label(self, d: Domain):
        if d.vlans:
            return f"VLAN {', '.join(map(str, sorted(d.vlans)))} на {', '.join(sorted(self.name(s) for s in d.switches))}"
        return "отдельный сегмент " + " — ".join(sorted({i.label for i in d.members}))

    # ------------------------------------------------------------------ L3
    def addressed(self):
        """[(Iface, IPv4Interface)] of every non-loopback address."""
        return [(i, a) for nid in sorted(self.ifaces) for i in self.ifaces[nid] if i.kind != "loopback" for a in i.addrs]
