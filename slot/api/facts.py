"""Facts collected from guests in the background (CONTRACTS.md 4.6).

A collector is one guest command plus the parser of its output. It runs through the QGA broker at
METRICS priority, so it yields to student and Manager commands. Results live in memory only: the
Manager polls them and keeps the history. Nothing here ever runs a command that changes a guest.
"""

import asyncio
import copy
import ipaddress
import json
import os
import time
from dataclasses import dataclass
from typing import Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request

from api.client import get_lifecycle
from api.qga import Priority, QgaBusy, QgaError, QgaTimeout, QgaUnavailable

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


def linux_net_interface(link, macs):
    """One `ip -d addr` entry -> an interface of live-state.md 3.1, or None for devices not covered."""
    name = link["ifname"]
    info = link.get("linkinfo") or {}
    kind = info.get("info_kind")
    flags = link.get("flags") or []
    operstate = link.get("operstate")
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
    entry["admin"] = "up" if "UP" in flags else "down"
    # `UNKNOWN` is what loopback and some virtual NICs report while they do work.
    up = operstate == "UP" or (operstate == "UNKNOWN" and "LOWER_UP" in flags)
    entry["oper"] = "up" if up else "down"
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
    defaults = [r for r in routes if r["dst"] == "default" and "via" in r and "dev" in r]
    if defaults:
        best = min(defaults, key=lambda r: r.get("metric", 0))
        for entry in interfaces:
            if entry["name"] == best["dev"]:
                entry["gateway"] = best["via"]
    return {"interfaces": interfaces, "routes": routes}


@dataclass(frozen=True)
class Collector:
    name: str  # key in the response; the schema carries the version
    schema: str
    command: str
    applies: Callable[[dict, dict], bool]  # (GNS3 node, its ticket spec)
    parse: Callable[[str, dict], dict]


LINUX_NET = Collector(
    name="linux.net",
    schema="linux.net/1",
    command=LINUX_NET_COMMAND,
    applies=lambda node, spec: node.get("node_type") == "qemu" and spec.get("qga", True),
    parse=lambda stdout, node: parse_linux_net(stdout, adapter_macs(node)),
)
COLLECTORS = (LINUX_NET,)
COLLECTOR_IDS = [collector.schema for collector in COLLECTORS]


def classify(exc):
    """Broker/transport exception -> live-state.md error code."""
    if isinstance(exc, QgaTimeout):
        return "timeout"
    if isinstance(exc, (QgaUnavailable, OSError)):
        return "unreachable"
    return "failed"


def failure_of(result):
    """A guest command that ran but did not succeed."""
    stderr = (result.get("stderr") or "").strip()
    if result.get("exit_code") == 127 or "is unknown" in stderr:  # no `ip`, or an `ip` without -j
        return FactError("unsupported", "The guest has no usable `ip -j`")
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
                for collector in COLLECTORS:
                    if not collector.applies(node, spec):
                        continue
                    key = (lab["project_id"], node["node_id"], collector.name)
                    if force or time.monotonic() >= self.next_due.get(key, 0):
                        jobs.append(self.attempt(key, node, collector))
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

    async def attempt(self, key, node, collector):
        epoch = self.epoch
        async with self.slots:
            failure = None
            data = None
            try:
                if node.get("status") != "started":
                    raise FactError("stopped", "The node is not started")
                result = await self.lc.qga.exec(
                    node["node_id"],
                    collector.command,
                    timeout=self.timeout,
                    priority=Priority.METRICS,
                    wait=False,
                    max_age=2,
                )
                if result.get("exit_code") != 0:
                    raise failure_of(result)
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
                failure = (classify(exc), f"{type(exc).__name__}: {exc}"[:MESSAGE_LIMIT])
        if epoch != self.epoch:
            return
        self.next_due[key] = time.monotonic() + self.interval
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
