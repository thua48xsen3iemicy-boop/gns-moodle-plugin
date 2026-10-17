"""Proxmox VE API client with entry-point failover and UPID task waiting.

Any cluster node proxies /nodes/{other}/... to the right node, so failover only changes
the entry point. The node that hosts a CT is looked up in /cluster/resources each time
it is needed (CTs migrate), never stored for good.

Failover policy (see docs/DESIGN.md, "Proxmox"):
  * connection errors, timeouts and 502/503/504 mark the entry node as down for
    PVE_RETRY_AFTER seconds and the request moves to the next node in the ring;
  * a mutating request is only retried when it surely did not reach the server
    (connect error); otherwise the outcome is unknown and PveUncertain is raised so the
    caller re-reads the real CT state instead of repeating the action;
  * 4xx and other 5xx are answers about the request itself and are returned as errors.
"""

import asyncio
import logging
import ssl
import time
from urllib.parse import quote

import httpx

log = logging.getLogger("lab_manager.proxmox")

SWITCH_STATUSES = {502, 503, 504}
UNREACHABLE_TARGET = {595, 596}  # pveproxy could not reach the node that owns the resource


class PveError(RuntimeError):
    """Base class. Messages never contain the token."""


class PveApiError(PveError):
    def __init__(self, status: int, message: str):
        super().__init__(f"Proxmox API {status}: {message}")
        self.status = status


class PveUnavailable(PveError):
    """No entry node answered."""


class PveUncertain(PveError):
    """A mutating request may or may not have been applied."""


class PveTaskFailed(PveError):
    def __init__(self, upid: str, exitstatus: str):
        super().__init__(f"Proxmox task failed: {exitstatus}")
        self.upid = upid
        self.exitstatus = exitstatus


class PveTaskUnknown(PveError):
    """Task state cannot be read (its node is down or the wait timed out)."""


def upid_node(upid: str) -> str:
    # UPID:{node}:{pid}:{pstart}:{starttime}:{type}:{id}:{user}:
    parts = upid.split(":")
    if len(parts) < 3 or parts[0] != "UPID" or not parts[1]:
        raise PveError("Malformed UPID")
    return parts[1]


def _reason(resp: httpx.Response) -> str:
    # PVE puts the error text into the reason phrase and sometimes into "errors"/"message".
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        if body.get("errors"):
            return "; ".join(f"{k}: {v}" for k, v in body["errors"].items())[:500]
        if body.get("message"):
            return str(body["message"])[:500]
    return (resp.reason_phrase or "error")[:500]


class PveClient:
    def __init__(
        self,
        nodes,
        token_id: str,
        token_secret: str,
        *,
        ca_file=None,
        timeout: float = 10.0,
        retry_after: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock=time.monotonic,
    ):
        if not nodes:
            raise ValueError("At least one Proxmox node is required")
        self.nodes = list(nodes)
        self.retry_after = retry_after
        self.clock = clock
        self.task_interval = 1.0
        self._current = 0
        self._down_until: dict[str, float] = {}
        self._node_cache: dict[int, tuple[str, float]] = {}
        if transport is None:
            if ca_file is None:
                raise ValueError("ca_file is required: TLS is always verified")
            verify = ssl.create_default_context(cafile=str(ca_file))
        else:
            verify = True
        self._http = httpx.AsyncClient(
            transport=transport,
            verify=verify,
            timeout=httpx.Timeout(timeout),
            headers={"Authorization": f"PVEAPIToken={token_id}={token_secret}"},
            trust_env=False,
        )

    async def close(self):
        await self._http.aclose()

    # ------------------------------------------------------------------ transport
    def _order(self):
        """Last good node first, then the ring; nodes marked down go last (oldest first)."""
        now = self.clock()
        ring = [self.nodes[(self._current + i) % len(self.nodes)] for i in range(len(self.nodes))]
        up = [n for n in ring if self._down_until.get(n, 0) <= now]
        down = sorted((n for n in ring if n not in up), key=lambda n: self._down_until[n])
        return up + down

    def _mark_down(self, node: str, why: str):
        if self._down_until.get(node, 0) <= self.clock():
            log.warning("Proxmox entry node %s unavailable (%s); trying next", node, why)
        self._down_until[node] = self.clock() + self.retry_after

    def _mark_up(self, node: str):
        if self._down_until.pop(node, None) is not None:
            log.info("Proxmox entry node %s is back", node)
        self._current = self.nodes.index(node)

    def node_is_down(self, node: str) -> bool:
        return self._down_until.get(node, 0) > self.clock()

    async def request(self, method: str, path: str, *, params=None, data=None):
        mutating = method.upper() != "GET"
        last_error = "no nodes tried"
        for node in self._order():
            url = f"https://{node}/api2/json{path}"
            try:
                resp = await self._http.request(method, url, params=params, data=data)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # The request never reached this node: safe to retry anywhere.
                self._mark_down(node, type(exc).__name__)
                last_error = type(exc).__name__
                continue
            except httpx.TransportError as exc:
                self._mark_down(node, type(exc).__name__)
                last_error = type(exc).__name__
                if mutating:
                    raise PveUncertain(
                        f"{method} {path}: {type(exc).__name__}; outcome unknown"
                    ) from exc
                continue
            if resp.status_code in SWITCH_STATUSES:
                self._mark_down(node, f"HTTP {resp.status_code}")
                last_error = f"HTTP {resp.status_code}"
                if mutating:
                    raise PveUncertain(f"{method} {path}: HTTP {resp.status_code}; outcome unknown")
                continue
            self._mark_up(node)
            if resp.status_code >= 400:
                raise PveApiError(resp.status_code, _reason(resp))
            try:
                return resp.json().get("data")
            except (ValueError, AttributeError) as exc:
                raise PveApiError(resp.status_code, "Malformed JSON response") from exc
        raise PveUnavailable(f"No Proxmox node answered ({last_error})")

    # ------------------------------------------------------------------ cluster
    async def find_ct_node(self, vmid: int, *, max_age: float = 10.0) -> str:
        cached = self._node_cache.get(vmid)
        if cached and self.clock() - cached[1] < max_age:
            return cached[0]
        resources = await self.request("GET", "/cluster/resources", params={"type": "vm"})
        for res in resources or []:
            if res.get("vmid") == vmid and res.get("type") == "lxc":
                self._node_cache[vmid] = (res["node"], self.clock())
                return res["node"]
        raise PveApiError(404, f"CT {vmid} not found in cluster resources")

    def forget_ct_node(self, vmid: int):
        self._node_cache.pop(vmid, None)

    async def _ct(self, method, vmid, suffix, **kwargs):
        node = await self.find_ct_node(vmid)
        path = f"/nodes/{quote(node, safe='')}/lxc/{vmid}{suffix}"
        try:
            return node, await self.request(method, path, **kwargs)
        except PveApiError as exc:
            if exc.status in UNREACHABLE_TARGET or exc.status == 500 and "does not exist" in str(exc):
                # CT migrated or its node is down: look it up again next time.
                self.forget_ct_node(vmid)
            raise

    # ------------------------------------------------------------------ CT operations
    async def ct_status(self, vmid: int) -> dict:
        node, data = await self._ct("GET", vmid, "/status/current")
        return dict(data or {}, node=node)

    async def ct_config(self, vmid: int) -> dict:
        _, data = await self._ct("GET", vmid, "/config")
        return data or {}

    async def ct_set_config(self, vmid: int, values: dict, delete: list[str] | None = None):
        """PUT config. Only for stopped CTs: mount changes are not hotplugged on purpose."""
        payload = dict(values)
        if delete:
            payload["delete"] = ",".join(delete)
        node, data = await self._ct("PUT", vmid, "/config", data=payload)
        if isinstance(data, str) and data.startswith("UPID:"):
            return data
        return None

    async def ct_start(self, vmid: int) -> str:
        return (await self._ct("POST", vmid, "/status/start"))[1]

    async def ct_shutdown(self, vmid: int, timeout: int) -> str:
        return (await self._ct("POST", vmid, "/status/shutdown", data={"timeout": timeout}))[1]

    async def ct_stop(self, vmid: int) -> str:
        """Last resort: a hard stop of the whole container."""
        return (await self._ct("POST", vmid, "/status/stop"))[1]

    # ------------------------------------------------------------------ tasks
    async def task_status(self, upid: str) -> dict:
        node = upid_node(upid)
        path = f"/nodes/{quote(node, safe='')}/tasks/{quote(upid, safe='')}/status"
        try:
            return await self.request("GET", path) or {}
        except PveUnavailable as exc:
            raise PveTaskUnknown("Cannot read task status: no Proxmox node answered") from exc
        except PveApiError as exc:
            if exc.status in UNREACHABLE_TARGET or exc.status >= 500:
                raise PveTaskUnknown(f"Cannot read task status on {node}: {exc}") from exc
            raise

    async def wait_task(self, upid: str, *, timeout: float = 120.0, interval: float | None = None):
        """Wait until status=stopped and require exitstatus=OK.

        PveTaskUnknown means the caller must re-check the real CT state; the task is not
        assumed to have succeeded.
        """
        deadline = self.clock() + timeout
        while True:
            status = await self.task_status(upid)
            if status.get("status") == "stopped":
                exitstatus = status.get("exitstatus", "")
                if exitstatus != "OK":
                    raise PveTaskFailed(upid, str(exitstatus)[:500])
                return status
            if self.clock() >= deadline:
                raise PveTaskUnknown("Proxmox task did not finish in time")
            await asyncio.sleep(self.task_interval if interval is None else interval)
