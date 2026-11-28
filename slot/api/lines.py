"""Additional serial console lines of a QEMU node (`NodeSpec.extra_consoles`, CONTRACTS.md 4.2 and 6.4).

Line 0 is the node's console as before: ttyS0 behind GNS3's telnet port. Lines 1..3 are more serial ports of the guest,
ttyS1..ttyS3, each with its own login: a student keeps several independent terminals on a workstation node, like the tabs
of an SSH client.

- QEMU: every line is a chardev on a Unix socket of the slot (`/opt/gns3/serial/<node_id>.<n>.sock`, next to the guest
  agent's sockets) and an ISA serial port with a fixed index, so line n is ttyS<n> wherever GNS3 puts its own "-serial".
  The directory belongs to gns3 and is made by the slot template (install/lab-provision), as /opt/gns3/qga: /opt/gns3 is
  root's. The slot declares `extra-consoles` only while it can write there.
- Fan-out: the socket takes one client, so the slot keeps one connection per line (`LineHub`) and copies its output to
  every console of the line, as GNS3 does with the telnet port of line 0. There is no Telnet on a line: bytes go as they are.
- Login: systemd starts serial-getty only on the `console=` port of the kernel (ttyS0). The slot starts
  `serial-getty@ttyS<n>.service` through the guest agent when a line opens, and again on input after a guest reboot
  (`Gettys`). The unit is started, not enabled: nothing changes on the guest's disk.
"""
import asyncio
import logging
import os
import time
from pathlib import Path

from api.qga import Priority

log = logging.getLogger("lab-slot.lines")

SERIAL_DIR = Path("/opt/gns3/serial")
MAX_LINES = 3  # the kernel makes four 8250 ports by default and ttyS0 is the node's console
TIMEOUT = 5.0  # the queue and the getty command together
# Output a console has not taken yet, in chunks: a browser that stops reading loses its console, not the others theirs.
QUEUE_CHUNKS = 1024


def socket_path(node_id, line):
    return SERIAL_DIR / f"{node_id}.{int(line)}.sock"


def options(node_id, count):
    """The QEMU options of the node's lines 1..count."""
    parts = []
    for n in range(1, int(count or 0) + 1):
        parts.append(
            f"-chardev socket,id=line{n},path={socket_path(node_id, n)},server=on,wait=off "
            f"-device isa-serial,index={n},chardev=line{n}"
        )
    return " ".join(parts)


def enabled():
    """Can QEMU make the sockets of the lines? The directory exists and the slot (gns3, as QEMU) may write in it."""
    return SERIAL_DIR.is_dir() and os.access(SERIAL_DIR, os.W_OK | os.X_OK)


def parse_line(value):
    """The line of `?line=`: 0 without the parameter, 1..3 as written by the gateway, None for anything else."""
    if value is None or value == "0":
        return 0
    if value in {str(n) for n in range(1, MAX_LINES + 1)}:
        return int(value)
    return None


def refresh():
    return float(os.getenv("SLOT_WINSIZE_REFRESH", "30"))


class _Hub:
    __slots__ = ("key", "reader", "writer", "consoles", "task")

    def __init__(self, key, reader, writer):
        self.key = key
        self.reader = reader
        self.writer = writer
        self.consoles = []  # queues of the open consoles of the line
        self.task = None


class LineHub:
    """One connection per line of a running node; the output goes to every console of the line."""

    def __init__(self):
        self.hubs = {}
        self.lock = asyncio.Lock()

    async def join(self, pid, node_id, line):
        """Opens the line for a new console: returns (hub, queue); OSError when the line has no socket."""
        key = (pid, node_id, line)
        async with self.lock:
            hub = self.hubs.get(key)
            if hub is None:
                reader, writer = await asyncio.open_unix_connection(str(socket_path(node_id, line)))
                hub = self.hubs[key] = _Hub(key, reader, writer)
                hub.task = asyncio.create_task(self._pump(hub))
            queue = asyncio.Queue(QUEUE_CHUNKS)
            hub.consoles.append(queue)
            return hub, queue

    async def leave(self, hub, queue):
        async with self.lock:
            if queue in hub.consoles:
                hub.consoles.remove(queue)
            if hub.consoles or self.hubs.get(hub.key) is not hub:
                return
            del self.hubs[hub.key]
        # No await from here: a console that leaves is often being cancelled, and the pump closes the writer itself.
        if hub.task is not None:
            hub.task.cancel()
        hub.writer.close()

    @staticmethod
    async def write(hub, data):
        hub.writer.write(data)
        await hub.writer.drain()

    async def _pump(self, hub):
        try:
            while True:
                data = await hub.reader.read(8192)
                if not data:
                    break
                for queue in list(hub.consoles):
                    try:
                        queue.put_nowait(data)
                    except asyncio.QueueFull:
                        hub.consoles.remove(queue)  # this console stops reading; the others go on
                        self._drop(queue)
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            # The node stopped or the socket closed: every console of the line ends, the next one connects anew.
            if self.hubs.get(hub.key) is hub:
                del self.hubs[hub.key]
            for queue in list(hub.consoles):
                self._drop(queue)
            hub.writer.close()

    @staticmethod
    def _drop(queue):
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(None)  # the end of the console's output


class _Getty:
    __slots__ = ("tried", "task")

    def __init__(self):
        self.tried = None  # monotonic time of the last attempt
        self.task = None


class Gettys:
    """Starts serial-getty on a line of a Linux guest through its agent; one command at a time per line."""

    def __init__(self):
        self.lines = {}

    def opened(self, lifecycle, pid, node_id, line):
        """A console of the line opened: start the getty now."""
        self._start(lifecycle, pid, node_id, line, force=True)

    def touch(self, lifecycle, pid, node_id, line):
        """Input in the line: start the getty again once in SLOT_WINSIZE_REFRESH seconds (the guest may have rebooted)."""
        self._start(lifecycle, pid, node_id, line, force=False)

    def _start(self, lifecycle, pid, node_id, line, force):
        key = (pid, node_id, line)
        entry = self.lines.get(key)
        if entry is None:
            for other in [k for k in self.lines if k[0] != pid]:
                del self.lines[other]  # another lab runs in the slot now
            entry = self.lines[key] = _Getty()
        if entry.task is not None:
            return
        now = time.monotonic()
        if not force and entry.tried is not None and now - entry.tried < refresh():
            return
        entry.tried = now
        entry.task = asyncio.create_task(self._run(lifecycle, pid, node_id, line, entry))

    async def _run(self, lifecycle, pid, node_id, line, entry):
        try:
            await self.apply(lifecycle, pid, node_id, line)
        finally:
            entry.task = None

    @staticmethod
    def command(line):
        return f"systemctl start serial-getty@ttyS{int(line)}.service"

    @classmethod
    async def apply(cls, lifecycle, pid, node_id, line):
        """Returns whether the guest started the unit; failures leave the console alone and are only logged."""
        try:
            if not lifecycle.has_guest_agent(pid, node_id):
                return False
            if await lifecycle.guest_family(node_id, Priority.COMMAND) != "posix":
                return False
            result = await lifecycle.qga.exec(node_id, cls.command(line), timeout=TIMEOUT, priority=Priority.COMMAND)
        except Exception as exc:  # noqa: BLE001 - a background task; the line works on without a login
            log.info("getty not started on ttyS%s of %s: %s: %s", line, node_id, type(exc).__name__, exc)
            return False
        if result.get("exit_code") != 0:
            log.info("getty not started on ttyS%s of %s: exit %s", line, node_id, result.get("exit_code"))
            return False
        return True
