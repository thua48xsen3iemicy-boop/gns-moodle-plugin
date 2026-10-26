"""Facts of a running lab: reading the slot's collector results and building the plugin's snapshot.

Pure functions; the storage is in store.py, the polling in orchestrator.py. Formats: CONTRACTS.md
2.7 (snapshot for the plugin) and 4.6 (results of the slot), plugin/docs/live-state.md (facts).
"""

import json
from datetime import datetime, timezone

SNAPSHOT_SCHEMA = "gns3lab.facts/1"
MAX_RECORD_BYTES = 256 * 1024  # A collector result larger than this is dropped, not stored
MESSAGE_LIMIT = 200
# Keys of a node that the manager sets itself; a collector's data cannot overwrite them.
NODE_KEYS = {"name", "status", "collected_at", "error"}


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("not a timestamp")
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError("timestamp without a zone")
    return moment.astimezone(timezone.utc)


def iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_slot_facts(snapshot):
    """Records of a `gns3lab.slot-facts/1` document: [(node_id, node_name, collector, record)].

    Liberal in what it takes: a malformed node or record is skipped, the rest is kept. A record is
    {schema, attempted_at, observed_at | None, ok, error | None, data | None} with real timestamps.
    """
    records = []
    nodes = snapshot.get("nodes") if isinstance(snapshot, dict) else None
    if not isinstance(nodes, dict):
        return records
    for node_id, node in nodes.items():
        if not isinstance(node_id, str) or not isinstance(node, dict) or not isinstance(node.get("name"), str):
            continue
        collectors = node.get("collectors")
        if not isinstance(collectors, dict):
            continue
        for collector, raw in collectors.items():
            try:
                record = parse_record(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(collector, str) and collector:
                records.append((node_id, node["name"], collector, record))
    return records


def parse_record(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("schema"), str) or type(raw.get("ok")) is not bool:
        raise ValueError("malformed record")
    error, data = raw.get("error"), raw.get("data")
    if error is not None:
        if not isinstance(error, dict) or not isinstance(error.get("code"), str):
            raise ValueError("malformed error")
        error = {"code": error["code"][:64], "message": str(error.get("message", ""))[:MESSAGE_LIMIT]}
    if raw["ok"] and not isinstance(data, dict):
        raise ValueError("a successful record carries data")
    if data is not None and not isinstance(data, dict):
        raise ValueError("data is an object")
    if not raw["ok"] and error is None:
        error = {"code": "failed", "message": ""}
    if len(json.dumps(data or {})) > MAX_RECORD_BYTES:
        raise ValueError("record too large")
    observed = parse_time(raw["observed_at"]) if raw.get("observed_at") is not None else None
    if raw["ok"] and observed is None:
        raise ValueError("a successful record has observed_at")
    return {
        "schema": raw["schema"][:64],
        "attempted_at": parse_time(raw["attempted_at"]),
        "observed_at": observed,
        "ok": raw["ok"],
        "error": None if raw["ok"] else error,
        "data": data,
    }


def differs(old, new):
    """Whether `new` is a change worth a journal row: other data, other schema, or a collection that
    started or stopped failing (or fails differently). Timestamps and error texts are not."""
    return (
        old["schema"], old["ok"], (old["error"] or {}).get("code"), old["data"],
    ) != (new["schema"], new["ok"], (new["error"] or {}).get("code"), new["data"])


def build_state(rev, rows, statuses, ttl):
    """The `gns3lab.facts/1` document of a session from its stored results.

    statuses: node_id -> 'started' | 'stopped' | 'suspended' for the nodes of a running session; a node
    that is not in it is reported stopped (a finished lab). Nothing stored: rev 0, no nodes.
    """
    by_node = {}
    for row in rows:
        by_node.setdefault(row["node_id"], []).append(row)
    nodes, latest = {}, None
    for node_id, items in sorted(by_node.items()):
        items.sort(key=lambda row: row["collector"])
        node = {}
        for row in items:
            node.update({k: v for k, v in (row["data"] or {}).items() if k not in NODE_KEYS})
        node["name"] = items[0]["node_name"]
        node["status"] = statuses.get(node_id, "stopped")
        observed = [row["observed_at"] for row in items if row["observed_at"] is not None]
        if observed:
            node["collected_at"] = iso(max(observed))
        failing = next((row for row in items if not row["ok"]), None)
        if failing is not None:
            node["error"] = dict(failing["error"] or {"code": "failed", "message": ""})
        nodes[node_id] = node
        newest = max(row["attempted_at"] for row in items)
        latest = newest if latest is None else max(latest, newest)
    state = {"schema": SNAPSHOT_SCHEMA, "rev": rev}
    if latest is not None:
        state["generated_at"] = iso(latest)
    state.update(ttl=ttl, nodes=nodes)
    return state
