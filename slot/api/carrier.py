"""Carrier of QEMU network cards follows the far end of their cable, as a real cable would.

QEMU keeps the carrier of a virtual NIC up whatever happens at the other end of a GNS3 link: a
guest never sees NO-CARRIER when the peer port is shut down or the peer device is off. The slot
mirrors it through the QEMU monitor (`set_link <netdev> off|on`, the command GNS3 itself uses to
suspend a QEMU link). The guest's own configuration is never touched: `admin` stays as the
student set it, only `oper` follows the cable.

The far end is down when its node is not started, when its IOL port does not answer the L1
keepalives (`no_rx`), or when the facts of a peer guest say that its port is `admin=down`.
Unknown, failed or stale observations keep the carrier up: nothing is invented.
"""

import asyncio
import logging
import os
import re
import time
from contextlib import suppress
from pathlib import Path

LOG = logging.getLogger("uvicorn.error.carrier")
MONITOR = re.compile(r"-monitor\s+tcp:127\.0\.0\.1:(\d{1,5}),server")
NETDEV = re.compile(r"^gns3-\d{1,2}$")
PROMPT = b"(qemu) "
# A NIC kept off is told so again now and then: a node restarted by GNS3 comes back with its link up.
REASSERT = 30.0


def enabled():
    return os.getenv("SLOT_L1_CARRIER", "1") == "1"


def command_line(node):
    """The QEMU command line of a started node: GNS3's own record, else the process of that node."""
    line = node.get("command_line")
    if isinstance(line, str) and line:
        return line
    marker = node.get("node_id")
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if b"-uuid" in args and marker and marker.encode() in args and any(b"qemu-system" in a for a in args[:1]):
            return " ".join(a.decode(errors="replace") for a in args if a)
    return ""


def monitor_of(node, adapter):
    """Monitor port and netdev id of one adapter of a QEMU node, or None if it cannot be addressed."""
    line = command_line(node)
    port = MONITOR.search(line)
    netdev = f"gns3-{int(adapter)}"
    if not port or f"netdev={netdev}" not in line or not NETDEV.match(netdev):
        return None
    number = int(port.group(1))
    return (number, netdev) if 0 < number < 65536 else None


async def hmp(port, command, timeout=3.0):
    """One HMP command over the node's monitor; returns its output (without echo and prompt)."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout)
    try:
        await asyncio.wait_for(reader.readuntil(PROMPT), timeout)
        writer.write(command.encode() + b"\n")
        await writer.drain()
        out = await asyncio.wait_for(reader.readuntil(PROMPT), timeout)
    finally:
        writer.close()
        with suppress(Exception):
            await writer.wait_closed()
    text = out[:-len(PROMPT)].decode(errors="replace").replace("\r", "")
    lines = [line for line in text.split("\n") if line.strip() and line.strip() != command]
    return "\n".join(lines).strip()


async def set_link(port, netdev, up):
    if not NETDEV.match(netdev):
        raise ValueError("Unexpected netdev id")
    out = await hmp(port, f"set_link {netdev} {'on' if up else 'off'}")
    if out:
        raise RuntimeError(out[:200])


class Carrier:
    def __init__(self, send=None):
        self.send = send or set_link
        self.applied = {}  # (node_id, adapter) -> (up, monotonic time)
        self.retry = {}  # (node_id, adapter) -> monotonic time of the next attempt after a failure
        self.problem = set()

    def reset(self):
        self.applied.clear()
        self.retry.clear()

    def desired(self, by_id, sides, k, far_down):
        """Carrier of the QEMU end `k` of a link: down only for a known reason."""
        peer = sides[1 - k]
        node = by_id.get(peer["node_id"], {})
        if node.get("status") != "started":
            return False
        return not far_down(peer, node)

    async def apply(self, by_id, links, far_down, allowed):
        """Bring every QEMU NIC on a link to the state of the far end; returns the nodes whose carrier changed."""
        changed = set()
        if not enabled():
            return changed
        wanted = {}
        for link in links:
            sides = link.get("nodes", [])
            if len(sides) != 2 or link.get("suspend"):
                continue  # GNS3 owns the link state of a suspended link
            for k, side in enumerate(sides):
                node = by_id.get(side["node_id"], {})
                if node.get("node_type") != "qemu" or node.get("status") != "started":
                    continue
                wanted[(side["node_id"], int(side["adapter_number"]))] = (node, self.desired(by_id, sides, k, far_down))
        # A NIC that left the picture (node stopped, link suspended or removed) is no longer ours to keep.
        for table in (self.applied, self.retry):
            for key in list(table):
                if key not in wanted:
                    del table[key]
        now = time.monotonic()
        for key, (node, up) in wanted.items():
            known = self.applied.get(key)
            if known is not None and known[0] == up and (up or now - known[1] < REASSERT):
                continue
            if now < self.retry.get(key, 0):
                continue
            if not allowed():
                return changed
            target = monitor_of(node, key[1])
            if target is None:
                self.report(key, now, "CARRIER_MONITOR_UNKNOWN", "QEMU monitor of this adapter is not known")
                continue
            try:
                await self.send(*target, up)
            except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, RuntimeError, ValueError) as exc:
                self.report(key, now, "CARRIER_SET_FAILED", str(exc) or type(exc).__name__)
                continue
            if known is None or known[0] != up:
                if known is not None or not up:  # first sight with the carrier up changes nothing in the guest
                    changed.add(key[0])
                LOG.info("L1_CARRIER node=%s adapter=%s state=%s", key[0], key[1], "on" if up else "off")
            self.applied[key] = (up, now)
            self.retry.pop(key, None)
            if key in self.problem:
                LOG.info("L1_CARRIER_RECOVERED node=%s adapter=%s", *key)
                self.problem.discard(key)
        return changed

    def report(self, key, now, code, message):
        # The monitor can be busy with GNS3 for a moment: retry later, log the first failure only.
        self.retry[key] = now + (3.0 if code == "CARRIER_SET_FAILED" else REASSERT)
        if key not in self.problem:
            LOG.warning("%s node=%s adapter=%s: %s", code, key[0], key[1], message[:200])
            self.problem.add(key)
