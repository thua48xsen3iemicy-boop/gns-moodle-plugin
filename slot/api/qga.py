"""Serialized access to QEMU guest agents (QGA): one queued channel per node.

QEMU serves a single client per chardev socket and qemu-ga handles one request at a
time, so exchanges with one node must never overlap. Callers do not touch sockets; they
submit requests to the broker and await the result. The lock covers one request/response
exchange, not a whole guest command: guest-exec is asynchronous, so a long command is
polled between other callers' exchanges instead of blocking them.
"""

import asyncio
import base64
import heapq
import itertools
import json
import secrets
import time
from enum import IntEnum

READ_LIMIT = 64 * 1024 * 1024  # qemu-ga returns up to 16 MiB per stream, base64-encoded
IDLE_SECONDS = 15.0  # release the single-client socket when nobody uses it
POLL_FIRST = 0.05  # guest-exec-status polling: quick commands finish in the first polls
POLL_MAX = 0.5
MAX_CHANNELS = 64
MAX_CACHED = 256


class Priority(IntEnum):
    """Lower value is served first; an exchange in flight is never interrupted."""

    LIFECYCLE = 0  # readiness checks and provisioning
    COMMAND = 1  # commands requested by Manager (auto-checks)
    METRICS = 2  # background sampling: best effort, may be skipped or refused


EXCHANGE_TIMEOUT = {
    Priority.LIFECYCLE: 5.0,
    Priority.COMMAND: 5.0,
    Priority.METRICS: 2.0,
}


class QgaError(RuntimeError):
    code = "QGA_ERROR"


class QgaUnavailable(QgaError):
    """No connection, no sync with the agent, or the channel is backing off."""

    code = "QGA_UNREACHABLE"


class QgaTimeout(QgaError, TimeoutError):
    code = "QGA_TIMEOUT"


class QgaBusy(QgaError):
    """The caller asked not to wait and the channel is occupied."""

    code = "QGA_BUSY"


class QgaCommandError(QgaError):
    """The guest agent is alive and answered with an error."""

    code = "QGA_COMMAND_ERROR"


def encode(command, args=None):
    message = {"execute": command}
    if args is not None:
        message["arguments"] = args
    return json.dumps(message).encode() + b"\n"


def decode(line):
    """One protocol line, or None for noise (sync delimiter, stale or partial data)."""
    try:
        message = json.loads(line.lstrip(b"\xff"))
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


class _Request:
    __slots__ = ("command", "args", "priority", "timeout", "expires", "future")

    def __init__(self, command, args, priority, timeout, expires):
        self.command = command
        self.args = args
        self.priority = priority
        self.timeout = timeout
        self.expires = expires
        self.future = asyncio.get_running_loop().create_future()


class _Channel:
    def __init__(self, node_id, path_of, idle, threshold, base, cap):
        self.node_id = node_id
        self._path_of = path_of
        self._idle = idle
        self._threshold, self._base, self._cap = threshold, base, cap
        self._heap = []
        self._seq = itertools.count()
        self._wake = asyncio.Event()
        self._worker = None
        self._reader = self._writer = None
        self._inflight = None
        self._inflight_since = 0.0
        self._closed = False
        self.failures = 0
        self.retry_at = 0.0
        self.last_ok = None
        self.last_error = None
        self.latency_ms = None

    # -- caller side -------------------------------------------------------------

    def _pending(self):
        return sum(1 for _, _, req in self._heap if not req.future.done())

    def busy(self):
        return self._inflight is not None or self._pending() > 0

    def backing_off(self):
        return self.failures >= self._threshold and time.monotonic() < self.retry_at

    def submit(self, command, args, priority, timeout, wait, max_wait):
        if self._closed:
            raise QgaError("QGA broker is closed")
        if priority is Priority.METRICS and self.backing_off():
            raise QgaUnavailable(
                f"QGA is backing off for another {self.retry_at - time.monotonic():.1f}s"
            )
        if not wait and self.busy():
            raise QgaBusy("QGA channel is busy")
        expires = None if max_wait is None else time.monotonic() + max_wait
        request = _Request(command, args, priority, timeout, expires)
        heapq.heappush(self._heap, (priority, next(self._seq), request))
        self._wake.set()
        if self._worker is None:
            self._worker = asyncio.get_running_loop().create_task(
                self._run(), name="qga-" + self.node_id
            )
        return request.future

    def snapshot(self):
        if self.failures >= self._threshold:
            status = "unreachable"
        else:
            status = "busy" if self.busy() else "idle"
        now = time.monotonic()
        return {
            "node_id": self.node_id,
            "status": status,
            "connected": self._writer is not None,
            "queue_depth": self._pending(),
            "in_flight": self._inflight.command if self._inflight else None,
            "in_flight_seconds": (
                round(now - self._inflight_since, 3) if self._inflight else None
            ),
            "consecutive_failures": self.failures,
            "retry_in": round(max(0.0, self.retry_at - now), 3),
            "last_ok_at": self.last_ok,
            "last_error": self.last_error,
            "latency_ms": self.latency_ms,
        }

    async def close(self):
        self._closed = True
        worker = self._worker
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self._drop()

    # -- worker ------------------------------------------------------------------

    async def _run(self):
        try:
            while True:
                request = await self._next()
                if request is None:
                    return
                if request.expires is not None and time.monotonic() > request.expires:
                    self._settle(
                        request,
                        QgaTimeout("QGA request expired while waiting for the channel"),
                    )
                elif request.priority is Priority.METRICS and self.backing_off():
                    self._settle(request, QgaUnavailable("QGA is backing off"))
                else:
                    await self._serve(request)
        finally:
            # No await between the decision to stop and here: a request submitted later
            # sees no worker and starts a new one.
            self._worker = None
            self._drop()
            while self._heap:
                self._settle(heapq.heappop(self._heap)[2], QgaError("QGA channel closed"))

    async def _next(self):
        while True:
            while self._heap:
                request = heapq.heappop(self._heap)[2]
                if not request.future.done():  # a cancelled caller is never served
                    return request
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), self._idle)
            except TimeoutError:
                if not self._heap:
                    return None

    async def _serve(self, request):
        self._inflight, self._inflight_since = request, time.monotonic()
        started = time.monotonic()
        try:
            async with asyncio.timeout(request.timeout):
                if self._writer is None or self._writer.is_closing():
                    await self._connect()
                result = await self._exchange(request.command, request.args)
        except QgaCommandError as exc:
            self._succeeded(started)  # the agent answered, so the channel is healthy
            self._settle(request, exc)
        except TimeoutError:
            self._failed("QGA_TIMEOUT")
            self._settle(
                request,
                QgaTimeout(f"QGA {request.command} timed out after {request.timeout:g}s"),
            )
        except (OSError, ValueError) as exc:
            self._failed("QGA_UNREACHABLE")
            self._settle(
                request,
                QgaUnavailable(f"QGA connection failed: {type(exc).__name__}: {exc}"),
            )
        except asyncio.CancelledError:
            self._drop()
            self._settle(request, QgaError("QGA channel closed"))
            raise
        except Exception as exc:  # noqa: BLE001 - never let one request stop the worker.
            self._failed("QGA_ERROR")
            self._settle(request, QgaError(f"{type(exc).__name__}: {exc}"))
        else:
            self._succeeded(started)
            self._settle(request, result=result)
        finally:
            self._inflight = None

    @staticmethod
    def _settle(request, exc=None, result=None):
        if not request.future.done():
            if exc is not None:
                request.future.set_exception(exc)
            else:
                request.future.set_result(result)

    def _succeeded(self, started):
        elapsed = (time.monotonic() - started) * 1000
        self.latency_ms = round(
            elapsed if self.latency_ms is None else 0.8 * self.latency_ms + 0.2 * elapsed, 1
        )
        self.failures = 0
        self.retry_at = 0.0
        self.last_ok = time.time()

    def _failed(self, code):
        self._drop()  # a late reply must not reach the next request
        self.failures += 1
        self.last_error = code
        if self.failures >= self._threshold:
            delay = min(self._base * 2 ** (self.failures - self._threshold), self._cap)
            self.retry_at = time.monotonic() + delay

    # -- protocol ----------------------------------------------------------------

    def _drop(self):
        writer, self._reader, self._writer = self._writer, None, None
        if writer is not None:
            writer.close()

    async def _connect(self):
        reader, writer = await asyncio.open_unix_connection(
            str(self._path_of(self.node_id)), limit=READ_LIMIT
        )
        try:
            # Resynchronize: drops anything a previous client left in the agent's buffer.
            marker = secrets.randbelow(2**31 - 1) + 1
            writer.write(b"\xff" + encode("guest-sync-delimited", {"id": marker}))
            await writer.drain()
            while True:
                line = await reader.readline()
                if not line:
                    raise ConnectionError("QGA connection closed")
                message = decode(line)
                if message is not None and message.get("return") == marker:
                    break
        except BaseException:
            writer.close()
            raise
        self._reader, self._writer = reader, writer

    async def _exchange(self, command, args):
        self._writer.write(encode(command, args))
        await self._writer.drain()
        while True:
            line = await self._reader.readline()
            if not line:
                raise ConnectionError("QGA connection closed")
            message = decode(line)
            if message is None:
                continue
            if "error" in message:
                raise QgaCommandError(str(message["error"]))
            if "return" in message:
                return message["return"]


class QgaBroker:
    """`socket_path(node_id)` is resolved on every connect, so a moved QGA directory works."""

    def __init__(
        self,
        socket_path,
        *,
        idle_seconds=IDLE_SECONDS,
        breaker_threshold=3,
        breaker_base=2.0,
        breaker_max=30.0,
    ):
        self._socket_path = socket_path
        self._channel_args = (idle_seconds, breaker_threshold, breaker_base, breaker_max)
        self._channels = {}
        self._results = {}
        self._flights = {}
        self._closed = False

    def _channel(self, node_id):
        channel = self._channels.get(node_id)
        if channel is None:
            if not node_id or "/" in node_id or node_id in (".", ".."):
                raise ValueError("Invalid node id")
            if len(self._channels) >= MAX_CHANNELS:
                for key, idle in list(self._channels.items()):
                    if idle._worker is None and not idle._heap:
                        del self._channels[key]
            channel = self._channels[node_id] = _Channel(
                node_id, self._socket_path, *self._channel_args
            )
        return channel

    async def call(
        self,
        node_id,
        command,
        args=None,
        *,
        priority=Priority.COMMAND,
        timeout=None,
        wait=True,
        max_wait=None,
    ):
        """One QGA request/response exchange.

        `wait=False` raises QgaBusy instead of queueing behind other work. `max_wait`
        bounds the time spent in the queue; `timeout` bounds the exchange itself.
        """
        if self._closed:
            raise QgaError("QGA broker is closed")
        future = self._channel(node_id).submit(
            command,
            args,
            priority,
            EXCHANGE_TIMEOUT[priority] if timeout is None else timeout,
            wait,
            max_wait,
        )
        return await future

    async def exec(
        self,
        node_id,
        command,
        *,
        timeout=20.0,
        priority=Priority.COMMAND,
        wait=True,
        max_age=None,
    ):
        """Run `/bin/sh -c command` in the guest; returns exit_code, stdout and stderr.

        With `max_age` (seconds) a result that fresh is reused and identical concurrent
        requests share one execution. Only for read-only commands.
        """
        if max_age is None:
            return await self._exec(node_id, command, timeout, priority, wait)
        hit = self._results.get((node_id, command))
        if hit is not None and time.monotonic() - hit[0] <= max_age:
            return dict(hit[1])
        key = (node_id, command, priority, wait)
        flight = self._flights.get(key)
        if flight is None:
            flight = asyncio.create_task(
                self._exec_cached(node_id, command, timeout, priority, wait)
            )
            self._flights[key] = flight
            flight.add_done_callback(lambda task, k=key: self._flight_done(k, task))
        return dict(await asyncio.shield(flight))

    async def _exec_cached(self, node_id, command, timeout, priority, wait):
        result = await self._exec(node_id, command, timeout, priority, wait)
        self._results[(node_id, command)] = (time.monotonic(), result)
        if len(self._results) > MAX_CACHED:
            oldest = sorted(self._results, key=lambda k: self._results[k][0])
            for key in oldest[: MAX_CACHED // 4]:
                del self._results[key]
        return result

    def _flight_done(self, key, task):
        if self._flights.get(key) is task:
            del self._flights[key]
        if not task.cancelled():
            task.exception()  # mark retrieved: every waiter may have gone away

    async def _exec(self, node_id, command, timeout, priority, wait):
        deadline = time.monotonic() + timeout
        started = await self.call(
            node_id,
            "guest-exec",
            {"path": "/bin/sh", "arg": ["-c", command], "capture-output": True},
            priority=priority,
            wait=wait,
            max_wait=timeout,
        )
        if not isinstance(started, dict) or type(started.get("pid")) is not int:
            raise QgaError("Malformed guest-exec reply")
        delay = POLL_FIRST
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QgaTimeout("Guest command did not finish before timeout")
            await asyncio.sleep(min(delay, remaining))
            delay = min(delay * 1.5, POLL_MAX)
            # The last poll after the deadline still gets a chance to see the exit.
            status = await self.call(
                node_id,
                "guest-exec-status",
                {"pid": started["pid"]},
                priority=priority,
                max_wait=max(deadline - time.monotonic(), 0.0) + 1.0,
            )
            if status.get("exited"):
                return {
                    "exit_code": status.get("exitcode"),
                    "stdout": base64.b64decode(status.get("out-data", "")).decode(
                        errors="replace"
                    ),
                    "stderr": base64.b64decode(status.get("err-data", "")).decode(
                        errors="replace"
                    ),
                }

    def state(self, node_id):
        """Snapshot of one channel; an unknown node is reported as idle."""
        channel = self._channels.get(node_id)
        if channel is None:
            return {
                "node_id": node_id,
                "status": "idle",
                "connected": False,
                "queue_depth": 0,
                "in_flight": None,
                "in_flight_seconds": None,
                "consecutive_failures": 0,
                "retry_in": 0.0,
                "last_ok_at": None,
                "last_error": None,
                "latency_ms": None,
            }
        return channel.snapshot()

    def states(self):
        return {node_id: ch.snapshot() for node_id, ch in self._channels.items()}

    async def forget(self, node_id):
        """Drop a node that is gone (stopped or wiped lab): closes its socket and cache."""
        channel = self._channels.pop(node_id, None)
        for key in [k for k in self._results if k[0] == node_id]:
            del self._results[key]
        if channel is not None:
            await channel.close()

    async def close(self):
        self._closed = True
        channels, self._channels = list(self._channels.values()), {}
        await asyncio.gather(*(ch.close() for ch in channels), return_exceptions=True)
