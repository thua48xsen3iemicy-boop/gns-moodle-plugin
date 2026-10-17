"""Client of the Lab Node Agent running on every Proxmox node (mTLS).

The agent is the only privileged piece: it sets the slot's bind mounts (code at mp0,
student directory at mp1) and starts/stops the CT with pct on its own node. It is not a
general API, so the manager still uses the Proxmox API read-only to find the node of a CT
and to check what is configured.

Agent endpoints (all synchronous, one operation per CT at a time):
    GET  /slots/{vmid}/status            -> {"vmid", "state": "running"|"stopped"}
    PUT  /slots/{vmid}/mounts            {code_revision, student_key, data_mode}   (CT stopped)
    POST /slots/{vmid}/start             {..mounts.., actions[]}  mounts, pct start, guest actions
    POST /slots/{vmid}/stop              {force}  pct shutdown --timeout 60, or pct stop if force

Error mapping (the agent returns FastAPI {"detail": "..."} strings):
    403  VMID outside the lab range, privileged CT or no 'labslot' tag   -> AgentRejected
    404  CT is not on this node (migrated)                                 -> AgentWrongNode
    409  "Another operation on this slot is in progress"                   -> AgentBusy
    409  "Slot must be stopped to change mount points"                     -> AgentNotStopped
    409  "Proxmox command failed: ..."                                     -> AgentCommandFailed
    422  student/code directory missing, symlink or not on CephFS          -> AgentRejected
    503/504, connection errors, timeouts: the outcome is unknown           -> AgentUncertain
"""

import ssl

import httpx


class AgentError(RuntimeError):
    def __init__(self, status: int | None, message: str):
        super().__init__(f"Node agent {status or 'unreachable'}: {message}")
        self.status = status
        self.message = message


class AgentRejected(AgentError):
    """Configuration or request problem; repeating does not help."""


class AgentWrongNode(AgentError):
    pass


class AgentBusy(AgentError):
    pass


class AgentNotStopped(AgentError):
    pass


class AgentCommandFailed(AgentError):
    """pct returned an error; the CT state must be re-read."""


class AgentUncertain(AgentError):
    """The request may or may not have been executed (timeout, 503/504, connection lost)."""


def _classify(resp: httpx.Response) -> AgentError:
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    text = detail if isinstance(detail, str) else "validation error" if detail else resp.reason_phrase
    text = str(text)[:800]
    if resp.status_code in (403, 422):
        return AgentRejected(resp.status_code, text)
    if resp.status_code == 404:
        return AgentWrongNode(404, text)
    if resp.status_code == 409:
        if "in progress" in text:
            return AgentBusy(409, text)
        if "must be stopped" in text:
            return AgentNotStopped(409, text)
        return AgentCommandFailed(409, text)
    if resp.status_code in (503, 504) or resp.status_code >= 500:
        return AgentUncertain(resp.status_code, text)
    return AgentRejected(resp.status_code, text)


class AgentClient:
    def __init__(self, url_template: str, *, ca_file=None, cert_file=None, key_file=None,
                 timeout: float = 30.0, start_timeout: float = 330.0, stop_timeout: float = 100.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.url_template = url_template
        self.timeout = timeout
        self.start_timeout = start_timeout
        self.stop_timeout = stop_timeout
        if transport is None:
            # mTLS: the agent authenticates the manager by its client certificate.
            verify = ssl.create_default_context(cafile=str(ca_file))
            verify.load_cert_chain(str(cert_file), str(key_file))
        else:
            verify = True
        self._http = httpx.AsyncClient(transport=transport, verify=verify, trust_env=False,
                                       timeout=httpx.Timeout(timeout))

    async def close(self):
        await self._http.aclose()

    def base(self, node: str) -> str:
        return self.url_template.format(node=node)

    async def _call(self, method, node, path, *, json=None, timeout=None, mutating=False):
        url = self.base(node) + path
        try:
            resp = await self._http.request(method, url, json=json, timeout=timeout or self.timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Not delivered: nothing happened on the node.
            raise AgentUncertain(None, f"{type(exc).__name__} to agent on {node}") from exc
        except httpx.HTTPError as exc:
            raise AgentUncertain(None, f"{type(exc).__name__}; outcome unknown"
                                 if mutating else type(exc).__name__) from exc
        if resp.status_code != 200:
            raise _classify(resp)
        try:
            return resp.json()
        except ValueError as exc:
            raise AgentUncertain(resp.status_code, "Malformed agent response") from exc

    async def health(self, node: str) -> bool:
        try:
            await self._call("GET", node, "/health")
            return True
        except AgentError:
            return False

    async def status(self, node: str, vmid: int) -> str:
        body = await self._call("GET", node, f"/slots/{vmid}/status")
        state = body.get("state")
        if state not in ("running", "stopped"):
            raise AgentUncertain(200, "Unexpected CT state from agent")
        return state

    @staticmethod
    def mounts_body(student_key: str, code_revision: str | None, data_mode: str = "rw") -> dict:
        return {"code_revision": code_revision, "student_key": student_key, "data_mode": data_mode}

    async def set_mounts(self, node, vmid, *, student_key, code_revision=None, data_mode="rw"):
        return await self._call("PUT", node, f"/slots/{vmid}/mounts",
                                json=self.mounts_body(student_key, code_revision, data_mode),
                                mutating=True)

    async def start(self, node, vmid, *, student_key, code_revision=None, data_mode="rw", actions=()):
        body = dict(self.mounts_body(student_key, code_revision, data_mode), actions=list(actions))
        return await self._call("POST", node, f"/slots/{vmid}/start", json=body,
                                timeout=self.start_timeout, mutating=True)

    async def stop(self, node, vmid, *, force=False):
        return await self._call("POST", node, f"/slots/{vmid}/stop", json={"force": force},
                                timeout=self.stop_timeout, mutating=True)
