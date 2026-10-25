"""Versioned JSON requests over a local Unix stream socket."""

import argparse
import asyncio
import contextlib
import json
import os
import signal
import socket
import stat
import struct
import time
from pathlib import Path

from . import VERSION
from .capture import Captures
from .core import ApiError, Linux, Vlans, integer, require

MAX_REQUEST = 65536
MAX_RESPONSE = 2 * 1024 * 1024


class Service:
    def __init__(self, linux, state):
        self.linux = linux
        self.vlans = Vlans(linux, state)
        self.captures = Captures(linux)
        self.clients = 0

    async def dispatch(self, method, params):
        require(isinstance(method, str) and isinstance(params, dict), "Invalid method or params")
        if method in {"status", "ports", "vlans"}:
            require(not params, "This method has no parameters")
        if method == "status":
            bridge = await self.linux.query("ip", "-j", "-d", "link", "show", "dev", "br0")
            return {"service_version": VERSION, "bridge": bridge,
                    "port_count": len(await self.linux.ports()),
                    "capture_templates": ["arp", "vlan", "ethernet"],
                    "vlan_last_error": self.vlans.last_error}
        if method == "ports":
            return {"ports": await self.linux.ports()}
        if method == "fdb":
            require(set(params) <= {"offset", "limit"}, "Unknown FDB parameter")
            offset, limit = params.get("offset", 0), params.get("limit", 256)
            require(integer(offset, 0, 1000000) and integer(limit, 1, 1024), "Invalid pagination")
            rows = await self.linux.query("bridge", "-j", "fdb", "show", "br", "br0")
            return {"entries": rows[offset:offset + limit], "total": len(rows),
                    "next_offset": offset + limit if offset + limit < len(rows) else None}
        if method == "vlans":
            return await self.vlans.read()
        if method == "vlan.apply":
            return await self.vlans.configure(params)
        if method == "capture.start":
            return await self.captures.start(params)
        if method in {"capture.read", "capture.stop"}:
            require(set(params) == {"id"} and isinstance(params["id"], str), "Expected capture id")
            if method == "capture.read":
                return self.captures.read(params["id"])
            return await self.captures.stop(params["id"])
        raise ApiError("METHOD_NOT_FOUND", "Unknown API method")

    async def handle(self, reader, writer):
        ident = None
        admitted = False
        try:
            peer = writer.get_extra_info("socket")
            _, uid, _ = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.geteuid():
                raise ApiError("FORBIDDEN", "Socket owner only")
            if self.clients >= 16:
                raise ApiError("BUSY", "Too many clients")
            self.clients += 1
            admitted = True
            raw = await asyncio.wait_for(reader.readline(), 5)
            require(raw.endswith(b"\n") and len(raw) <= MAX_REQUEST, "Invalid request framing")
            try:
                request = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise ApiError("INVALID_ARGUMENT", "Invalid JSON") from exc
            require(isinstance(request, dict) and
                    set(request) == {"version", "id", "method", "params"}, "Invalid envelope")
            ident = request["id"]
            require(isinstance(ident, str) and 1 <= len(ident) <= 64, "Invalid request id")
            require(type(request["version"]) is int and request["version"] == 1,
                    "Unsupported API version")
            result = await self.dispatch(request["method"], request["params"])
            response = {"version": 1, "id": ident, "ok": True,
                        "observed_at": time.time(), "result": result}
        except ApiError as exc:
            response = self.error(ident, exc.code, exc.message)
        except (ValueError, TypeError, KeyError, UnicodeError):
            response = self.error(ident, "INVALID_ARGUMENT", "Invalid request or data")
        except TimeoutError:
            response = self.error(ident, "TIMEOUT", "Operation timed out")
        except Exception:
            response = self.error(ident, "INTERNAL_ERROR", "Guest service operation failed")
        finally:
            if admitted:
                self.clients -= 1
        try:
            encoded = json.dumps(response, separators=(",", ":")).encode() + b"\n"
            if len(encoded) > MAX_RESPONSE:
                encoded = json.dumps(self.error(ident, "RESULT_TOO_LARGE", "Response exceeds limit")).encode() + b"\n"
            writer.write(encoded)
            await asyncio.wait_for(writer.drain(), 5)
        except (OSError, TimeoutError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    @staticmethod
    def error(ident, code, message):
        # Never echo an invalid or oversized request id into the response.
        ident = ident if isinstance(ident, str) and len(ident) <= 64 else None
        return {"version": 1, "id": ident, "ok": False,
                "error": {"code": code, "message": message}}

    async def maintain(self):
        while True:
            try:
                await self.vlans.reconcile()
            except Exception as exc:
                self.vlans.last_error = str(exc)[:512]
            self.captures.expire()
            await asyncio.sleep(2)


async def serve(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise RuntimeError("Refusing to replace a non-socket path")
        path.unlink()
    service = Service(Linux(), state)
    # Bind with private permissions before allowing requests.
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    path.chmod(0o600)
    sock.listen(16)
    sock.setblocking(False)
    server = await asyncio.start_unix_server(service.handle, sock=sock, limit=MAX_REQUEST)
    maint = asyncio.create_task(service.maintain())
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        async with server:
            await stop.wait()
    finally:
        maint.cancel()
        await asyncio.gather(maint, return_exceptions=True)
        await service.captures.close()
        path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default="/run/switch-api/api.sock")
    parser.add_argument("--state", default="/etc/switch-api/vlans.json")
    args = parser.parse_args()
    asyncio.run(serve(args.socket, args.state))


if __name__ == "__main__":
    main()
