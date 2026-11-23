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


# ---------------------------------------------------------------------- scripts that lean on earlier ones

SITES = {"id": "sites", "title": "Сайты отвечают на R1", "node": "PC1",
         "run": "curl -s http://shop.lab/", "expect": {"exit": 0, "match": "^(?P<path>/\\S+)\\n/$"}}
FILE = {"id": "file", "title": "Файл сайта на PC2", "node": "PC2", "run": "test -f {{sites.path}} && cat -- {{sites.path}}",
        "expect": {"exit": 0, "contains": ["{{sites.path}}"]}}


def test_a_value_of_an_earlier_script_and_needs_are_checked():
    norm, problems = define(SITES, FILE, {**NGINX, "id": "after", "needs": ["sites", "file"]})
    assert problems == []
    assert norm["scripts"][1]["shell"] == "sh"  # The value is quoted for the guest's shell: the definition says it
    assert norm["scripts"][2]["needs"] == ["sites", "file"] and "needs" not in norm["scripts"][0]
    assert scripts_mod.needs_of(norm["scripts"][1]) == ["sites"]


@pytest.mark.parametrize("scripts, expected", [
    ([FILE, SITES], ("checks.scripts[0].run", "unknown_reference")),  # Only an earlier script
    ([SITES, {**FILE, "run": "cat {{sites.root}}"}], ("checks.scripts[1].run", "unknown_reference")),
    ([SITES, {**FILE, "run": "true", "expect": {"contains": ["{{site.path}}"]}}],
     ("checks.scripts[1].expect.contains", "unknown_reference")),
    ([SITES, {**FILE, "run": "true", "expect": {"match": "^{{sites.x}}$"}}],
     ("checks.scripts[1].expect.match", "unknown_reference")),
    ([SITES, {**FILE, "node": "R1"}], ("checks.scripts[1].shell", "required")),  # Family unknown: say the shell
    ([SITES, {**FILE, "run": "{{sites.path}}" * 8}], ("checks.scripts[1].run", "out_of_range")),
    ([SITES, {**NGINX, "needs": ["nginx"]}], ("checks.scripts[1].needs", "unknown_reference")),
    ([SITES, {**NGINX, "needs": ["sites", "sites"]}], ("checks.scripts[1].needs", "bad_type")),
    ([SITES, {**NGINX, "needs": []}], ("checks.scripts[1].needs", "bad_type")),
])
def test_bad_references(scripts, expected):
    norm, problems = define(*scripts)
    assert expected in codes(problems) and norm is None


def test_references_need_no_shell_outside_run_or_with_one_given():
    assert define(SITES, {**FILE, "node": "R1", "shell": "powershell"})[1] == []
    assert define(SITES, {**FILE, "node": "R1", "run": "true"})[1] == []
    assert define(SITES, {**FILE, "run": "echo {{x.y z}} {{.Name}}"})[1] == []  # Not a reference: left as text


def test_a_captured_value_goes_on_quoted():
    scripts = scripts_of(SITES, FILE)
    path = "/srv/it's/$HOME/index.html"
    results, calls = run(scripts, {"n-PC1": {"exit_code": 0, "stdout": path + "\n/\n", "stderr": ""},
                                   "n-PC2": {"exit_code": 0, "stdout": path, "stderr": ""}})
    quoted = "'/srv/it'\\''s/$HOME/index.html'"
    assert calls[1][1] == f"test -f {quoted} && cat -- {quoted}"
    assert [r["status"] for r in results] == ["pass", "pass"]
    assert results[0]["captures"] == {"path": path} and "captures" not in results[1]


def test_quoting_for_each_shell():
    value = "a'b\u2019c\"d$e\\f"
    assert scripts_mod.quote_sh(value) == "'a'\\''b\u2019c\"d$e\\f'"
    assert scripts_mod.quote_powershell(value) == "'a''b\u2019\u2019c\"d$e\\f'"
    assert scripts_mod.quote_routeros(value) == '"a\'b\u2019c\\"d\\$e\\\\f"'
    scripts = scripts_of(SITES, {**FILE, "node": "CHR", "run": ":put [/file get {{sites.path}} size]"})
    _, calls = run(scripts, {"n-PC1": {"exit_code": 0, "stdout": "/a$b\n/", "stderr": ""},
                             "n-CHR": {"exit_code": 0, "stdout": "/a$b", "stderr": ""}})
    assert calls[1][1] == ':put [/file get "/a\\$b" size]' and calls[1][3] == "routeros"


def test_a_value_that_cannot_go_on_is_a_mismatch():
    scripts = scripts_of({**SITES, "expect": {"match": "^(?P<path>/.*)$"}}, FILE)
    for stdout in ("/" + "x" * 256, "/a\tb"):
        results, calls = run(scripts, {"n-PC1": {"exit_code": 0, "stdout": stdout, "stderr": ""}})
        assert results[0]["reason"] == "mismatch" and "captures" not in results[0]
        assert (results[1]["status"], results[1]["reason"], results[1]["blocked_by"]) == ("skipped", "needs_failed",
                                                                                          "sites")
        assert len(calls) == 1
    alone = scripts_of({**SITES, "expect": {"match": "^(?P<path>/.*)$"}})
    results, _ = run(alone, {"n-PC1": {"exit_code": 0, "stdout": "/" + "x" * 300, "stderr": ""}})
    assert results[0]["status"] == "pass"  # Nobody takes the value: its length does not matter
    assert len(results[0]["captures"]["path"]) == 256


def test_needs_hold_a_script_back():
    scripts = scripts_of(SITES, {**NGINX, "node": "PC2", "needs": ["sites"]}, {**NGINX, "id": "free", "node": "PC2"})
    answers = {"n-PC1": {"exit_code": 1, "stdout": "", "stderr": ""},
               "n-PC2": {"exit_code": 0, "stdout": "active\n200", "stderr": ""}}
    results, calls = run(scripts, answers)
    assert [(r["status"], r.get("reason"), r.get("blocked_by")) for r in results] == [
        ("fail", "exit", None), ("skipped", "needs_failed", "sites"), ("pass", None, None)]
    assert [c[0] for c in calls] == ["n-PC1", "n-PC2"]
    results, _ = run(scripts, {**answers, "n-PC1": "unavailable"})
    assert results[1]["reason"] == "collect_failed" and results[1]["blocked_by"] == "sites"


def test_a_held_back_script_is_no_finding():
    scripts = scripts_of(SITES, {**NGINX, "needs": ["sites"]})
    held = {**result("nginx", "PC1", "skipped", "needs_failed"), "blocked_by": "sites"}
    out = graded(scripts, [result("sites", "PC1", "fail", "exit"), held])
    assert [f["script_id"] for f in out["findings"]] == ["sites"] and out["mark"] == 4
    assert out["outcome"] == "graded"
    transient = {**result("nginx", "PC1", "skipped", "collect_failed"), "blocked_by": "sites"}
    out = graded(scripts, [result("sites", "PC1", "skipped", "collect_failed"), transient])
    assert out["outcome"] == "incomplete"


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


def test_the_library_of_snippets_passes_the_manager():
    """Every snippet of examples/checks is one ready element of `scripts`, and the README names each of them."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "examples" / "checks"
    files = sorted(root.glob("*.json"))
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert len(files) >= 7
    items = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    for f in files:
        assert f"`{f.name}`" in readme, f.name
    nodes = {item["node"] for item in items}
    family = {item["node"]: item["shell"] if item["shell"] != "powershell" else None for item in items}
    support = {name: {"ok": True, "family": family[name]} for name in nodes}
    norm, problems = check_definition({"format": "gns3lab.checks/1", "scripts": items}, nodes, script_nodes=support)
    assert problems == [], problems
    assert len({s["id"] for s in norm["scripts"]}) == len(items)
    assert sum(s["timeout"] for s in norm["scripts"]) <= scripts_mod.MAX_TOTAL_TIMEOUT
    nginx = next(s for s in norm["scripts"] if s["id"] == "nginx")
    assert scripts_mod.judge(nginx, "ab" * 8, {"exit_code": 0, "stdout": "active\r\n200\r\n"})[:2] == (None, False)
    assert scripts_mod.judge(nginx, "ab" * 8, {"exit_code": 3, "stdout": "inactive\n000"})[0] == "exit"
    ssh = next(s for s in norm["scripts"] if s["id"] == "ssh-key")
    assert scripts_mod.judge(ssh, "ab" * 8, {"exit_code": 0, "stdout": "ab" * 8})[0] is None
    assert scripts_mod.judge(ssh, "ab" * 8, {"exit_code": 0, "stdout": "ok"})[0] == "mismatch"
    lease = next(s for s in norm["scripts"] if s["id"] == "dhcp-lease")
    out = "2: ens3    inet 192.168.10.57/24 brd 192.168.10.255 scope global dynamic ens3\\       valid_lft 86321sec"
    assert scripts_mod.judge(lease, "ab" * 8, {"exit_code": 0, "stdout": out})[0] is None
    isolated = next(s for s in norm["scripts"] if s["id"] == "isolated-guest")
    assert scripts_mod.judge(isolated, "ab" * 8, {"exit_code": 0, "stdout": "reachable\n"})[0] == "mismatch"
