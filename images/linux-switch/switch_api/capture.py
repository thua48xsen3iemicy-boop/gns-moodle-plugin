"""Short, filtered tcpdump sessions; parse Ethernet/ARP, never store PCAP files."""

import asyncio
import socket
import struct
import time
import uuid
from collections import OrderedDict

from .core import ApiError, integer, require

# Fixed offsets avoid the stateful offset changes of the BPF 'vlan' primitive.
TAG = "(ether[12:2] = 0x8100 or ether[12:2] = 0x88a8)"
INNER_TAG = "(ether[16:2] = 0x8100 or ether[16:2] = 0x88a8)"
FILTERS = {
    "arp": f"ether[12:2] = 0x0806 or ({TAG} and ether[16:2] = 0x0806) or "
           f"({TAG} and {INNER_TAG} and ether[20:2] = 0x0806)",
    "vlan": TAG,
    "ethernet": "len >= 14",
}


def decode(frame):
    if len(frame) < 14:
        return None
    mac = lambda b: ":".join(f"{v:02x}" for v in b)
    result = {"destination": mac(frame[:6]), "source": mac(frame[6:12]), "tags": []}
    etype = int.from_bytes(frame[12:14], "big")
    pos = 14
    while etype in (0x8100, 0x88a8) and len(result["tags"]) < 2:
        if len(frame) < pos + 4:
            return None
        tci, inner = struct.unpack("!HH", frame[pos:pos + 4])
        result["tags"].append({"tpid": etype, "vid": tci & 0xfff,
                               "pcp": tci >> 13, "dei": (tci >> 12) & 1})
        etype, pos = inner, pos + 4
    result["ether_type"] = etype
    if etype == 0x0806 and len(frame) >= pos + 28:
        htype, ptype, hlen, plen, op = struct.unpack("!HHBBH", frame[pos:pos + 8])
        if (htype, ptype, hlen, plen) == (1, 0x0800, 6, 4):
            data = frame[pos + 8:pos + 28]
            result["arp"] = {"operation": op, "sender_mac": mac(data[:6]),
                             "sender_ip": socket.inet_ntoa(data[6:10]),
                             "target_mac": mac(data[10:16]),
                             "target_ip": socket.inet_ntoa(data[16:20])}
    return result


class Captures:
    def __init__(self, linux):
        self.linux = linux
        self.sessions = OrderedDict()

    def expire(self):
        now = time.monotonic()
        for key, session in list(self.sessions.items()):
            if session["task"].done() and now - session["completed"] > 120:
                del self.sessions[key]

    async def start(self, args):
        require(set(args) <= {"port", "template", "seconds", "packets"}, "Unknown capture parameter")
        port, template = args.get("port"), args.get("template")
        seconds, packets = args.get("seconds", 10), args.get("packets", 32)
        require(isinstance(template, str) and template in FILTERS, "Expected arp, vlan or ethernet template")
        require(integer(seconds, 1, 15) and integer(packets, 1, 128), "Invalid capture limits")
        ports = {p["ifname"] for p in await self.linux.ports()}
        require(isinstance(port, str) and port in ports, "Port is not a current br0 member")
        self.expire()
        active = [s for s in self.sessions.values() if not s["task"].done()]
        if len(active) >= 2 or any(s["port"] == port for s in active):
            raise ApiError("CAPTURE_BUSY", "At most two sessions, one per port")
        while len(self.sessions) >= 8:
            key = next((k for k, s in self.sessions.items() if s["task"].done()), None)
            if key is None:
                raise ApiError("CAPTURE_BUSY", "No session capacity")
            del self.sessions[key]
        ident = uuid.uuid4().hex
        s = {"id": ident, "port": port, "template": template, "seconds": seconds,
             "packets": packets, "state": "starting", "reason": None, "error": None,
             "seen": 0, "events": [], "started_at": time.time(), "completed": 0}
        s["task"] = asyncio.create_task(self._run(s))
        self.sessions[ident] = s
        return self.public(s)

    async def _run(self, s):
        proc = None
        try:
            # stdout is binary PCAP in a pipe. No file, promiscuous mode or DNS.
            proc = await asyncio.create_subprocess_exec(
                "tcpdump", "-i", s["port"], "-Q", "in", "-p", "-nn", "-U", "-Z", "nobody",
                "-s", "128", "-B", "64", "-c", str(s["packets"]), "-w", "-",
                FILTERS[s["template"]], stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            async with asyncio.timeout(s["seconds"]):
                header = await proc.stdout.readexactly(24)
                formats = {b"\xd4\xc3\xb2\xa1": ("<", 1e6), b"\xa1\xb2\xc3\xd4": (">", 1e6),
                           b"\x4d\x3c\xb2\xa1": ("<", 1e9), b"\xa1\xb2\x3c\x4d": (">", 1e9)}
                if header[:4] not in formats:
                    raise ValueError("Unsupported capture format")
                endian, scale = formats[header[:4]]
                if struct.unpack(endian + "I", header[20:24])[0] != 1:
                    raise ValueError("Capture link type must be Ethernet")
                s["state"] = "running"
                while s["seen"] < s["packets"]:
                    record = await proc.stdout.readexactly(16)
                    sec, sub, size, original = struct.unpack(endian + "IIII", record)
                    if size > 128 or size > original:
                        raise ValueError("Invalid capture record length")
                    frame = await proc.stdout.readexactly(size)
                    s["seen"] += 1
                    event = decode(frame)
                    if event:
                        self.add_event(s, event, sec + sub / scale, original, size)
                s["reason"] = "packet_limit"
        except TimeoutError:
            s["reason"] = "timeout"
        except asyncio.CancelledError:
            s["reason"] = "stopped"
        except asyncio.IncompleteReadError:
            s["state"], s["reason"] = "failed", "capture_ended"
            s["error"] = "tcpdump ended before the requested packet limit"
        except Exception as exc:
            s["state"], s["reason"], s["error"] = "failed", "capture_error", str(exc)[:256]
        finally:
            if proc is not None and proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), 1)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
            if s["state"] != "failed":
                s["state"] = "done"
            s["completed"] = time.monotonic()

    @staticmethod
    def add_event(s, event, stamp, original, captured):
        # Merge repeated observations; cap unique events independently of packets.
        for old in s["events"]:
            if old["packet"] == event:
                old["count"] += 1
                old["last_at"] = stamp
                return
        if len(s["events"]) < 64:
            s["events"].append({"packet": event, "count": 1, "first_at": stamp,
                                "last_at": stamp, "wire_bytes": original,
                                "captured_bytes": captured})

    @staticmethod
    def public(s):
        return {k: v for k, v in s.items() if k not in {"task", "completed"}}

    def read(self, ident):
        self.expire()
        if ident not in self.sessions:
            raise ApiError("CAPTURE_NOT_FOUND", "Unknown or expired capture")
        return self.public(self.sessions[ident])

    async def stop(self, ident):
        self.read(ident)
        task = self.sessions[ident]["task"]
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            # A task cancelled before its first instruction cannot run its finally.
            s = self.sessions[ident]
            if not s["completed"]:
                s.update(state="done", reason="stopped", completed=time.monotonic())
        return self.read(ident)

    async def close(self):
        for ident in list(self.sessions):
            await self.stop(ident)
