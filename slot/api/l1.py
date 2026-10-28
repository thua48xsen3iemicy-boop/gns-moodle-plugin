"""IOL L1 keepalives for managed links; no Ethernet injection or guest commands.

An IOL probe also supplies carrier. For an IOL/IOL pair, wait on the endpoint that
stopped replying and leave its peer silent. Probe the peer again only on recovery.
Replies establish responsiveness, not a definitive diagnosis of `shutdown`.
"""

import asyncio
import logging
import math
import os
import socket
import stat
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

LOG = logging.getLogger("uvicorn.error.l1")
HEADER = struct.Struct("!HHBBBB")


class L1Error(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def positive(name, default):
    value = float(os.getenv(name, default))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def endpoint(side):
    adapter, port = int(side["adapter_number"]), int(side["port_number"])
    if not 0 <= adapter <= 15 or not 0 <= port <= 15:
        raise L1Error("L1_PORT_INVALID", "IOL port cannot be encoded in one byte")
    return side["node_id"], (port << 4) | adapter


@dataclass
class Pair:
    ends: tuple
    waiting: tuple | None = None
    misses: dict = field(default_factory=dict)

    def primary(self):
        return (self.waiting,) if self.waiting is not None else self.ends

    def observe(self, replies, limit):
        """Return a recovered endpoint's peer for a second, conditional probe."""
        if self.waiting is not None:
            if self.waiting in replies:
                return next(e for e in self.ends if e != self.waiting)
            return None
        for end in self.ends:
            self.misses[end] = 0 if end in replies else self.misses.get(end, 0) + 1
        failed = [e for e in self.ends if self.misses[e] >= limit]
        if failed:
            self.waiting = failed[0]
            LOG.info("L1_WAIT endpoint=%s peer=%s", self.waiting, self.ends)
        return None

    def recovered(self, peer, replies):
        if peer in replies:
            self.waiting = None
            self.misses.clear()
            LOG.info("L1_RECOVERED endpoints=%s", self.ends)
        else:
            self.waiting = peer


class Channel:
    """One owned responder socket per IOL application; never remove foreign paths."""

    def __init__(self, directory, app):
        self.app = app
        self.bridge = app + 512
        self.local = directory / f"L1{self.bridge}"
        self.remote = str(directory / f"L1{app}")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.identity = None
        self.sent_at = {}
        self.received = {}
        try:
            self.sock.bind(str(self.local))  # existing/stale sockets are an explicit conflict
            info = self.local.lstat()
            self.identity = info.st_dev, info.st_ino
        except OSError as exc:
            self.sock.close()
            raise L1Error("L1_SOCKET_UNAVAILABLE", f"Cannot bind {self.local}: {exc}") from exc

    def accept(self, packet, sender, max_age):
        if len(packet) != HEADER.size or sender != self.remote:
            return
        dest, source, dest_if, source_if, kind, channel = HEADER.unpack(packet)
        if (dest, source, kind, channel) != (self.bridge, self.app, 3, 0):
            return
        sent = self.sent_at.get(source_if)
        if (dest_if == source_if and sent is not None
                and time.monotonic() - sent <= max_age):
            # Keep other ports too: a conditional peer probe shares this socket
            # with the primary probes of every other physical link on this IOL.
            self.received[source_if] = sent

    def take(self, ports, max_age):
        replies = set()
        now = time.monotonic()
        for port in ports:
            sent = self.received.pop(port, None)
            if (sent is not None and sent == self.sent_at.get(port)
                    and now - sent <= max_age):
                replies.add(port)
        return replies

    async def exchange(self, ports, timeout, allowed, max_age=None):
        max_age = timeout if max_age is None else max_age
        replies = set()
        if not allowed():
            return replies
        # IOL replies can arrive after the short receive deadline. Consume valid
        # queued RX from a recent send instead of throwing it away every round.
        # This protocol has no nonce; bound reuse by send age and consume RX once.
        for _ in range(1024):
            try:
                packet, sender = self.sock.recvfrom(4096)
            except BlockingIOError:
                break
            self.accept(packet, sender, max_age)
        replies.update(self.take(ports, max_age))
        for port in sorted(ports):
            if not allowed():
                return set()
            packet = HEADER.pack(self.app, self.bridge, port, port, 3, 0)
            try:
                self.sock.sendto(packet, self.remote)
                self.sent_at[port] = time.monotonic()
            except (FileNotFoundError, ConnectionRefusedError, BlockingIOError):
                continue  # a stopped/booting IOL does not acknowledge this round
        deadline = asyncio.get_running_loop().time() + timeout
        while replies != ports and allowed():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                packet, sender = await asyncio.wait_for(
                    asyncio.get_running_loop().sock_recvfrom(self.sock, 4096), remaining
                )
            except TimeoutError:
                break
            self.accept(packet, sender, max_age)
            replies.update(self.take(ports, max_age))
        return replies if allowed() else set()

    def close(self):
        self.sock.close()
        try:
            info = self.local.lstat()
            if self.identity == (info.st_dev, info.st_ino) and stat.S_ISSOCK(info.st_mode):
                self.local.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            LOG.error("L1_SOCKET_CLEANUP_FAILED path=%s: %s", self.local, exc)


class Keepalives:
    def __init__(self, lifecycle):
        self.lc = lifecycle
        self.interval = positive("SLOT_L1_INTERVAL", "3")
        self.timeout = positive("SLOT_L1_REPLY_TIMEOUT", "0.75")
        self.fact_age = positive("SLOT_L1_FACT_MAX_AGE", "90")
        self.miss_limit = int(os.getenv("SLOT_L1_MISSES", "2"))
        if self.miss_limit < 1 or self.timeout >= self.interval:
            raise ValueError("SLOT_L1_MISSES must be positive and reply timeout less than interval")
        self.directory = Path(f"/tmp/netl1{os.geteuid()}")
        self.channels = {}
        self.pairs = {}
        self.epoch = 0
        self.problem = None
        self.task = asyncio.create_task(self.run())

    def reset(self):
        self.epoch += 1
        for channel in self.channels.values():
            channel.close()
        self.channels.clear()
        self.pairs.clear()

    def allowed(self, epoch, pid):
        lab = self.lc.get("lab", pid)
        return (self.epoch == epoch and self.lc.claimed and lab is not None
                and lab.get("state") == "READY")

    def prepare(self, nodes):
        self.directory.mkdir(mode=0o700, exist_ok=True)
        info = self.directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o022):
            raise L1Error("L1_SOCKET_DIR_UNSAFE", f"Unsafe socket directory {self.directory}")
        applications = {}
        for node in nodes:
            app = (node.get("properties") or {}).get("application_id")
            if type(app) is not int or not 1 <= app <= 65535 - 512:
                raise L1Error("L1_APPLICATION_INVALID", "Missing or invalid IOL application_id")
            if app in applications.values():
                raise L1Error("L1_APPLICATION_CONFLICT", "Duplicate IOL application_id")
            applications[node["node_id"]] = app
        if set(applications.values()) & {app + 512 for app in applications.values()}:
            raise L1Error("L1_APPLICATION_CONFLICT", "Responder IDs overlap IOL application IDs")
        for node_id, channel in list(self.channels.items()):
            if applications.get(node_id) != channel.app:
                channel.close()
                del self.channels[node_id]
        for node_id, app in applications.items():
            if node_id not in self.channels:
                self.channels[node_id] = Channel(self.directory, app)

    def guest_ready(self, pid, side):
        records = self.lc.facts.results.get(pid, {}).get(side["node_id"], {}).get("collectors", {})
        # switch.net describes bridge ports that linux.net deliberately omits.
        for name in ("switch.net", "windows.net", "linux.net"):
            record = records.get(name)
            if record is None:
                continue
            if not record.get("ok"):
                return False
            try:
                observed = datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00")).timestamp()
            except (KeyError, TypeError, ValueError):
                return False
            if not 0 <= time.time() - observed <= self.fact_age:
                return False
            for interface in (record.get("data") or {}).get("interfaces", []):
                if interface.get("port") == {"adapter": side["adapter_number"], "port": side["port_number"]}:
                    return interface.get("admin") == "up"
        return False  # unknown, failed and stale facts never supply carrier

    async def probe(self, ends, epoch, pid):
        by_node = {}
        for node_id, port in ends:
            by_node.setdefault(node_id, set()).add(port)

        async def one(node_id, ports):
            replies = await self.channels[node_id].exchange(
                ports, self.timeout, lambda: self.allowed(epoch, pid),
                max_age=self.interval + self.timeout,
            )
            return {(node_id, port) for port in replies}

        results = await asyncio.gather(
            *(one(n, ports) for n, ports in by_node.items()), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return set().union(*results) if results else set()

    async def cycle(self):
        labs = [lab for lab in self.lc.all("lab") if lab.get("state") == "READY"]
        if not self.lc.claimed or not labs:
            if self.channels or self.pairs:
                self.reset()
            return
        if len(labs) != 1:
            raise L1Error("L1_LAB_CONFLICT", "More than one READY lab in a single slot")
        pid = labs[0]["project_id"]
        epoch = self.epoch
        nodes = await self.lc.api("GET", f"projects/{pid}/nodes")
        links = await self.lc.api("GET", f"projects/{pid}/links")
        if not self.allowed(epoch, pid):
            return
        by_id = {n["node_id"]: n for n in nodes}
        iols = {n["node_id"]: n for n in nodes
                if n.get("node_type") == "iou" and n.get("status") == "started"
                and (n.get("properties") or {}).get("l1_keepalives")}
        if not iols:
            self.reset()
            return
        self.prepare(iols.values())
        pairs, probes = {}, set()
        occupied = set()
        for link in links:
            sides = link.get("nodes", [])
            if link.get("suspend") or len(sides) != 2:
                continue
            if not all(by_id.get(s["node_id"], {}).get("status") == "started" for s in sides):
                continue
            cisco = [s for s in sides if s["node_id"] in iols]
            if not cisco:
                continue
            ends = tuple(sorted(endpoint(s) for s in cisco))
            if len(set(ends)) != len(ends) or any(end in occupied for end in ends):
                raise L1Error("L1_LINK_CONFLICT", "An IOL port belongs to multiple links")
            occupied.update(ends)
            if len(cisco) == 2:
                key = (pid, ends)
                pair = self.pairs.get(key) or Pair(ends)
                pairs[key] = pair
                probes.update(pair.primary())
            else:
                peer = next(s for s in sides if s is not cisco[0])
                kind = by_id[peer["node_id"]].get("node_type")
                # A mixed IOL pair has no observable peer state: do not invent carrier.
                if kind not in ("qemu", "iou") or (kind == "qemu" and self.guest_ready(pid, peer)):
                    probes.update(ends)
        self.pairs = pairs
        replies = await self.probe(probes, epoch, pid)
        if not self.allowed(epoch, pid):
            return
        recovered = []
        for pair in pairs.values():
            peer = pair.observe(replies, self.miss_limit)
            if peer is not None:
                recovered.append((pair, peer))
        if recovered:
            replies = await self.probe({peer for _, peer in recovered}, epoch, pid)
            if self.allowed(epoch, pid):
                for pair, peer in recovered:
                    pair.recovered(peer, replies)

    async def run(self):
        while True:
            started = time.monotonic()
            try:
                await self.cycle()
                if self.problem is not None:
                    LOG.info("L1_SERVICE_RECOVERED")
                    self.problem = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # background boundary: stop carrier and retry, never silently fail
                self.reset()
                code = exc.code if isinstance(exc, L1Error) else "L1_POLL_FAILED"
                problem = (code, str(exc)[:300])
                if problem != self.problem:
                    LOG.error("%s: %s", *problem)
                    self.problem = problem
            await asyncio.sleep(max(0.05, self.interval - (time.monotonic() - started)))

    async def stop(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.reset()
