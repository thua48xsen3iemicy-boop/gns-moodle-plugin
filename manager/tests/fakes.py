"""In-memory fakes for Proxmox VE, Lab Slot and the console gateway (httpx.MockTransport)."""

import json
import re
import uuid
from urllib.parse import parse_qs, unquote

import httpx


def _form(request):
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


GIB = 1024 ** 3


class FakePve:
    """A cluster of nodes; entry hosts are '<node>:8006'. CTs live on one node and may migrate."""

    def __init__(self, timeline=None, nodes=("pve1", "pve2", "pve3")):
        self.nodes = list(nodes)
        self.node_mem = {n: {"maxmem": 64 * GIB, "mem": 8 * GIB, "maxcpu": 16, "status": "online"} for n in nodes}
        self.timeline = timeline if timeline is not None else []
        self.cts = {}
        self.vms = {}  # QEMU guests, only seen in /cluster/resources: {vmid: {node, status, maxmem, maxcpu}}
        self.tasks = {}
        self.down_hosts = set()  # Entry points refusing connections
        self.task_node_down = set()  # Nodes whose tasks cannot be read (595)
        self.requests = []  # (host, method, path)
        self.task_polls = 1
        self.exit_status = {}  # action -> exitstatus override
        self.responses = {}  # (method, path regex) -> (status, json body) one-shot overrides
        self.counter = 0

    def add_ct(self, vmid, node="pve2", status="stopped", config=None, maxmem=4 * GIB):
        self.cts[vmid] = {"node": node, "status": status, "maxmem": maxmem,
                          "config": dict(config or {"hostname": f"slot{vmid}", "cores": 2, "memory": 4096})}

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
            data = [{"vmid": v, "type": "lxc", "node": ct["node"], "status": ct["status"], "maxmem": ct["maxmem"],
                     "maxcpu": int(ct["config"].get("cores", 2)),
                     "mem": ct.get("mem", 0) if ct["status"] == "running" else 0}
                    for v, ct in self.cts.items()]
            data += [{"vmid": v, "type": "qemu", **vm} for v, vm in self.vms.items()]
            if request.url.params.get("type") != "vm":
                data += [{"type": "node", "node": n, **m} for n, m in self.node_mem.items()]
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
            if "memory" in form:
                ct["maxmem"] = int(form["memory"]) * 1024 * 1024
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


class FakeAgent:
    """Lab Node Agent semantics (status/mounts/start/stop/reset) on top of FakePve's CTs."""

    def __init__(self, pve: FakePve, ceph_root="/mnt/pve/cfs", code_dir="gns3-ro/slot",
                 students_dir="gns3-rw/students"):
        self.pve = pve
        self.timeline = pve.timeline
        self.students = {"placeholder"}  # Existing students/<key> directories
        self.missing_students = set()  # Directories that vanish after provisioning (race)
        self.wrong_owner = set()  # Existing directories with an unexpected owner
        self.student_ids_configured = True
        self.error_codes = True  # True: {"detail": {"code", "message"}} as in docs/AGENT_REQUESTS.md
        self.ceph_root = ceph_root
        self.code_dir = code_dir
        self.students_dir = students_dir
        self.requests = []
        self.busy = False
        self.untagged = set()
        self.start_fails = None  # detail text of a 409 "Proxmox command failed"
        self.lose_start_response = False  # Start happens, then the response is lost
        self.shutdown_hangs = False  # Graceful shutdown leaves the CT running
        self.shutdown_fails_but_stops = False  # 409 from pct shutdown although the CT stopped
        self.fences = {}
        self.park_fails = False  # PUT mounts fails with a pct error
        self.no_snapshot = set()  # Slots without the clean snapshot: reset answers SNAPSHOT_MISSING
        self.reset_fails = False  # pct rollback fails
        self.reset_unsupported = False  # An agent older than 1.2: the route does not exist
        self.uncertain = {}  # vmid -> pct subcommand of the `uncertain` mark: changes and claims get 409
        self.stop_never = False  # pct shutdown and pct stop fail, the CT keeps running
        self.start_times_out = False  # pct exec times out: the CT runs, the agent marks the slot uncertain
        self.start_budget = 480  # start_budget_seconds of GET /capabilities; None: agent older than 1.2.1

    def transport(self):
        return httpx.MockTransport(self.handle)

    def _err(self, status, detail):
        return httpx.Response(status, json={"detail": detail})

    def _mounts(self, ct, body):
        key = body["student_key"]
        if key in self.missing_students or key not in self.students:
            if self.error_codes:
                return self._err(422, {"code": "SOURCE_MISSING", "message": "Student directory missing"})
            return self._err(422, "Required CephFS directory does not exist")
        code = f"{self.ceph_root}/{self.code_dir}" + (f"/{body['code_revision']}" if body.get("code_revision") else "")
        ro = 1 if body.get("data_mode") == "ro" else 0
        ct["config"]["mp0"] = f"{code},mp=/srv/gns3-ro/slot,ro=1,shared=1"  # agent 6a735df
        ct["config"]["mp1"] = f"{self.ceph_root}/{self.students_dir}/{key},mp=/opt/gns3/projects,ro={ro},shared=1"
        return None

    def handle(self, request: httpx.Request):
        node = request.url.host.split(".")[0]
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append((node, request.method, path, body))
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/capabilities":
            if self.start_budget is None:
                return self._err(404, "Not Found")
            return httpx.Response(200, json={"version": "1.2.1", "start_budget_seconds": self.start_budget,
                                             "guest_action_timeout_seconds": 120})
        m = re.fullmatch(r"/students/([A-Za-z0-9][A-Za-z0-9_-]{0,63})", path)
        if m and request.method == "PUT":
            key = m.group(1)
            if not self.student_ids_configured:
                return self._err(503, "Configure mapped gns3 host UID/GID first")
            if key in self.wrong_owner:
                return self._err(409, {"code": "STUDENT_DIR_OWNER", "message": "Existing student directory has a different owner"})
            self.students.add(key)
            self.timeline.append(("agent:student", key))
            return httpx.Response(200, json={"student_key": key, "ready": True})
        m = re.fullmatch(r"/slots/(\d+)/(status|mounts|start|stop|claim|size|reset)", path)
        if not m or (m.group(2) == "reset" and self.reset_unsupported):
            return self._err(404, "Not Found")
        vmid, action = int(m.group(1)), m.group(2)
        ct = self.pve.cts.get(vmid)
        if ct is None or ct["node"] != node:
            return self._err(404, "LXC not found on this node")
        if vmid in self.untagged:
            return self._err(403, "Container is not marked as a lab slot")
        if self.busy:
            return self._err(409, "Another operation on this slot is in progress")
        if vmid in self.uncertain and action != "status":
            return self._err(409, {"code": "COMMAND_UNCERTAIN",
                                   "message": "Previous host command needs operator reconciliation"})
        if action == "claim":
            token = (body["epoch"], body["generation"])
            if token < self.fences.get(vmid, (0, 0)):
                return self._err(409, {"code": "STALE_OWNER", "message": "superseded"})
            self.fences[vmid] = token
            return httpx.Response(200, json={"vmid": vmid, **body})
        if action != "status":
            token = (int(request.headers.get("X-Lab-Epoch", 0)), int(request.headers.get("X-Lab-Generation", 0)))
            if token != self.fences.get(vmid):
                return self._err(409, {"code": "STALE_OWNER", "message": "claim first"})
        if action == "size":
            assert ct["status"] == "stopped"
            ct["config"].update(cores=str(body["cores"]), memory=str(body["memory_mb"]))
            self.timeline.append(("agent:size", vmid))
            return httpx.Response(200, json={"vmid": vmid, **body})
        if action == "status":
            ownership = {"epoch": self.fences.get(vmid, (0, 0))[0], "generation": self.fences.get(vmid, (0, 0))[1]}
            if vmid in self.uncertain:
                ownership.update(uncertain=True, command=self.uncertain[vmid])
            return httpx.Response(200, json={"vmid": vmid, "state": ct["status"], "ownership": ownership})
        if action in ("mounts", "start") and ct["status"] != "stopped":
            return self._err(409, "Slot must be stopped to change mount points")
        if action == "reset":
            if ct["status"] != "stopped":
                return self._err(409, {"code": "NOT_STOPPED", "message": "Slot must be stopped to roll back"})
            if vmid in self.no_snapshot:
                return self._err(422, {"code": "SNAPSHOT_MISSING", "message": "Slot has no 'lab-clean' snapshot"})
            if self.reset_fails:
                return self._err(409, {"code": "PCT_FAILED", "message": "Proxmox command failed: rollback"})
            # The snapshot is taken without the agent's mounts: the rollback takes them away.
            ct["config"].pop("mp0", None)
            ct["config"].pop("mp1", None)
            self.timeline.append(("agent:reset", vmid))
            return httpx.Response(200, json={"vmid": vmid, "state": "stopped", "reset": True,
                                             "snapshot": "lab-clean"})
        if action == "mounts":
            if self.park_fails:
                return self._err(409, "Proxmox command failed: unable to apply mount points")
            err = self._mounts(ct, body)
            if err:
                return err
            self.timeline.append(("agent:mounts", body["student_key"], body["data_mode"]))
            return httpx.Response(200, json={"vmid": vmid, "mounts_configured": True})
        if action == "start":
            err = self._mounts(ct, body)
            if err:
                return err
            if self.start_fails:
                return self._err(409, f"Proxmox command failed: {self.start_fails}")
            ct["status"] = "running"
            self.timeline.append(("agent:start", body["student_key"]))
            if self.start_times_out:
                self.uncertain[vmid] = "exec"
                return self._err(504, {"code": "PCT_TIMEOUT", "command": "exec",
                                       "message": "Proxmox command timed out; check actual container state"})
            if self.lose_start_response:
                self.lose_start_response = False
                raise httpx.ReadTimeout("lost", request=request)
            return httpx.Response(200, json={"vmid": vmid, "state": "running",
                                             "completed_actions": body["actions"]})
        # stop
        if ct["status"] == "stopped":
            return httpx.Response(200, json={"vmid": vmid, "state": "stopped"})
        self.timeline.append(("agent:stop", "force" if body["force"] else "shutdown"))
        if self.stop_never:
            return self._err(409, {"code": "PCT_FAILED", "message": "Proxmox command failed: stop"})
        if not body["force"] and self.shutdown_hangs:
            return self._err(409, "Proxmox command failed: shutdown timed out")
        if not body["force"] and self.shutdown_fails_but_stops:
            ct["status"] = "stopped"
            return self._err(409, "Proxmox command failed: exit code 1")
        ct["status"] = "stopped"
        return httpx.Response(200, json={"vmid": vmid, "state": "stopped"})


class FakeSlot:
    """Lab Slot semantics from slot/api/lifecycle.py, reduced to what the manager relies on.

    projects: registry shared by all fake slots, like the student directories (/opt/gns3/projects).
    Slot 0.4.0 (student_state=True) keeps its lab record there, so any slot continues a project
    another slot created. Slot 0.3 (student_state=False) kept it in its own SQLite: a project known
    in the directory but not to this slot fails ("Provisioning completion is unknown").
    """

    def __init__(self, address, timeline=None, projects=None, student_state=True):
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
        self.projects = projects if projects is not None else {}
        self.stopped_nodes = set()  # Node names reported as stopped in the topology
        self.student_state = student_state
        self.fence = (0, 0)
        self.facts_enabled = False  # Slot 0.6.0 serves /facts; older slots answer 404
        self.deploy_bodies = []  # Bodies of POST /v1/labs/deploy as the slot received them
        self.facts = {}  # project_id -> {node_id: {"name", "collectors": {...}}} served by GET /facts
        self.refreshes = []  # project ids the manager asked to collect now

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
                return self._json(409, {"detail": {"code": "SLOT_OCCUPIED", "project_id": other,
                                                   "message": "Another laboratory owns this slot; stop it first"}})
        lab = self.labs.get(pid)
        record = self.projects.get(pid)
        missing = bool(record and record.get("missing"))
        if lab is None and action == "deploy" and record and record["student_state"] and self.student_state:
            # The record in the student directory: same nodes, nothing recreated.
            lab = self.labs[pid] = {"project_id": pid, "ticket": record["ticket"], "state": "STOPPED"}
            self.node_ids[pid] = record["node_ids"]
        if action != "deploy" and not lab:
            return self._json(404, {"detail": "Unknown managed laboratory"})
        allowed = {"deploy": {None, "READY", "STOPPED", "FAILED"}, "resume": {"READY", "STOPPED", "FAILED"},
                   "stop": {"READY", "STOPPED", "FAILED"}}[action]
        if (lab or {}).get("state") not in allowed:
            return self._json(409, {"detail": {"code": "INVALID_TRANSITION", "message": "no"}})
        if lab and ticket and ticket != lab["ticket"]:
            return self._json(409, {"detail": "Existing project manifest differs"})
        cold = False
        if lab is None:
            cold = record is not None
            lab = self.labs[pid] = {"project_id": pid, "ticket": ticket}
            self.node_ids[pid] = {n["name"]: str(uuid.uuid4()) for n in ticket["nodes"]}
            if record is None:
                self.projects[pid] = {"address": self.address, "ticket": ticket, "node_ids": self.node_ids[pid],
                                      "student_state": self.student_state}
        lab["state"] = "STOPPING" if action == "stop" else "DEPLOYING"
        op = {"operation_id": str(uuid.uuid4()), "project_id": pid, "action": action, "status": "RUNNING",
              "fingerprint": fingerprint, "remaining": self.op_polls, "error": None,
              "fail": {"code": "PROVISIONING_UNKNOWN" if self.student_state else "RuntimeError",
                       "message": "Provisioning completion is unknown; refusing to "
                       "skip or replay commands. Operator reconciliation required"} if cold else
                      {"code": "PROJECT_MISSING", "message": "Saved project is missing"} if missing else None}
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
        if path == "/v1/fence":
            body = json.loads(request.content)
            token = (body["epoch"], body["generation"])
            if token < self.fence:
                return self._json(409, {"detail": {"code": "STALE_OWNER"}})
            self.fence = token
            return self._json(200, body)
        if request.method == "POST":
            token = (int(request.headers.get("X-Lab-Epoch", 0)), int(request.headers.get("X-Lab-Generation", 0)))
            if token != self.fence:
                return self._json(409, {"detail": {"code": "STALE_OWNER"}})
        facts = re.fullmatch(r"/v1/labs/([0-9a-f-]+)/facts(/refresh)?", path)
        if facts and self.facts_enabled:
            if facts.group(2):
                self.refreshes.append(facts.group(1))
                return self._json(202, {"accepted": True})
            return self._json(200, {"schema": "gns3lab.slot-facts/1", "project_id": facts.group(1),
                                    "nodes": self.facts.get(facts.group(1), {})})
        key = request.headers.get("Idempotency-Key")
        if path == "/v1/labs/deploy" and request.method == "POST":
            ticket = json.loads(request.content)
            self.deploy_bodies.append(ticket)
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
                      "console_type": n.get("console", "telnet"),
                      "console": 5900 if n.get("console") == "vnc" else 5000,
                      "status": "stopped" if n["name"] in self.stopped_nodes else status,
                      "ports": [{"name": "Ethernet0", "short_name": "e0", "adapter_number": 0, "port_number": 0}],
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
                    if self.fail_next or op.get("fail"):
                        op.update(status="FAILED", error=self.fail_next or op["fail"])
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
