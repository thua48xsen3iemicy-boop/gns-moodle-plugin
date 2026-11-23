"""Teacher scripts of an auto-check (CONTRACTS 2.9 `scripts`, docs/autochecks/README.md §7).

A script runs in a guest of the lab through the slot's `qga/exec`; the manager only substitutes the run marker and the
values an earlier script captured, sends the text and compares the answer. Regular expressions are RE2 (linear time):
a teacher's pattern must not be able to hang the manager.

A script may lean on earlier ones (docs/autochecks/README.md §7.7): `needs` names scripts that must pass first, and
`{{<id>.<group>}}` takes the named group `(?P<group>...)` of an earlier script's `expect.match`. In `run` the value is
quoted for the script's shell, so whatever a student's guest printed stays one plain argument.
"""

import re
import secrets
import time

import re2

SHELLS = ("sh", "powershell", "routeros")
GROUPS = ("l1", "l2", "addressing", "gateways", "routing", "host")
CLASSES = ("critical", "major", "minor", "note")
ID_RE = re2.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
FIELDS = {"id", "title", "node", "shell", "run", "timeout", "expect", "group", "class", "needs"}
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
BLOCKED = "needs_failed"
# {{<script id>.<group name>}}: a value captured by an earlier script. Our own fixed pattern, so the stdlib `re`.
REF_RE = re.compile(r"\{\{([a-z0-9][a-z0-9_-]{0,31})\.([A-Za-z_][A-Za-z0-9_]{0,31})\}\}")
MAX_CAPTURE = 256  # UTF-8 bytes of a captured value
QUOTED_MAX = 4 * MAX_CAPTURE + 2  # The longest a value gets once quoted: sh turns ' into '\''
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

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


def substitute(text: str, nonce: str, values: dict | None = None, quote=None) -> str:
    """The text with the marker and the captured values in; quote(value) wraps each value (in `run`)."""
    text = text.replace(NONCE, nonce)
    if values is None:
        return text

    def one(m):
        value = values.get((m[1], m[2]))
        if value is None:
            return m[0]
        return quote(value) if quote else value

    return REF_RE.sub(one, text)


def compile_pattern(pattern: str, nonce: str, values: dict | None = None):
    """The RE2 of `match` with the marker and the captured values in as literal text. Raises re2.error."""
    pattern = pattern.replace(NONCE, re2.escape(nonce))
    if values:
        pattern = REF_RE.sub(lambda m: re2.escape(values[(m[1], m[2])]) if (m[1], m[2]) in values else m[0], pattern)
    return re2.compile(pattern, _OPTIONS)


def quote_sh(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def quote_powershell(value: str) -> str:
    # PowerShell takes the typographic single quotes for ' too: each is doubled inside a '...' string.
    for ch in "'\u2018\u2019\u201a\u201b":
        value = value.replace(ch, ch + ch)
    return "'" + value + "'"


def quote_routeros(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


QUOTES = {"sh": quote_sh, "powershell": quote_powershell, "routeros": quote_routeros}


def refs_of(script: dict) -> list:
    """The (script id, group) pairs a normalized script takes, in order of appearance, without repeats."""
    expect = script.get("expect") or {}
    texts = [script.get("run") or "", expect.get("match") or ""]
    texts += list(expect.get("contains", ())) + list(expect.get("not_contains", ()))
    return list(dict.fromkeys(pair for text in texts for pair in REF_RE.findall(text)))


def needs_of(script: dict) -> list:
    """The scripts that must pass before this one: `needs` and every script whose value it takes."""
    return list(dict.fromkeys(list(script.get("needs", ())) + [sid for sid, _ in refs_of(script)]))


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
    groups = {}  # id of an earlier script -> the named groups of its `expect.match`
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
        run_refs = []
        if run is None:
            bad("run", "required")
        elif not isinstance(run, str) or not run.strip():
            bad("run", "bad_type", "non-empty string")
        else:
            run_refs = REF_RE.findall(run)
            if len(REF_RE.sub("x" * QUOTED_MAX, substitute(run, dummy)).encode()) > MAX_RUN_BYTES:
                bad("run", "out_of_range", f"at most {MAX_RUN_BYTES} bytes, each {{{{id.group}}}} counted as "
                                           f"{QUOTED_MAX}")
            for sid_ref, name in run_refs:
                reference_problem(groups, sid_ref, name, lambda code, message: bad("run", code, message))
            if run_refs and shell is None:
                if family is not None:
                    shell = family  # A value in `run` is quoted for the guest's shell: the definition says which
                else:
                    bad("shell", "required", "the run takes a value of an earlier script: quoting needs the shell")
        timeout = item.get("timeout", DEFAULT_TIMEOUT)
        if not is_int(timeout):
            bad("timeout", "bad_type", "integer")
        elif not 1 <= timeout <= MAX_TIMEOUT:
            bad("timeout", "out_of_range", f"1..{MAX_TIMEOUT}")
        else:
            total += timeout
        expect = expect_of(item.get("expect", {"exit": 0}), lambda field, code, message="": bad(
            f"expect.{field}" if field else "expect", code, message), dummy, groups)
        needs = item.get("needs")
        if needs is not None:
            if (not isinstance(needs, list) or not 1 <= len(needs) <= MAX_SCRIPTS - 1
                    or not all(isinstance(x, str) for x in needs) or len(set(needs)) != len(needs)):
                bad("needs", "bad_type", "a list of distinct script ids")
                needs = None
            else:
                for x in needs:
                    if x not in groups:
                        bad("needs", "unknown_reference", f"{x[:40]}: no earlier script with this id")
        group = item.get("group", "host")
        if group not in GROUPS:
            bad("group", "out_of_range", ", ".join(GROUPS))
        cls = item.get("class", "major")
        if cls not in CLASSES:
            bad("class", "out_of_range", ", ".join(CLASSES))
        if isinstance(sid, str) and ID_RE.fullmatch(sid) and sid not in groups:
            groups[sid] = set()
            if expect and "match" in expect:
                groups[sid] = set(compile_pattern(expect["match"], dummy, placeholders(expect["match"])).groupindex)
        if ok:
            entry = {"id": sid, "title": title.strip(), "node": node, "run": run, "timeout": timeout,
                     "expect": expect, "group": group, "class": cls}
            if shell is not None:
                entry["shell"] = shell
            if needs is not None:
                entry["needs"] = list(needs)
            out.append(entry)
    if total > MAX_TOTAL_TIMEOUT:
        problem("scripts", "out_of_range", f"the timeouts add up to {total} s, at most {MAX_TOTAL_TIMEOUT}")
    return out


def placeholders(text: str) -> dict:
    """A stand-in value for every {{id.group}} of a text: enough to compile a pattern before a run."""
    return {pair: "x" for pair in REF_RE.findall(text)}


def reference_problem(groups: dict, sid: str, name: str, bad):
    """Report a {{sid.name}} that no earlier script captures."""
    ref = f"{{{{{sid}.{name}}}}}"
    if sid not in groups:
        bad("unknown_reference", f"{ref}: no earlier script with this id")
    elif name not in groups[sid]:
        bad("unknown_reference", f"{ref}: the expect.match of {sid} has no group (?P<{name}>...)")


def expect_of(raw, bad, dummy, groups=None):
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
                compile_pattern(pattern, dummy, placeholders(pattern))
                out["match"] = pattern
                for sid, name in REF_RE.findall(pattern):
                    reference_problem(groups or {}, sid, name, lambda code, message: bad("match", code, message))
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
                for sid, name in dict.fromkeys(pair for x in items for pair in REF_RE.findall(x)):
                    reference_problem(groups or {}, sid, name, lambda code, message, key=key: bad(key, code, message))
    return out


# ---------------------------------------------------------------------- a run


def cut(text: str, limit: int):
    """(text cut to `limit` UTF-8 bytes on a character boundary, whether it was cut)."""
    raw = text.encode()
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode(errors="ignore"), True


def judge(script: dict, nonce: str, answer: dict, values: dict | None = None,
          wanted: frozenset = frozenset()) -> tuple[str | None, bool, dict]:
    """(None if the answer meets `expect`, else the reason: exit or mismatch; whether stdout was cut for compare;
    the named groups of `match`).

    values: what earlier scripts captured, for {{id.group}}. wanted: groups a later script takes; such a value longer
    than MAX_CAPTURE bytes or with a control character is a mismatch: it cannot go on as one plain argument.
    """
    expect = script["expect"]
    values = values or {}
    stdout, cut_here = cut(str(answer.get("stdout") or "").replace("\r\n", "\n"), COMPARED_BYTES)
    stdout = stdout.rstrip()
    captures = {}
    if "exit" in expect and answer.get("exit_code") != expect["exit"]:
        return "exit", cut_here, captures
    if "match" in expect:
        found = compile_pattern(expect["match"], nonce, values).search(stdout)
        if found is None:
            return "mismatch", cut_here, captures
        captures = {name: value or "" for name, value in found.groupdict().items()}
        for name in wanted:
            value = captures.get(name, "")
            if len(value.encode()) > MAX_CAPTURE or CONTROL_RE.search(value):
                return "mismatch", cut_here, {}
    if any(substitute(s, nonce, values) not in stdout for s in expect.get("contains", ())):
        return "mismatch", cut_here, {}
    if any(substitute(s, nonce, values) in stdout for s in expect.get("not_contains", ())):
        return "mismatch", cut_here, {}
    return None, cut_here, captures


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
    out, done, values = [], {}, {}
    wanted = {}  # script id -> the groups of its `match` that later scripts take
    for script in scripts:
        for sid, name in refs_of(script):
            wanted.setdefault(sid, set()).add(name)
    for script in scripts:
        result = await run_one(script, nonce, node_ids, statuses, exec_shell, execute, done, values,
                               frozenset(wanted.get(script["id"], ())))
        done[script["id"]] = result
        out.append(result)
    return out


async def run_one(script, nonce, node_ids, statuses, exec_shell, execute, done, values, wanted):
    """`result.scripts[]` of one script; a pass adds its captured groups to `values`."""
    for sid in needs_of(script):
        need = done.get(sid) or {}
        if need.get("status") != "pass":
            # A script left unchecked by a transient failure leaves its followers so too: the run is then incomplete
            reason = TRANSIENT if need.get("status") == "skipped" and need.get("reason") == TRANSIENT else BLOCKED
            return entry(script, "skipped", reason) | {"blocked_by": sid}
    node_id = node_ids.get(script["node"])
    if node_id is None or statuses.get(node_id) != "started":
        return entry(script, "fail", "node_stopped")
    shell = script.get("shell")
    if not exec_shell and shell == "powershell":
        return entry(script, "skipped", "unsupported_os")
    command = substitute(script["run"], nonce, values, QUOTES.get(shell, quote_sh))
    began = time.monotonic()
    try:
        answer = await execute(node_id, command, script["timeout"], shell if exec_shell else None)
    except ScriptError as exc:
        ms = int((time.monotonic() - began) * 1000)
        if exc.kind == "timeout":
            return entry(script, "fail", "timeout", duration_ms=ms)
        if exc.kind == "shell_mismatch":
            return entry(script, "skipped", "unsupported_os", duration_ms=ms)
        return entry(script, "skipped", TRANSIENT, duration_ms=ms)
    ms = int((time.monotonic() - began) * 1000)
    if not isinstance(answer, dict):
        return entry(script, "skipped", TRANSIENT, duration_ms=ms)
    reason, cut_before, captures = judge(script, nonce, answer, values, wanted)
    out = entry(script, "fail" if reason else "pass", reason, answer, ms, cut_before)
    if not reason and captures:
        for name, value in captures.items():
            values[(script["id"], name)] = value
        out["captures"] = {name: cut(value, MAX_CAPTURE)[0] for name, value in captures.items()}
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
