"""Facts collected from guests in the background (CONTRACTS.md 4.6).

A collector is one guest command plus the parser of its output. It runs through the QGA broker at
METRICS priority, so it yields to student and Manager commands. Results live in memory only: the
Manager polls them and keeps the history. Nothing here ever runs a command that changes a guest.
The exception to "a guest command" is `ios.config`: Cisco IOL has no guest to ask, so it reads the saved
configuration GNS3 keeps in the node directory and never touches the student's console.
"""

import asyncio
import copy
import ipaddress
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from api import ios_config
from api.client import get_lifecycle
from api.qga import Priority, QgaBusy, QgaCommandError, QgaError, QgaTimeout, QgaUnavailable

SCHEMA = "gns3lab.slot-facts/1"
MESSAGE_LIMIT = 200

router = APIRouter(prefix="/v1/labs/{project_id}/facts", tags=["facts"])


def iso(moment):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment))


class FactError(RuntimeError):
    """A collection failed; `code` is one of the live-state.md error codes."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def mac_int(mac):
    return int(mac.replace(":", "").replace("-", ""), 16)


def mac_text(value):
    raw = f"{value:012x}"
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


def adapter_macs(node):
    """MAC -> adapter number for a QEMU node, from the base MAC GNS3 keeps for it.

    GNS3 numbers the adapters upwards from `mac_address`. If the base is missing or does not parse,
    nothing is mapped: an interface without `port` is "unknown", a guessed port would be a lie.
    """
    props = node.get("properties") or {}
    base, count = props.get("mac_address"), props.get("adapters")
    if not isinstance(base, str) or type(count) is not int or count < 1:
        return {}
    try:
        start = mac_int(base)
    except ValueError:
        return {}
    if start + count > 1 << 48:
        return {}
    return {mac_text(start + number): number for number in range(count)}


# One exec per run keeps the node's QGA queue short. `-d` adds linkinfo (VLAN id, device kind).
LINUX_NET_COMMAND = (
    "a=$(ip -j -d addr) && r=$(ip -j route) && "
    "printf '{\"addr\":%s,\"route\":%s}' \"$a\" \"$r\""
)


def normalized_mac(value):
    try:
        return mac_text(mac_int(value))
    except (ValueError, TypeError):
        return None


def link_states(link):
    """(admin, oper) of an `ip -j` link in the words of live-state.md 3.1."""
    flags = link.get("flags") or []
    operstate = link.get("operstate")
    # `UNKNOWN` is what loopback and some virtual NICs report while they do work.
    up = operstate == "UP" or (operstate == "UNKNOWN" and "LOWER_UP" in flags)
    return ("up" if "UP" in flags else "down"), ("up" if up else "down")


def linux_net_interface(link, macs):
    """One `ip -d addr` entry -> an interface of live-state.md 3.1, or None for devices not covered."""
    name = link["ifname"]
    info = link.get("linkinfo") or {}
    kind = info.get("info_kind")
    entry = {"name": name}
    if link.get("link_type") == "loopback":
        entry["kind"] = "loopback"
    elif kind == "vlan":
        data = info.get("info_data") or {}
        if type(data.get("id")) is not int or not link.get("link"):
            return None
        entry.update(kind="subif", parent=link["link"], dot1q=data["id"])
    elif kind is None and link.get("link_type") == "ether":
        entry["kind"] = "physical"
    else:
        return None  # bridges, bonds, tunnels...: linux.net/1 does not describe them
    entry["admin"], entry["oper"] = link_states(link)
    mac = normalized_mac(link.get("address")) if entry["kind"] != "loopback" else None
    if mac:
        entry["mac"] = mac
        if entry["kind"] == "physical" and mac in macs:
            entry["port"] = {"adapter": macs[mac], "port": 0}
    entry["ipv4"] = [
        str(ipaddress.ip_interface(f"{a['local']}/{a['prefixlen']}"))
        for a in link.get("addr_info") or []
        if a.get("family") == "inet"
    ]
    return entry


def linux_net_route(route):
    dst = route.get("dst")
    if not isinstance(dst, str) or ":" in dst:
        return None
    if dst != "default":
        dst = str(ipaddress.ip_network(dst if "/" in dst else dst + "/32", strict=False))
    entry = {"dst": dst}
    if route.get("gateway"):
        entry["via"] = str(ipaddress.ip_address(route["gateway"]))
    if route.get("dev"):
        entry["dev"] = route["dev"]
    if route.get("protocol"):
        entry["proto"] = route["protocol"]
    if type(route.get("metric")) is int:
        entry["metric"] = route["metric"]
    return entry


def set_gateway(interfaces, routes):
    """`gateway` of the interface that carries the default route with the lowest metric."""
    defaults = [r for r in routes if r["dst"] == "default" and "via" in r and "dev" in r]
    if defaults:
        best = min(defaults, key=lambda r: r.get("metric", 0))
        for entry in interfaces:
            if entry["name"] == best["dev"]:
                entry["gateway"] = best["via"]


def parse_linux_net(stdout, macs):
    """Turn the output of LINUX_NET_COMMAND into a fragment of a live-state.md node.

    Only values that describe state are kept. Lifetimes of DHCP/SLAAC addresses, route expiry
    and counters change on their own and would make every poll look like a change.
    """
    try:
        document = json.loads(stdout)
        links, routes = document["addr"], document["route"]
        if not isinstance(links, list) or not isinstance(routes, list):
            raise TypeError("not a list")
        interfaces = [
            entry
            for link in sorted(links, key=lambda item: item.get("ifindex", 0))
            if (entry := linux_net_interface(link, macs)) is not None
        ]
        routes = sorted(
            (entry for route in routes if (entry := linux_net_route(route)) is not None),
            key=lambda e: (e["dst"], e.get("metric", 0), e.get("via", ""), e.get("dev", "")),
        )
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise FactError("failed", f"Unexpected output of ip -j: {type(exc).__name__}") from exc
    set_gateway(interfaces, routes)
    return {"interfaces": interfaces, "routes": routes}


# Windows guest (family "windows"): one PowerShell run prints one JSON document. The script is
# ASCII-only and so is its output: non-ASCII characters of adapter names leave as \uXXXX, so the
# console code page of the guest does not matter. Enumerations are cast to strings, ConvertTo-Json
# would print them as numbers. `@()` keeps one-element results arrays.
WINDOWS_NET_COMMAND = r"""$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    $adapters = @(Get-NetAdapter | ForEach-Object {
        [pscustomobject]@{ Name = [string]$_.Name; Index = [int]$_.ifIndex; Mac = [string]$_.MacAddress; Status = [string]$_.Status }
    })
    $addresses = @(Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object {
        [pscustomobject]@{ Index = [int]$_.InterfaceIndex; Ip = [string]$_.IPAddress; Prefix = [int]$_.PrefixLength; State = [string]$_.AddressState }
    })
    $routes = @(Get-NetRoute -AddressFamily IPv4 | ForEach-Object {
        [pscustomobject]@{ Index = [int]$_.InterfaceIndex; Dst = [string]$_.DestinationPrefix; Next = [string]$_.NextHop; Metric = [int]$_.RouteMetric + [int]$_.InterfaceMetric; Proto = [string]$_.Protocol }
    })
    $json = [pscustomobject]@{ adapters = $adapters; addresses = $addresses; routes = $routes } | ConvertTo-Json -Depth 4 -Compress
    [regex]::Replace($json, '[^\x00-\x7F]', { param($m) '\u{0:x4}' -f [int][char]$m.Value })
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
"""
# `Get-NetAdapter.Status` -> admin: only these are an administrative "down"; everything but Up is
# an operational "down" (Disconnected is a cable that is not there, as `NO-CARRIER` on Linux).
WINDOWS_ADMIN_DOWN = {"Disabled", "Not Present"}


def as_list(value):
    """ConvertTo-Json prints a single object, not a one-element array, if a guest PowerShell unrolls it."""
    if isinstance(value, dict):
        return [value]
    if not isinstance(value, list):
        raise TypeError("not a list")
    return value


def windows_net_interface(adapter, macs):
    if type(adapter["Index"]) is not int or not isinstance(adapter["Name"], str):
        raise TypeError("adapter without name or index")
    status = adapter["Status"]
    if not isinstance(status, str):
        raise TypeError("adapter status is not a string")
    entry = {"name": adapter["Name"], "kind": "physical"}
    entry["admin"] = "down" if status in WINDOWS_ADMIN_DOWN else "up"
    entry["oper"] = "up" if status == "Up" else "down"
    mac = normalized_mac(adapter.get("Mac"))
    if mac:
        entry["mac"] = mac
        if mac in macs:
            entry["port"] = {"adapter": macs[mac], "port": 0}
    entry["ipv4"] = []
    return entry


def windows_net_route(route, names):
    """One `Get-NetRoute` entry -> a route of live-state.md 3.3, or None for what `ip route` does not show.

    Windows lists host routes of its own addresses and broadcasts (`Local`, /32), multicast and the
    loopback. They carry no information about the network and Linux keeps them in the `local` table.
    """
    dev = names.get(route["Index"])
    network = ipaddress.ip_network(route["Dst"], strict=True)
    proto = route["Proto"].lower()
    if dev is None or network.is_multicast or (proto == "local" and network.prefixlen == 32):
        return None
    entry = {"dst": "default" if network.prefixlen == 0 else str(network)}
    via = ipaddress.ip_address(route["Next"])
    if not via.is_unspecified:
        entry["via"] = str(via)
    entry["dev"] = dev
    if proto:
        entry["proto"] = proto
    if type(route["Metric"]) is int:
        entry["metric"] = route["Metric"]
    return entry


def parse_windows_net(stdout, macs):
    """Turn the output of WINDOWS_NET_COMMAND into `interfaces` and `routes` of a live-state.md node.

    Same shape and the same rules as `parse_linux_net`: state only, nothing that changes by itself
    (no lease times). The loopback is not an adapter for Windows, so it is not listed. An address
    that failed duplicate detection is not in use and is left out; every other one is configured
    and is listed, also on an adapter without a cable.
    """
    try:
        document = json.loads(stdout)
        adapters = sorted(as_list(document["adapters"]), key=lambda item: item["Index"])
        interfaces = [windows_net_interface(adapter, macs) for adapter in adapters]
        by_index = {adapter["Index"]: entry for adapter, entry in zip(adapters, interfaces)}
        for address in as_list(document["addresses"]):
            entry = by_index.get(address["Index"])
            if entry is not None and address["State"] != "Duplicate":
                entry["ipv4"].append(
                    str(ipaddress.ip_interface(f"{address['Ip']}/{address['Prefix']}"))
                )
        for entry in interfaces:
            entry["ipv4"].sort(key=ipaddress.ip_interface)
        names = {index: entry["name"] for index, entry in by_index.items()}
        routes = sorted(
            (
                entry
                for route in as_list(document["routes"])
                if (entry := windows_net_route(route, names)) is not None
            ),
            key=lambda e: (e["dst"], e.get("metric", 0), e.get("via", ""), e.get("dev", "")),
        )
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise FactError(
            "failed", f"Unexpected output of Get-NetAdapter: {type(exc).__name__}"
        ) from exc
    set_gateway(interfaces, routes)
    return {"interfaces": interfaces, "routes": routes}


def guest_is_windows(info):
    """`guest-get-osinfo` reports `id: mswindows` for Windows; the name is the second witness."""
    if not isinstance(info, dict):
        return False
    return str(info.get("id", "")).lower() == "mswindows" or "windows" in str(info.get("name", "")).lower()


# Linux switch (images/linux-switch): one exec asks the guest API twice. No switchctl means the node
# is not a switch (exit 127). A failed call still prints its envelope, so the statuses of the two
# calls are not checked here: the parser reads `ok`. An empty answer becomes `null`.
SWITCH_NET_COMMAND = (
    "c=/usr/local/sbin/switchctl; [ -x \"$c\" ] || exit 127; "
    "p=$(\"$c\" ports); v=$(\"$c\" vlans); "
    "printf '{\"ports\":%s,\"vlans\":%s}' \"${p:-null}\" \"${v:-null}\""
)
MAX_VLAN = 4094


def switch_net_result(document, method):
    """`result` of the guest envelope of one `switchctl` call; an `ok: false` envelope is a failure."""
    envelope = document[method]
    if not isinstance(envelope, dict) or type(envelope.get("ok")) is not bool:
        raise FactError("failed", f"switchctl {method} printed no valid response")
    if not envelope["ok"]:
        error = envelope.get("error")
        error = error if isinstance(error, dict) else {}
        raise FactError(
            "failed", f"switchctl {method}: {error.get('code')}: {error.get('message')}"[:MESSAGE_LIMIT]
        )
    result = envelope["result"]
    if not isinstance(result, dict):
        raise TypeError("result is not an object")
    return result


def switch_memberships(actual):
    """`bridge -j vlan show` -> port name -> {vlan id: flags}; ranges (`vlan`..`vlanEnd`) are expanded."""
    members = {}
    for entry in actual:
        vlans = members.setdefault(entry["ifname"], {})
        for item in entry.get("vlans") or []:
            first = item["vlan"]
            last = item.get("vlanEnd", first)
            if type(first) is not int or type(last) is not int or not 1 <= first <= last <= MAX_VLAN:
                raise ValueError("VLAN id out of range")
            flags = item.get("flags") or []
            for vid in range(first, last + 1):
                vlans.setdefault(vid, set()).update(flags)
    return members


def switchport_of(vlans):
    """The port mode by the VLANs it is a member of (facts, not the policy that was asked for)."""
    if not vlans:
        return {"mode": "none"}
    # An access port has PVID and untagged egress in its only VLAN. Without a PVID a trunk takes no
    # untagged frames in and puts none out, so `native` is null.
    natives = sorted(vid for vid, flags in vlans.items() if {"PVID", "Egress Untagged"} <= flags)
    if len(vlans) == 1 and natives:
        return {"mode": "access", "vlan": natives[0]}
    return {"mode": "trunk", "allowed": sorted(vlans), "native": natives[0] if natives else None}


def switch_net_port(port, macs, members):
    entry = {"name": port["ifname"], "kind": "physical"}
    entry["admin"], entry["oper"] = link_states(port)
    mac = normalized_mac(port.get("address"))
    if mac:
        entry["mac"] = mac
        if mac in macs:
            entry["port"] = {"adapter": macs[mac], "port": 0}
    entry["switchport"] = switchport_of(members.get(port["ifname"], {}))
    entry["mirror"] = True  # nobody manages these ports: the scheme repeats the other end of the cable
    return entry


def parse_switch_net(stdout, macs):
    """Turn the output of SWITCH_NET_COMMAND into the `interfaces` of a live-state.md node.

    The ports are those of `switchctl ports`; entries of `bridge vlan` for anything else (`br0`) are
    ignored. Counters (`stats64`) and the VLAN policy are not copied, only the state.
    """
    try:
        document = json.loads(stdout)
        ports = switch_net_result(document, "ports")["ports"]
        members = switch_memberships(switch_net_result(document, "vlans")["actual"])
        if not isinstance(ports, list):
            raise TypeError("ports is not a list")
        interfaces = [
            switch_net_port(port, macs, members)
            for port in sorted(ports, key=lambda item: item.get("ifindex", 0))
        ]
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise FactError("failed", f"Unexpected output of switchctl: {type(exc).__name__}") from exc
    return {"interfaces": interfaces}


# The MAC table of the same switch (switch.fdb/1): one page of `switchctl fdb`, wrapped like the
# switch.net answers so that its envelope is read by the same code. One page is a slice of a single
# kernel dump; the next page would come from another dump.
FDB_LIMIT = 1024
SWITCH_FDB_COMMAND = (
    "c=/usr/local/sbin/switchctl; [ -x \"$c\" ] || exit 127; "
    f"f=$(\"$c\" fdb --params '{{\"limit\":{FDB_LIMIT}}}'); "
    "printf '{\"fdb\":%s}' \"${f:-null}\""
)
FDB_STATIC_STATES = ("permanent", "static")
MAC_FORM = re.compile(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}")


def fdb_entry(row):
    """A row of `bridge -j fdb show br br0` -> `{mac, iface, vlan}`, None for a service row.

    Learned rows have no `permanent`/`static` state and are not the device's own (`self`): the
    port MACs, the group addresses and whatever an administrator added are not the switch's lessons.
    Of the learned row only the three facts are kept, so timers that iproute2 may add stay out.
    """
    if not isinstance(row, dict):
        raise TypeError("fdb row is not an object")
    flags = row.get("flags") or []
    if not isinstance(flags, list):
        raise TypeError("flags is not a list")
    if row.get("state") in FDB_STATIC_STATES or "self" in flags:
        return None
    mac, iface, vlan = row["mac"], row["ifname"], row["vlan"]  # the bridge always filters by VLAN
    if not isinstance(mac, str) or not MAC_FORM.fullmatch(mac.lower()):
        raise ValueError("MAC address does not parse")
    if not isinstance(iface, str) or not iface:
        raise ValueError("port name is empty")
    if type(vlan) is not int or not 1 <= vlan <= MAX_VLAN:
        raise ValueError("VLAN id out of range")
    return {"mac": mac.lower(), "iface": iface, "vlan": vlan}


def fdb_too_large(envelope):
    """The guest refused the table because it is big: a property of the table, not a failed collection."""
    if not isinstance(envelope, dict) or envelope.get("ok") is not False:
        return False
    error = envelope.get("error")
    return isinstance(error, dict) and error.get("code") == "RESULT_TOO_LARGE"


def parse_switch_fdb(stdout):
    """Turn the output of SWITCH_FDB_COMMAND into the `mac_table` of a live-state.md node.

    A table that does not fit the guest's limits is published as `truncated` and empty: the manager
    carries any failed collector to the `error` of the node, and a switch that is merely busy with a
    big table would look broken for good.
    """
    try:
        document = json.loads(stdout)
        if fdb_too_large(document["fdb"]):
            return {"mac_table": {"entries": [], "truncated": True}}
        result = switch_net_result(document, "fdb")
        rows = result["entries"]
        if not isinstance(rows, list):
            raise TypeError("entries is not a list")
        truncated = result["next_offset"] is not None
        learned = [entry for entry in map(fdb_entry, rows) if entry is not None]
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise FactError("failed", f"Unexpected output of switchctl: {type(exc).__name__}") from exc
    learned.sort(key=lambda entry: (entry["vlan"], entry["mac"], entry["iface"]))
    return {"mac_table": {"entries": learned, "truncated": truncated}}


@dataclass(frozen=True)
class Collector:
    name: str  # key in the response; the schema carries the version
    schema: str
    applies: Callable[[dict, dict], bool]  # (GNS3 node, its ticket spec)
    command: str = ""
    parse: Callable[[str, dict], dict] | None = None
    missing: str = ""  # the failure message when the guest lacks the tool
    # A collector that reads files of the slot's host instead of asking the guest: (project_id, node,
    # previous snapshot) -> snapshot or None. It needs no QGA channel, no guest family and no running node.
    local: Callable[[str, dict, object], object] | None = None
    # The guest tells whether it takes part: exit 127 means "not here" and nothing is published,
    # failures of the exchange stay silent until the first success (see Facts.attempt).
    probe: bool = False
    # The guest family the collector is for: "posix" (a shell in /bin/sh) or "windows" (PowerShell).
    # The guest tells it (Facts.detect_family); a collector never runs on the other family.
    family: str = "posix"
    shell: str = "sh"  # how QgaBroker.exec runs `command`
    timeout_scale: float = 1.0  # times SLOT_FACTS_TIMEOUT; a cold PowerShell needs seconds to start


LINUX_NET = Collector(
    name="linux.net",
    schema="linux.net/1",
    command=LINUX_NET_COMMAND,
    applies=lambda node, spec: node.get("node_type") == "qemu" and spec.get("qga", True),
    parse=lambda stdout, node: parse_linux_net(stdout, adapter_macs(node)),
    missing="The guest has no usable `ip -j`",
)
# The ticket has no "switch" role for a node, so every QEMU node with QGA is asked and the guest answers.
SWITCH_NET = Collector(
    name="switch.net",
    schema="switch.net/1",
    command=SWITCH_NET_COMMAND,
    applies=LINUX_NET.applies,
    parse=lambda stdout, node: parse_switch_net(stdout, adapter_macs(node)),
    missing="The guest has no switchctl",
    probe=True,
)
# Its own role memory: the table changes with every lesson of the switch, ports and VLANs hardly ever,
# and the manager journals the whole `data` of a collector whenever it changes.
SWITCH_FDB = Collector(
    name="switch.fdb",
    schema="switch.fdb/1",
    command=SWITCH_FDB_COMMAND,
    applies=LINUX_NET.applies,
    parse=lambda stdout, node: parse_switch_fdb(stdout),
    missing="The guest has no switchctl",
    probe=True,
)
WINDOWS_NET = Collector(
    name="windows.net",
    schema="windows.net/1",
    command=WINDOWS_NET_COMMAND,
    applies=LINUX_NET.applies,
    parse=lambda stdout, node: parse_windows_net(stdout, adapter_macs(node)),
    missing="The guest has no usable Get-NetAdapter",
    family="windows",
    shell="powershell",
    timeout_scale=3.0,
)
# Cisco IOL: what the device boots with, from startup-config.cfg or NVRAM (api/ios_config.py).
IOS_CONFIG = Collector(
    name="ios.config",
    schema="ios.config/1",
    applies=lambda node, spec: node.get("node_type") == "iou",
    local=ios_config.collect,
)
COLLECTORS = (LINUX_NET, SWITCH_NET, SWITCH_FDB, WINDOWS_NET, IOS_CONFIG)
COLLECTOR_IDS = [collector.schema for collector in COLLECTORS] + ["iol.l1/1"]


def classify(exc):
    """Broker/transport exception -> live-state.md error code."""
    if isinstance(exc, QgaTimeout):
        return "timeout"
    if isinstance(exc, (QgaUnavailable, OSError)):
        return "unreachable"
    return "failed"


def failure_of(result, collector):
    """A guest command that ran but did not succeed."""
    stderr = (result.get("stderr") or "").strip()
    # no tool, an `ip` without -j, or a PowerShell without the NetTCPIP cmdlets
    if result.get("exit_code") == 127 or "is unknown" in stderr or "is not recognized" in stderr:
        return FactError("unsupported", collector.missing)
    return FactError("failed", f"Command exited with {result.get('exit_code')}: {stderr}"[:MESSAGE_LIMIT])


class Facts:
    """Background collection for the labs in READY, and the latest results per node."""

    def __init__(self, lifecycle):
        self.lc = lifecycle
        self.interval = float(os.getenv("SLOT_FACTS_INTERVAL", "30"))
        self.timeout = float(os.getenv("SLOT_FACTS_TIMEOUT", "10"))
        self.min_refresh = float(os.getenv("SLOT_FACTS_MIN_REFRESH", "5"))
        self.tick = max(min(self.interval, 2.0), 0.05)
        self.slots = asyncio.Semaphore(int(os.getenv("SLOT_FACTS_CONCURRENCY", "8")))
        self.results = {}  # project_id -> node_id -> {"name", "collectors": {name: result}}
        self.next_due = {}  # (project_id, node_id, collector) -> monotonic time
        # What the guests told about their role, for probe collectors; same keys as next_due.
        self.absent = set()  # the guest is not what the collector looks for: not asked again
        self.confirmed = set()  # the collector has succeeded at least once: its failures are published
        # What the guest said about itself: (project_id, node_id) -> "posix" | "windows". Until it
        # has, no collector of a family runs and nothing is published: a record of the wrong family
        # would stay as a stranger's error (the manager never deletes a collector's row).
        self.families = {}
        self.snapshots = {}  # same keys: the outcome for the last version of a file (local collectors)
        self.epoch = 0  # bumped by reset(): results of an attempt that started earlier are dropped
        self.force = False
        self.last_refresh = float("-inf")
        self.wake = asyncio.Event()
        self.task = asyncio.create_task(self.run())

    def reset(self):
        """The command owner changed: nothing collected for the previous one may be served."""
        self.epoch += 1
        self.results.clear()
        self.next_due.clear()
        self.absent.clear()
        self.confirmed.clear()
        self.families.clear()
        self.snapshots.clear()

    def request_refresh(self):
        now = time.monotonic()
        if now - self.last_refresh >= self.min_refresh:
            self.last_refresh = now
            self.force = True
            self.wake.set()

    def snapshot(self, project_id):
        lab = self.lc.get("lab", project_id)
        nodes = {}
        if lab and lab.get("state") == "READY":
            for node_id, entry in self.results.get(project_id, {}).items():
                nodes[node_id] = copy.deepcopy(entry)
            l1 = getattr(self.lc, "l1", None)
            for node_id, entry in getattr(l1, "results", {}).get(project_id, {}).items():
                target = nodes.setdefault(node_id, {"name": entry["name"], "collectors": {}})
                target["collectors"].update(copy.deepcopy(entry["collectors"]))
        return {"schema": SCHEMA, "project_id": project_id, "nodes": nodes}

    async def run(self):
        while True:
            try:
                await asyncio.wait_for(self.wake.wait(), self.tick)
            except TimeoutError:
                pass
            self.wake.clear()
            try:
                await self.cycle()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - collecting facts must never take the slot down.
                await asyncio.sleep(self.tick)

    async def cycle(self):
        labs = {
            lab["project_id"]: lab for lab in self.lc.all("lab") if lab.get("state") == "READY"
        }
        for project_id in [p for p in self.results if p not in labs]:
            del self.results[project_id]
        for key in [k for k in self.next_due if k[0] not in labs]:
            del self.next_due[key]
        for memory in (self.absent, self.confirmed):
            memory.difference_update([k for k in memory if k[0] not in labs])
        for memory in (self.families, self.snapshots):
            for key in [k for k in memory if k[0] not in labs]:
                del memory[key]
        if not self.lc.claimed:
            return
        force, self.force = self.force, False
        jobs = []
        for lab in labs.values():
            specs = {spec["name"]: spec for spec in lab["ticket"]["nodes"]}
            for node in lab.get("nodes", []):
                spec = specs.get(node.get("name"))
                if spec is None:
                    continue
                due = []
                family = self.families.get((lab["project_id"], node["node_id"]))
                for collector in COLLECTORS:
                    if not collector.applies(node, spec):
                        continue
                    if collector.local is None and family is not None and collector.family != family:
                        continue
                    key = (lab["project_id"], node["node_id"], collector.name)
                    if key in self.absent:
                        continue
                    if force or time.monotonic() >= self.next_due.get(key, 0):
                        due.append((key, node, collector))
                if due:
                    jobs.append(self.attempt_each(due))
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

    async def attempt_each(self, due):
        """The collectors of one node one after another: they share a QGA channel, and an attempt
        that finds it taken by a sibling would be skipped as busy."""
        epoch = self.epoch
        for key, node, collector in [d for d in due if d[2].local is not None]:
            if epoch != self.epoch:
                return
            try:
                await self.attempt_local(key, node, collector)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a broken collector must not stop the next one.
                pass
        due = [d for d in due if d[2].local is None]
        if not due:
            return
        project_id, node_id, _ = due[0][0]
        family = self.families.get((project_id, node_id))
        if family is None:
            try:
                family = await self.detect_family(due[0][1])
            except QgaBusy:
                for key, _, _ in due:
                    self.next_due[key] = time.monotonic() + self.tick
                return
            if epoch != self.epoch:
                return
            if family is None:  # cannot be asked now (stopped, no answer): publish nothing, ask again later
                for key, _, _ in due:
                    self.next_due[key] = time.monotonic() + self.interval
                return
            self.families[(project_id, node_id)] = family
        for key, node, collector in due:
            if collector.family != family:
                continue
            if epoch != self.epoch:
                return  # the owner changed: these nodes belong to the previous one
            try:
                await self.attempt(key, node, collector)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a broken collector must not stop the next one.
                pass

    async def detect_family(self, node):
        """"windows" or "posix" as the guest agent says; None while the guest cannot be asked.

        An agent that does not know `guest-get-osinfo` answers with an error: that is a Linux guest
        with an old agent, which is what every node was before this check existed. QgaBusy goes
        to the caller: the channel is taken by someone else, look again soon.
        """
        if node.get("status") != "started":
            return None
        try:
            async with self.slots:
                info = await self.lc.qga.call(
                    node["node_id"],
                    "guest-get-osinfo",
                    priority=Priority.METRICS,
                    wait=False,
                    timeout=self.timeout,
                )
        except QgaCommandError:
            return "posix"
        except QgaBusy:
            raise
        except (QgaError, OSError):
            return None
        return "windows" if guest_is_windows(info) else "posix"

    def drop(self, key):
        """Forget what was published for a collector, and the node if nothing else is left."""
        project_id, node_id, name = key
        nodes = self.results.get(project_id, {})
        entry = nodes.get(node_id)
        if entry is not None:
            entry["collectors"].pop(name, None)
            if not entry["collectors"]:
                del nodes[node_id]

    async def attempt(self, key, node, collector):
        epoch = self.epoch
        async with self.slots:
            failure = None
            data = None
            absent = False
            transport = False  # the exchange itself failed: the guest has said nothing about its role
            try:
                if node.get("status") != "started":
                    raise FactError("stopped", "The node is not started")
                result = await self.lc.qga.exec(
                    node["node_id"],
                    collector.command,
                    timeout=self.timeout * collector.timeout_scale,
                    priority=Priority.METRICS,
                    wait=False,
                    max_age=2,
                    shell=collector.shell,
                )
                if collector.probe and result.get("exit_code") == 127:
                    absent = True  # the guest does not have the tool: not a node for this collector
                elif result.get("exit_code") != 0:
                    raise failure_of(result, collector)
                else:
                    data = collector.parse(result.get("stdout") or "", node)
            except QgaBusy:
                # Yielding to someone else is not a failure: keep what we have and look again soon.
                self.next_due[key] = time.monotonic() + self.tick
                return
            except asyncio.CancelledError:
                raise
            except FactError as exc:
                failure = (exc.code, str(exc))
            except (QgaError, OSError) as exc:
                transport = True
                failure = (classify(exc), f"{type(exc).__name__}: {exc}"[:MESSAGE_LIMIT])
        if epoch != self.epoch:
            return
        self.next_due[key] = time.monotonic() + self.interval
        if absent:
            self.absent.add(key)
            self.confirmed.discard(key)
            self.drop(key)
            return
        if collector.probe:
            if failure is None:
                self.confirmed.add(key)
            elif (transport or failure[0] == "stopped") and key not in self.confirmed:
                # Unknown role: the manager never deletes a collector's row, so a record published now
                # would stay as a stranger's error on an ordinary host. Only what the guest itself said
                # (`failed` from a switchctl that exists) is published before the first success.
                return
        self.record(key, node, collector, data, failure)

    def record(self, key, node, collector, data, failure):
        """Publish the outcome of an attempt: the data, or the failure next to the last known data."""
        project_id, node_id, name = key
        entry = self.results.setdefault(project_id, {}).setdefault(
            node_id, {"name": node["name"], "collectors": {}}
        )
        entry["name"] = node["name"]
        now = iso(time.time())
        record = entry["collectors"].get(name) or {}
        record["schema"] = collector.schema
        record["attempted_at"] = now
        if failure is None:
            record.update(observed_at=now, ok=True, error=None, data=data)
        else:
            record.update(ok=False, error={"code": failure[0], "message": failure[1]})
        entry["collectors"][name] = record

    async def attempt_local(self, key, node, collector):
        """A collector that reads a file: no QGA channel, so no `busy`, and the node may be stopped.

        Nothing saved is no record (the node has no facts yet) unless one was published before: the device
        will boot without a configuration, so the record is emptied (`data: {}`), not kept as it was.
        A read that failed while the source was being written is not published: look again soon.
        """
        epoch = self.epoch
        project_id, node_id, name = key
        failure = None
        snapshot = None
        try:
            snapshot = await asyncio.to_thread(collector.local, project_id, node, self.snapshots.get(key))
        except ios_config.Changing:
            if epoch == self.epoch:
                self.next_due[key] = time.monotonic() + self.tick  # like a busy channel: keep what we have
            return
        except ios_config.ConfigError as exc:
            failure = (exc.code, str(exc)[:MESSAGE_LIMIT])
        except OSError as exc:
            failure = ("failed", f"{type(exc).__name__}: {exc}"[:MESSAGE_LIMIT])
        if epoch != self.epoch:
            return
        self.next_due[key] = time.monotonic() + self.interval
        data = None
        if snapshot is not None:
            self.snapshots[key] = snapshot
            if snapshot.error is None:
                data = snapshot.data
            else:
                failure = ("failed", snapshot.error[:MESSAGE_LIMIT])
        elif failure is None:
            self.snapshots.pop(key, None)
            published = self.results.get(project_id, {}).get(node_id, {}).get("collectors", {})
            if name not in published:
                return
            data = {}  # a configuration without interfaces is `interfaces: []`; no configuration has no fields
        self.record(key, node, collector, data, failure)

    async def stop(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


@router.get("")
async def facts(request: Request, project_id: UUID):
    return get_lifecycle(request).facts.snapshot(str(project_id))


@router.post("/refresh", status_code=202)
async def refresh(request: Request, project_id: UUID):
    lifecycle = get_lifecycle(request)
    lifecycle.status(str(project_id))  # 404 for a laboratory this slot does not manage
    lifecycle.facts.request_refresh()
    return {"accepted": True}
