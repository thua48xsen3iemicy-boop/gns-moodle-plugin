"""The example lab of docs/lab-setup/examples/routeros-gateway: setup steps and the activity checks that verify them.

The example is meant to be pasted into the lab editor and the activity form, so this test keeps it valid for the
manager as it is: the lab with its steps passes `check`, the steps land in the slot's ticket as expected, the checks
document passes, and the checks judge what RouterOS and Debian print as their authors meant. Answers of the guests
below are what CHR 7.25.1 and Debian 12 are expected to print; the stand test of the example's README confirms them.
"""

import asyncio
import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_labdef import ALL, ROOT

from lab_manager import labsetup, scripts
from lab_manager.images import load_images
from lab_manager.labdef import check, check_checks, deploy_ticket

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "lab-setup" / "examples" / "routeros-gateway"
pytestmark = pytest.mark.skipif(not EXAMPLE.is_dir(), reason="the docs are not in this tree (a release of the manager)")

IMAGES = load_images(ROOT / "examples" / "images.json")
PID = "5d1c7a52-3b8e-4c1f-9a0d-6e2f4b8c1a37"


def load(name):
    return json.loads((EXAMPLE / name).read_text(encoding="utf-8"))


def lab():
    definition = load("lab.json")
    definition["setup"] = load("setup.json")["setup"]
    return definition


def steps():
    return load("setup.json")["setup"]


def checks():
    return load("checks.json")


# ---------------------------------------------------------------- the lab and its steps

def test_the_lab_with_its_steps_is_valid():
    checked = check(lab(), IMAGES, ALL)
    # CHR as a gateway is the teacher's (here: the setup's) business; nothing else is reported
    assert sorted((w["path"], w["code"]) for w in checked.warnings) == [
        ("segments[0].gateway", "gateway_not_configured"), ("segments[1].gateway", "gateway_not_configured")]
    assert [s["id"] for s in checked.definition["setup"]] == [s["id"] for s in steps()]


def test_routeros_steps_are_the_only_provisioning_of_r1_and_fit_the_slot():
    checked = check(lab(), IMAGES, ALL)
    nodes = {n["name"]: n for n in deploy_ticket(checked.definition, IMAGES, project_id=PID,
                                                 project_name="lab-1")["nodes"]}
    r1 = [p["command"] for p in nodes["R1"]["provisioning"]]
    own = [s for s in steps() if s["node"] == "R1"]
    # The constructor leaves a CHR gateway alone: R1 gets only its setup steps, the first one must wait for the ports
    assert r1 == [labsetup.wrap(s, "routeros") for s in own]
    assert ":while ([:len [/interface ethernet find]] < 2" in own[0]["run"]
    assert all(len(c) <= labsetup.MAX_COMMAND for c in r1)
    for name in ("PC1", "SRV1"):  # Linux: the segment's network script first, then the step
        commands = [p["command"] for p in nodes[name]["provisioning"]]
        assert commands[-1] == labsetup.wrap(next(s for s in steps() if s["node"] == name), "sh")
        assert len(commands) >= 2


def test_steps_are_repeatable_by_construction():
    """A differential image layer runs the steps again over their own result (docs/lab-setup/README.md §5)."""
    for step in steps():
        if step.get("shell") != "routeros":
            continue
        for line in step["run"].splitlines():
            line = line.strip()
            if re.match(r"^/\S.* add ", line):  # Every add is preceded by a remove or guarded by :if
                menu = line.split(" add ")[0]
                before = step["run"].split(line)[0]
                assert f"{menu} remove" in before, f"{step['id']}: {line!r} is not repeatable"
            elif re.match(r"^:if .* do=\{/\S.* add ", line):
                assert "= 0)" in line, f"{step['id']}: {line!r}"


def routeros_problems(text):
    """What a RouterOS script must not have: unbalanced brackets or quotes, a menu line that only changes the
    context (it leaks out of the manager's `:do {}`), globals."""
    out, stack, quoted, escaped = [], [], False, False
    pairs = {"}": "{", "]": "[", ")": "("}
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("#"):
            continue
        if re.fullmatch(r"\s*/[a-z -]+", line):
            out.append(f"line {n}: menu without a command")
        if ":global" in line:
            out.append(f"line {n}: :global")
        for ch in line:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = not quoted
            elif quoted:
                continue
            elif ch in "{[(":
                stack.append(ch)
            elif ch in pairs:
                if not stack or stack.pop() != pairs[ch]:
                    out.append(f"line {n}: unbalanced {ch}")
        if quoted:
            out.append(f"line {n}: unclosed quote")
            quoted = False
    if stack:
        out.append(f"unclosed {''.join(stack)}")
    return out


def all_routeros():
    return ([(s["id"], s["run"]) for s in steps() if s.get("shell") == "routeros"]
            + [(s["id"], s["run"]) for s in checks()["scripts"] if s.get("shell") == "routeros"])


@pytest.mark.parametrize("sid,text", all_routeros(), ids=[i for i, _ in all_routeros()])
def test_routeros_scripts_are_well_formed(sid, text):
    assert routeros_problems(text) == []
    assert routeros_problems(labsetup.wrap({"id": sid, "run": text}, "routeros")) == []


def all_sh():
    return ([(s["id"], s["run"]) for s in steps() if s.get("shell") == "sh"]
            + [(s["id"], s["run"]) for s in checks()["scripts"] if s.get("shell") == "sh"])


@pytest.mark.skipif(shutil.which("dash") is None and shutil.which("sh") is None, reason="no POSIX shell")
@pytest.mark.parametrize("sid,text", all_sh(), ids=[i for i, _ in all_sh()])
def test_sh_scripts_parse_in_a_posix_shell(sid, text):
    shell = shutil.which("dash") or shutil.which("sh")  # Debian runs /bin/sh = dash
    for body in (text, labsetup.wrap({"id": sid, "run": text}, "sh")):
        done = subprocess.run([shell, "-n", "-c", body], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr


# ---------------------------------------------------------------- the checks of the activity

def test_the_checks_document_is_valid_for_the_lab():
    checked = check(lab(), IMAGES, ALL)
    normalized, problems = check_checks(checks(), checked.definition, IMAGES)
    assert problems == []
    assert len(normalized["scripts"]) == len(checks()["scripts"])


def test_the_checks_are_valid_without_the_setup_too():
    """Activity checks are checked against the lab they are used with; the lab without steps has the same nodes."""
    plain = copy.deepcopy(lab())
    plain.pop("setup")
    assert check_checks(checks(), check(plain, IMAGES, ALL).definition, IMAGES)[1] == []


# What the guests print when the setup did its work: script id -> answer of the slot's qga/exec
GOOD = {
    "r1-identity": {"exit_code": 0, "stdout": "R1-SETUP\r\n"},
    "r1-gw-lan1": {"exit_code": 0, "stdout": "lan1 ether1 running=true\r\n"},
    "r1-gw-lan2": {"exit_code": 0, "stdout": "lan2 ether2 running=true\r\n"},
    "r1-loopback": {"exit_code": 0, "stdout": "lo0 1\r\n"},
    "r1-dns": {"exit_code": 0, "stdout": "srv1.lab 192.168.20.10\r\n"},
    "r1-user": {"exit_code": 0, "stdout": "auditor read\r\n"},
    "r1-services": {"exit_code": 0, "stdout": "telnet disabled=true\r\nftp disabled=true\r\n"
                                              "www disabled=true\r\napi disabled=true\r\n"},
    "r1-fw": {"exit_code": 0, "stdout": "rules=4 drop=1\r\n"},
    "srv1-gw": {"exit_code": 0, "stdout": "--- 192.168.20.1 ping statistics ---\n"
                                          "2 packets transmitted, 2 received, 0% packet loss, time 1001ms\n"},
    "pc1-gw": {"exit_code": 0, "stdout": "--- 192.168.10.1 ping statistics ---\n2 packets transmitted, 2 received\n"},
    "pc1-srv1": {"exit_code": 0, "stdout": "2 packets transmitted, 2 received\n"},
    "pc1-hosts": {"exit_code": 0, "stdout": "192.168.20.10   srv1.lab\n10.255.255.1    r1.lab\n"},
    "pc1-r1-lo": {"exit_code": 0, "stdout": "2 packets transmitted, 2 received\n"},
    "srv1-user": {"exit_code": 0, "stdout": "/bin/bash P\n"},
    "srv1-file": {"exit_code": 0, "stdout": "gns3lab setup ok: SRV1\n"},
}
LOST = {"exit_code": 1, "stdout": "2 packets transmitted, 0 received, +2 errors, 100% packet loss\n"}

# What breaks -> (answers that change, {failed id: reason}, {skipped id: blocked_by})
SCENARIOS = {
    "all good": ({}, {}, {}),
    # The student removed the LAN1 address of R1 (or r1-ports gave it to no port): one failure, PC1 checks wait
    "no LAN1 address on R1": (
        {"r1-gw-lan1": {"exit_code": 0, "stdout": "lan1 count=0\r\n"}},
        {"r1-gw-lan1": "mismatch"},
        {"pc1-gw": "r1-gw-lan1", "pc1-srv1": "pc1-gw", "pc1-r1-lo": "pc1-gw"}),
    # Ports by name (etherN) instead of by MAC put LAN1 on the port to SRV1: R1 itself looks fine, the hosts do not
    "LAN1 and LAN2 swapped on R1 ports": (
        {"r1-gw-lan1": {"exit_code": 0, "stdout": "lan1 ether2 running=true\r\n"},
         "r1-gw-lan2": {"exit_code": 0, "stdout": "lan2 ether1 running=true\r\n"},
         "srv1-gw": LOST, "pc1-gw": LOST},
        {"srv1-gw": "exit", "pc1-gw": "exit"},
        {"pc1-srv1": "pc1-gw", "pc1-r1-lo": "pc1-gw"}),
    "the student enabled telnet": (
        {"r1-services": {"exit_code": 0, "stdout": "telnet disabled=false\r\nftp disabled=true\r\n"
                                                   "www disabled=true\r\napi disabled=true\r\n"}},
        {"r1-services": "mismatch"}, {}),
    # RouterOS gives no exit code: a script error is -1, and the error text is the last line of the output
    "a RouterOS error in a check": (
        {"r1-dns": {"exit_code": -1, "stdout": "no such item\r\n", "stderr": "no such item"}},
        {"r1-dns": "exit"}, {}),
    "the loopback is gone": (
        {"r1-loopback": {"exit_code": 0, "stdout": "lo0 0\r\n"}},
        {"r1-loopback": "mismatch"}, {"pc1-r1-lo": "r1-loopback"}),
    # cloud-init rebuilt /etc/hosts at a reboot from a template without the names
    "names lost on PC1": (
        {"pc1-hosts": {"exit_code": 2, "stdout": ""}},
        {"pc1-hosts": "mismatch"}, {"pc1-r1-lo": "pc1-hosts"}),
    "the firewall rule disabled": (
        {"r1-fw": {"exit_code": 0, "stdout": "rules=4 drop=0\r\n"}},
        {"r1-fw": "mismatch"}, {}),
    "no password for netops": (
        {"srv1-user": {"exit_code": 0, "stdout": "/bin/bash L\n"}},
        {"srv1-user": "mismatch"}, {}),
}


def run_checks(answers):
    normalized, problems = check_checks(checks(), check(lab(), IMAGES, ALL).definition, IMAGES)
    assert problems == []
    by_command = {s["run"]: s["id"] for s in normalized["scripts"]}
    nodes = {"R1": "n-r1", "PC1": "n-pc1", "SRV1": "n-srv1"}
    sent = []

    async def execute(node_id, command, timeout, shell):
        sid = by_command[command]
        script = next(s for s in normalized["scripts"] if s["id"] == sid)
        assert node_id == nodes[script["node"]] and shell == script["shell"]
        sent.append(sid)
        return {"stderr": "", **answers[sid]}

    result = asyncio.run(scripts.run(normalized["scripts"], nonce="0123456789abcdef", node_ids=nodes,
                                     statuses={n: "started" for n in nodes.values()}, exec_shell=True,
                                     execute=execute))
    return result, sent


@pytest.mark.parametrize("name", SCENARIOS)
def test_one_fault_gives_its_own_failures_and_the_dependent_checks_wait(name):
    changed, failed, skipped = SCENARIOS[name]
    result, sent = run_checks({**GOOD, **changed})
    assert {r["id"]: r["reason"] for r in result if r["status"] == "fail"} == failed
    assert {r["id"]: r["blocked_by"] for r in result if r["status"] == "skipped"} == skipped
    assert all(r["reason"] == "needs_failed" for r in result if r["status"] == "skipped")
    assert set(sent) == {r["id"] for r in result if r["status"] != "skipped"}


def test_every_check_can_fail():
    """No check passes on any output: each one has a scenario above, or fails on an empty answer."""
    for script in checks()["scripts"]:
        result, _ = run_checks({**GOOD, script["id"]: {"exit_code": 0, "stdout": ""}})
        mine = next(r for r in result if r["id"] == script["id"])
        if script["id"].endswith(("-gw", "-srv1", "-r1-lo")):  # ping: the exit code decides
            result, _ = run_checks({**GOOD, script["id"]: LOST})
            mine = next(r for r in result if r["id"] == script["id"])
        assert mine["status"] == "fail", script["id"]


# ---------------------------------------------------------------- show.py of the stand test

def test_show_py_wraps_the_steps_as_the_manager_does():
    import importlib.util
    spec = importlib.util.spec_from_file_location("example_show", EXAMPLE / "show.py")
    show = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(show)
    assert (show.SH_WRAPPER, show.ROUTEROS_WRAPPER) == (labsetup.SH_WRAPPER, labsetup.ROUTEROS_WRAPPER)
    for step in steps():
        assert show.wrap(step) == labsetup.wrap(step, step["shell"])
    assert show.steps("R1") and show.checks("R1") and show.checks("PC1") and show.checks("SRV1")
