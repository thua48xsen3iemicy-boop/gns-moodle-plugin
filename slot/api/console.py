"""WebSocket bridge to a GNS3 console, one connection per node.

telnet: serial console, Telnet negotiation is stripped from the stream.
vnc: QEMU screen, the raw RFB stream is passed through untouched.

Subprotocols: "lab-slot" takes every frame as keyboard input. "lab-slot.2" (telnet only, CONTRACTS.md §6.1) takes binary
frames as input and text frames as control JSON: `{"resize": {"cols", "rows"}}` sets the window size of a Linux guest.

History (telnet only, CONTRACTS.md §6.2): the slot keeps the tail of each node's output and replays it to a new console that
asks for it with `?history=1`, so a teacher who opens the console of a student sees what was printed before.

Lines (CONTRACTS.md §6.4): `?line=1..3` opens an additional serial console line of a QEMU node (ttyS1..ttyS3, api/lines.py)
instead of the GNS3 telnet port; frames, window size and history work the same, per line.
"""
import asyncio
import json
import os
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api import lines
from api.auth import offered_protocols, websocket_token_ok
from api.winsize import WindowSizes, key as line_key, parse_control

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


def history_limit():
    """Bytes of output kept per node; 0 turns the history off."""
    try:
        return max(0, int(os.environ.get("SLOT_CONSOLE_HISTORY", str(64 * 1024))))
    except ValueError:
        return 64 * 1024


# A trimmed history starts at the beginning of a line when a newline is this close to the cut: a replay that starts in the
# middle of an escape sequence or a UTF-8 character would print garbage.
LINE_SEARCH = 4096


class _NodeHistory:
    __slots__ = ("pid", "data", "writers")

    def __init__(self, pid):
        self.pid = pid
        self.data = bytearray()
        self.writers = []  # open consoles of the node, oldest first; only the oldest records


class ConsoleHistory:
    """The tail of the output of each node's text console.

    Every console of a node opens its own telnet connection and GNS3 sends the output to all of them, so only the oldest
    open console records it: the output is kept once. Output printed while no console is open is not seen by the slot.
    """

    def __init__(self):
        self.nodes = {}

    def opened(self, pid, node_id, console):
        """Registers a console and returns the history to replay to it (taken before it records anything)."""
        for other in [n for n, e in self.nodes.items() if e.pid != pid]:
            del self.nodes[other]  # another lab runs in the slot now
        entry = self.nodes.get(node_id)
        if entry is None:
            entry = self.nodes[node_id] = _NodeHistory(pid)
        entry.writers.append(console)
        return bytes(entry.data)

    def record(self, node_id, console, data):
        entry = self.nodes.get(node_id)
        if entry is None or not entry.writers or entry.writers[0] is not console:
            return
        limit = history_limit()
        entry.data += data
        excess = len(entry.data) - limit
        if excess > 0:
            cut = entry.data.find(b"\n", excess, excess + LINE_SEARCH)
            del entry.data[:excess if cut < 0 else cut + 1]

    def closed(self, node_id, console):
        entry = self.nodes.get(node_id)
        if entry is not None and console in entry.writers:
            entry.writers.remove(console)  # the history stays for the next console


def line_hub(app):
    hub = getattr(app.state, "line_hub", None)
    if hub is None:
        hub = app.state.line_hub = lines.LineHub()
    return hub


def gettys(app):
    starter = getattr(app.state, "gettys", None)
    if starter is None:
        starter = app.state.gettys = lines.Gettys()
    return starter


def console_history(app):
    history = getattr(app.state, "console_history", None)
    if history is None:
        history = app.state.console_history = ConsoleHistory()
    return history


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
    line = lines.parse_line(websocket.query_params.get("line"))
    if line is None:
        await websocket.close(code=1008)
        return
    client = websocket.app.state.gns3_http
    try:
        r = await client.get(f"projects/{project_id}/nodes/{node_id}")
        if r.status_code != 200:
            await websocket.close(code=1008)
            return
        node = r.json()
        if line:
            await line_console(websocket, str(project_id), str(node_id), line, node)
            return
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
    history = console_history(websocket.app)
    me = object()  # this console in the history of the node
    past = history.opened(str(project_id), str(node_id), me)
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
                history.record(str(node_id), me, text)
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

    tasks = []
    try:
        if past and websocket.query_params.get("history") == "1":
            await websocket.send_bytes(past)  # before the live output, which is read only after this
        tasks = [asyncio.create_task(to_browser()), asyncio.create_task(to_telnet())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in done:
            t.result()
    except (OSError, RuntimeError, WebSocketDisconnect):
        pass
    finally:
        for t in tasks:
            t.cancel()  # also when the handler itself is cancelled
        await asyncio.gather(*tasks, return_exceptions=True)
        history.closed(str(node_id), me)
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


async def line_console(websocket: WebSocket, pid, node_id, line, node):
    """An additional console line of a running QEMU node: a shared connection to its socket, no Telnet."""
    if node.get("status") != "started" or node.get("node_type") != "qemu" or node.get("console_type") != "telnet":
        await websocket.close(code=1008)
        return
    hubs = line_hub(websocket.app)
    try:
        hub, queue = await hubs.join(pid, node_id, line)
    except OSError:
        await websocket.close(code=1008)  # the node has no such line
        return
    offered = offered_protocols(websocket.headers.get("sec-websocket-protocol"))
    control = PROTOCOL_2 in offered
    history = console_history(websocket.app)
    me = object()
    hkey = line_key(node_id, line)
    past = history.opened(pid, hkey, me)
    lifecycle = getattr(websocket.app.state, "lifecycle", None)
    guest = lifecycle is not None and getattr(websocket.app.state, "initialized", False)
    sizes = window_sizes(websocket.app) if control and guest else None
    logins = gettys(websocket.app) if guest else None

    async def to_browser():
        while True:
            data = await queue.get()
            if data is None:
                return  # the line closed or this console fell behind
            history.record(hkey, me, data)
            await websocket.send_bytes(data)

    gone = False  # the gateway disconnected: nothing is sent back

    async def to_line():
        nonlocal gone
        while True:
            packet = await websocket.receive()
            if packet["type"] == "websocket.disconnect":
                gone = True
                return
            if control:
                if packet.get("text") is not None:
                    size = parse_control(packet["text"])
                    if size and sizes is not None:
                        sizes.resize(lifecycle, node_id, *size, line=line)
                    continue
                data = packet.get("bytes") or b""
                if data and sizes is not None:
                    sizes.touch(lifecycle, node_id, line=line)
            else:
                data = packet.get("bytes") or (packet.get("text") or "").encode()
            if not data:
                continue
            if logins is not None:
                logins.touch(lifecycle, pid, node_id, line)
            await hubs.write(hub, data)

    if sizes is not None:
        sizes.opened(pid, node_id, line)
    if logins is not None:
        logins.opened(lifecycle, pid, node_id, line)
    tasks = []
    try:
        await websocket.accept(subprotocol=PROTOCOL_2 if control else PROTOCOL)
        if past and websocket.query_params.get("history") == "1":
            await websocket.send_bytes(past)
        tasks = [asyncio.create_task(to_browser()), asyncio.create_task(to_line())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in done:
            t.result()
    except (OSError, RuntimeError, WebSocketDisconnect):
        pass
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        history.closed(hkey, me)
        if sizes is not None:
            sizes.closed(node_id, line)
        await hubs.leave(hub, queue)
        if not gone:
            try:
                await websocket.close()
            except RuntimeError:
                pass
