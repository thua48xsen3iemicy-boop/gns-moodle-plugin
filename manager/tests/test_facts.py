"""Facts of a running lab: slot results -> journal in PostgreSQL -> /sessions/{id}/state (CONTRACTS 2.7, DESIGN 6.7)."""

import copy
import json
import time
from types import SimpleNamespace

from conftest import make_env

from lab_manager.facts import build_state, differs, parse_slot_facts, parse_time

T1, T2, T3 = "2026-10-17T10:00:30Z", "2026-10-17T10:01:00Z", "2026-10-17T10:01:30Z"
DATA = {"interfaces": [{"name": "ens3", "kind": "physical", "admin": "up", "oper": "up",
                        "ipv4": ["10.0.12.1/30"]}],
        "routes": [{"dst": "10.0.12.0/30", "dev": "ens3", "proto": "kernel"}]}


def collected(data=DATA, *, ok=True, error=None, attempted=T1, observed=T1, schema="linux.net/1"):
    record = {"schema": schema, "attempted_at": attempted, "ok": ok, "error": error}
    if observed is not None:
        record["observed_at"] = observed
    if data is not None:
        record["data"] = data
    return record


def slot_node(name, **collectors):
    return {"name": name, "collectors": collectors or {"linux.net": collected()}}


def records(nodes):
    return parse_slot_facts({"schema": "gns3lab.slot-facts/1", "project_id": "p", "nodes": nodes})


# --- parsing and comparing --------------------------------------------------------------------


def test_parse_keeps_good_records_and_skips_bad_ones():
    nodes = {
        "n1": slot_node("PC1"),
        "n2": {"name": "PC2", "collectors": {
            "linux.net": collected(),
            "naive.time": collected(attempted="2026-10-17T10:00:30"),  # no zone
            "no.data": collected(None),  # a success without data
            "bad.error": collected(None, ok=False, error="boom", observed=None),
            "huge": collected({"x": "y" * 300_000}),
        }},
        "n3": {"collectors": {"linux.net": collected()}},  # no name
        "n4": {"name": "PC4", "collectors": "nope"},
        7: slot_node("PC7"),
    }
    parsed = records(nodes)
    assert [(n, name, c) for n, name, c, _ in parsed] == [("n1", "PC1", "linux.net"), ("n2", "PC2", "linux.net")]
    record = parsed[0][3]
    assert record["attempted_at"] == parse_time(T1) and record["ok"] is True and record["error"] is None
    assert parse_slot_facts("x") == [] and parse_slot_facts({"nodes": []}) == []


def test_a_failed_record_keeps_data_and_always_has_an_error():
    (n1, _, _, failed), = records({"n1": slot_node("PC1", **{"linux.net": collected(
        DATA, ok=False, error={"code": "timeout", "message": "slow"}, attempted=T2)})})
    assert failed["ok"] is False and failed["data"] == DATA and failed["observed_at"] == parse_time(T1)
    assert failed["error"] == {"code": "timeout", "message": "slow"}
    (_, _, _, no_error), = records({"n1": slot_node("PC1", **{"linux.net": collected(
        None, ok=False, observed=None)})})
    assert no_error["error"]["code"] == "failed" and no_error["data"] is None and no_error["observed_at"] is None


def test_only_real_changes_differ():
    base = records({"n": slot_node("A")})[0][3]
    later = records({"n": slot_node("A", **{"linux.net": collected(attempted=T2, observed=T2)})})[0][3]
    assert not differs(base, later)  # new timestamps are not a change
    other = copy.deepcopy(DATA)
    other["interfaces"][0]["oper"] = "down"
    assert differs(base, records({"n": slot_node("A", **{"linux.net": collected(other)})})[0][3])
    assert differs(base, records({"n": slot_node("A", **{"linux.net": collected(schema="linux.net/2")})})[0][3])
    failing = records({"n": slot_node("A", **{"linux.net": collected(
        DATA, ok=False, error={"code": "timeout", "message": "a"})})})[0][3]
    assert differs(base, failing)
    same_failure = records({"n": slot_node("A", **{"linux.net": collected(
        DATA, ok=False, error={"code": "timeout", "message": "other words"}, attempted=T3)})})[0][3]
    assert not differs(failing, same_failure)
    assert differs(failing, records({"n": slot_node("A", **{"linux.net": collected(
        DATA, ok=False, error={"code": "unreachable", "message": "a"})})})[0][3])


def row(node_id, collector, data, *, name="PC1", ok=True, error=None, observed=T1, attempted=T1):
    return {"node_id": node_id, "node_name": name, "collector": collector, "data": data, "ok": ok,
            "error": error, "observed_at": parse_time(observed) if observed else None,
            "attempted_at": parse_time(attempted)}


def test_live_l1_preserves_interfaces_and_carries_its_own_freshness():
    saved = {"interfaces": [{"name": "Ethernet0/0", "admin": "down", "ipv4": ["10.0.0.1/24"]}]}
    live = {"l1_ports": {"ttl": 15, "ports": [{"port": {"adapter": 0, "port": 0},
                                               "state": "up", "reason": "both_responding"}]}}
    original = copy.deepcopy(live)
    rows = [row("sw", "ios.config", saved), row("sw", "iol.l1", live, observed=T2, attempted=T2)]
    state = build_state(1, rows, {"sw": "started"}, 90)["nodes"]["sw"]
    assert state["interfaces"] == saved["interfaces"]
    assert state["l1_ports"] == {**live["l1_ports"], "ok": True, "observed_at": T2}
    assert live == original  # assembling a snapshot must not mutate stored collector data
    rows[1].update(ok=False, error={"code": "failed", "message": "L1 unavailable"})
    state = build_state(2, rows, {"sw": "started"}, 90)["nodes"]["sw"]
    assert state["l1_ports"]["ok"] is False and "error" not in state
    assert state["interfaces"] == saved["interfaces"]  # failure is scoped to L1, not the saved config
    first = collected(live, schema="iol.l1/1")
    heartbeat = collected(live, schema="iol.l1/1", observed=T2, attempted=T2)
    assert not differs(first, heartbeat)


async def test_l1_sessions_poll_fast_without_changing_guest_collection_period():
    from lab_manager.orchestrator import Orchestrator

    snapshot = {"schema": "gns3lab.slot-facts/1", "nodes": {"sw": slot_node("SW1", **{
        "iol.l1": collected({"l1_ports": {"ttl": 15, "ports": []}}, schema="iol.l1/1")})}}
    calls = []

    async def read(_pid):
        return snapshot

    async def store(_sid, rows):
        calls.extend(rows)

    service = SimpleNamespace(_facts_next={}, _facts_refreshed={}, _slot=lambda _: SimpleNamespace(facts=read),
                              settings=SimpleNamespace(facts_poll_interval=15),
                              store=SimpleNamespace(record_facts=store))
    before = time.monotonic()
    await Orchestrator._poll_facts(service, {"id": "s", "project_id": "p"})
    assert before + 3 <= service._facts_next["s"] <= time.monotonic() + 3
    assert calls[0][2] == "iol.l1"


def test_build_state_merges_collectors_and_marks_failures():
    rows = [
        row("n1", "linux.net", DATA, observed=T1, attempted=T1),
        row("n1", "other", {"vlans": [{"id": 10}], "name": "hijack", "status": "x", "error": 1},
            observed=T2, attempted=T3, ok=False, error={"code": "timeout", "message": "slow"}),
        row("n2", "linux.net", None, name="PC2", ok=False, observed=None, attempted=T2,
            error={"code": "unreachable", "message": "no channel"}),
    ]
    state = build_state(5, rows, {"n1": "started", "n2": "suspended"}, 90)
    assert state["schema"] == "gns3lab.facts/1" and state["rev"] == 5 and state["ttl"] == 90
    assert state["generated_at"] == T3
    n1, n2 = state["nodes"]["n1"], state["nodes"]["n2"]
    assert n1["name"] == "PC1" and n1["status"] == "started"  # a collector cannot overwrite these
    assert n1["interfaces"] == DATA["interfaces"] and n1["vlans"] == [{"id": 10}]
    assert n1["collected_at"] == T2 and n1["error"] == {"code": "timeout", "message": "slow"}
    assert n2 == {"name": "PC2", "status": "suspended",
                  "error": {"code": "unreachable", "message": "no channel"}}  # never collected: no collected_at


def test_switch_net_replaces_the_interfaces_of_linux_net_and_keeps_its_routes():
    ports = [{"name": "ens3", "kind": "physical", "admin": "up", "oper": "up", "mac": "0c:00:00:00:00:01",
              "switchport": {"mode": "access", "vlan": 10}},
             {"name": "ens4", "kind": "physical", "admin": "up", "oper": "up", "mac": "0c:00:00:00:00:02",
              "switchport": {"mode": "trunk", "allowed": [10, 20], "native": None}}]
    # Rows arrive in any order: the precedence comes from the collector names, not from the storage.
    rows = [row("sw", "switch.net", {"interfaces": ports}, name="SW1"),
            row("sw", "linux.net", DATA, name="SW1")]
    node = build_state(1, rows, {"sw": "started"}, 90)["nodes"]["sw"]
    assert node["interfaces"] == ports  # replaced as a whole, not merged by name
    assert node["routes"] == DATA["routes"]
    assert build_state(1, rows[::-1], {"sw": "started"}, 90) == build_state(1, rows, {"sw": "started"}, 90)


def test_switch_fdb_adds_the_mac_table_next_to_the_ports_of_switch_net():
    ports = [{"name": "ens3", "kind": "physical", "admin": "up", "oper": "up", "mirror": True,
              "switchport": {"mode": "access", "vlan": 10}}]
    table = {"mac_table": {"entries": [{"mac": "52:54:00:aa:00:01", "iface": "ens3", "vlan": 10}],
                           "truncated": False}}
    rows = [row("sw", "switch.fdb", table, name="SW1"),
            row("sw", "switch.net", {"interfaces": ports}, name="SW1"),
            row("sw", "linux.net", DATA, name="SW1")]
    node = build_state(1, rows, {"sw": "started"}, 90)["nodes"]["sw"]
    assert node["mac_table"] == table["mac_table"]  # the field passes through as it is
    assert node["interfaces"] == ports and node["routes"] == DATA["routes"]  # and takes nothing from the others
    assert build_state(1, rows[::-1], {"sw": "started"}, 90) == build_state(1, rows, {"sw": "started"}, 90)
    # A table that could not be read this time is the error of the node, and the last known table stays.
    failing = row("sw", "switch.fdb", table, name="SW1", ok=False, error={"code": "failed", "message": "BUSY"})
    node = build_state(2, [failing, *rows[1:]], {"sw": "started"}, 90)["nodes"]["sw"]
    assert node["error"] == {"code": "failed", "message": "BUSY"} and node["mac_table"] == table["mac_table"]


def test_a_new_lesson_of_the_switch_is_a_journal_row_of_the_table_only():
    # The reason for a collector of its own: the manager journals the whole `data` of a collector on any change.
    quiet = {"entries": [{"mac": "52:54:00:aa:00:01", "iface": "ens3", "vlan": 10}], "truncated": False}
    learned = {"entries": [*quiet["entries"], {"mac": "52:54:00:aa:00:02", "iface": "ens4", "vlan": 10}],
               "truncated": False}
    ports = {"interfaces": DATA["interfaces"]}

    def stored(table):
        nodes = {"sw": slot_node("SW1", **{
            "switch.net": collected(ports, schema="switch.net/1"),
            "switch.fdb": collected({"mac_table": table}, schema="switch.fdb/1"),
        })}
        return {collector: record for _, _, collector, record in records(nodes)}

    before, after = stored(quiet), stored(learned)
    assert differs(before["switch.fdb"], after["switch.fdb"])
    assert not differs(before["switch.net"], after["switch.net"])  # the ports are not copied into the journal
    assert not differs(before["switch.fdb"], stored(quiet)["switch.fdb"])


def test_build_state_of_a_lab_without_facts_and_of_a_stopped_one():
    assert build_state(0, [], {}, 90) == {"schema": "gns3lab.facts/1", "rev": 0, "ttl": 90, "nodes": {}}
    stopped = build_state(2, [row("n1", "linux.net", DATA)], {}, 90)
    assert stopped["nodes"]["n1"]["status"] == "stopped"  # a finished lab: nothing is running


# --- the journal ------------------------------------------------------------------------------


async def started(env, user_id=42):
    r = await env.client.post("/api/v1/sessions", json={"user_id": user_id, "instance_id": 1, "lab_ref": "cisco-3x2"})
    sid = r.json()["session_id"]
    await env.run()
    s = await env.services.store.session(sid)
    assert s["status"] == "running"
    slot = env.slots[s["slot_vmid"]]
    return sid, slot, str(s["project_id"]), slot.node_ids[str(s["project_id"])]


async def log_of(env, sid):
    async with env.services.pool.connection() as conn:
        cur = await conn.execute("SELECT * FROM session_facts_log WHERE session_id = %s ORDER BY id", (sid,))
        return await cur.fetchall()


async def test_the_journal_gets_a_row_only_for_a_change(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        sid, _slot, _pid, ids = await started(env)
        store, n1, n2 = env.services.store, "n1", "n2"
        assert await store.record_facts(sid, records({n1: slot_node("PC1")})) == 1
        stored = await store.facts_rows(sid)
        assert len(stored) == 1 and stored[0]["data"] == DATA and stored[0]["node_name"] == "PC1"
        assert len(await log_of(env, sid)) == 1

        # The same facts, collected again: only the timestamps move.
        later = {n1: slot_node("PC1", **{"linux.net": collected(attempted=T2, observed=T2)})}
        assert await store.record_facts(sid, records(later)) == 1
        stored = await store.facts_rows(sid)
        assert stored[0]["observed_at"] == parse_time(T2) and len(await log_of(env, sid)) == 1

        changed = copy.deepcopy(DATA)
        changed["interfaces"][0]["ipv4"] = ["10.0.12.9/30"]
        assert await store.record_facts(sid, records({n1: slot_node("PC1", **{"linux.net": collected(
            changed, attempted=T3, observed=T3)})})) == 2
        log = await log_of(env, sid)
        assert [r["rev"] for r in log] == [1, 2] and log[1]["data"] == changed and log[0]["data"] == DATA

        # A collection that fails keeps the last known data and is journalled once.
        down = {"code": "unreachable", "message": "channel is backing off"}
        failing = lambda message, at: {n1: slot_node("PC1", **{"linux.net": collected(  # noqa: E731
            changed, ok=False, error={"code": "unreachable", "message": message}, attempted=at, observed=T3)})}
        assert await store.record_facts(sid, records(failing(down["message"], "2026-10-17T10:02:00Z"))) == 3
        assert await store.record_facts(sid, records(failing("still the same", "2026-10-17T10:02:30Z"))) == 3
        stored = (await store.facts_rows(sid))[0]
        assert stored["ok"] is False and stored["data"] == changed and stored["observed_at"] == parse_time(T3)
        assert stored["error"]["message"] == "still the same"  # the latest words, no new row
        assert len(await log_of(env, sid)) == 3

        # Recovery is a change again; two nodes changing in one read share one rev.
        assert await store.record_facts(sid, records({
            n1: slot_node("PC1", **{"linux.net": collected(changed, attempted="2026-10-17T10:03:00Z",
                                                          observed="2026-10-17T10:03:00Z")}),
            n2: slot_node("PC2"),
        })) == 4
        assert [r["rev"] for r in (await log_of(env, sid))[3:]] == [4, 4]
        assert (await store.session(sid))["facts_rev"] == 4

        assert await store.record_facts("s-0000000000000000", records({n1: slot_node("PC1")})) is None
        assert ids  # the session ran with a real slot topology


async def test_facts_go_away_with_their_session(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        async with env.services.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT conrelid::regclass::text AS tbl, confdeltype FROM pg_constraint"
                " WHERE contype = 'f' AND confrelid = 'sessions'::regclass AND conrelid::regclass::text LIKE %s",
                ("session_facts%",),
            )
            rules = {r["tbl"]: r["confdeltype"] for r in await cur.fetchall()}
        assert rules == {"session_facts": "c", "session_facts_log": "c"}  # ON DELETE CASCADE


# --- polling and the API ----------------------------------------------------------------------


def facts_of(ids, name, data=DATA, **options):
    node_id = ids[name]
    return {node_id: slot_node(name, **{"linux.net": collected(data, **options)})}


async def state(env, sid, **params):
    return await env.client.get(f"/api/v1/sessions/{sid}/state", params=params)


async def test_the_state_follows_the_slot(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        for slot in env.slots.values():
            slot.facts_enabled = True
        sid, slot, pid, ids = await started(env)
        s = (await env.client.get(f"/api/v1/sessions/{sid}")).json()
        assert s["features"] == ["consoles", "facts"]  # the slot answered: facts are on offer

        r = await state(env, sid)
        assert r.status_code == 200
        assert r.json() == {"schema": "gns3lab.facts/1", "rev": 0, "ttl": 90, "nodes": {}}

        name = next(iter(ids))
        slot.facts[pid] = facts_of(ids, name)
        env.orch._facts_next.clear()  # the next tick reads the slot
        await env.run()
        body = (await state(env, sid)).json()
        assert body["rev"] == 1 and body["generated_at"] == T1 and body["ttl"] == 90
        node = body["nodes"][ids[name]]
        assert node["name"] == name and node["status"] == "started"
        assert node["interfaces"] == DATA["interfaces"] and node["routes"] == DATA["routes"]
        assert node["collected_at"] == T1 and "error" not in node

        # since_rev: nothing new is 304 without a body; an older rev gets the snapshot.
        r = await state(env, sid, since_rev=1)
        assert r.status_code == 304 and r.content == b""
        assert (await state(env, sid, since_rev=0)).status_code == 200
        for bad in ("-1", "x"):
            r = await env.client.get(f"/api/v1/sessions/{sid}/state", params={"since_rev": bad})
            assert r.status_code == 400 and r.json()["error"] == "invalid_request"

        # The same facts again are not a new rev; a failed collection is shown as such.
        slot.facts[pid] = facts_of(ids, name, attempted=T2, observed=T2)
        env.orch._facts_next.clear()
        await env.run()
        assert (await state(env, sid, since_rev=1)).status_code == 304
        slot.facts[pid] = facts_of(ids, name, ok=False, attempted=T3, observed=T2,
                                   error={"code": "timeout", "message": "guest did not answer"})
        env.orch._facts_next.clear()
        await env.run()
        body = (await state(env, sid)).json()
        node = body["nodes"][ids[name]]
        assert body["rev"] == 2 and node["error"]["code"] == "timeout" and node["interfaces"]  # last known facts stay
        assert node["collected_at"] == T2


async def test_the_last_state_stays_after_the_lab_stops(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        for slot in env.slots.values():
            slot.facts_enabled = True
        sid, slot, pid, ids = await started(env)
        name = next(iter(ids))
        slot.facts[pid] = facts_of(ids, name)
        env.orch._facts_next.clear()
        await env.run()
        assert (await state(env, sid)).json()["rev"] == 1

        # How the lab looked just before the stop is read once more, whatever the poll interval.
        changed = copy.deepcopy(DATA)
        changed["interfaces"][0]["oper"] = "down"
        slot.facts[pid] = facts_of(ids, name, changed, attempted=T3, observed=T3)
        assert (await env.client.post(f"/api/v1/sessions/{sid}/stop")).status_code == 200
        await env.run()
        s = (await env.client.get(f"/api/v1/sessions/{sid}")).json()
        assert s["status"] == "stopped" and s["features"] == []
        body = (await state(env, sid)).json()
        assert body["rev"] == 2 and body["nodes"][ids[name]]["interfaces"] == changed["interfaces"]
        assert body["nodes"][ids[name]]["status"] == "stopped" and body["nodes"][ids[name]]["collected_at"] == T3
        assert env.orch._facts_next.get(sid) is None  # nothing left in the memory of the leader


async def test_refresh_is_passed_to_the_slot_and_merged(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        for slot in env.slots.values():
            slot.facts_enabled = True
        r = await env.client.post("/api/v1/sessions", json={"user_id": 42, "instance_id": 1, "lab_ref": "cisco-3x2"})
        sid = r.json()["session_id"]
        r = await env.client.post(f"/api/v1/sessions/{sid}/state/refresh")
        assert r.status_code == 409 and r.json()["error"] == "session_not_running"  # still queued
        await env.run()
        slot = env.slots[(await env.services.store.session(sid))["slot_vmid"]]

        r = await env.client.post(f"/api/v1/sessions/{sid}/state/refresh")
        assert r.status_code == 202 and r.content == b""
        first = (await env.services.store.session(sid))["facts_refresh_at"]
        assert first is not None
        again = await env.client.post(f"/api/v1/sessions/{sid}/state/refresh")
        assert again.status_code == 202
        assert (await env.services.store.session(sid))["facts_refresh_at"] == first  # within 10 s: merged

        await env.run()
        assert len(slot.refreshes) == 1  # one request to the slot for the two of the plugin
        await env.run()
        assert len(slot.refreshes) == 1  # and it is not repeated

        assert (await env.client.post("/api/v1/sessions/s-0000000000000000/state/refresh")).status_code == 404
        assert (await state(env, "s-0000000000000000")).status_code == 404


async def test_a_session_of_another_site_is_not_visible(tmp_path, dsn):
    import httpx

    async with make_env(tmp_path, dsn) as env:
        r = await env.client.post("/api/v1/sessions", json={"user_id": 42, "instance_id": 1, "lab_ref": "cisco-3x2"})
        sid = r.json()["session_id"]
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=env.app), base_url="http://manager",
            headers={"Authorization": "Bearer " + "o" * 40, "X-Moodle-Site": "https://other.example.edu"},
        ) as other:
            assert (await other.get(f"/api/v1/sessions/{sid}/state")).status_code == 404
            assert (await other.post(f"/api/v1/sessions/{sid}/state/refresh")).status_code == 404
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://manager") as anon:
            assert (await anon.get(f"/api/v1/sessions/{sid}/state")).status_code == 401
        assert (await state(env, sid)).json()["nodes"] == {}  # queued: nothing yet, not an error


async def test_a_slot_without_facts_changes_nothing(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:  # slot 0.5: GET /facts is a 404
        sid, slot, _pid, _ids = await started(env)
        s = (await env.client.get(f"/api/v1/sessions/{sid}")).json()
        assert s["status"] == "running" and s["features"] == ["consoles"]  # no `facts`: nothing to ask for
        assert (await state(env, sid)).json() == {"schema": "gns3lab.facts/1", "rev": 0, "ttl": 90, "nodes": {}}
        polls = [r for r in slot.requests if r[1].endswith("/facts")]
        await env.run()
        await env.run()
        assert [r for r in slot.requests if r[1].endswith("/facts")] == polls  # not asked again for minutes
        assert env.orch._facts_next[sid] > 0
        await env.client.post(f"/api/v1/sessions/{sid}/state/refresh")  # the slot answers 404: ignored
        await env.run()
        assert (await env.client.get(f"/api/v1/sessions/{sid}")).json()["status"] == "running"


def test_settings_and_defaults(tmp_path):
    from conftest import make_settings

    settings = make_settings(tmp_path)
    assert settings.facts_poll_interval == 15 and settings.facts_ttl == 90
    assert settings.facts_ttl >= 3 * 30  # three collection periods of the slot's default


def test_json_roundtrip_of_a_state_document():
    state = build_state(1, [row("n1", "linux.net", DATA)], {"n1": "started"}, 90)
    assert json.loads(json.dumps(state)) == state  # nothing but JSON types reaches the plugin
