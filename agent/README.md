# Lab Node Agent — Proxmox LXC + CephFS

Small **privileged helper** for each Proxmox VE cluster node. **mTLS required**. The standard Proxmox API with its limited-permission token should still handle cloning, networks, CPU/RAM, resource discovery and normal cluster orchestration.

This helper intentionally accepts **no shell commands, absolute source paths, destination mount paths, VLAN/MAC/IP, or sudo requests** from the client. It operates only on stopped/started LXC guests already on the target node, in the configured VMID range, **tagged `labslot` and `unprivileged: 1`**.

## Node installation

Requires Proxmox VE, Python 3.10+ with `python3-venv`, working CephFS mounted exactly at `/mnt/pve/cfs` (`LAB_CEPH_ROOT`) on every node, `findmnt`, and outbound access to install `fastapi`, `pydantic`, `uvicorn` into a local venv (or use an internal PyPI mirror).

1. **On a secure admin system**, create a test PKI:

   ```bash
   ./make-demo-pki.sh pve1.lab.local=10.0.0.11 pve2.lab.local=10.0.0.12
   ```

   The sample generator is for a lab deployment; for production use your managed PKI, revocation and rotation processes. Never distribute either CA's *private key*. Use dedicated client and server CAs.

2. **On each node** prepare certificates (example for `pve2`):

   ```bash
   install -d -m 0700 /etc/lab-node-agent
   install -m 0600 pve2.lab.local.key /etc/lab-node-agent/server.key
   install -m 0644 pve2.lab.local.crt /etc/lab-node-agent/server.crt
   install -m 0644 manager-client-ca.crt /etc/lab-node-agent/manager-ca.crt
   ```

3. Copy `agent.py`, `requirements.txt`, `lab-node-agent.service`, `install-node.sh` to the node (e.g. `/root/node-agent/`) and run:

   ```bash
   apt-get install python3-venv
   cd /root/node-agent
   ./install-node.sh
   ```

4. In the service unit, set `LAB_VMID_MIN`, `LAB_VMID_MAX` to an ID range **reserved exclusively for lab slots**. Reload and restart:

   ```bash
   systemctl daemon-reload && systemctl restart lab-node-agent
   ```

5. Restrict TCP/8443 in the **host firewall** to the trusted Lab Manager IP(s) only. Protect server certificate keys. Make sure Lab Manager verifies the node certificate and connects by DNS name matching its SAN.

6. Mark the guest before first use (example guest 2201, on the correct node):

   ```bash
   pct set 2201 --tags labslot
   pct config 2201 | grep -E '^(tags|unprivileged):'
   ```

   Guest must have `unprivileged: 1`. This agent intentionally rejects privileged guests. The guest must be **stopped** when attaching mount points.

7. On CephFS pre-create the expected dirs with correct UID/GID/ACL policy; `agent.py` will NOT create arbitrary source folders; the student leaf may instead be provisioned with the endpoint below:

   ```bash
   mkdir -p /mnt/pve/cfs/gns3-ro/slot /mnt/pve/cfs/gns3-rw/students/student42
   ```

   `students` parent must be admin-controlled: a student must NOT be allowed to replace/rename their directory or inject symlinks into the path. Ensure correct container UID mappings for writable data.

## API endpoints

All endpoints require a valid **client TLS certificate** signed by `manager-client-ca`; request body is strict JSON (unknown fields fail validation).

| Method | Endpoint | Behavior |
|---|---|---|
| PUT | `/students/{student_key}` | Ensure a student directory with configured mapped gns3 ownership |
| GET | `/health` | Liveness (not storage health) |
| GET | `/slots/{vmid}/status` | Running or stopped; only tagged/approved local LXC |
| PUT | `/slots/{vmid}/mounts` | Attach approved CephFS paths while stopped |
| POST | `/slots/{vmid}/start` | Attach approved CephFS paths, start CT, execute fixed guest actions |
| POST | `/slots/{vmid}/stop` | Graceful shutdown or forced stop |

The request body for `POST /slots/2201/start`:

```json
{
  "student_key": "student42",
  "code_revision": null,
  "data_mode": "rw",
  "actions": ["init_slot"]
}
```

Mounts always have host path → guest path mapping:

- `mp0`: `/mnt/pve/cfs/gns3-ro/slot` (or `.../slot/<code_revision>`) → `/srv/gns3-ro/slot`, always **read-only**.
- `mp1`: `/mnt/pve/cfs/gns3-rw/students/<student_key>` → `/opt/gns3/projects`, **read-only** when `data_mode=ro`, or **read-write** when `data_mode=rw`.
- `mp2` (only when `LAB_IMAGES_DIR` is set): `$LAB_CEPH_ROOT/$LAB_IMAGES_DIR` → `/opt/gns3/images`,
  always **read-only**: the GNS3 images shared by all slots (`QEMU/`, `IOU/`, firmware). Unset,
  `mp2` is left alone (images in the slot's rootfs or on an administrator's mount point).

Host sources are `$LAB_CEPH_ROOT/$LAB_CODE_DIR` and `$LAB_CEPH_ROOT/$LAB_STUDENTS_DIR`
(unit defaults `/mnt/pve/cfs`, `gns3-ro/slot`, `gns3-rw/students`): the repository checkout
lives in `gns3-ro/` (its `slot/` is the code), student data in the separate `gns3-rw/` tree,
both on the one CephFS mounted at `LAB_CEPH_ROOT`. Both directories are relative paths of safe
names; an invalid value stops the service at startup. The Manager's `LAB_CEPH_ROOT`,
`LAB_CODE_DIR`, `LAB_STUDENTS_DIR` must be the same.

Only one safe path component may be supplied for `student_key` and `code_revision`: letters, numbers, `_` or `-`, up to 64 chars. The mount roots come from the administrator's unit (below), guest paths, mount indices, and the CephFS mount check are built into the agent. To add other approved directory classes, extend the server's explicit policy, not an arbitrary `source` field.

`PUT /slots/{vmid}/mounts` accepts the same fields except `actions`.

Graceful stop:

```json
{"force": false}
```

Force stop:

```json
{"force": true}
```

### Root actions at startup

`actions` are **named guest actions**, invoked by `pct exec` as root *inside the container after it starts*. They are **not arbitrary scripts executed as host root**:

- `init_slot` → `/usr/local/sbin/lab-slot-init` (fixed script inside the **guest**, not on the host; sample in `guest/lab-slot-init`)
- `daemon_reload` → `systemctl daemon-reload`
- `start_slot_service` → `systemctl start lab-slot.service`
- `restart_slot_service` → `systemctl restart lab-slot.service`

`lab-slot.service` must exist inside the guest for service actions to succeed. For `init_slot`, install the supplied `guest/lab-slot-init` (or your own audited replacement) inside the LXC at `/usr/local/sbin/lab-slot-init`, executable by root. To add a different operation, review and hard-code its argv on the node in `GUEST_ACTIONS`; don't provide raw shell text or arbitrary argv through HTTP. A privileged guest is deliberately rejected.

### Curl commands with mTLS

```bash
curl --cert /etc/lab-manager/manager.crt \
  --key /etc/lab-manager/manager.key \
  --cacert /etc/lab-manager/node-server-ca.crt \
  -H 'Content-Type: application/json' \
  -d '{"student_key":"student42","data_mode":"ro","actions":["daemon_reload"]}' \
  https://pve2.lab.local:8443/slots/2201/start

curl --cert /etc/lab-manager/manager.crt \
  --key /etc/lab-manager/manager.key \
  --cacert /etc/lab-manager/node-server-ca.crt \
  -H 'Content-Type: application/json' \
  -d '{"force":false}' \
  https://pve2.lab.local:8443/slots/2201/stop
```

## Important operational limitations

- The manager must address the **agent on the node that actually hosts the LXC**. Unlike the stock Proxmox API, this service does not proxy calls across the cluster.
- `start` is deliberately **not** allowed on already-running LXC; live changes to mounts are not attempted. The HTTP call blocks until mount assignment, `pct start`, and guest actions complete, possibly more than a minute.
- Each VMID is serialized with `flock` on that host, but this lock does **not** protect against simultaneous modifications by external `pct` processes or Proxmox API requests. Manager must coordinate lifecycle and clone operations.
- If a start request times out or a guest action fails, the guest **may already be running**. Read status before retrying; mounts are not rolled back automatically.
- CephFS bind mount `shared=1` marks it as shared to Proxmox; it does NOT mount CephFS on the destination node or configure file permissions. Check UID mapping and the same paths on all nodes.
- `ro=1` is enforced by LXC mount policy, while `rw` is subject to CephFS permissions. Neither flag by itself solves UID/GID ownership.
- mTLS authenticates the manager's certificate, but cannot protect a root service from bugs. Install updates, lock down firewall, rotate certs, monitor logs and audit changes. Place this service only on a trusted management network.
- For production, pin tested package versions and run staging tests on your installed PVE release; `pct` output and flags can change between major releases.

## Smoke tests (run before enabling access for users)

```bash
cd agent
python3 -m venv .venv
.venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest -q tests
journalctl -u lab-node-agent -f
```

Test: no client certificate → TLS handshake rejected; valid certificate → health OK; wrong VMID/tag/privileged guest → rejected; traversal strings like `../etc`, unexpected JSON fields or arbitrary action names → rejected (422). During rollout, use an empty test slot, not a student's active lab.

## Repository integration and trust boundary

Lab Manager → HTTPS/mTLS → agent on the selected physical Proxmox node → local
`pct` → unprivileged LXC. No SSH is used. `manager-example.py` is standalone;
existing repository components are untouched. There is no `/config` endpoint.
Run installation from `agent/`; installer paths are relative to the script.

The supplied systemd unit is the supported entry point: it requires client
certificates at the TLS handshake and disables proxy headers. Do not expose
`agent:app` through plain HTTP or a TLS-terminating proxy. Trust a dedicated CA
issuing client certificates exclusively to authorized Managers. Server CA and
SAN verification are mandatory; never use `verify=False` or curl `-k`.
Revocation is not implemented: rotate trust files and restart to remove trust.

Every source ancestor must be administrator-owned, without group/other write
access or extended POSIX access ACLs. Sticky writable ancestors are rejected too. A student directory itself may belong to mapped guest UIDs, but its
parent `students/` must remain root-controlled. `slot/` must be root-controlled
when selecting a revision. Walking uses directory FDs and `O_NOFOLLOW`.
ACL inspection failures cause rejection (a filesystem without ACL support has
no POSIX access ACL). Extended ancestor ACLs are conservatively rejected.
CephFS administrators and host root are trusted. Coordinate administrative
path, mount, ACL and Proxmox configuration changes externally: `pct` resolves
paths later, so remote administrative mutations cannot be made atomic with this
check. Do not deploy if untrusted users can rename ancestors or administer Ceph.

Repeated mounts while stopped reapply the specification. Repeated start on a
running container returns 409 without rerunning actions. Repeated stop succeeds
without another shutdown. Per-slot conflicts return 409. After command timeouts
or action failures the guest may already be running; inspect status and logs.
There is no automatic rollback or retry of guest actions.

## Diagnostics and validation

`journalctl -u lab-node-agent -f` shows operation argv, errors and timeouts.
`systemctl status lab-node-agent` checks service state;
`systemctl restart lab-node-agent` reloads code and certificates. Installation
enables startup at boot and restarts already-running instances on upgrade.
Use `systemctl edit lab-node-agent` for environment overrides, then
`systemctl daemon-reload` and restart.

422: invalid request or unsafe/missing source; 403: VMID/tag/privilege rejection;
404: no local container; 409: state conflict, concurrent operation or pct error;
503: unavailable storage/configuration/tool; 504: command timeout.
`/health` checks process liveness, not storage readiness.

Unit tests mock `pct`. The transport test starts only local temporary Uvicorn
with disposable certificates outside Git, checks valid/missing/untrusted client
certificates, and verifies server CA and hostname checking. No test installs the
service or calls real `pct`. Before production verify on your PVE release:
pct flags/output, pmxcfs parsing, CephFS/findmnt/ACL behavior, UID mappings,
RO/RW enforcement, guest actions, timeouts, service restart and reboot with
CephFS unavailable. These tests do not establish full Proxmox compatibility.

## Student directory provisioning

The Manager calls **PUT `/students/{student_key}`** (no body) before start, using
its usual mTLS credentials. The agent creates only `students/<student_key>` with
mode 0700 and the configured host-side UID/GID of the slot's `gns3` account.
It does not start LXC, invoke guest actions, create parent directories or touch
existing contents. Both first creation and repeats return 200 with
`{"student_key":"student42","ready":true}`. Existing directories must have the
expected owner; otherwise 409 is returned without changing ownership or contents.
Concurrent creation may temporarily return 409 until the creator assigns ownership;
retry safely. Failed provisioning can leave a root-owned empty directory requiring
administrator inspection; the agent never repairs it by recursively chowning.

The administrator creates and protects `/mnt/pve/cfs/gns3-rw/students` and configures:

```ini
# systemctl edit lab-node-agent
[Service]
Environment=LAB_STUDENT_HOST_UID=101000
Environment=LAB_STUDENT_HOST_GID=101000
```

These numbers are examples **only**: if `gns3` is UID/GID 1000 inside an LXC
using the standard 100000 offset, its host IDs are 101000. Determine actual
`gns3` IDs and LXC UID/GID mappings from the trusted slot template before enabling
this endpoint. Never use the host's `gns3` account by name or guess mappings.
Absent/invalid configuration returns 503. All slots using these directories
must share the configured mapping; differing mappings need a separate reviewed
policy. IDs and paths cannot be selected through HTTP. No Manager code is changed.
Parent symlinks, unsafe permissions, ACL inheritance and nested mounts are rejected.

```bash
curl --cert /etc/lab-manager/manager.crt \
  --key /etc/lab-manager/manager.key \
  --cacert /etc/lab-manager/node-server-ca.crt \
  -X PUT https://pve2.lab.local:8443/students/student42
```

## Mount contract with Slot

The destination paths match `slot/run.py`, `slot/lab-slot.service` and Slot's
project lock directory:

| Host source | LXC destination | Access |
|---|---|---|
| `/mnt/pve/cfs/gns3-ro/slot` or `/mnt/pve/cfs/gns3-ro/slot/<code_revision>` | `/srv/gns3-ro/slot` | RO |
| `/mnt/pve/cfs/gns3-rw/students/<student_key>` | `/opt/gns3/projects` | `data_mode` RO/RW |
| `/mnt/pve/cfs/<LAB_IMAGES_DIR>` (optional) | `/opt/gns3/images` | RO |

The code source must contain `run.py`, `release.py`, `main.py` and `venv/`
directly. Build the venv at `/srv/gns3-ro/slot/venv` in an environment compatible
with the slot template (see Slot's README). An old venv built under another
absolute path must be rebuilt. Normal GNS3 project creation needs `data_mode=rw`;
RO is supported for mounting but does not permit writable lab operations.

The LXC template must configure GNS3's project directory to
`/opt/gns3/projects`, have its images under `/opt/gns3/images` (including `QEMU/`
and `IOU/`; with `LAB_IMAGES_DIR` the agent mounts them there), and provision
`/opt/gns3/qga` with appropriate permissions. This agent manages only mp0, mp1
and, with `LAB_IMAGES_DIR`, mp2; it does not provision the QGA path, change GNS3
configuration, install the guest service or token, or move persistent Slot state
from `/var/lib/lab-slot`.

The images directory gets the same source checks as the others (on the CephFS mount, no symbolic
links, root-owned ancestors without group/other write). Inside the unprivileged CT its files show
as `nobody`, so they must be readable by others (`chmod -R go-w,o+rX`), IOL binaries executable
(`chmod 0755 IOU/*.bin`): GNS3 cannot fix modes on a read-only mount. GNS3 also writes
`<image>.md5sum` next to an image it reads; on a read-only mount it logs an error and hashes the
image again every time, so create those files on CephFS once:
`find . -type f ! -name '*.md5sum' -exec sh -c 'md5sum "$1" | cut -d" " -f1 >"$1.md5sum"' _ {} \;`

For previously configured containers: stop the LXC, update the agent, then
reapply mounts or call start. mp0/mp1 destinations are replaced; no student data
is moved or deleted. Check for any manually configured mounts overlapping the
new destinations and coordinate changes through the Manager. Running containers
are never remounted live.

## Coordination with Manager development

[Manager contract review](docs/MANAGER_CONTRACT_REVIEW.md) records questions and
integration requirements against a pinned snapshot of the Manager contract branch.
It distinguishes implemented Agent behavior from proposed extensions.

## Structured errors and guest initialization

Agent policy/operation errors now return
`{"detail":{"code":"SOURCE_UNSAFE","message":"..."}}`. HTTP statuses retain
their meanings; standard FastAPI validation errors remain a list under `detail`.
Clients must use `code` for logic and treat unknown errors conservatively.

| HTTP | Code | Meaning |
|---|---|---|
| 403 | `VMID_OUT_OF_RANGE`, `NOT_LAB_SLOT`, `PRIVILEGED_CT` | Slot policy rejection |
| 404 | `CT_NOT_ON_NODE` | No CT config on this node; rediscover node |
| 409 | `SLOT_BUSY`, `NOT_STOPPED` | Conflicting operation/state; re-read state |
| 409 | `PCT_FAILED` | pct failed; inspect actual CT state |
| 409 | `STUDENT_DIR_OWNER` | Student owner mismatch; no ownership repair |
| 422 | `SOURCE_MISSING`, `SOURCE_UNSAFE`, `INVALID_REQUEST` | Missing/unsafe path or explicit input rejection |
| 503 | `STUDENT_IDS_NOT_CONFIGURED` | Configure mapped host UID/GID; retry alone cannot fix it |
| 503 | `STORAGE_UNAVAILABLE` | Cannot inspect/access storage, ACL or CT configuration |
| 503 | `PCT_UNAVAILABLE` | pct/findmnt cannot execute, or CT status is unrecognized |
| 504 | `PCT_TIMEOUT` | Command timed out; outcome may be unknown |
| 500 | `AGENT_CONFIG_INVALID` | Invalid administrator-defined CephFS root |

A nonzero findmnt exit returns `STORAGE_UNAVAILABLE`, rather than `PCT_FAILED`:
it is a failed storage inspection, not a failed CT mutation. Missing source during
start can occur **after** `pct set` at the second validation; never infer that the
CT config is untouched. `STUDENT_DIR_OWNER` can still occur transiently between
another creator's mkdir and chown, including on another node. This release does
not distinguish that window from a permanent mismatch. Use bounded provisioning
retry if desired; never repair ownership automatically.

Update `/usr/local/sbin/lab-slot-init` **inside the trusted LXC template and existing
guests** from `guest/lab-slot-init` (root:root 0755); updating the host Agent alone
does not update guest files. Guest Python 3 and the unprivileged `gns3` account are
required. The fixed initialization action drops supplementary groups/GID/UID to
`gns3` before creating `/opt/gns3/projects/.slot-locks` (0700), then verifies its
owner/mode and rejects symlinks/non-directories. Existing lock files and student
contents are untouched; no recursive chown is performed. Preparation failure
prevents service restart. Normal start must use `data_mode=rw` and include
`init_slot`; other allowed actions do not prepare lock directories. A failure of
guest init is surfaced by pct as `PCT_FAILED`; the CT may already be running.

[Follow-up to Manager review](docs/MANAGER_REVIEW_FOLLOWUP.md) records the Agent
changes and remaining Manager concurrency/migration questions against commit 45ecd13.
