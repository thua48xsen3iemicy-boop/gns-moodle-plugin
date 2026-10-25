"""The VNC console (CONTRACTS 2.4, 2.6, 4.2, 6): images, the node of the slot's ticket and console tickets."""

import copy
import hashlib
import json

import pytest
from conftest import ROOT, make_env
from test_lab_definitions import start_def, ticket_of
from test_labdef import ALL, IMAGES, LAB, slot_models

from lab_manager.images import Image, ImageError, load_images
from lab_manager.labdef import check, deploy_ticket
from lab_manager.labs import Catalog
from lab_manager.labs import deploy_ticket as reference_ticket

LABS = ROOT / "examples" / "labs"
PID = "0b6c9d3e-6f1a-4c5e-9a7b-2d8e1f0a3c4b"

VNC_IMAGE = Image.model_validate({
    "id": "kali", "title": "Kali Linux", "kind": "qemu", "category": "host", "console_type": "vnc",
    "file": "kali.qcow2", "ram_mb": {"default": 2048, "min": 1024, "max": 4096},
    "cpus": {"default": 2, "min": 1, "max": 4}, "adapters": {"default": 1, "min": 1, "max": 2},
    "slot": {"disk_interface": "virtio"},
})
CATALOG = {**IMAGES, "kali": VNC_IMAGE}

# lab_revision (CONTRACTS 3.2) of the tickets below, computed with the manager 0.8.1 that had no `console` field.
# A lab without vnc nodes must keep exactly these revisions.
REVISION_OF_MOODLE_LAB = "sha256:dbc2bb6d46298ebf5f355ca0a4934436a03a7dec291a9a363ebf2d81ba6aa230"
REVISION_OF_CISCO_3X2 = "sha256:b539eea6951430324f9911a9de2d9334173c58841bc9a7a0d3e388e57847d5ff"


def lab_revision(ticket):
    """CONTRACTS 3.2: sha256 of the canonical JSON of {nodes, links} of the ticket."""
    body = json.dumps({"nodes": ticket["nodes"], "links": ticket.get("links") or []},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def vnc_lab():
    lab = copy.deepcopy(LAB)
    lab["nodes"].append({"name": "KALI", "image": "kali", "x": 0, "y": 320})
    lab["links"].append({"a": {"node": "KALI", "adapter": 0, "port": 0}, "b": {"node": "SW1", "adapter": 1, "port": 0}})
    return lab


def write_images(tmp_path, *images):
    path = tmp_path / "images.json"
    path.write_text(json.dumps({"images": list(images)}))
    return path


# --- image catalog ---------------------------------------------------------------------------------------------


def test_a_vnc_image_is_for_qemu_only(tmp_path):
    qemu = VNC_IMAGE.model_dump(mode="json", exclude_none=True)
    assert load_images(write_images(tmp_path, qemu))["kali"].console_type == "vnc"
    assert VNC_IMAGE.public()["console_type"] == "vnc"  # GET /images says so as for any console type

    iou = {"id": "iol", "title": "IOL", "kind": "iou", "console_type": "vnc", "file": "iol.bin",
           "ram_mb": {"default": 512, "min": 256, "max": 1024}, "adapters": {"default": 2, "min": 1, "max": 4}}
    with pytest.raises(ImageError, match=r"images\[0\].*vnc is for qemu only"):
        load_images(write_images(tmp_path, iou))
    with pytest.raises(ImageError, match="vnc is for qemu only"):
        load_images(write_images(tmp_path, {"id": "pc", "title": "VPCS", "kind": "vpcs", "console_type": "vnc"}))
    # The other console types are untouched.
    load_images(write_images(tmp_path, {**iou, "console_type": "telnet"},
                             {"id": "pc", "title": "VPCS", "kind": "vpcs", "console_type": "none"}))


# --- the slot's ticket: the field only on vnc nodes, lab_revision of other labs unchanged --------------------------


def test_only_a_vnc_node_carries_the_console_field():
    lab = check(vnc_lab(), CATALOG, ALL)
    ticket = deploy_ticket(lab.definition, CATALOG, project_id=PID, project_name="lab-1234abcd-w1")
    nodes = {n["name"]: n for n in ticket["nodes"]}
    assert nodes["KALI"]["console"] == "vnc" and nodes["KALI"]["kind"] == "qemu"
    assert nodes["KALI"]["image"] == "kali.qcow2" and nodes["KALI"]["disk_interface"] == "virtio"
    assert [name for name, n in nodes.items() if "console" in n] == ["KALI"]  # No "telnet" anywhere
    assert "requires" not in ticket and "schema" not in ticket  # Still deploy/1 (CONTRACTS 4.2)
    parsed = slot_models().DeployTicket.model_validate(ticket)  # What the slot of this repository accepts
    assert {n.name: n.console for n in parsed.nodes}["KALI"] == "vnc"


def test_labs_without_vnc_nodes_keep_their_revision():
    # The Moodle lab, built with a catalog that also has a vnc image the lab does not use.
    lab = check(LAB, CATALOG, ALL)
    ticket = deploy_ticket(lab.definition, CATALOG, project_id=PID, project_name="lab-1234abcd-w1")
    assert not any("console" in n for n in ticket["nodes"])
    assert lab_revision(ticket) == REVISION_OF_MOODLE_LAB
    assert lab.lab_def == check(LAB, IMAGES, ALL).lab_def  # The definition hash does not see the catalog either
    # A reference lab (ticket.json of the catalog) is not touched at all.
    ref = reference_ticket(Catalog(LABS).get("cisco-3x2"), project_id=PID, project_name="cisco-3x2-w1")
    assert not any("console" in n for n in ref["nodes"])
    assert lab_revision(ref) == REVISION_OF_CISCO_3X2
    # And the field is what changes the revision of a lab that has it.
    vnc = deploy_ticket(check(vnc_lab(), CATALOG, ALL).definition, CATALOG, project_id=PID, project_name="x")
    assert lab_revision(vnc) != REVISION_OF_MOODLE_LAB


# --- sessions ----------------------------------------------------------------------------------------------------


async def session(env, sid):
    return (await env.client.get(f"/api/v1/sessions/{sid}")).json()


async def test_a_vnc_lab_runs_as_deploy_1_with_the_field_on_its_node(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,), images=CATALOG) as env:
        slot = env.slots[201]
        r = await start_def(env, vnc_lab())
        assert r.status_code == 201, r.text
        sid = r.json()["session_id"]
        await env.run()
        s = await session(env, sid)
        assert s["status"] == "running", s["status_message"]

        stored = await env.services.store.session(sid)
        ws = await env.services.store.workspace(stored["workspace_id"])
        body = slot.deploy_bodies[0]
        assert body == ws["ticket"]  # The workspace's passport, as sent
        assert "schema" not in body and "requires" not in body  # deploy/1
        assert {n["name"]: n.get("console") for n in body["nodes"]} == {
            "SW1": None, "SW2": None, "PC1": None, "PC2": None, "KALI": "vnc"}
        assert not [r for r in slot.requests if r[1] == "/v1/capabilities"]  # The manager does not ask
        types = {n["name"]: s["nodes"][n["node_id"]]["console_type"] for n in s["topology"]["topology"]["nodes"]}
        assert types == {"SW1": "telnet", "SW2": "telnet", "PC1": "telnet", "PC2": "telnet", "KALI": "vnc"}

        await env.client.post(f"/api/v1/sessions/{sid}/stop")  # The next session replays the same ticket
        await env.run()
        again = (await start_def(env, vnc_lab())).json()["session_id"]
        await env.run()
        assert (await session(env, again))["status"] == "running"
        assert slot.deploy_bodies[-1] == body


async def test_labs_without_vnc_nodes_are_sent_exactly_as_before(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,), images=CATALOG) as env:  # A catalog that has a vnc image
        slot = env.slots[201]
        moodle = (await start_def(env, LAB)).json()["session_id"]
        await env.run()
        stored = await env.services.store.session(moodle)
        assert (await session(env, moodle))["status"] == "running"
        body = slot.deploy_bodies[0]
        assert body == (await env.services.store.workspace(stored["workspace_id"]))["ticket"]
        assert not any("console" in n for n in body["nodes"])
        assert lab_revision(body) == REVISION_OF_MOODLE_LAB  # Same lab_revision as before the field existed
        await env.client.post(f"/api/v1/sessions/{moodle}/stop")
        await env.run()
        r = await env.client.post("/api/v1/sessions", json={"user_id": 43, "instance_id": 1, "lab_ref": "cisco-3x2"})
        reference = r.json()["session_id"]
        await env.run()
        assert (await session(env, reference))["status"] == "running"
        ref_body = slot.deploy_bodies[-1]
        assert not any("console" in n for n in ref_body["nodes"])
        assert lab_revision(ref_body) == REVISION_OF_CISCO_3X2


# --- console tickets ---------------------------------------------------------------------------------------------


async def running_vnc_session(env):
    sid = (await start_def(env, vnc_lab())).json()["session_id"]
    await env.run()
    s = await session(env, sid)
    assert s["status"] == "running", s["status_message"]
    return sid, {n["name"]: n["node_id"] for n in s["topology"]["topology"]["nodes"]}


async def test_only_the_owner_gets_a_ticket_for_a_vnc_screen(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,), images=CATALOG) as env:
        sid, ids = await running_vnc_session(env)
        r = await ticket_of(env, sid, ids["KALI"], readonly=True)  # A teacher watching
        assert r.status_code == 403 and r.json()["error"] == "console_disabled", r.text
        assert "owner" in r.json()["message"]
        assert env.gateway.requests == []  # Nothing reached the gateway

        r = await ticket_of(env, sid, ids["KALI"])
        assert r.status_code == 200, r.text
        assert set(r.json()) == {"ticket", "gateway_url", "expires_in"}  # The answer is the usual one
        assert r.json()["gateway_url"] == "wss://gateway.example.edu"
        assert len(env.gateway.requests) == 1
        sent = env.gateway.requests[0]
        assert sent["console"] == "vnc" and sent["readonly"] is False and sent["nodes"] == [ids["KALI"]]
        assert sent["who"] == f"{sid}:owner"


async def test_telnet_tickets_are_what_they_were(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,), images=CATALOG) as env:
        sid, ids = await running_vnc_session(env)
        for readonly, who in ((False, "owner"), (True, "observer")):  # An observer still watches a terminal
            r = await ticket_of(env, sid, ids["PC1"], readonly=readonly)
            assert r.status_code == 200, r.text
            sent = env.gateway.requests[-1]
            assert "console" not in sent  # The gateway's default is telnet: the request is as before
            assert sent == {"slot": "10.0.0.1", "project_id": sent["project_id"], "nodes": [ids["PC1"]],
                            "readonly": readonly, "who": f"{sid}:{who}"}


async def test_a_vnc_ticket_check_goes_before_the_state_check(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,), images=CATALOG) as env:
        sid, ids = await running_vnc_session(env)
        env.slots[201].stopped_nodes.add("KALI")
        env.orch._live_cache.clear()
        # The screen belongs to the owner, and that is said before anything about its state.
        r = await ticket_of(env, sid, ids["KALI"], readonly=True)
        assert r.status_code == 403 and r.json()["error"] == "console_disabled"
        r = await ticket_of(env, sid, ids["KALI"])
        assert r.status_code == 409 and r.json()["error"] == "node_not_running"
        assert env.gateway.requests == []
        r = await ticket_of(env, sid, "00000000-0000-4000-8000-000000000000")
        assert r.status_code == 404 and r.json()["error"] == "node_not_found"
        env.slots[201].stopped_nodes.clear()
        env.orch._live_cache.clear()
        assert (await ticket_of(env, sid, ids["KALI"])).status_code == 200


async def test_the_type_of_a_screen_comes_from_the_snapshot_when_the_slot_is_silent(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,), images=CATALOG) as env:
        sid, ids = await running_vnc_session(env)
        env.slots[201].reachable = False
        env.orch._live_cache.clear()
        r = await ticket_of(env, sid, ids["KALI"], readonly=True)
        assert r.status_code == 403 and r.json()["error"] == "console_disabled"
        r = await ticket_of(env, sid, ids["KALI"])  # nodes_stale: a ticket is issued, as for any node
        assert r.status_code == 200, r.text
        assert env.gateway.requests[-1]["console"] == "vnc"


async def test_the_images_list_tells_the_console_type(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=CATALOG) as env:
        images = {i["id"]: i for i in (await env.client.get("/api/v1/images")).json()["images"]}
        assert images["kali"]["console_type"] == "vnc" and images["debian12"]["console_type"] == "telnet"
        assert "file" not in images["kali"] and "slot" not in images["kali"]
