"""Client of the Lab Node Agent running on every Proxmox node (mTLS).

The agent is the only privileged piece: it sets the slot's bind mounts (code at mp0,
student directory at mp1) and starts/stops the CT with pct on its own node. It is not a
general API, so the manager still uses the Proxmox API read-only to find the node of a CT
and to check what is configured.

Agent endpoints (all synchronous, one operation per CT at a time):
    GET  /capabilities                   -> {"version", "start_budget_seconds", "guest_action_timeout_seconds"}
                                         (agent 1.2.1; older agents answer 404)
    GET  /slots/{vmid}/status            -> {"vmid", "state": "running"|"stopped"}
    PUT  /slots/{vmid}/mounts            {code_revision, student_key, data_mode}   (CT stopped)
    POST /slots/{vmid}/start             {..mounts.., actions[]}  mounts, pct start, guest actions
    POST /slots/{vmid}/stop              {force}  pct shutdown --timeout 60, or pct stop if force
    POST /slots/{vmid}/reset             {}  agent 1.2: pct rollback of the stopped CT to its clean snapshot
                                         (disk and the whole CT config) -> {"state", "reset": true, "snapshot"}
    PUT  /students/{student_key}         ensure students/<key> (0700, mapped gns3 owner)
                                         -> {"student_key", "ready": true}; 409 if the existing
                                         directory has another owner (also briefly during a
                                         concurrent creation), 503 if the agent lacks the uid/gid

Errors. Preferred: {"detail": {"code": "...", "message": "..."}} with the codes in
docs/AGENT_REQUESTS.md; until the agent sends codes, the FastAPI {"detail": "..."} text of
feature/proxmox-node-agent is classified by HTTP status and wording:
    403  VMID outside the lab range, privileged CT or no 'labslot' tag   -> AgentRejected
    404  CT is not on this node (migrated)                                 -> AgentWrongNode
    409  "Another operation on this slot is in progress"                   -> AgentBusy
    409  "Slot must be stopped to change mount points"                     -> AgentNotStopped
    409  "Proxmox command failed: ..."                                     -> AgentCommandFailed
    422  "Required CephFS directory does not exist"                        -> AgentSourceMissing
    422  symlink, unsafe ancestor, ACL, not on CephFS, invalid request     -> AgentRejected
    503/504, connection errors, timeouts: the outcome is unknown           -> AgentUncertain
    409 COMMAND_UNCERTAIN, 504 PCT_TIMEOUT of a mutating command          -> AgentQuarantined
        the agent wrote an `uncertain` mark for the slot; it does not pass by itself, waiting is useless
"""

import ssl

import httpx
from .config import START_BUDGET_MARGIN
from .ownership import command_headers, current
from .store import Fenced


class AgentError(RuntimeError):
    def __init__(self, status: int | None, message: str, code: str | None = None, command: str | None = None):
        super().__init__(f"Node agent {status or 'unreachable'}{' ' + code if code else ''}: {message}")
        self.status = status
        self.message = message
        self.code = code
        self.command = command  # pct subcommand named by the agent (PCT_TIMEOUT, agent 1.2.1)


class AgentRejected(AgentError):
    """Configuration or request problem; repeating does not help."""


class AgentSourceMissing(AgentRejected):
    """The student (or code) directory is missing. During start the agent re-checks sources
    after `pct set`, so the CT mounts may already be changed: re-check before reuse."""


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


class AgentQuarantined(AgentUncertain):
    """The agent holds an `uncertain` mark for the slot (a mutating pct command timed out earlier, or
    now): every changing request and claim is refused with COMMAND_UNCERTAIN until an operator
    reconciles it. Subclass of AgentUncertain so that code which does not know it still stops; code
    that waits for AgentUncertain must not wait for this."""


class AgentStale(Fenced, AgentError):
    pass


CODES = {
    "CAPTURE_NODE_MISSING": AgentRejected,
    "CAPTURE_BASE_MISMATCH": AgentRejected,
    "CAPTURE_TOO_LARGE": AgentRejected,
    "CAPTURE_PATH_UNSAFE": AgentRejected,
    "IMAGE_EXISTS": AgentRejected,
    "STUDENT_DIR_BUSY": AgentRejected,
    "TIMEOUT": AgentUncertain,
    "STALE_OWNER": AgentStale,
    "COMMAND_UNCERTAIN": AgentQuarantined,
    "FENCE_STORAGE_ERROR": AgentUncertain,
    "VMID_OUT_OF_RANGE": AgentRejected,
    "NOT_LAB_SLOT": AgentRejected,
    "PRIVILEGED_CT": AgentRejected,
    "INVALID_REQUEST": AgentRejected,
    "SOURCE_UNSAFE": AgentRejected,
    "SOURCE_MISSING": AgentSourceMissing,
    "CT_NOT_ON_NODE": AgentWrongNode,
    "SLOT_BUSY": AgentBusy,
    "STUDENT_DIR_OWNER": AgentRejected,
    "NOT_STOPPED": AgentNotStopped,
    "SNAPSHOT_MISSING": AgentRejected,
    "SNAPSHOT_UNSAFE": AgentRejected,
    "PCT_FAILED": AgentCommandFailed,
    "STORAGE_UNAVAILABLE": AgentUncertain,
    "PCT_UNAVAILABLE": AgentUncertain,
    "PCT_TIMEOUT": AgentUncertain,  # Quarantine only when a mutating command timed out, see _classify
}


def _classify(resp: httpx.Response, mutating: bool = False) -> AgentError:
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    status = resp.status_code
    if isinstance(detail, dict) and detail.get("code") in CODES:
        code = detail["code"]
        command = detail.get("command")
        command = command[:20] if isinstance(command, str) else None
        cls = CODES[code]
        # The agent marks the slot uncertain when a mutating pct command times out; its `pct status`
        # and findmnt reads leave no mark (command "status", or no command at all from agent 1.2.1).
        if code == "PCT_TIMEOUT" and mutating and command != "status":
            cls = AgentQuarantined
        return cls(status, str(detail.get("message", code))[:800], code, command)
    text = detail if isinstance(detail, str) else "validation error" if detail else resp.reason_phrase
    text = str(text)[:800]
    if status == 422 and "does not exist" in text:
        return AgentSourceMissing(status, text)
    if status in (403, 422):
        return AgentRejected(status, text)
    if status == 404:
        return AgentWrongNode(404, text)
    if status == 409:
        if "in progress" in text:
            return AgentBusy(409, text)
        if "must be stopped" in text:
            return AgentNotStopped(409, text)
        if "different owner" in text:
            return AgentRejected(409, text)
        return AgentCommandFailed(409, text)
    if status >= 500:
        return AgentUncertain(status, text)
    return AgentRejected(status, text)


class AgentClient:
    def __init__(self, url_template: str, *, ca_file=None, cert_file=None, key_file=None,
                 timeout: float = 30.0, start_timeout: float = 720.0, mounts_timeout: float = 120.0,
                 stop_timeout: float = 130.0, reset_timeout: float = 270.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.url_template = url_template
        self.timeout = timeout
        self.start_timeout = start_timeout
        self.mounts_timeout = mounts_timeout
        self.stop_timeout = stop_timeout
        self.reset_timeout = reset_timeout  # The agent allows its rollback 180 s, plus two status reads
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
            headers = command_headers() if mutating else None
            resp = await self._http.request(method, url, json=json, headers=headers,
                                            timeout=timeout or self.timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Not delivered: nothing happened on the node.
            raise AgentUncertain(None, f"{type(exc).__name__} to agent on {node}") from exc
        except httpx.HTTPError as exc:
            raise AgentUncertain(None, f"{type(exc).__name__}; outcome unknown"
                                 if mutating else type(exc).__name__) from exc
        if resp.status_code != 200:
            raise _classify(resp, mutating)
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

    async def capture_capabilities(self, node):
        try:
            return await self._call("GET", node, "/capabilities")
        except AgentError:
            return {}

    async def capture_image(self, node, body, timeout):
        return await self._call("POST", node, "/images/capture", json=body, timeout=timeout)

    async def start_budget(self, node: str) -> int | None:
        """The agent's worst case for POST /start in seconds; None for an agent older than 1.2.1."""
        try:
            body = await self._call("GET", node, "/capabilities")
        except AgentWrongNode:
            return None
        budget = body.get("start_budget_seconds")
        return budget if isinstance(budget, int) and not isinstance(budget, bool) and budget > 0 else None

    async def slot_status(self, node: str, vmid: int) -> dict:
        """The agent's whole status: `state` and its `ownership` record (epoch, generation, uncertain, command)."""
        return await self._call("GET", node, f"/slots/{vmid}/status")

    async def status(self, node: str, vmid: int) -> str:
        body = await self.slot_status(node, vmid)
        state = body.get("state")
        if state not in ("running", "stopped"):
            raise AgentUncertain(200, "Unexpected CT state from agent")
        return state

    async def claim(self, node, vmid):
        authority = current.get()
        command_headers()
        body = authority.body()
        result = await self._call("POST", node, f"/slots/{vmid}/claim", json=body, mutating=True)
        if any(result.get(k) != v for k, v in body.items()):
            raise AgentUncertain(200, "Agent did not confirm ownership protocol")
        return result

    async def size(self, node, vmid, resources):
        return await self._call("PUT", node, f"/slots/{vmid}/size", json=resources,
                                timeout=self.mounts_timeout, mutating=True)

    @staticmethod
    def mounts_body(student_key: str, code_revision: str | None, data_mode: str = "rw") -> dict:
        return {"code_revision": code_revision, "student_key": student_key, "data_mode": data_mode}

    async def set_mounts(self, node, vmid, *, student_key, code_revision=None, data_mode="rw"):
        return await self._call("PUT", node, f"/slots/{vmid}/mounts",
                                json=self.mounts_body(student_key, code_revision, data_mode),
                                timeout=self.mounts_timeout, mutating=True)

    async def start(self, node, vmid, *, student_key, code_revision=None, data_mode="rw", actions=()):
        body = dict(self.mounts_body(student_key, code_revision, data_mode), actions=list(actions))
        return await self._call("POST", node, f"/slots/{vmid}/start", json=body,
                                timeout=self.start_timeout, mutating=True)

    async def ensure_student(self, node: str, student_key: str) -> dict:
        """Idempotent: creates the student directory or confirms it; never touches contents."""
        body = await self._call("PUT", node, f"/students/{student_key}", timeout=self.mounts_timeout,
                                mutating=True)
        if body.get("ready") is not True or body.get("student_key") != student_key:
            raise AgentUncertain(200, "Unexpected student provisioning response")
        return body

    async def stop(self, node, vmid, *, force=False):
        return await self._call("POST", node, f"/slots/{vmid}/stop", json={"force": force},
                                timeout=self.stop_timeout, mutating=True)

    async def reset(self, node, vmid):
        """Roll the stopped CT back to its clean snapshot. The CT config comes back as it was in the
        snapshot (no student mount, the size of the snapshot): park and size it again afterwards."""
        try:
            body = await self._call("POST", node, f"/slots/{vmid}/reset", json={},
                                    timeout=self.reset_timeout, mutating=True)
        except AgentWrongNode as exc:
            # An agent older than 1.2 has no such route: a bare 404 "Not Found", not CT_NOT_ON_NODE.
            if exc.code is None and exc.message == "Not Found":
                raise AgentRejected(404, "The node agent has no reset (needs agent 1.2)", "RESET_UNSUPPORTED") from exc
            raise
        if body.get("reset") is not True or body.get("state") != "stopped":
            raise AgentUncertain(200, "Unexpected reset response from agent")
        return body


async def start_budget_warnings(agent: "AgentClient", nodes, start_timeout: float) -> list[str]:
    """One line per node whose agent may need longer for POST /start than AGENT_START_TIMEOUT allows.

    Agents older than 1.2.1 do not report a budget (and keep their fixed 45 s per guest action): no line."""
    lines = []
    for node in nodes:
        try:
            budget = await agent.start_budget(node)
        except AgentError:
            continue  # Unreachable or not an agent: the health checks say so
        if budget is not None and budget + START_BUDGET_MARGIN > start_timeout:
            lines.append(f"Agent on {node} may spend {budget} s on a start; AGENT_START_TIMEOUT={int(start_timeout)} s "
                         f"is below {budget + START_BUDGET_MARGIN} s (budget + {START_BUDGET_MARGIN} s)")
    return lines
