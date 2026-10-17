"""In-memory fakes for Proxmox VE, Lab Slot and the console gateway (httpx.MockTransport)."""

import json
import re
import uuid
from urllib.parse import parse_qs, unquote

import httpx


def _form(request):
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


class FakePve:
    """A cluster of nodes; entry hosts are '<node>:8006'. CTs live on one node and may migrate."""

    def __init__(self, timeline=None, nodes=("pve1", "pve2", "pve3")):
        self.nodes = list(nodes)
        self.timeline = timeline if timeline is not None else []
        self.cts = {}
        self.tasks = {}
        self.down_hosts = set()  # Entry points refusing connections
        self.task_node_down = set()  # Nodes whose tasks cannot be read (595)
        self.requests = []  # (host, method, path)
        self.task_polls = 1
        self.exit_status = {}  # action -> exitstatus override
        self.responses = {}  # (method, path regex) -> (status, json body) one-shot overrides
        self.counter = 0

    def add_ct(self, vmid, node="pve2", status="stopped", config=None):
        self.cts[vmid] = {"node": node, "status": status, "config": dict(config or {"hostname": f"slot{vmid}"})}

    def transport(self):
        return httpx.MockTransport(self.handle)

    def _task(self, node, action, vmid, on_done):
        self.counter += 1
        upid = f"UPID:{node}:0000ABCD:0000EF01:{self.counter:08X}:{action}:{vmid}:lab-manager@pve!manager:"
        self.tasks[upid] = {"remaining": self.task_polls, "on_done": on_done, "action": action,
                            "exitstatus": self.exit_status.get(action, "OK")}
        return upid

    def handle(self, request: httpx.Request):
        host = request.url.host
        path = request.url.path.removeprefix("/api2/json")
        self.requests.append((host, request.method, path))
        if host in self.down_hosts:
            raise httpx.ConnectError("refused", request=request)
        assert request.headers["Authorization"].startswith("PVEAPIToken=")
        for (method, pattern), (status, body) in list(self.responses.items()):
            if method == request.method and re.fullmatch(pattern, path):
                del self.responses[(method, pattern)]
                return httpx.Response(status, json=body)
        if path == "/cluster/resources":
            data = [{"vmid": v, "type": "lxc", "node": ct["node"], "status": ct["status"]}
                    for v, ct in self.cts.items()]
            return httpx.Response(200, json={"data": data})
        m = re.fullmatch(r"/nodes/([^/]+)/tasks/([^/]+)/status", path)
        if m:
            node, upid = m.group(1), unquote(m.group(2))
            if node in self.task_node_down:
                return httpx.Response(595, json={"data": None})
            task = self.tasks.get(upid)
            if task is None:
                return httpx.Response(500, json={"data": None, "message": "no such task"})
            if task["remaining"] > 0:
                task["remaining"] -= 1
                return httpx.Response(200, json={"data": {"status": "running", "upid": upid}})
            if task["on_done"]:
                if task["exitstatus"] == "OK":
                    task["on_done"]()
                task["on_done"] = None
            return httpx.Response(200, json={"data": {"status": "stopped", "exitstatus": task["exitstatus"]}})
        m = re.fullmatch(r"/nodes/([^/]+)/lxc/(\d+)(/.*)", path)
        if not m:
            return httpx.Response(501, json={"data": None})
        node, vmid, rest = m.group(1), int(m.group(2)), m.group(3)
        ct = self.cts.get(vmid)
        if ct is None or ct["node"] != node:
            return httpx.Response(500, json={"data": None,
                                             "message": f"Configuration file 'nodes/{node}/lxc/{vmid}.conf' does not exist"})
        if rest == "/status/current" and request.method == "GET":
            return httpx.Response(200, json={"data": {"vmid": vmid, "status": ct["status"]}})
        if rest == "/config" and request.method == "GET":
            return httpx.Response(200, json={"data": dict(ct["config"])})
        if rest == "/config" and request.method == "PUT":
            if ct["status"] != "stopped":
                return httpx.Response(500, json={"data": None, "message": "CT is running"})
            form = _form(request)
            for key in filter(None, form.pop("delete", "").split(",")):
                ct["config"].pop(key, None)
            ct["config"].update(form)
            self.timeline.append(("pve:config", dict(form)))
            return httpx.Response(200, json={"data": None})
        actions = {
            "/status/start": ("vzstart", "running"),
            "/status/shutdown": ("vzshutdown", "stopped"),
            "/status/stop": ("vzstop", "stopped"),
        }
        if request.method == "POST" and rest in actions:
            action, target = actions[rest]
            if action == "vzstart" and ct["status"] == "running":
                return httpx.Response(500, json={"data": None, "message": f"CT {vmid} already running"})
            self.timeline.append(("pve:" + action, vmid))

            def done(ct=ct, target=target):
                ct["status"] = target

            return httpx.Response(200, json={"data": self._task(node, action, vmid, done)})
        return httpx.Response(501, json={"data": None})


class FakeSlot:
    """Lab Slot semantics from slot/api/lifecycle.py, reduced to what the manager relies on."""

    def __init__(self, address, timeline=None):
        self.address = address
        self.timeline = timeline if timeline is not None else []
        self.healthy = True
        self.ready = True
        self.labs = {}
        self.ops = {}
        self.keys = {}
        self.node_ids = {}
        self.op_polls = 1
        self.fail_next = None  # error dict for the next operation
        self.requests = []
        self.reachable = True

    def transport(self):
        return httpx.MockTransport(self.handle)

    def _json(self, status, body):
        return httpx.Response(status, json=body)

    def _present(self, op, replayed=False):
        lab = self.labs.get(op["project_id"])
        return {"operation_id": op["operation_id"], "project_id": op["project_id"], "action": op["action"],
                "status": op["status"], "replayed": replayed,
                "laboratory": {"state": lab["state"]} if lab else None}

    def _submit(self, action, pid, ticket, key):
        fingerprint = json.dumps([action, pid, ticket], sort_keys=True)
        if key in self.keys:
            op = self.ops[self.keys[key]]
            if op["fingerprint"] != fingerprint:
                return self._json(409, {"detail": {"code": "IDEMPOTENCY_KEY_CONFLICT", "message": "x"}})
            expected = {"deploy": "READY", "resume": "READY", "stop": "STOPPED"}[action]
            lab = self.labs.get(op["project_id"])
            if op["status"] == "SUCCEEDED" and (not lab or lab["state"] != expected):
                return self._json(409, {"detail": {"code": "STALE_IDEMPOTENCY_KEY", "message": "stale",
                                                   **self._present(op, True)}})
            return self._json(202, self._present(op, True))
        if any(op["status"] == "RUNNING" for op in self.ops.values()):
            return self._json(409, {"detail": {"code": "OPERATION_IN_PROGRESS", "message": "busy"}})
        for other, lab in self.labs.items():
            if other != pid and lab["state"] != "STOPPED":
                return self._json(409, {"detail": "Another laboratory owns this slot; stop it first"})
        lab = self.labs.get(pid)
        if action != "deploy" and not lab:
            return self._json(404, {"detail": "Unknown managed laboratory"})
        allowed = {"deploy": {None, "READY", "STOPPED", "FAILED"}, "resume": {"READY", "STOPPED", "FAILED"},
                   "stop": {"READY", "STOPPED", "FAILED"}}[action]
        if (lab or {}).get("state") not in allowed:
            return self._json(409, {"detail": {"code": "INVALID_TRANSITION", "message": "no"}})
        if lab is None:
            lab = self.labs[pid] = {"project_id": pid, "ticket": ticket}
            self.node_ids[pid] = {n["name"]: str(uuid.uuid4()) for n in ticket["nodes"]}
        lab["state"] = "STOPPING" if action == "stop" else "DEPLOYING"
        op = {"operation_id": str(uuid.uuid4()), "project_id": pid, "action": action, "status": "RUNNING",
              "fingerprint": fingerprint, "remaining": self.op_polls, "error": None}
        self.ops[op["operation_id"]] = op
        self.keys[key] = op["operation_id"]
        self.timeline.append(("slot:" + action, self.address))
        return self._json(202, self._present(op))

    def restart(self):
        """Slot service restart: running operations fail, READY labs become FAILED."""
        for op in self.ops.values():
            if op["status"] == "RUNNING":
                op.update(status="FAILED", error={"code": "SERVICE_RESTART", "message": "Interrupted"})
                self.labs[op["project_id"]]["state"] = "FAILED"
        for lab in self.labs.values():
            if lab["state"] not in ("STOPPED", "FAILED"):
                lab["state"] = "FAILED"

    def handle(self, request: httpx.Request):
        path = request.url.path
        self.requests.append((request.method, path, request.headers.get("Idempotency-Key")))
        if not self.reachable:
            raise httpx.ConnectError("refused", request=request)
        if path == "/healthz":
            return self._json(200 if self.healthy else 503, {"status": "ok"})
        assert request.headers["Authorization"] == "Bearer " + "a" * 40
        if path == "/v1/readyz":
            self.timeline.append(("slot:readyz", self.ready))
            return self._json(200 if self.ready else 503,
                              {"status": "ready" if self.ready else "not_ready",
                               "checks": {"gns3": {"ok": self.ready}}})
        key = request.headers.get("Idempotency-Key")
        if path == "/v1/labs/deploy" and request.method == "POST":
            ticket = json.loads(request.content)
            return self._submit("deploy", ticket["project_id"], ticket, key)
        m = re.fullmatch(r"/v1/labs/([0-9a-f-]+)/(resume|stop|status|topology)", path)
        if m and request.method == "POST":
            return self._submit(m.group(2), m.group(1), None, key)
        if m and m.group(2) == "status":
            lab = self.labs.get(m.group(1))
            if not lab:
                return self._json(404, {"detail": "Unknown managed laboratory"})
            return self._json(200, {"project_id": lab["project_id"], "state": lab["state"], "error": None})
        if m and m.group(2) == "topology":
            lab = self.labs[m.group(1)]
            ids = self.node_ids[m.group(1)]
            status = "started" if lab["state"] == "READY" else "stopped"
            nodes = [{"node_id": ids[n["name"]], "name": n["name"], "node_type": n["kind"], "x": 0, "y": 0,
                      "console_type": "telnet", "status": status, "console": 5000,
                      "properties": {"options": "-chardev socket,path=/opt/gns3/qga/x.sock"}}
                     for n in lab["ticket"]["nodes"]]
            return self._json(200, {"nodes": nodes, "links": []})
        m = re.fullmatch(r"/v1/operations/([0-9a-f-]+)", path)
        if m:
            op = self.ops.get(m.group(1))
            if not op:
                return self._json(404, {"detail": "Unknown operation"})
            if op["status"] == "RUNNING":
                if op["remaining"] > 0:
                    op["remaining"] -= 1
                else:
                    lab = self.labs[op["project_id"]]
                    if self.fail_next:
                        op.update(status="FAILED", error=self.fail_next)
                        lab["state"] = "FAILED"
                        self.fail_next = None
                    else:
                        op["status"] = "SUCCEEDED"
                        lab["state"] = "STOPPED" if op["action"] == "stop" else "READY"
            body = self._present(op)
            body.update(stage=None, progress=50 if op["status"] == "RUNNING" else 100, error=op["error"])
            return self._json(200, body)
        return self._json(404, {"detail": "Not Found"})


class FakeGateway:
    def __init__(self):
        self.requests = []

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, request):
        assert request.headers["X-Internal-Key"] == "gw-key"
        body = json.loads(request.content)
        self.requests.append(body)
        return httpx.Response(200, json={"ticket": "t-" + str(len(self.requests)), "expires_in": 30})


class MultiTransport(httpx.AsyncBaseTransport):
    """Route slot requests by host to the right FakeSlot."""

    def __init__(self, slots):
        self.slots = {s.address: s for s in slots}

    async def handle_async_request(self, request):
        return self.slots[request.url.host].handle(request)
