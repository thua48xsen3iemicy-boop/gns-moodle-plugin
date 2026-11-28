"""Additional console lines of a node (CONTRACTS 2.3, 2.6, 4.2, 6.4): the catalog, the lab definition, the ticket and
the session."""

import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import make_env
from lab_manager.config import SiteLimits
from lab_manager.images import Image, load_images
from lab_manager.labdef import LabInvalid, check, deploy_ticket
from lab_manager.labs import extra_console_problems
from lab_manager.orchestrator import extra_console_counts

IMAGES = load_images(Path(__file__).resolve().parents[1] / "examples" / "images.json")
PID = "6d0f1c1e-3c58-4c0b-9a6e-0d6f6f3c1a11"
BASE = {"id": "x", "title": "X", "ram_mb": {"default": 256, "min": 128, "max": 512},
        "adapters": {"default": 2, "min": 2, "max": 4}, "extra_consoles": True}


def lab(lines=..., image="debian12"):
    node = {"name": "WS1", "image": image, **({"extra_consoles": lines} if lines is not ... else {})}
    return {"format": "gns3lab.lab/1", "nodes": [node, {"name": "PC2", "image": "debian12"}], "links": []}


def problems(definition):
    with pytest.raises(LabInvalid) as caught:
        check(definition, IMAGES, SiteLimits())
    return [(p["path"], p["code"]) for p in caught.value.problems]


def test_catalog_flag_is_public():
    assert IMAGES["debian12"].public()["extra_consoles"] == {"max": 3}
    assert "extra_consoles" not in IMAGES["cisco-iou-l2"].public()
    assert Image(**BASE, kind="qemu", file="x.qcow2").extra_consoles


@pytest.mark.parametrize("extra", [
    {"kind": "iou", "file": "x.bin"},
    {"kind": "vpcs", "ram_mb": None, "adapters": None},
    {"kind": "qemu", "file": "x.qcow2", "console_type": "vnc"},
    {"kind": "qemu", "file": "x.qcow2", "console_type": "none"},
    {"kind": "qemu", "file": "x.qcow2", "qga": False},
    {"kind": "qemu", "file": "x.qcow2", "net_config": "routeros"},
    {"kind": "qemu", "file": "x.qcow2", "uplink_port": 0, "console_type": "none"},
    {"kind": "qemu", "file": "x.qcow2", "extra_consoles": 1},  # strict: a boolean
])
def test_catalog_flag_needs_a_linux_qemu_with_a_text_console_and_an_agent(extra):
    with pytest.raises(ValidationError):
        Image(**{**BASE, **extra})


def test_lines_reach_the_ticket_of_their_node_only():
    checked = check(lab(3), IMAGES, SiteLimits())
    assert checked.definition["nodes"][0]["extra_consoles"] == 3
    first, second = deploy_ticket(checked.definition, IMAGES, project_id=PID, project_name="p")["nodes"]
    assert first["extra_consoles"] == 3 and "extra_consoles" not in second


def test_zero_is_no_field():
    plain = check(lab(), IMAGES, SiteLimits())
    zero = check(lab(0), IMAGES, SiteLimits())
    assert zero.lab_def == plain.lab_def and "extra_consoles" not in zero.definition["nodes"][0]
    assert check(lab(1), IMAGES, SiteLimits()).lab_def != plain.lab_def


@pytest.mark.parametrize("value, code", [(4, "out_of_range"), (-1, "out_of_range"), (True, "bad_type"),
                                         ("2", "bad_type"), (1.0, "bad_type"), (None, "bad_type")])
def test_bad_numbers(value, code):
    assert problems(lab(value)) == [("nodes[0].extra_consoles", code)]


def test_an_image_without_the_flag_takes_no_lines():
    assert problems(lab(1, image="cisco-iou-l2")) == [("nodes[0].extra_consoles", "unsupported_image")]
    assert check(lab(0, image="cisco-iou-l2"), IMAGES, SiteLimits())  # 0 is no field anywhere


def test_reference_lab_ticket_follows_the_slot_rules():
    good = {"nodes": [{"name": "WS1", "kind": "qemu", "extra_consoles": 2}, {"name": "R1", "kind": "iou"}]}
    assert extra_console_problems(good) == []
    for bad in ({"kind": "qemu", "extra_consoles": 0}, {"kind": "qemu", "extra_consoles": 4},
                {"kind": "qemu", "extra_consoles": True}, {"kind": "iou", "extra_consoles": 1},
                {"kind": "qemu", "console": "vnc", "extra_consoles": 1},
                {"kind": "qemu", "qga": False, "extra_consoles": 1}):
        assert extra_console_problems({"nodes": [{"name": "WS1", **bad}]}), bad


def test_line_counts_of_a_ticket():
    ticket = {"nodes": [{"name": "WS1", "extra_consoles": 3}, {"name": "PC2"}, {"name": "X", "extra_consoles": True}]}
    assert extra_console_counts(ticket) == {"WS1": 3}
    assert extra_console_counts({}) == {}


async def test_info_and_session_show_the_lines(tmp_path, dsn):
    from test_lab_definitions import start_def
    from test_labdef import LAB

    definition = copy.deepcopy(LAB)
    definition["nodes"][2]["extra_consoles"] = 2  # PC1, debian12
    async with make_env(tmp_path, dsn, images=IMAGES) as env:
        assert "extra_consoles" in (await env.client.get("/api/v1/info")).json()["features"]
        r = await start_def(env, definition)
        assert r.status_code == 201, r.text
        sid = r.json()["session_id"]
        await env.run()
        s = (await env.client.get(f"/api/v1/sessions/{sid}")).json()
        assert s["status"] == "running", s["status_message"]
        ids = {n["name"]: n["node_id"] for n in s["topology"]["topology"]["nodes"]}
        assert s["nodes"][ids["PC1"]]["extra_consoles"] == 2
        assert all("extra_consoles" not in s["nodes"][ids[name]] for name in ("SW1", "SW2", "PC2"))
        r = await env.client.post(f"/api/v1/sessions/{sid}/console-tickets",
                                  json={"node_id": ids["PC1"], "readonly": False, "issued_for_user_id": 42})
        assert r.status_code == 200  # one ticket of the node opens every line of it
