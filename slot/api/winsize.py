"""Window size of the browser terminal for the serial console of a Linux guest (CONTRACTS.md §6.1).

A Linux guest on ttyS0 takes its screen for 80x24 while the terminal in the browser fits its window: nano and other
full-screen programs then draw past a smaller window and the student edits another line than the one on the screen.
The console gateway sends the size of the window (`{"resize": {"cols", "rows"}}` over lab-slot.2); the slot sets it on
the guest's ttyS0 through QGA (`stty`), and the kernel tells the program on the screen (SIGWINCH).

Per node, only the last size matters: a frame waits for SLOT_WINSIZE_DELAY seconds of quiet, and while a command runs
the next frames are only remembered, so a node never has more than one size command in the QGA queue. After a logout
serial-getty starts again and the guest may be back to 80x24: input re-applies the last size when SLOT_WINSIZE_REFRESH
seconds have passed since the previous attempt. Failures leave the console alone and are only logged.

A new console of a node (a reloaded page, another browser) gets nothing until the guest prints, and the kernel tells the
program nothing when the size stays the same: nano would leave a black terminal. So the first size after a console
opens is set with a nudge, one row less and then the size itself: two real changes, SIGWINCH, and the full-screen
program redraws its whole screen (a shell at its prompt redraws the prompt line).

An additional console line n of a node (CONTRACTS.md 6.4, api/lines.py) is ttyS<n> of the guest: it has its own size,
pause, refresh and redraw, kept apart from line 0.
"""
import asyncio
import json
import logging
import os
import time

from api.qga import Priority

log = logging.getLogger("lab-slot.winsize")

MAX_SIZE = 1000
SERIAL = "/dev/ttyS0"  # the console of serial-getty (api/nodes.py, Lifecycle.provision)
TIMEOUT = 5.0  # the queue and the command together


def delay():
    return float(os.getenv("SLOT_WINSIZE_DELAY", "0.25"))


def refresh():
    return float(os.getenv("SLOT_WINSIZE_REFRESH", "30"))


def parse_control(text):
    """(cols, rows) of a `{"resize": {"cols", "rows"}}` control frame, None for anything else."""
    try:
        frame = json.loads(text)
    except ValueError:
        return None
    size = frame.get("resize") if isinstance(frame, dict) else None
    if not isinstance(size, dict):
        return None
    cols, rows = size.get("cols"), size.get("rows")
    if not all(type(v) is int and 1 <= v <= MAX_SIZE for v in (cols, rows)):  # bool is not a size
        return None
    return cols, rows


def tty(line=0):
    """The guest's serial port of a console line: ttyS0 for the node's console, ttyS<n> for an additional line."""
    return SERIAL if not line else f"/dev/ttyS{int(line)}"


def command(cols, rows, redraw=False, serial=SERIAL):
    """The stty command; with `redraw` the size changes twice, so the program on the screen redraws it."""
    size = f"stty -F {serial} rows {int(rows)} cols {int(cols)}"
    if not redraw:
        return size
    other = int(rows) - 1 if rows > 1 else int(rows) + 1
    return f"stty -F {serial} rows {other} cols {int(cols)} && {size}"


def key(node_id, line=0):
    """Line 0 is kept under the node's id, as before lines existed."""
    return node_id if not line else f"{node_id}/{int(line)}"


class _Node:
    __slots__ = ("pid", "node_id", "serial", "size", "changed", "dirty", "tried", "worker", "consoles", "redraw")

    def __init__(self, pid, node_id=None, line=0):
        self.pid = pid
        self.node_id = node_id
        self.serial = tty(line)
        self.size = None  # (cols, rows) last asked for
        self.changed = 0.0  # monotonic time of the last change: the quiet pause counts from it
        self.dirty = False  # a size waits to be applied
        self.tried = None  # monotonic time of the last attempt
        self.worker = None
        self.consoles = 0
        self.redraw = False  # a console opened since the last command: the next one makes the screen redraw


class WindowSizes:
    """The window size of each node's serial console, applied to the guest one command at a time."""

    def __init__(self):
        self.nodes = {}

    def opened(self, pid, node_id, line=0):
        k = key(node_id, line)
        entry = self.nodes.get(k)
        if entry is None or entry.pid != pid:
            entry = self.nodes[k] = _Node(pid, node_id, line)
        entry.consoles += 1
        entry.redraw = True

    def closed(self, node_id, line=0):
        k = key(node_id, line)
        entry = self.nodes.get(k)
        if entry is None:
            return
        entry.consoles -= 1
        if entry.consoles <= 0 and entry.worker is None:
            del self.nodes[k]

    def resize(self, lifecycle, node_id, cols, rows, line=0):
        """A new size from a console of the node's line: applied after the quiet pause."""
        k = key(node_id, line)
        entry = self.nodes.get(k)
        if entry is None:
            return
        entry.size = (cols, rows)
        entry.changed = time.monotonic()
        entry.dirty = True
        self._start(lifecycle, k, entry)

    def touch(self, lifecycle, node_id, line=0):
        """Input in a console of the node's line: re-applies the last size once in SLOT_WINSIZE_REFRESH seconds."""
        k = key(node_id, line)
        entry = self.nodes.get(k)
        if entry is None or entry.size is None or entry.dirty or entry.worker is not None:
            return
        now = time.monotonic()
        if entry.tried is not None and now - entry.tried < refresh():
            return
        entry.changed = now - delay()  # no pause: the size is known and the student is typing
        entry.dirty = True
        self._start(lifecycle, k, entry)

    def _start(self, lifecycle, k, entry):
        if entry.worker is None:
            entry.worker = asyncio.create_task(self._run(lifecycle, k, entry))

    async def _run(self, lifecycle, k, entry):
        try:
            while entry.dirty:
                wait = entry.changed + delay() - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                    continue
                entry.dirty = False
                entry.tried = time.monotonic()
                redraw, entry.redraw = entry.redraw, False
                node_id = entry.node_id if entry.node_id is not None else k
                if not await self.apply(lifecycle, entry.pid, node_id, *entry.size, redraw=redraw,
                                        serial=entry.serial) and redraw:
                    entry.redraw = True  # the screen is still to be redrawn by the next attempt
        finally:
            entry.worker = None
            if entry.consoles <= 0 and self.nodes.get(k) is entry:
                del self.nodes[k]

    @staticmethod
    async def apply(lifecycle, pid, node_id, cols, rows, redraw=False, serial=SERIAL):
        """Sets the size on a serial port (ttyS0 by default) of a Linux guest with an agent; returns whether it succeeded.

        With `redraw` the size is nudged first, so a full-screen program redraws even when the size is the same.
        """
        try:
            if not lifecycle.has_guest_agent(pid, node_id):
                return False
            if await lifecycle.guest_family(node_id, Priority.COMMAND) != "posix":
                return False  # Windows has no serial getty, RouterOS its own console
            result = await lifecycle.qga.exec(node_id, command(cols, rows, redraw, serial), timeout=TIMEOUT,
                                              priority=Priority.COMMAND)
        except Exception as exc:  # noqa: BLE001 - a background task; the console works on without the size
            log.info("window size %sx%s not set on %s: %s: %s", cols, rows, node_id, type(exc).__name__, exc)
            return False
        if result.get("exit_code") != 0:
            log.info("window size %sx%s not set on %s: exit %s", cols, rows, node_id, result.get("exit_code"))
            return False
        return True
