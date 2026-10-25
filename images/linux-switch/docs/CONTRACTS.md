# Switch guest API contract v1

Implementation version: 0.1.2. Scope: the API inside the Linux switch VM only.
This document does not change the Slot, Manager or Moodle Plugin wire contracts.

## Transport and trust

QGA executes `/usr/local/sbin/switchctl METHOD --params 'JSON'`. The CLI sends one
UTF-8 JSON line to `/run/switch-api/api.sock`, reads one response and exits.
Alternatively `--params -` reads JSON from stdin for local callers. No HTTP or IP
listener exists. The socket directory is root-only (0700), socket mode is 0600,
and the server checks SO_PEERCRED against its effective UID (root in production).
QGA and local root are trusted administrators, including for VLAN changes.
This is not an authorization boundary against a student with root guest access.

Request (all four fields required, no extra envelope fields):

```json
{"version":1,"id":"request-1","method":"ports","params":{}}
```

Success:

```json
{"version":1,"id":"request-1","ok":true,"observed_at":1792800000.0,"result":{"ports":[]}}
```

Failure:

```json
{"version":1,"id":"request-1","ok":false,"error":{"code":"INVALID_ARGUMENT","message":"Invalid pagination"}}
```

IDs are strings of 1..64 characters. An invalid/unreadable ID is returned as null.
Timestamps are Unix seconds. CLI exit code is 0 on success, 1 on API/transport
failure; argument-parser usage errors use exit code 2 and stderr instead of JSON.
The CLI timeout is 30 seconds. Requests are limited to 64 KiB including newline,
responses to 2 MiB. Kernel command output is limited to 1 MiB, command stderr to
4 KiB and each command to five seconds. At most 16 RPC clients are served at once.
Large FDBs can therefore return RESULT_TOO_LARGE even when pagination is requested.

## Methods

| Method | Parameters | Result |
|---|---|---|
| `status` | `{}` | service_version, bridge, port_count, capture_templates, vlan_last_error |
| `ports` | `{}` | ports: `ip -j -s link show master br0` records |
| `fdb` | optional offset (default 0), limit (default 256, maximum 1024) | entries, total, next_offset or null |
| `vlans` | `{}` | desired policy or null, applied, last_error or null, actual memberships |
| `vlan.apply` | ports map described below | applied=false, state=pending |
| `capture.start` | port, template, optional seconds and packets | capture snapshot |
| `capture.read` | id | capture snapshot; non-consuming |
| `capture.stop` | id | final capture snapshot; idempotent while retained |

Bridge, port, FDB and actual VLAN records retain iproute2's JSON field names.
They can contain additional fields across package updates. FDB records include
local/static entries, not only learned addresses. Consumers must inspect flags
and state, and must not count all records as learned MACs. Pagination is not a
transactional snapshot: MAC learning can change rows between requests.

## VLAN configuration

The bridge always has VLAN filtering enabled and default PVID 0. Until a policy
is supplied, the daemon puts all current ports into access VLAN 1. This preserves
one untagged broadcast domain. It is not transparent forwarding of arbitrary tags.

Example configuration:

```json
{"ports":{"eth0":{"mode":"access","vlan":10},"eth1":{"mode":"trunk","allowed":[10,20],"native":20}}}
```

Supply exactly all current bridge port names, at most 64 ports. Access requires
one VLAN ID in 1..4094. Trunk requires 1..128 distinct allowed VLAN IDs; optional
native must occur in that list. A trunk without native has no PVID and no
untagged egress VLAN. An access port is configured with PVID and untagged egress;
it can also accept an incoming tag matching its VLAN because Linux bridge does
not enforce strict untagged-only ingress in this implementation.

Validated intent is atomically saved to `/etc/switch-api/vlans.json` with mode
0600, then applied by the daemon. Poll `vlans` until applied=true and inspect
actual. This is asynchronous, not a claim that configuration has already taken
effect. If an apply fails, last_error is retained and the saved policy is retried
on the next maintenance pass. BUSY means configuration was not accepted: retry.

Policies bind both interface name and MAC. After a saved policy exists, additional
or replacement cards receive no VLAN membership until a new complete policy is
submitted. Removed ports retain saved intent and regain it if the same name/MAC
returns. Bridge creation with filtering enabled and no default PVID avoids a
transparent forwarding window at boot. No host-side bridge VLAN membership is
assigned, since this guest has no management IP on br0.

Applying a policy removes memberships then adds the desired ones; it is not
atomic across ports and can interrupt traffic. A failed command can leave partial
membership until retry. The daemon reconciles topology changes every two seconds,
without continuous traffic inspection. It owns VLAN configuration; manual changes
or networkd reconfiguration require reapplying the policy/restarting the daemon.
To return to the initial default, remove the saved file and restart the service
from an administrator console; there is no remote reset method in v1.

## Bounded educational capture

```json
{"port":"eth0","template":"arp","seconds":10,"packets":32}
```

Port must be a current br0 member. Templates are `arp`, `vlan`, `ethernet`.
Filters are fixed server-side BPF; arbitrary filters and commands are rejected.
`arp` covers untagged Ethernet/IPv4 ARP and up to two 802.1Q/802.1ad tags.
`vlan` covers frames with outer EtherType 0x8100 or 0x88a8.
`ethernet` observes Ethernet headers and is still explicitly time/packet bounded.

Defaults: 10 seconds, 32 matching packets. Bounds: 1..15 seconds, 1..128 matching
packets, snaplen 128 bytes, tcpdump buffer request 64 KiB. Only ingress is captured
(`-Q in`): an event identifies reception on a port, never inferred egress. No
promiscuous mode, name resolution, payload export, PCAP files or disk capture.
Frames are streamed through a pipe and decoded into header metadata only.

At most two active sessions, at most one on a port, eight retained sessions total.
Completed sessions expire after 120 seconds and can be evicted earlier to admit
another capture. Maximum 64 distinct header events per session; repeats increment
count. Further unique headers are omitted while seen continues to count packets.
All session data disappears on service restart. Polling never starts capture.
No traffic or fewer packets than requested finishes by timeout; this is success,
not evidence of a down port. A tcpdump/startup/link error produces state=failed.

Snapshot fields: id, port, template, seconds, packets, started_at, state
(starting/running/done/failed), reason (null/packet_limit/timeout/stopped/
capture_ended/capture_error), error, seen, events. Each event contains packet,
count, first_at, last_at, wire_bytes and captured_bytes. Length fields describe
the first observation of grouped headers, not accumulated bytes. Packet contains
source/destination MAC, numeric ether_type, up to two tags (tpid, vid, pcp, dei),
and optional decoded Ethernet/IPv4 arp (operation, sender_mac, sender_ip,
target_mac, target_ip). Non-standard or truncated ARP may have no arp object.

Capture timestamps come from PCAP; started_at is the guest clock. Timing across
VMs is not synchronized by this protocol. Kernel drops/traffic missed while the
capture is inactive are not measured. VLAN hardware offload can affect tag
visibility; validate against known tagged traffic on the chosen QEMU NIC model.
The API does not promise to reconstruct every forwarding step or pause time.

## Errors

INVALID_ARGUMENT, METHOD_NOT_FOUND, FORBIDDEN, BUSY, COMMAND_FAILED, TIMEOUT,
RESULT_TOO_LARGE, VLAN_APPLY_FAILED, CAPTURE_BUSY, CAPTURE_NOT_FOUND,
INTERNAL_ERROR. CLIENT_ERROR is generated by switchctl for local transport or
JSON errors. Messages are diagnostic text, not machine-readable identifiers.
The current asynchronous VLAN path exposes failures through vlans.last_error.

Neither request IDs nor capture.start provide retry deduplication. Read operations
are safe to retry. Before retrying a timed-out VLAN write, read desired policy.
Before retrying capture.start, account for a session that may already be running;
it will expire by its hard timeout even if the response was lost.
