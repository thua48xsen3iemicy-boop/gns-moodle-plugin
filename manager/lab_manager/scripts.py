"""Teacher scripts of an auto-check (CONTRACTS 2.9 `scripts`, docs/autochecks/README.md §7).

A script runs in a guest of the lab through the slot's `qga/exec`; the manager only substitutes the run marker,
sends the text and compares the answer. Regular expressions are RE2 (linear time): a teacher's pattern must not be
able to hang the manager.
"""

import secrets
import time

import re2

SHELLS = ("sh", "powershell", "routeros")
GROUPS = ("l1", "l2", "addressing", "gateways", "routing", "host")
CLASSES = ("critical", "major", "minor", "note")
ID_RE = re2.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
FIELDS = {"id", "title", "node", "shell", "run", "timeout", "expect", "group", "class"}
EXPECT_FIELDS = {"exit", "match", "contains", "not_contains"}
NONCE = "{{nonce}}"
NONCE_LEN = 16
MAX_SCRIPTS = 20
MAX_RUN_BYTES = 8000
MAX_TIMEOUT = 60
DEFAULT_TIMEOUT = 20
MAX_TOTAL_TIMEOUT = 120
MAX_PATTERN = 500
MAX_STRINGS = 10
COMPARED_BYTES = 64 * 1024
KEPT_STDOUT = 2048
KEPT_STDERR = 1024
LINUX_NET_CONFIG = ("ifupdown", "networkmanager", "netplan", "networkd")
TRANSIENT = "collect_failed"

_OPTIONS = re2.Options()
_OPTIONS.log_errors = False  # A bad pattern is a validation problem, not a line in the manager's log


def family_of(net_config):
    """The shell of an image's guest family as the manager knows it from net_config; None if unknown."""
    if net_config == "routeros":
        return "routeros"
    if net_config in LINUX_NET_CONFIG:
        return "sh"
    return None


def new_nonce() -> str:
    return secrets.token_hex(NONCE_LEN // 2)


def substitute(text: str, nonce: str) -> str:
    return text.replace(NONCE, nonce)


def compile_pattern(pattern: str, nonce: str):
    """The RE2 of `match` with the marker in as literal text. Raises re2.error."""
    return re2.compile(pattern.replace(NONCE, re2.escape(nonce)), _OPTIONS)


def is_int(value):
    return type(value) is int


def check(raw, node_names: set, script_nodes: dict | None, problem):
    """Validate and normalize `checks.scripts`. Reports through problem(path, code, message); returns the list.

    script_nodes: node name -> {"ok": bool, "family": "sh" | "routeros" | None}; None skips the node checks.
    """
    if not isinstance(raw, list):
        problem("scripts", "bad_type", "list")
        return []
    if len(raw) > MAX_SCRIPTS:
        problem("scripts", "out_of_range", f"at most {MAX_SCRIPTS} scripts")
        return []
    out, seen, total = [], set(), 0
    dummy = "0" * NONCE_LEN
    for k, item in enumerate(raw):
        path = f"scripts[{k}]"
        if not isinstance(item, dict):
            problem(path, "bad_type", "object")
            continue
        ok = True

        def bad(field, code, message=""):
            nonlocal ok
            ok = False
            problem(f"{path}.{field}" if field else path, code, message)

        for key in sorted(set(item) - FIELDS):
            bad(key, "unknown_field")
        sid = item.get("id")
        if sid is None:
            bad("id", "required")
        elif not isinstance(sid, str) or not ID_RE.fullmatch(sid):
            bad("id", "bad_name", ID_RE.pattern)
        elif sid in seen:
            bad("id", "duplicate_name", sid)
        else:
            seen.add(sid)
        title = item.get("title")
        if title is None:
            bad("title", "required")
        elif not isinstance(title, str):
            bad("title", "bad_type", "string")
        elif not 1 <= len(title.strip()) <= 120:
            bad("title", "out_of_range", "1..120 characters")
        node = item.get("node")
        family = None
        if node is None:
            bad("node", "required")
        elif not isinstance(node, str):
            bad("node", "bad_type", "node name")
        elif node not in node_names:
            bad("node", "unknown_node", node[:80])
        elif script_nodes is not None:
            info = script_nodes.get(node) or {}
            if not info.get("ok"):
                bad("node", "script_node_unsupported", "a QEMU node with a guest agent")
            family = info.get("family")
        shell = item.get("shell")
        if shell is not None:
            if shell not in SHELLS:
                bad("shell", "out_of_range", ", ".join(SHELLS))
            elif family is not None and shell != family:
                bad("shell", "shell_mismatch", f"the guest of {node} runs {family}")
        run = item.get("run")
        if run is None:
            bad("run", "required")
        elif not isinstance(run, str) or not run.strip():
            bad("run", "bad_type", "non-empty string")
        elif len(substitute(run, dummy).encode()) > MAX_RUN_BYTES:
            bad("run", "out_of_range", f"at most {MAX_RUN_BYTES} bytes")
        timeout = item.get("timeout", DEFAULT_TIMEOUT)
        if not is_int(timeout):
            bad("timeout", "bad_type", "integer")
        elif not 1 <= timeout <= MAX_TIMEOUT:
            bad("timeout", "out_of_range", f"1..{MAX_TIMEOUT}")
        else:
            total += timeout
        expect = expect_of(item.get("expect", {"exit": 0}), lambda field, code, message="": bad(
            f"expect.{field}" if field else "expect", code, message), dummy)
        group = item.get("group", "host")
        if group not in GROUPS:
            bad("group", "out_of_range", ", ".join(GROUPS))
        cls = item.get("class", "major")
        if cls not in CLASSES:
            bad("class", "out_of_range", ", ".join(CLASSES))
        if ok:
            entry = {"id": sid, "title": title.strip(), "node": node, "run": run, "timeout": timeout,
                     "expect": expect, "group": group, "class": cls}
            if shell is not None:
                entry["shell"] = shell
            out.append(entry)
    if total > MAX_TOTAL_TIMEOUT:
        problem("scripts", "out_of_range", f"the timeouts add up to {total} s, at most {MAX_TOTAL_TIMEOUT}")
    return out


def expect_of(raw, bad, dummy):
    if not isinstance(raw, dict):
        bad("", "bad_type", "object")
        return None
    for key in sorted(set(raw) - EXPECT_FIELDS):
        bad(key, "unknown_field")
    if not set(raw) & EXPECT_FIELDS:
        bad("", "required", "exit, match, contains or not_contains")
        return None
    out = {}
    if "exit" in raw:
        if not is_int(raw["exit"]):
            bad("exit", "bad_type", "integer")
        else:
            out["exit"] = raw["exit"]
    if "match" in raw:
        pattern = raw["match"]
        if not isinstance(pattern, str) or not 1 <= len(pattern) <= MAX_PATTERN:
            bad("match", "bad_type", f"string of 1..{MAX_PATTERN} characters")
        else:
            try:
                compile_pattern(pattern, dummy)
                out["match"] = pattern
            except re2.error as exc:
                message = exc.args[0].decode(errors="replace") if exc.args and isinstance(exc.args[0], bytes) else str(exc)
                bad("match", "bad_regex", message[:200])
    for key in ("contains", "not_contains"):
        if key in raw:
            items = raw[key]
            if (not isinstance(items, list) or not 1 <= len(items) <= MAX_STRINGS
                    or not all(isinstance(x, str) and 1 <= len(x) <= MAX_PATTERN for x in items)):
                bad(key, "bad_type", f"1..{MAX_STRINGS} non-empty strings of at most {MAX_PATTERN} characters")
            else:
                out[key] = list(items)
    return out


# ---------------------------------------------------------------------- a run


def cut(text: str, limit: int):
    """(text cut to `limit` UTF-8 bytes on a character boundary, whether it was cut)."""
    raw = text.encode()
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode(errors="ignore"), True


def judge(script: dict, nonce: str, answer: dict) -> tuple[str | None, bool]:
    """(None if the answer meets `expect`, else the reason: exit or mismatch; whether stdout was cut for compare)."""
    expect = script["expect"]
    stdout, cut_here = cut(str(answer.get("stdout") or "").replace("\r\n", "\n"), COMPARED_BYTES)
    stdout = stdout.rstrip()
    if "exit" in expect and answer.get("exit_code") != expect["exit"]:
        return "exit", cut_here
    if "match" in expect and compile_pattern(expect["match"], nonce).search(stdout) is None:
        return "mismatch", cut_here
    if any(substitute(s, nonce) not in stdout for s in expect.get("contains", ())):
        return "mismatch", cut_here
    if any(substitute(s, nonce) in stdout for s in expect.get("not_contains", ())):
        return "mismatch", cut_here
    return None, cut_here


def entry(script, status, reason=None, answer=None, duration_ms=None, cut_before=False):
    answer = answer or {}
    stdout, cut_out = cut(str(answer.get("stdout") or ""), KEPT_STDOUT)
    stderr, cut_err = cut(str(answer.get("stderr") or ""), KEPT_STDERR)
    out = {"id": script["id"], "node": script["node"], "status": status}
    if reason:
        out["reason"] = reason
    code = answer.get("exit_code")
    out.update(exit_code=code if is_int(code) else None, stdout=stdout, stderr=stderr,
               truncated=bool(answer.get("truncated")) or cut_out or cut_err or cut_before, duration_ms=duration_ms)
    return out


async def run(scripts: list, *, nonce: str, node_ids: dict, statuses: dict, exec_shell: bool, execute) -> list:
    """Run the scripts one by one in their order: `result.scripts` of CONTRACTS 2.9.

    execute(node_id, command, timeout, shell) -> {exit_code, stdout, stderr[, truncated]}; it raises
    ScriptError(kind) with kind "timeout", "shell_mismatch" or "unavailable" (any other failure of the slot).
    exec_shell: the slot announces `exec-shell` (PowerShell, the `shell` field, EXEC_TIMEOUT).
    """
    out = []
    for script in scripts:
        node_id = node_ids.get(script["node"])
        if node_id is None or statuses.get(node_id) != "started":
            out.append(entry(script, "fail", "node_stopped"))
            continue
        shell = script.get("shell")
        if not exec_shell and shell == "powershell":
            out.append(entry(script, "skipped", "unsupported_os"))
            continue
        began = time.monotonic()
        try:
            answer = await execute(node_id, substitute(script["run"], nonce), script["timeout"],
                                   shell if exec_shell else None)
        except ScriptError as exc:
            ms = int((time.monotonic() - began) * 1000)
            if exc.kind == "timeout":
                out.append(entry(script, "fail", "timeout", duration_ms=ms))
            elif exc.kind == "shell_mismatch":
                out.append(entry(script, "skipped", "unsupported_os", duration_ms=ms))
            else:
                out.append(entry(script, "skipped", TRANSIENT, duration_ms=ms))
            continue
        ms = int((time.monotonic() - began) * 1000)
        if not isinstance(answer, dict):
            out.append(entry(script, "skipped", TRANSIENT, duration_ms=ms))
            continue
        reason, cut_before = judge(script, nonce, answer)
        out.append(entry(script, "fail" if reason else "pass", reason, answer, ms, cut_before))
    return out


class ScriptError(Exception):
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def message(script: dict, result: dict) -> str:
    reason = result.get("reason")
    if reason == "exit":
        why = f"код выхода {result.get('exit_code')}, ожидался {script['expect'].get('exit')}"
    elif reason == "timeout":
        why = f"не завершился за {script['timeout']} с"
    elif reason == "node_stopped":
        why = f"нода {script['node']} остановлена"
    else:
        why = "вывод не совпал с ожидаемым"
    return f"{script['title']}: {why}"
