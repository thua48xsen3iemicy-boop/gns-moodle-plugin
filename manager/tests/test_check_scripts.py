"""Teacher scripts of an auto-check: the format, a run in the guests, the grade (CONTRACTS 2.9 `scripts`)."""

import asyncio
import copy

import pytest

from lab_manager import scripts as scripts_mod
from lab_manager.checks import Finding, check_definition, grade, normalize_grading
from lab_manager.labdef import LabInvalid, check
from lab_manager.labs import script_nodes_of
from test_checks import DEFINITION
from test_labdef import ALL
from test_segments import IMAGES

NODES = {"PC1", "PC2", "R1", "SW1", "CHR"}
SUPPORTED = {"PC1": {"ok": True, "family": "sh"}, "PC2": {"ok": True, "family": "sh"},
             "R1": {"ok": True, "family": None}, "SW1": {"ok": False, "family": None},
             "CHR": {"ok": True, "family": "routeros"}}
NGINX = {"id": "nginx", "title": "Веб-сервер на PC1", "node": "PC1",
         "run": "systemctl is-active nginx; curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/",
         "expect": {"exit": 0, "match": "^active\\n200$"}}


def define(*scripts, script_nodes=SUPPORTED):
    return check_definition({"format": "gns3lab.checks/1", "scripts": list(scripts)}, NODES,
                            script_nodes=script_nodes)


def codes(problems):
    return {(p["path"], p["code"]) for p in problems}


def test_a_script_gets_its_defaults():
    norm, problems = define(NGINX, {"id": "ping", "title": "Ping", "node": "CHR", "shell": "routeros",
                                    "run": ":put [/ping 10.0.0.1 count=1]", "timeout": 5, "group": "routing",
                                    "class": "critical"})
    assert problems == []
    assert norm["scripts"] == [
        {**NGINX, "timeout": 20, "group": "host", "class": "major"},
        {"id": "ping", "title": "Ping", "node": "CHR", "shell": "routeros", "run": ":put [/ping 10.0.0.1 count=1]",
         "timeout": 5, "expect": {"exit": 0}, "group": "routing", "class": "critical"}]
    norm, problems = check_definition({"format": "gns3lab.checks/1", "scripts": []}, NODES)
    assert problems == [] and "scripts" not in norm


@pytest.mark.parametrize("change, expected", [
    ({"extra": 1}, ("checks.scripts[0].extra", "unknown_field")),
    ({"id": "Nginx!"}, ("checks.scripts[0].id", "bad_name")),
    ({"node": "PCX"}, ("checks.scripts[0].node", "unknown_node")),
    ({"node": "SW1"}, ("checks.scripts[0].node", "script_node_unsupported")),
    ({"node": "CHR"}, None),  # No shell given: the guest's own
    ({"node": "CHR", "shell": "sh"}, ("checks.scripts[0].shell", "shell_mismatch")),
    ({"node": "R1", "shell": "powershell"}, None),  # Family unknown before the run
    ({"shell": "bash"}, ("checks.scripts[0].shell", "out_of_range")),
    ({"run": ""}, ("checks.scripts[0].run", "bad_type")),
    ({"run": "x" * 8000}, None),
    ({"run": "x" * 7993 + "{{nonce}}"}, ("checks.scripts[0].run", "out_of_range")),  # 16 hex in place of 9
    ({"timeout": 61}, ("checks.scripts[0].timeout", "out_of_range")),
    ({"timeout": "5"}, ("checks.scripts[0].timeout", "bad_type")),
    ({"expect": {}}, ("checks.scripts[0].expect", "required")),
    ({"expect": {"match": "(a)\\1"}}, ("checks.scripts[0].expect.match", "bad_regex")),
    ({"expect": {"contains": []}}, ("checks.scripts[0].expect.contains", "bad_type")),
    ({"expect": {"stderr": "x"}}, ("checks.scripts[0].expect.stderr", "unknown_field")),
    ({"group": "web"}, ("checks.scripts[0].group", "out_of_range")),
    ({"class": "fatal"}, ("checks.scripts[0].class", "out_of_range")),
    ({"title": None}, ("checks.scripts[0].title", "required")),
])
def test_bad_scripts(change, expected):
    script = {k: v for k, v in {**NGINX, **change}.items() if v is not None}
    norm, problems = define(script)
    if expected is None:
        assert problems == [] and norm["scripts"]
    else:
        assert expected in codes(problems) and norm is None


def test_limits_of_the_list():
    many = [{**NGINX, "id": f"s{k}", "timeout": 1} for k in range(21)]
    assert codes(define(*many)[1]) == {("checks.scripts", "out_of_range")}
    slow = [{**NGINX, "id": f"s{k}", "timeout": 60} for k in range(3)]
    assert codes(define(*slow)[1]) == {("checks.scripts", "out_of_range")}
    twice = define(NGINX, NGINX)[1]
    assert codes(twice) == {("checks.scripts[1].id", "duplicate_name")}


def test_the_lab_definition_knows_which_nodes_run_scripts():
    lab = copy.deepcopy(DEFINITION)
    lab["nodes"].append({"name": "PC9", "image": "vpcs"})
    lab["checks"]["scripts"] = [{**NGINX, "node": "PC9"}, {**NGINX, "id": "pc", "shell": "routeros"},
                                {**NGINX, "id": "r1", "node": "R1"}]
    with pytest.raises(LabInvalid) as exc:
        check(lab, IMAGES, ALL)
    assert codes(exc.value.problems) == {("checks.scripts[0].node", "script_node_unsupported"),
                                         ("checks.scripts[1].shell", "shell_mismatch")}
    lab["checks"]["scripts"] = [NGINX | {"node": "PC2"}, NGINX | {"id": "sw", "node": "SW1"}]
    assert check(lab, IMAGES, ALL).definition["checks"]["scripts"][0]["node"] == "PC2"


def test_the_catalog_lab_knows_which_nodes_run_scripts():
    ticket = {"nodes": [{"name": "SW1", "kind": "iou"}, {"name": "D1", "kind": "qemu"},
                        {"name": "D2", "kind": "qemu", "qga": False}, {"name": "ISP", "kind": "qemu", "uplink_port": 0}]}
    assert script_nodes_of(ticket) == {"SW1": {"ok": False, "family": None}, "D1": {"ok": True, "family": None},
                                       "D2": {"ok": False, "family": None}, "ISP": {"ok": False, "family": None}}


def test_overrides_take_script_failed():
    assert normalize_grading({"overrides": {"script_failed": "minor"}})["overrides"] == {"script_failed": "minor"}


# ---------------------------------------------------------------------- a run

def scripts_of(*items):
    return define(*items)[0]["scripts"]


def run(scripts, answers, statuses=None, exec_shell=True, nonce="ab" * 8):
    """answers: node_id -> answer dict or ScriptError kind; returns (results, calls)."""
    calls = []

    async def execute(node_id, command, timeout, shell):
        calls.append((node_id, command, timeout, shell))
        answer = answers[node_id]
        if isinstance(answer, str):
            raise scripts_mod.ScriptError(answer)
        return answer

    ids = {n: "n-" + n for n in NODES}
    statuses = statuses if statuses is not None else {i: "started" for i in ids.values()}
    results = asyncio.run(scripts_mod.run(scripts, nonce=nonce, node_ids=ids, statuses=statuses,
                                          exec_shell=exec_shell, execute=execute))
    return results, calls


def test_scripts_pass_and_fail():
    scripts = scripts_of(NGINX, {**NGINX, "id": "ssh", "node": "PC2", "run": "ssh admin@pc1 echo {{nonce}}",
                                 "expect": {"exit": 0, "contains": ["{{nonce}}"], "not_contains": ["denied"]}})
    results, calls = run(scripts, {"n-PC1": {"exit_code": 0, "stdout": "active\r\n200\r\n", "stderr": ""},
                                   "n-PC2": {"exit_code": 0, "stdout": "abababababababab\n", "stderr": ""}})
    assert [r["status"] for r in results] == ["pass", "pass"]
    assert calls[1] == ("n-PC2", "ssh admin@pc1 echo abababababababab", 20, None)
    assert results[0]["stdout"] == "active\r\n200\r\n" and results[0]["exit_code"] == 0
    assert results[0]["truncated"] is False and isinstance(results[0]["duration_ms"], int)

    results, _ = run(scripts, {"n-PC1": {"exit_code": 3, "stdout": "inactive\n000", "stderr": ""},
                               "n-PC2": {"exit_code": 0, "stdout": "ok", "stderr": ""}})
    assert [(r["status"], r.get("reason")) for r in results] == [("fail", "exit"), ("fail", "mismatch")]
    results, _ = run(scripts[1:], {"n-PC2": {"exit_code": 0, "stdout": "abababababababab denied", "stderr": ""}})
    assert results[0]["reason"] == "mismatch"


def test_what_the_slot_says_becomes_a_reason():
    scripts = scripts_of(NGINX)
    for kind, expected in (("timeout", ("fail", "timeout")), ("shell_mismatch", ("skipped", "unsupported_os")),
                           ("unavailable", ("skipped", "collect_failed"))):
        results, _ = run(scripts, {"n-PC1": kind})
        assert (results[0]["status"], results[0]["reason"]) == expected
        assert results[0]["exit_code"] is None and results[0]["stdout"] == ""
    results, calls = run(scripts, {}, statuses={"n-PC1": "stopped"})
    assert (results[0]["status"], results[0]["reason"], results[0]["duration_ms"]) == ("fail", "node_stopped", None)
    assert calls == []


def test_powershell_needs_exec_shell():
    scripts = scripts_of({**NGINX, "node": "R1", "shell": "powershell", "expect": {"exit": 0}})
    results, calls = run(scripts, {"n-R1": {"exit_code": 0, "stdout": "", "stderr": ""}}, exec_shell=False)
    assert results[0]["reason"] == "unsupported_os" and calls == []
    results, calls = run(scripts, {"n-R1": {"exit_code": 0, "stdout": "", "stderr": ""}})
    assert results[0]["status"] == "pass" and calls[0][3] == "powershell"
    sh = scripts_of({**NGINX, "shell": "sh"})
    _, calls = run(sh, {"n-PC1": {"exit_code": 0, "stdout": "active\n200", "stderr": ""}}, exec_shell=False)
    assert calls[0][3] is None  # An older slot takes no shell field: it picks the guest's own


def test_long_output_is_cut_on_a_character():
    scripts = scripts_of({**NGINX, "expect": {"exit": 0}})
    results, _ = run(scripts, {"n-PC1": {"exit_code": 0, "stdout": "я" * 2000, "stderr": "e" * 2000}})
    assert results[0]["stdout"] == "я" * 1024 and results[0]["stderr"] == "e" * 1024
    assert results[0]["truncated"] is True and results[0]["status"] == "pass"
    results, _ = run(scripts, {"n-PC1": {"exit_code": 0, "stdout": "x", "stderr": "", "truncated": True}})
    assert results[0]["truncated"] is True


def test_only_the_first_64_kib_are_compared():
    scripts = scripts_of({**NGINX, "expect": {"contains": ["tail"]}})
    results, _ = run(scripts, {"n-PC1": {"exit_code": 0, "stdout": "x" * 65536 + "tail", "stderr": ""}})
    assert results[0]["reason"] == "mismatch" and results[0]["truncated"] is True


# ---------------------------------------------------------------------- the grade

def graded(scripts, results, focus=None, overrides=None, findings=()):
    checks = {"format": "gns3lab.checks/1", "scripts": scripts, **({"focus": focus} if focus else {})}
    return grade([], list(findings), normalize_grading({"overrides": overrides or {}}), checks, results, "ab" * 8)


def result(sid, node, status, reason=None):
    return {"id": sid, "node": node, "status": status, **({"reason": reason} if reason else {}),
            "exit_code": None, "stdout": "", "stderr": "", "truncated": False, "duration_ms": None}


def test_a_failed_script_is_a_finding_of_its_class():
    scripts = scripts_of(NGINX, {**NGINX, "id": "ssh", "title": "SSH", "group": "routing", "class": "minor"})
    out = graded(scripts, [result("nginx", "PC1", "fail", "mismatch"), result("ssh", "PC1", "pass")])
    assert out["findings"] == [{
        "id": "f1", "code": "script_failed", "script_id": "nginx", "group": "host", "class": "major",
        "base_class": "major", "counted": True, "caused_by": None, "nodes": ["PC1"], "ifaces": [],
        "message": "Веб-сервер на PC1: вывод не совпал с ожидаемым"}]
    assert out["counts"]["major"] == 1 and out["mark"] == 4 and out["outcome"] == "graded"
    assert out["rules"] == [{"code": "script_failed", "group": "host", "status": "fail"}]
    assert out["nonce"] == "ab" * 8 and [s["id"] for s in out["scripts"]] == ["nginx", "ssh"]

    raised = graded(scripts, [result("nginx", "PC1", "pass"), result("ssh", "PC1", "fail", "exit")], focus=["routing"])
    assert raised["findings"][0]["class"] == "major" and raised["findings"][0]["base_class"] == "minor"
    over = graded(scripts, [result("nginx", "PC1", "fail", "timeout"), result("ssh", "PC1", "pass")],
                  overrides={"script_failed": "note"})
    assert over["findings"][0]["class"] == "note" and over["findings"][0]["counted"] is False and over["mark"] == 5
    assert over["findings"][0]["message"] == "Веб-сервер на PC1: не завершился за 20 с"


def test_a_stopped_node_explains_its_script():
    stopped = Finding("node_stopped", "PC1 остановлена", ["PC1"], [], set(), id="f1")
    out = graded(scripts_of(NGINX), [result("nginx", "PC1", "fail", "node_stopped")], findings=[stopped])
    assert out["findings"][1]["caused_by"] == "f1" and out["findings"][1]["counted"] is False
    assert out["findings"][1]["id"] == "f2" and out["findings"][1]["message"] == "Веб-сервер на PC1: нода PC1 остановлена"


def test_a_guest_that_did_not_answer_leaves_the_run_incomplete():
    scripts = scripts_of(NGINX, {**NGINX, "id": "r", "group": "routing"})
    out = graded(scripts, [result("nginx", "PC1", "skipped", "collect_failed"), result("r", "PC1", "pass")])
    assert out["outcome"] == "incomplete" and out["mark"] is None
    out = graded(scripts, [result("nginx", "PC1", "skipped", "collect_failed"), result("r", "PC1", "pass")],
                 focus=["routing"])
    assert out["outcome"] == "graded"  # The script outside the focus does not hold the mark back
    out = graded(scripts, [result("nginx", "PC1", "skipped", "unsupported_os"),
                           result("r", "PC1", "skipped", "unsupported_os")])
    assert out["outcome"] == "graded"
    assert out["rules"] == [{"code": "script_failed", "group": "host", "status": "skipped", "reason": "unsupported_os"}]


def test_a_run_without_scripts():
    out = grade([], [], normalize_grading(None), {"format": "gns3lab.checks/1"})
    assert out["scripts"] == [] and out["nonce"] is None and out["rules"] == []
