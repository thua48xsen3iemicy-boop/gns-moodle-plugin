"""The "Internet" node (NodeSpec.uplink_port, CONTRACTS 2.5, 2.6, 4.2, 7): lab definitions, catalog labs, slot choice."""

import copy
import json

import pytest
from conftest import SLOT_TOKEN, make_env
from test_lab_definitions import start_def
from test_labdef import ALL, IMAGES, LAB, changed, problems, slot_models
from test_vnc import REVISION_OF_MOODLE_LAB, lab_revision

from lab_manager.config import SlotConfig
from lab_manager.labdef import check, deploy_ticket
from lab_manager.labs import uplink_problems

PID = "0b6c9d3e-6f1a-4c5e-9a7b-2d8e1f0a3c4b"


def inet_lab(**node):
    lab = copy.deepcopy(LAB)
    lab["nodes"].append({"name": "NET", "image": "lab-uplink", "x": 0, "y": 320, "adapters": 3, "uplink_port": 0, **node})
    lab["links"].append({"a": {"node": "SW1", "adapter": 1, "port": 0}, "b": {"node": "NET", "adapter": 1, "port": 0}})
    return lab


def ticket_of(lab):
    return deploy_ticket(check(lab, IMAGES, ALL).definition, IMAGES, project_id=PID, project_name="n")


def test_uplink_port_reaches_the_ticket_of_that_node_only_and_the_slot_accepts_it():
    ticket = ticket_of(inet_lab())
    assert [n["name"] for n in ticket["nodes"] if "uplink_port" in n] == ["NET"]
    assert next(n for n in ticket["nodes"] if n["name"] == "NET")["uplink_port"] == 0
    parsed = slot_models().DeployTicket.model_validate(ticket)
    assert [n.uplink_port for n in parsed.nodes if n.uplink_port is not None] == [0]


def test_a_lab_without_uplink_keeps_its_ticket_and_revision():
    ticket = ticket_of(LAB)
    assert "uplink_port" not in json.dumps(ticket)
    assert lab_revision(ticket) == REVISION_OF_MOODLE_LAB


def test_uplink_port_is_part_of_lab_def():
    plain, marked = inet_lab(), inet_lab()
    del plain["nodes"][-1]["uplink_port"]
    assert check(plain, IMAGES, ALL).lab_def != check(marked, IMAGES, ALL).lab_def


@pytest.mark.parametrize("lab,expected", [
    (inet_lab(uplink_port=3), [("nodes[4].uplink_port", "out_of_range")]),
    (inet_lab(uplink_port=-1), [("nodes[4].uplink_port", "out_of_range")]),
    (inet_lab(uplink_port="0"), [("nodes[4].uplink_port", "out_of_range")]),
    (inet_lab(uplink_port=True), [("nodes[4].uplink_port", "out_of_range")]),
    (inet_lab(uplink_port=1), [("links[3].b", "uplink_port_in_use")]),
])
def test_invalid_uplink_port(lab, expected):
    assert problems(lab) == expected


def test_only_a_qemu_node_has_an_uplink_port():
    lab = changed(**{"nodes__0__uplink_port": 0})
    assert problems(lab) == [("nodes[0].uplink_port", "unknown_field")]
    assert problems(changed(**{"nodes__3__uplink_port": 0})) == [("nodes[3].uplink_port", "unknown_field")]


def test_at_most_one_uplink_node():
    lab = inet_lab()
    lab["nodes"].append({"name": "NET2", "image": "lab-uplink", "uplink_port": 0})
    assert problems(lab) == [("nodes[5].uplink_port", "too_many_uplinks")]


def test_the_default_adapters_of_the_image_bound_the_port():
    lab = inet_lab(uplink_port=2)
    del lab["nodes"][-1]["adapters"]  # the image default is 2 adapters
    assert problems(lab) == [("nodes[4].uplink_port", "out_of_range")]


# ---- catalog (reference) labs

def catalog_ticket(**node):
    return {"nodes": [{"name": "NET", "kind": "qemu", "image": "i.qcow2", "adapters": 2, "uplink_port": 0, **node},
                      {"name": "PC", "kind": "vpcs"}],
            "links": [{"a": {"node": "NET", "adapter": 1}, "b": {"node": "PC"}}]}


def test_catalog_ticket_with_a_good_uplink_passes_unchanged():
    assert uplink_problems(catalog_ticket()) == []
    assert uplink_problems({"nodes": [{"name": "a", "kind": "vpcs"}]}) == []


@pytest.mark.parametrize("change", [
    {"uplink_port": 2}, {"uplink_port": "0"}, {"kind": "iou"},
])
def test_catalog_ticket_with_a_bad_uplink(change):
    assert uplink_problems(catalog_ticket(**change))


def test_catalog_ticket_with_two_uplinks_or_a_linked_port():
    two = catalog_ticket()
    two["nodes"].append({"name": "N2", "kind": "qemu", "image": "i", "uplink_port": 0})
    assert uplink_problems(two)
    linked = catalog_ticket()
    linked["links"].append({"a": {"node": "NET", "adapter": 0}, "b": {"node": "PC"}})
    assert uplink_problems(linked)


# ---- slot choice

def slots(*with_uplink):
    return {v: SlotConfig(vmid=v, address=f"10.0.0.{v - 200}", api_token=SLOT_TOKEN, console_token="c" * 40,
                          features=["uplink"] if v in with_uplink else [])
            for v in (201, 202)}


def test_slots_json_features():
    assert SlotConfig(vmid=201, address="10.0.0.1", api_token=SLOT_TOKEN).features == []
    with pytest.raises(ValueError):
        SlotConfig(vmid=201, address="10.0.0.1", api_token=SLOT_TOKEN, features=["warp"])


async def test_no_slot_with_uplink_refuses_the_session(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=IMAGES, slots=slots()) as env:
        r = await start_def(env, inet_lab())
        assert r.status_code == 409 and r.json()["error"] == "unsupported_lab"
        assert (await start_def(env, LAB, user_id=43)).status_code == 201  # labs without the node are not affected


async def test_a_lab_with_uplink_gets_only_a_slot_that_offers_it(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=IMAGES, slots=slots(202)) as env:
        r = await start_def(env, inet_lab())
        assert r.status_code == 201, r.text
        sid = r.json()["session_id"]
        await env.run(1)
        assert (await env.services.store.session(sid))["slot_vmid"] == 202
        # a lab without the node still takes the first free slot, here the other one
        other = (await start_def(env, LAB, user_id=43)).json()["session_id"]
        await env.run(1)
        assert (await env.services.store.session(other))["slot_vmid"] == 201
