"""WebSocket bridge to a GNS3 console, one connection per node.

telnet: serial console, Telnet negotiation is stripped from the stream.
vnc: QEMU screen, the raw RFB stream is passed through untouched.

Subprotocols: "lab-slot" takes every frame as keyboard input. "lab-slot.2" (telnet only, CONTRACTS.md §6.1) takes binary
frames as input and text frames as control JSON: `{"resize": {"cols", "rows"}}` sets the window size of a Linux guest.
"""
import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.auth import offered_protocols, websocket_token_ok
from api.winsize import WindowSizes, parse_control

router = APIRouter(tags=["console"])

# GNS3 2.2.61 hands out VNC consoles from 5900 up (display = port - 5900) and refuses lower
# ones (compute/base_node.py), so nothing below is a VNC console of a node.
VNC_PORTS = range(5900, 65536)
PROTOCOL, PROTOCOL_2 = "lab-slot", "lab-slot.2"


def window_sizes(app):
    """The window sizes of the slot's consoles, made on the first console that needs them."""
    sizes = getattr(app.state, "window_sizes", None)
    if sizes is None:
        sizes = app.state.window_sizes = WindowSizes()
    return sizes


class TelnetStripper:
    """Remove Telnet option negotiation and decline options, preserve terminal data."""
    def __init__(self):
        self.state = "data"
        self.verb = 0

    def feed(self, chunk: bytes) -> tuple[bytes, bytes]:
        out, replies = bytearray(), bytearray()
        for b in chunk:
            if self.state == "data":
                if b == 255:
                    self.state = "iac"
                else:
                    out.append(b)
            elif self.state == "iac":
                if b == 255:
                    out.append(255)
                    self.state = "data"
                elif b in (251, 252, 253, 254):
                    self.verb = b
                    self.state = "option"
                elif b == 250:
                    self.state = "subneg"
                else:
                    self.state = "data"
            elif self.state == "option":
                if self.verb in (251, 252):
                    replies.extend((255, 254, b))  # WILL/WONT -> DONT
                else:
                    replies.extend((255, 252, b))  # DO/DONT -> WONT
                self.state = "data"
            elif self.state == "subneg":
                if b == 255:
                    self.state = "subneg_iac"
            elif self.state == "subneg_iac":
                self.state = "data" if b == 240 else "subneg"
        return bytes(out), bytes(replies)


async def bridge_vnc(websocket: WebSocket, reader, writer):
    """Raw WebSocket <-> TCP bridge: binary frames both ways, no byte is parsed or changed.

    Whichever side ends first, the other one is closed and no task or socket is left behind.
    """

    async def to_browser():
        while True:
            data = await reader.read(65536)
            if not data:
                return
            await websocket.send_bytes(data)

    async def to_vnc():
        while True:
            packet = await websocket.receive()
            if packet["type"] == "websocket.disconnect":
                return
            data = packet.get("bytes")
            if data is None:
                # RFB is binary; re-encoding a text frame would corrupt the stream silently.
                await websocket.close(code=1003)
                return
            writer.write(data)
            await writer.drain()

    tasks = [asyncio.create_task(to_browser()), asyncio.create_task(to_vnc())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            t.result()
    except (OSError, RuntimeError, WebSocketDisconnect):
        pass
    finally:
        # Also on cancellation of the handler itself: stop the other direction and collect
        # every task result so that none is left running or reported as never retrieved.
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        try:
            await websocket.close()
        except (OSError, RuntimeError, WebSocketDisconnect):
            pass


@router.websocket("/v1/console/{project_id}/{node_id}")
async def console_ws(websocket: WebSocket, project_id: UUID, node_id: UUID):
    if not websocket_token_ok(websocket.headers.get("sec-websocket-protocol")):
        await websocket.close(code=1008)
        return
    client = websocket.app.state.gns3_http
    try:
        r = await client.get(f"projects/{project_id}/nodes/{node_id}")
        if r.status_code != 200:
            await websocket.close(code=1008)
            return
        node = r.json()
        port = node.get("console")
        console_type = node.get("console_type")
        if node.get("status") != "started" or console_type not in ("telnet", "vnc") or not isinstance(port, int):
            await websocket.close(code=1008)
            return
        if console_type == "vnc" and port not in VNC_PORTS:
            await websocket.close(code=1008)
            return
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
    except (OSError, ValueError):
        await websocket.close(code=1011)
        return

    offered = offered_protocols(websocket.headers.get("sec-websocket-protocol"))
    control = console_type == "telnet" and PROTOCOL_2 in offered
    await websocket.accept(subprotocol=PROTOCOL_2 if control else PROTOCOL)
    if console_type == "vnc":
        await bridge_vnc(websocket, reader, writer)
        return
    stripper = TelnetStripper()
    # Only a QEMU node can have a guest agent; whether it has one and runs Linux is asked when a size comes.
    lifecycle = getattr(websocket.app.state, "lifecycle", None)
    sizes = None
    if control and node.get("node_type") == "qemu" and lifecycle is not None \
            and getattr(websocket.app.state, "initialized", False):
        sizes = window_sizes(websocket.app)
        sizes.opened(str(project_id), str(node_id))

    async def to_browser():
        while True:
            data = await reader.read(8192)
            if not data:
                return
            text, reply = stripper.feed(data)
            if reply:
                writer.write(reply)
                await writer.drain()
            if text:
                await websocket.send_bytes(text)

    async def to_telnet():
        while True:
            packet = await websocket.receive()
            if packet["type"] == "websocket.disconnect":
                return
            if control:
                if packet.get("text") is not None:
                    size = parse_control(packet["text"])
                    if size and sizes is not None:
                        sizes.resize(lifecycle, str(node_id), *size)
                    continue  # control frames never reach the console
                data = packet.get("bytes") or b""
                if data and sizes is not None:
                    sizes.touch(lifecycle, str(node_id))
            else:
                data = packet.get("bytes") or (packet.get("text") or "").encode()
            writer.write(data.replace(b"\xff", b"\xff\xff"))
            await writer.drain()

    tasks = [asyncio.create_task(to_browser()), asyncio.create_task(to_telnet())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in done:
            t.result()
    except (OSError, RuntimeError, WebSocketDisconnect):
        pass
    finally:
        if sizes is not None:
            sizes.closed(str(node_id))
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        try:
            await websocket.close()
        except RuntimeError:
            pass
