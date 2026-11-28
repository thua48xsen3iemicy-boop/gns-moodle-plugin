"""The teacher's own setup steps of a lab, `setup` of gns3lab.lab/2 (docs/lab-setup/README.md, CONTRACTS 2.6)."""

import copy
import json
import subprocess

import pytest

from conftest import make_env
from test_lab_definitions import start_def
from test_labdef import ALL, slot_models
from test_segments import IMAGES, LAB2

from lab_manager import labsetup
from lab_manager.images import Image
from lab_manager.labdef import LabInvalid, check, deploy_ticket

PID = "0b6c9d3e-6f1a-4c5e-9a7b-2d8e1f0a3c4b"
EXTRA = {
    "blackbox": Image(id="blackbox", title="B", kind="qemu", file="b.qcow2", qga=False,
                      ram_mb={"default": 512, "min": 256, "max": 1024}, adapters={"default": 1, "min": 1, "max": 4}),
}
ALL_IMAGES = {**IMAGES, **EXTRA}

# LAB2 (SW1 Linux switch; PC1, PC2 Debian; PC3 VPCS; R1 IOL router, the gateway of LAN1) plus nodes of other kinds.
LAB = copy.deepcopy(LAB2)
LAB["nodes"] += [{"name": "R2", "image": "cisco-iou-l3"}, {"name": "MT1", "image": "mikrotik-chr7"},
                 {"name": "PC4", "image": "vpcs-boot"}, {"name": "NET", "image": "lab-uplink"},
                 {"name": "BB", "image": "blackbox"}]
STEPS = [
    {"id": "web", "title": "nginx на PC1", "node": "PC1", "timeout": 90,
     "run": "apt-get install -y nginx >/dev/null && systemctl enable --now nginx"},
    {"id": "ospf", "title": "OSPF на R1", "node": "R1", "config": "router ospf 1\n network 10.0.0.0 0.0.0.255 area 0\n"},
    {"id": "pc-dns", "title": "DNS на PC3", "node": "PC3", "config": "ip dns 10.0.0.53"},
    {"id": "motd", "title": "  motd  ", "node": "PC1", "shell": "sh", "run": "echo hi > /etc/motd"},
]


def lab_with(steps, base=LAB):
    lab = copy.deepcopy(base)
    lab["setup"] = steps
    return lab


def problems(lab):
    with pytest.raises(LabInvalid) as exc:
        check(lab, ALL_IMAGES, ALL)
    return [(p["path"], p["code"]) for p in exc.value.problems]


def ticket(definition):
    return deploy_ticket(definition, ALL_IMAGES, project_id=PID, project_name="lab-1")


def nodes_of(definition):
    return {n["name"]: n for n in ticket(definition)["nodes"]}


def step(**fields):
    """A step on PC1; a field given as None is left out."""
    return {k: v for k, v in {"id": "s", "title": "T", "node": "PC1", "run": "true", **fields}.items() if v is not None}


# ---------------------------------------------------------------- validation

def test_valid_steps_are_normalized_and_part_of_the_lab_def():
    checked = check(lab_with(STEPS), ALL_IMAGES, ALL)
    assert checked.definition["setup"] == [
        {"id": "web", "title": "nginx на PC1", "node": "PC1", "run": STEPS[0]["run"], "timeout": 90},
        {"id": "ospf", "title": "OSPF на R1", "node": "R1", "config": STEPS[1]["config"]},
        {"id": "pc-dns", "title": "DNS на PC3", "node": "PC3", "config": "ip dns 10.0.0.53"},
        {"id": "motd", "title": "motd", "node": "PC1", "run": "echo hi > /etc/motd", "timeout": 60, "shell": "sh"},
    ]
    plain = check(LAB, ALL_IMAGES, ALL)
    assert checked.lab_def != plain.lab_def  # A step is part of the lab
    assert check(lab_with(STEPS[::-1]), ALL_IMAGES, ALL).lab_def != checked.lab_def  # So is the order of the steps
    assert check(lab_with([]), ALL_IMAGES, ALL).lab_def == plain.lab_def  # No steps: no field
    assert "setup" not in check(lab_with([]), ALL_IMAGES, ALL).definition


def test_a_lab_with_only_setup_is_lab2_with_empty_segments():
    lab = {"format": "gns3lab.lab/2", "title": "X", "nodes": [{"name": "PC1", "image": "debian12"}], "links": [],
           "segments": [], "setup": [step()]}
    checked = check(lab, ALL_IMAGES, ALL)
    assert checked.definition["segments"] == [] and checked.definition["setup"][0]["id"] == "s"
    assert ("setup", "unknown_field") in problems({**lab, "format": "gns3lab.lab/1"})  # lab/2 only


@pytest.mark.parametrize("steps,expected", [
    ({"id": "a"}, ("setup", "bad_type")),
    ([step(id="s%d" % i) for i in range(31)], ("setup", "out_of_range")),
    ([step(id="s%d" % i, timeout=120) for i in range(6)], ("setup", "out_of_range")),  # 720 s > 600 s
    ([1], ("setup[0]", "bad_type")),
    ([step(id=None)], ("setup[0].id", "required")),
    ([step(id="Web")], ("setup[0].id", "bad_name")),
    ([step(), step()], ("setup[1].id", "duplicate_name")),
    ([step(title="")], ("setup[0].title", "out_of_range")),
    ([step(title="x" * 121)], ("setup[0].title", "out_of_range")),
    ([step(title=None)], ("setup[0].title", "required")),
    ([step(node="R9")], ("setup[0].node", "unknown_node")),
    ([step(node="NET")], ("setup[0].node", "setup_node_unsupported")),  # The Internet node
    ([step(node="BB")], ("setup[0].node", "setup_node_unsupported")),  # QEMU without a guest agent
    ([step(required=False)], ("setup[0].required", "unknown_field")),  # Stage 2
    ([step(extra=1)], ("setup[0].extra", "unknown_field")),
    ([step(config="x")], ("setup[0].config", "unknown_field")),  # QEMU takes `run`
    ([step(node="R1")], ("setup[0].run", "unknown_field")),  # IOL takes `config`
    ([step(node="PC3", run=None, config="ip dns 1.1.1.1", timeout=5)], ("setup[0].timeout", "unknown_field")),
    ([step(node="R1", run=None, config="hostname R1", shell="sh")], ("setup[0].shell", "unknown_field")),
    ([step(run=None)], ("setup[0].run", "required")),
    ([step(run="  ")], ("setup[0].run", "bad_type")),
    ([step(run="x" * 8001)], ("setup[0].run", "too_large")),
    ([step(run="я" * 4001)], ("setup[0].run", "too_large")),  # Bytes of UTF-8, not characters
    ([step(shell="bash")], ("setup[0].shell", "out_of_range")),
    ([step(shell="routeros")], ("setup[0].shell", "shell_mismatch")),  # Debian runs sh
    ([step(node="MT1", shell="sh")], ("setup[0].shell", "shell_mismatch")),
    ([step(shell="powershell")], ("setup[0].shell", "shell_mismatch")),  # Stage 2: setup-windows
    ([step(timeout=0)], ("setup[0].timeout", "out_of_range")),
    ([step(timeout=121)], ("setup[0].timeout", "out_of_range")),
    ([step(timeout="60")], ("setup[0].timeout", "bad_type")),
    ([step(node="R1", run=None)], ("setup[0].config", "required")),
    ([step(node="R1", run=None, config="hostname R1\r\n")], ("setup[0].config", "bad_format")),
    ([step(node="PC3", run=None, config="ip dns 10.0.0.53\tx")], ("setup[0].config", "bad_format")),
    ([step(node="R1", run=None, config="description сервер")], ("setup[0].config", "bad_format")),
])
def test_problems(steps, expected):
    assert expected in problems(lab_with(steps))


def test_a_node_with_its_own_problem_is_not_reported_again():
    lab = lab_with([step(node="PC9")])
    lab["nodes"].append({"name": "PC9", "image": "no-such-image"})
    assert problems(lab) == [("nodes[10].image", "unknown_image")]


def test_limits_that_fit():
    steps = [step(id="s%d" % i, timeout=20) for i in range(30)]  # 30 steps, 600 s
    assert len(check(lab_with(steps), ALL_IMAGES, ALL).definition["setup"]) == 30
    check(lab_with([step(run="x" * 8000, shell="sh")]), ALL_IMAGES, ALL)
    check(lab_with([step(node="MT1", run=":put 1", shell="routeros")]), ALL_IMAGES, ALL)


def test_final_configurations_must_fit_the_slot():
    line = "x" * 1000 + "\n"
    big = [step(id=f"r{i}", node="R2", run=None, config=line * 7) for i in range(10)]  # About 70 000 characters
    assert problems(lab_with(big)) == [("setup[9]", "too_large")]  # The last step of the node
    vpcs = [step(id=f"v{i}", node="PC4", run=None, config=line) for i in range(5)]
    assert problems(lab_with(vpcs)) == [("setup[4]", "too_large")]
    check(lab_with(vpcs[:4]), ALL_IMAGES, ALL)


def test_an_iol_node_with_steps_but_no_ios_hostname_is_warned():
    lab = lab_with([step(node="r_2", run=None, config="ip routing")])
    lab["nodes"].append({"name": "r_2", "image": "cisco-iou-l3"})
    warnings = check(lab, ALL_IMAGES, ALL).warnings
    assert [w["code"] for w in warnings] == ["hostname_not_set"]


# ---------------------------------------------------------------- compilation

def test_no_steps_no_change_of_the_ticket():
    plain = check(LAB, ALL_IMAGES, ALL).definition
    empty = check(lab_with([]), ALL_IMAGES, ALL).definition
    assert json.dumps(ticket(plain), sort_keys=True) == json.dumps(ticket(empty), sort_keys=True)
    # Steps on PC1 only: every other node is byte for byte what it was.
    with_steps = nodes_of(check(lab_with([STEPS[0]]), ALL_IMAGES, ALL).definition)
    for name, node in nodes_of(plain).items():
        if name != "PC1":
            assert json.dumps(with_steps[name], sort_keys=True) == json.dumps(node, sort_keys=True)


def test_qemu_steps_follow_the_segments_in_order():
    plain = nodes_of(check(LAB, ALL_IMAGES, ALL).definition)["PC1"]["provisioning"]
    nodes = nodes_of(check(lab_with(STEPS), ALL_IMAGES, ALL).definition)
    provisioning = nodes["PC1"]["provisioning"]
    assert provisioning[:len(plain)] == plain  # The commands of the image and the network of the segments first
    assert provisioning[len(plain):] == [
        {"command": labsetup.wrap(STEPS[0], "sh"), "timeout_seconds": 90},
        {"command": labsetup.wrap(STEPS[3], "sh"), "timeout_seconds": 60},
    ]
    assert provisioning[-2]["command"].startswith("e=$(mktemp)||")
    assert "\n" + STEPS[0]["run"] + "\n" in provisioning[-2]["command"]
    slot_models().DeployTicket.model_validate(ticket(check(lab_with(STEPS), ALL_IMAGES, ALL).definition))


def test_routeros_steps_are_wrapped_for_routeros():
    definition = check(lab_with([step(node="MT1", run="/ip service disable telnet")]), ALL_IMAGES, ALL).definition
    command = nodes_of(definition)["MT1"]["provisioning"][-1]["command"]
    assert command == (':do {\n/ip service disable telnet\n} on-error={:error "gns3lab-setup s exit 1"}\n')


def test_the_longest_step_fits_the_slot():
    longest = step(id="a" * 32, run="x" * 8000)
    assert len(labsetup.wrap(longest, "sh")) <= labsetup.MAX_COMMAND
    assert len(labsetup.wrap(longest, "routeros")) <= labsetup.MAX_COMMAND
    definition = check(lab_with([longest]), ALL_IMAGES, ALL).definition
    slot_models().DeployTicket.model_validate(ticket(definition))


def test_iol_steps_go_before_the_final_end():
    plain = nodes_of(check(LAB, ALL_IMAGES, ALL).definition)["R1"]["nvram_config"]
    config = nodes_of(check(lab_with(STEPS), ALL_IMAGES, ALL).definition)["R1"]["nvram_config"]
    assert plain.endswith("logging synchronous\nend\n")
    assert config == plain[:-len("end\n")] + "router ospf 1\n network 10.0.0.0 0.0.0.255 area 0\nend\n"


def test_an_iol_node_without_segments_gets_the_compiler_header():
    steps = [step(id="a", node="R2", run=None, config="ip routing"),
             step(id="b", node="R2", run=None, config="router ospf 1\n")]
    config = nodes_of(check(lab_with(steps), ALL_IMAGES, ALL).definition)["R2"]["nvram_config"]
    assert config == ("hostname R2\nno ip domain-lookup\nline con 0\n exec-timeout 0 0\n logging synchronous\n"
                      "ip routing\nrouter ospf 1\nend\n")


def test_vpcs_steps_follow_the_address():
    nodes = nodes_of(check(lab_with(STEPS + [step(id="boot", node="PC4", run=None, config="ip dns 8.8.8.8\n")]),
                           ALL_IMAGES, ALL).definition)
    assert nodes["PC3"]["startup_script"] == "ip 192.168.10.3/24 192.168.10.254\nip dns 10.0.0.53\n"
    assert nodes["PC4"]["startup_script"] == "set pcname X\nip dns 8.8.8.8\n"  # After what the image runs


def run_sh(command):
    return subprocess.run(["sh", "-c", command], capture_output=True, text=True, timeout=30)


def test_the_sh_wrapper_keeps_the_exit_code_and_ends_stderr_with_the_marker():
    failed = run_sh(labsetup.wrap(step(id="web", run="echo out\necho noise >&2\necho 'E: no nginx' >&2\nexit 100"),
                                  "sh"))
    assert failed.returncode == 100 and failed.stdout == "out\n"
    last = [line for line in failed.stderr.splitlines() if line.strip()][-1]
    assert last == "gns3lab-setup web exit 100: E: no nginx"
    # What the manager gets is the slot's error text with that line (slot/api/lifecycle.py provision_failure)
    text = f"Mandatory provisioning command failed (exit 100) on node PC1: {last}"
    definition = check(lab_with(STEPS), ALL_IMAGES, ALL).definition
    definition["setup"][0]["id"] = "web"
    assert labsetup.failure(text, definition) == {"step": "web", "node": "PC1", "exit_code": 100,
                                                  "message": "E: no nginx"}
    ok = run_sh(labsetup.wrap(step(run="set -e\ncd /\necho fine >&2\nexit 0"), "sh"))
    assert ok.returncode == 0 and ok.stderr == "fine\n"  # A step that passes: nothing added
    quiet = run_sh(labsetup.wrap(step(id="q", run="false"), "sh"))
    assert quiet.returncode == 1 and quiet.stderr.splitlines()[-1] == "gns3lab-setup q exit 1: "


def test_failure_needs_a_step_of_this_lab():
    definition = check(lab_with(STEPS), ALL_IMAGES, ALL).definition
    assert labsetup.failure("...: gns3lab-setup nope exit 3: x", definition) is None
    assert labsetup.failure("...: gns3lab-setup ospf exit 3: x", definition) is None  # IOL steps do not fail
    assert labsetup.failure("Mandatory provisioning command failed (exit 1) on node PC1: E", definition) is None
    assert labsetup.failure("on node MT1: gns3lab-setup web exit 1", definition) == {
        "step": "web", "node": "PC1", "exit_code": 1, "message": ""}


# ---------------------------------------------------------------- the manager

SESSION_LAB = {"format": "gns3lab.lab/2", "title": "Setup", "links": [], "segments": [],
               "nodes": [{"name": "PC1", "image": "debian12"}],
               "setup": [{"id": "web", "title": "nginx", "node": "PC1", "run": "exit 100", "timeout": 90}]}


async def test_feature_and_validate(tmp_path, dsn):
    async with make_env(tmp_path, dsn) as env:  # No image catalog: no lab definitions, no setup
        assert "lab-setup" not in (await env.client.get("/api/v1/info")).json()["features"]
    async with make_env(tmp_path, dsn, images=ALL_IMAGES) as env:
        features = (await env.client.get("/api/v1/info")).json()["features"]
        assert "lab-setup" in features and "lab_constructor" in features
        r = await env.client.post("/api/v1/labs/validate", json={"lab": lab_with([step(node="NET")])})
        assert r.status_code == 422
        assert r.json()["problems"][0]["path"] == "setup[0].node"
        assert r.json()["problems"][0]["code"] == "setup_node_unsupported"


async def test_a_failed_step_fails_the_session_with_setup_failed(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=ALL_IMAGES) as env:
        env.slots[201].fail_next = {"code": "RuntimeError", "message": (
            "Mandatory provisioning command failed (exit 100) on node PC1: "
            "gns3lab-setup web exit 100: E: Unable to locate package nginx")}
        r = await start_def(env, lab=SESSION_LAB)
        assert r.status_code == 201, r.text
        sid = r.json()["session_id"]
        await env.run()
        s = (await env.client.get(f"/api/v1/sessions/{sid}")).json()
        assert s["status"] == "failed"
        assert s["error"] == {"code": "SETUP_FAILED", "retryable": False}
        assert s["setup_error"] == {"step": "web", "node": "PC1", "exit_code": 100,
                                    "message": "E: Unable to locate package nginx"}
        assert "Сообщите преподавателю" in s["status_message"] and "nginx" not in s["status_message"]
        assert (await env.services.store.slot(201))["state"] == "free"  # The lab's fault, not the slot's


async def test_a_failure_without_the_marker_stays_a_slot_failure(tmp_path, dsn):
    async with make_env(tmp_path, dsn, images=ALL_IMAGES) as env:
        env.slots[201].fail_next = {"code": "RuntimeError", "message": (
            "Mandatory provisioning command failed (exit 1) on node PC1: net_config mismatch")}
        sid = (await start_def(env, lab=SESSION_LAB)).json()["session_id"]
        await env.run()
        s = (await env.client.get(f"/api/v1/sessions/{sid}")).json()
        assert s["error"] == {"code": "SLOT_OPERATION_FAILED", "retryable": True} and "setup_error" not in s


async def test_the_first_start_may_take_the_timeouts_of_the_steps(tmp_path, dsn):
    from test_orchestrator import _backdate_stage

    async with make_env(tmp_path, dsn, images=ALL_IMAGES, lab_op_timeout=60) as env:
        env.slots[201].op_polls = 10 ** 6  # The deploy does not end
        sid = (await start_def(env, lab=SESSION_LAB)).json()["session_id"]
        await env.run(10)
        assert (await env.services.store.session(sid))["stage"] == "lab_wait"
        await _backdate_stage(env, sid, "120 seconds")  # Over 60 s, within 60 + 90 s of the step
        await env.run(2)
        assert (await env.services.store.session(sid))["stage"] == "lab_wait"
        await _backdate_stage(env, sid, "160 seconds")
        await env.run(2)
        s = await env.services.store.session(sid)
        assert s["error"]["code"] == "TIMEOUT" and s["error"]["stage"] == "lab_wait", s["status_message"]
