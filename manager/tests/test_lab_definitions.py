"""Labs defined in Moodle (plugin round 4): images, validate, sessions started from a definition."""

import copy

from conftest import SITE_SECRET, SITE_URL, make_env
from test_labdef import IMAGES, LAB

from lab_manager.config import SiteConfig, SiteLimits
from lab_manager.images import Image
from lab_manager.orchestrator import consoleless_names


def limited_sites(**limits):
    return {SITE_URL: SiteConfig(url=SITE_URL, slug="main", secret=SITE_SECRET, limits=SiteLimits(**limits))}


async def start_def(env, lab=LAB, user_id=42, instance_id=1):
    r = await env.client.post("/api/v1/sessions", json={"user_id": user_id, "instance_id": instance_id,
                                                        "lab": lab, "course_id": 3})
    return r


async def test_disabled_without_image_catalog(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:
        info = (await env.client.get("/api/v1/info")).json()
        assert "lab_definitions" not in info["features"] and "limits" not in info
        assert (await env.client.get("/api/v1/images")).status_code == 404
        r = await env.client.post("/api/v1/labs/validate", json={"lab": LAB})
        assert r.status_code == 400 and r.json()["error"] == "unsupported_field"
        r = await start_def(env)
        assert r.status_code == 400 and r.json()["error"] == "unsupported_field"


async def test_info_images_and_site_limits(tmp_path, dsn):
    sites = limited_sites(max_nodes=30, max_memory_mb=16384, max_cpus=8, images=["cisco-iou-l2", "debian12"])
    async with make_env(tmp_path, dsn, images=IMAGES, sites=sites) as env:
        info = (await env.client.get("/api/v1/info")).json()
        assert {"images", "lab_definitions"} <= set(info["features"])
        assert info["lab_formats"] == ["gns3lab.lab/1", "gns3lab.lab/2"] and "lab_constructor" in info["features"]
        assert info["limits"] == {"max_nodes": 30, "max_memory_mb": 16384, "max_cpus": 8,
                                  "images": ["cisco-iou-l2", "debian12"]}
        images = (await env.client.get("/api/v1/images")).json()["images"]
        assert [i["id"] for i in images] == ["cisco-iou-l2", "debian12"]  # Only what the site may use
        assert all("file" not in i and "slot" not in i for i in images)
        r = await env.client.post("/api/v1/labs/validate", json={"lab": LAB})  # PC2 is VPCS: not allowed
        assert r.status_code == 422
        assert {"path": "nodes[3].image", "code": "image_not_allowed", "message": "vpcs"} in r.json()["problems"]


async def test_validate(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=IMAGES) as env:
        r = await env.client.post("/api/v1/labs/validate", json={"lab": LAB})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["lab_def"].startswith("sha256:") and body["warnings"] == []
        assert body["resources"] == {"cores": 4, "memory_mb": 4608}
        bad = copy.deepcopy(LAB)
        bad["nodes"][2]["adapters"] = 9
        r = await env.client.post("/api/v1/labs/validate", json={"lab": bad})
        assert r.status_code == 422 and r.json()["error"] == "lab_invalid"
        assert r.json()["problems"] == [{"path": "nodes[2].adapters", "code": "out_of_range", "message": "1..4"}]
        r = await env.client.post("/api/v1/labs/validate", json={"lab": LAB, "x": 1})
        assert r.status_code == 400 and r.json()["error"] == "unsupported_field"


async def test_session_from_definition(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=IMAGES) as env:
        lab_def = (await env.client.post("/api/v1/labs/validate", json={"lab": LAB})).json()["lab_def"]
        r = await start_def(env)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["lab_ref"] is None and body["lab_def"] == lab_def
        await env.run()
        r = await env.client.get(f"/api/v1/sessions/{body['session_id']}")
        s = r.json()
        assert s["status"] == "running", s["status_message"]
        nodes = {n["name"]: n for n in s["topology"]["topology"]["nodes"]}
        assert nodes["SW1"]["symbol"] == ":/symbols/multilayer_switch.svg"  # From the image catalog
        assert nodes["PC2"]["symbol"] == ":/symbols/vpcs_guest.svg"
        assert s["topology"]["topology"]["drawings"] == []  # Drawings stay in Moodle

        ticket = next(iter(env.slots[201].labs.values()))["ticket"]
        assert ticket["name"].startswith("lab-" + lab_def[7:15] + "-w")
        assert {n["name"]: n.get("image") for n in ticket["nodes"]} == {
            "SW1": "x86_64_crb_linux_l2-adventerprisek9-ms.bin", "SW2": "x86_64_crb_linux_l2-adventerprisek9-ms.bin",
            "PC1": "debian12.qcow2", "PC2": None}
        config = env.pve.cts[201]["config"]
        assert config["cores"] == "4" and config["memory"] == "4608"  # Sized from the definition

        # Same definition again: the same live session; another one: a conflict naming lab_def.
        again = await start_def(env)
        assert again.status_code == 200 and again.json()["session_id"] == body["session_id"]
        other = copy.deepcopy(LAB)
        other["nodes"][0]["x"] = 0
        r = await start_def(env, other)
        assert r.status_code == 409 and r.json()["conflict"]["lab_def"] == lab_def
        r = await env.client.get("/api/v1/sessions", params={"instance_id": 1})
        assert r.json()[0]["lab_def"] == lab_def


async def test_exactly_one_lab_and_invalid_definition(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=IMAGES) as env:
        r = await env.client.post("/api/v1/sessions", json={"user_id": 42, "instance_id": 1, "lab": LAB,
                                                            "lab_ref": "cisco-3x2"})
        assert r.status_code == 400 and r.json()["error"] == "invalid_request"
        r = await env.client.post("/api/v1/sessions", json={"user_id": 42, "instance_id": 1})
        assert r.status_code == 400
        bad = copy.deepcopy(LAB)
        bad["nodes"][0]["image"] = "removed"
        r = await start_def(env, bad)
        assert r.status_code == 422 and r.json()["problems"][0]["code"] == "unknown_image"


async def test_image_removed_after_start_fails_session(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=dict(IMAGES)) as env:
        sid = (await start_def(env)).json()["session_id"]
        del env.settings.images["debian12"]  # The administrator removed it meanwhile
        await env.run()
        s = (await env.client.get(f"/api/v1/sessions/{sid}")).json()
        assert s["status"] == "failed" and s["error"] == {"code": "LAB_UNAVAILABLE", "retryable": False}
        assert (await env.services.store.session(sid))["slot_vmid"] is None  # No CT started for nothing


async def test_resume_workspace_follows_lab_def(tmp_path, dsn):
    async with make_env(tmp_path, dsn, vmids=(201,), images=IMAGES) as env:
        first = (await start_def(env)).json()["session_id"]
        await env.run()
        await env.client.post(f"/api/v1/sessions/{first}/stop")
        await env.run()
        renamed = dict(copy.deepcopy(LAB), title="Новое имя")  # Same lab_def: same workspace
        second = (await start_def(env, renamed)).json()["session_id"]
        await env.run()
        await env.client.post(f"/api/v1/sessions/{second}/stop")
        await env.run()
        moved = copy.deepcopy(LAB)
        moved["nodes"][0]["x"] = 0  # A new version of the lab: a new workspace
        third = (await start_def(env, moved)).json()["session_id"]
        await env.run()
        store = env.services.store
        a, b, c = [await store.session(x) for x in (first, second, third)]
        assert a["project_id"] == b["project_id"] != c["project_id"]
        assert c["status"] == "running"


# --- images without a console (the Linux switch: nobody manages it, whatever the login is) ---------

SWITCH_IMAGE = Image.model_validate({
    "id": "linux-switch", "title": "Linux Switch", "kind": "qemu", "category": "switch", "console_type": "none",
    "symbol": ":/symbols/ethernet_switch.svg", "file": "linux-switch.qcow2",
    "ram_mb": {"default": 512, "min": 256, "max": 2048}, "cpus": {"default": 1, "min": 1, "max": 2},
    "adapters": {"default": 5, "min": 1, "max": 16}, "slot": {"disk_interface": "virtio"},
})


def ticket_of(env, sid, node_id, readonly=False):
    return env.client.post(f"/api/v1/sessions/{sid}/console-tickets",
                           json={"node_id": node_id, "readonly": readonly, "issued_for_user_id": 42})


def test_consoleless_names_follow_the_image_file_not_its_id():
    ticket = {"nodes": [{"name": "SW", "image": "linux-switch.qcow2"}, {"name": "PC1", "image": "debian12.qcow2"},
                        {"name": "PC2"}]}  # VPCS has no image
    assert consoleless_names(ticket, {"linux-switch": SWITCH_IMAGE, **IMAGES}) == {"SW"}
    plain = {k: v for k, v in IMAGES.items() if v.console_type != "none"}
    assert consoleless_names(ticket, plain) == set()  # nothing declared without a console
    assert consoleless_names({}, {"linux-switch": SWITCH_IMAGE}) == set()


async def test_a_moodle_lab_with_the_linux_switch_hands_out_no_console_for_it(tmp_path, dsn):
    lab = copy.deepcopy(LAB)
    lab["nodes"].append({"name": "SW3", "image": "linux-switch", "x": 0, "y": -160})
    lab["links"].append({"a": {"node": "SW3", "adapter": 0, "port": 0}, "b": {"node": "SW1", "adapter": 1, "port": 0}})
    async with make_env(tmp_path, dsn, images={**IMAGES, "linux-switch": SWITCH_IMAGE}) as env:
        r = await start_def(env, lab)
        assert r.status_code == 201, r.text
        sid = r.json()["session_id"]
        await env.run()
        s = (await env.client.get(f"/api/v1/sessions/{sid}")).json()
        assert s["status"] == "running", s["status_message"]
        ids = {n["name"]: n["node_id"] for n in s["topology"]["topology"]["nodes"]}
        assert s["nodes"][ids["SW3"]]["console_type"] == "none"  # The plugin hides it and refuses on its side
        assert s["nodes"][ids["PC1"]]["console_type"] == "telnet"  # Everything else is as before

        for readonly in (False, True):  # The owner and a read-only observer alike
            r = await ticket_of(env, sid, ids["SW3"], readonly)
            assert r.status_code == 403 and r.json()["error"] == "console_disabled", r.text
        assert env.gateway.requests == []  # Nothing reached the gateway
        assert (await ticket_of(env, sid, ids["PC1"])).status_code == 200
        assert len(env.gateway.requests) == 1


async def test_a_reference_lab_follows_the_image_of_its_nodes_too(tmp_path, dsn):
    iou = IMAGES["cisco-iou-l2"]  # x86_64_crb_linux_l2-adventerprisek9-ms.bin: the file the IOU nodes of cisco-3x2 use
    hidden = iou.model_copy(update={"console_type": "none"})
    async with make_env(tmp_path, dsn, images={**IMAGES, "cisco-iou-l2": hidden}) as env:
        r = await env.client.post("/api/v1/sessions", json={"user_id": 42, "instance_id": 1, "lab_ref": "cisco-3x2"})
        sid = r.json()["session_id"]
        await env.run()
        s = (await env.client.get(f"/api/v1/sessions/{sid}")).json()
        assert s["status"] == "running", s["status_message"]
        types = {n["name"]: s["nodes"][n["node_id"]]["console_type"] for n in s["topology"]["topology"]["nodes"]}
        assert {t for name, t in types.items() if name.startswith("SW")} == {"none"}  # The IOU switches
        assert {t for name, t in types.items() if not name.startswith("SW")} == {"telnet"}
        router = next(n["node_id"] for n in s["topology"]["topology"]["nodes"] if n["name"].startswith("SW"))
        r = await ticket_of(env, sid, router)
        assert r.status_code == 403 and r.json()["error"] == "console_disabled"


async def test_segments_validate_images_and_session(tmp_path, dsn):
    from test_segments import IMAGES as CONSTRUCTOR_IMAGES
    from test_segments import LAB2

    async with make_env(tmp_path, dsn, images=CONSTRUCTOR_IMAGES) as env:
        r = await env.client.get("/api/v1/images")
        flags = {i["id"]: i["preconfig"] for i in r.json()["images"]}
        assert flags["vpcs"] and flags["debian12"] and not flags["linux-switch"] and not flags["cisco-iou-l3"]
        r = await env.client.post("/api/v1/labs/validate", json={"lab": LAB2})
        assert r.status_code == 200, r.text
        assert r.json()["warnings"] == [{"path": "segments[0].gateway", "code": "gateway_not_configured",
                                         "message": "the router is set by the student"}]
        bad = copy.deepcopy(LAB2)
        bad["segments"][0]["subnet"] = {"fixed": "192.168.10.1/24"}
        bad["segments"][0]["members"][0]["host"] = 0
        r = await env.client.post("/api/v1/labs/validate", json={"lab": bad})
        assert r.status_code == 422
        assert [(p["path"], p["code"]) for p in r.json()["problems"]] == [("segments[0].subnet.fixed", "bad_subnet")]

        r = await start_def(env, LAB2)
        assert r.status_code == 201, r.text
        await env.run()
        ticket = next(iter(env.slots[201].labs.values()))["ticket"]
        nodes = {n["name"]: n for n in ticket["nodes"]}
        assert nodes["PC1"]["provisioning"][0]["timeout_seconds"] == 120
        assert nodes["PC3"]["startup_script"] == "ip 192.168.10.3/24 192.168.10.254\n"
