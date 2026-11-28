"""The teacher's own setup steps of a lab: field `setup` of gns3lab.lab/2 (docs/lab-setup/README.md, CONTRACTS 2.6).

A step belongs to one node and runs once, when the student's project is created, after the built-in setup of that node
(the commands of the image, random interface names, the network of the segments), steps in the order of the list:

- a QEMU node with a guest agent takes `run`, a script of its guest's shell: a `ProvisionCommand` after the built-in ones;
- an IOL node takes `config`, lines of `startup-config`: they go into `NodeSpec.nvram_config` before its final `end`;
- a VPCS node takes `config`, VPCS commands: they go into `NodeSpec.startup_script` after the address of the segment.

A failed `run` fails the lab (lab constructor §7.4). The slot keeps only the last line of the command's stderr in its error
text (`provision_failure` of the slot, cut to 200 characters), so the manager wraps `run` to make that line a marker:

    gns3lab-setup <id> exit <code>: <the last non-empty stderr line of the step>

and failure() finds it in the slot's error: SETUP_FAILED with `setup_error` instead of a retryable SLOT_OPERATION_FAILED.
Stage 2 (a report of every command from the slot) makes the wrapper unnecessary.
"""

import re

from . import iosconfig
from . import scripts as scripts_mod

FIELDS = {"id", "title", "node", "run", "config", "shell", "timeout"}
RUN_FIELDS = {"id", "title", "node", "run", "shell", "timeout"}
CONFIG_FIELDS = {"id", "title", "node", "config"}
SHELLS = scripts_mod.SHELLS
STAGE1_SHELLS = ("sh", "routeros")  # powershell needs `setup-windows` (stage 2)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MAX_STEPS = 30
MAX_TITLE = 120
MAX_RUN_BYTES = 8000  # ProvisionCommand.command of the slot takes 8192 characters; the wrapper takes the rest
MAX_COMMAND = 8192
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 120  # ProvisionCommand.timeout_seconds of the slot
MAX_TOTAL_TIMEOUT = 600
MAX_IOS_CONFIG = iosconfig.MAX_CONFIG
MAX_VPCS_SCRIPT = 4096
CONFIG_RE = re.compile(r"[\x20-\x7e\n]*")  # Printable ASCII and line feeds: what IOS and VPCS read from a file
MARKER = "gns3lab-setup"
MESSAGE_CHARS = 100  # Of the step's stderr line in the marker: the marker stays within the slot's 200 characters
# The marker in the error text of the slot: "... on node SRV1: gns3lab-setup web exit 100: E: Unable to locate ..."
MARKER_RE = re.compile(r"\bgns3lab-setup ([a-z0-9][a-z0-9_-]{0,31}) exit (-?\d{1,6})(?::[ ]?(.*))?")

# /bin/sh: the step runs in a subshell (its `exit`, `set -e` and `cd` stay inside), its stderr goes through a file so
# that the last line can be repeated in the marker, the marker is the last line of stderr, the exit code is kept.
SH_WRAPPER = ('e=$(mktemp)||e=/tmp/gns3lab$$\n(\n{run}\n)2>$e\n'
              'c=$?;cat $e>&2;[ $c = 0 ]||printf \'\\n%s\\n\' "' + MARKER + ' {id} exit $c: '
              '$(grep . $e|tail -1|cut -c-' + str(MESSAGE_CHARS) + ')">&2;rm -f $e;exit $c\n')
# RouterOS: an error of the script is caught and raised again as the marker. RouterOS gives no exit code (the slot
# reports -1) and `on-error` does not get the original message: the marker says `exit 1` and carries no text.
ROUTEROS_WRAPPER = ':do {{\n{run}\n}} on-error={{:error "' + MARKER + ' {id} exit 1"}}\n'


def is_int(value):
    return type(value) is int


def step_nodes(nodes, images: dict) -> dict:
    """Node name -> {"kind": "run" | "ios" | "vpcs" | None, "family"}: what a step of the node is made of.

    `run`: a QEMU node with a guest agent that is not the "Internet" node (the nodes a check script may run on);
    `ios`, `vpcs`: `config`; None: the node takes no step.
    """
    out = {}
    for node in nodes:
        image = images.get(node.get("image")) if isinstance(node, dict) else None
        if image is None or not isinstance(node.get("name"), str):
            continue
        kind = None
        if image.kind == "qemu" and image.qga and image.uplink_port is None:
            kind = "run"
        elif image.kind == "iou":
            kind = "ios"
        elif image.kind == "vpcs":
            kind = "vpcs"
        out[node["name"]] = {"kind": kind, "family": scripts_mod.family_of(image.net_config)}
    return out


def check(raw, *, nodes: dict, known: set, problem) -> list:
    """Validate and normalize `setup`. Reports through problem(path, code, message); returns the steps.

    nodes: step_nodes() of the sound nodes; known: the names of every node sent (a node with its own problem is
    reported there, not again here).
    """
    if not isinstance(raw, list):
        problem("setup", "bad_type", "list")
        return []
    if len(raw) > MAX_STEPS:
        problem("setup", "out_of_range", f"at most {MAX_STEPS} steps")
        return []
    out, seen, total = [], set(), 0
    for k, item in enumerate(raw):
        path = f"setup[{k}]"
        if not isinstance(item, dict):
            problem(path, "bad_type", "object expected")
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
        elif not 1 <= len(title.strip()) <= MAX_TITLE:
            bad("title", "out_of_range", f"1..{MAX_TITLE} characters")
        node = item.get("node")
        info = None
        if node is None:
            bad("node", "required")
        elif not isinstance(node, str):
            bad("node", "bad_type", "node name")
        elif node not in known:
            bad("node", "unknown_node", node[:80])
        elif node not in nodes:
            ok = False  # The node has its own problem
        elif nodes[node]["kind"] is None:
            bad("node", "setup_node_unsupported",
                "a QEMU node with a guest agent (not the Internet node), an IOL or a VPCS node")
        else:
            info = nodes[node]
        if info is None:
            continue
        entry = {"id": sid, "title": title.strip() if isinstance(title, str) else title, "node": node}
        if info["kind"] == "run":
            for key in sorted(set(item) & (FIELDS - RUN_FIELDS)):
                bad(key, "unknown_field", f"{node} takes `run`")
            run = item.get("run")
            if run is None:
                bad("run", "required")
            elif not isinstance(run, str) or not run.strip():
                bad("run", "bad_type", "non-empty string")
            elif len(run.encode()) > MAX_RUN_BYTES:
                bad("run", "too_large", f"at most {MAX_RUN_BYTES} bytes")
            shell = item.get("shell")
            if shell is not None:
                if shell not in SHELLS:
                    bad("shell", "out_of_range", ", ".join(SHELLS))
                elif shell not in STAGE1_SHELLS:
                    bad("shell", "shell_mismatch", f"{shell} steps are not supported by this manager")
                elif info["family"] is not None and shell != info["family"]:
                    bad("shell", "shell_mismatch", f"the guest of {node} runs {info['family']}")
            timeout = item.get("timeout", DEFAULT_TIMEOUT)
            if not is_int(timeout):
                bad("timeout", "bad_type", "integer")
            elif not 1 <= timeout <= MAX_TIMEOUT:
                bad("timeout", "out_of_range", f"1..{MAX_TIMEOUT}")
            else:
                total += timeout
            entry.update(run=run, timeout=timeout)
            if shell is not None:
                entry["shell"] = shell
        else:
            for key in sorted(set(item) & (FIELDS - CONFIG_FIELDS)):
                bad(key, "unknown_field", f"{node} takes `config`")
            config = item.get("config")
            if config is None:
                bad("config", "required")
            elif not isinstance(config, str) or not config.strip():
                bad("config", "bad_type", "non-empty string")
            elif not CONFIG_RE.fullmatch(config):
                bad("config", "bad_format", "printable ASCII lines")
            entry["config"] = config
        if ok:
            out.append(entry)
    if total > MAX_TOTAL_TIMEOUT:
        problem("setup", "out_of_range", f"the timeouts add up to {total} s, at most {MAX_TOTAL_TIMEOUT}")
    return out


def shell_of(step: dict, family) -> str:
    """The shell a `run` step is written in: its own `shell`, else the guest's family, else /bin/sh (what the slot
    runs on every guest it does not know as RouterOS)."""
    return step.get("shell") or family or "sh"


def wrap(step: dict, shell: str) -> str:
    """The provisioning command of a `run` step: the script with the failure marker (module docstring)."""
    template = ROUTEROS_WRAPPER if shell == "routeros" else SH_WRAPPER
    return template.format(run=step["run"], id=step["id"])


def lines_of(steps) -> list:
    """The `config` lines of the steps in order, without the empty lines at their ends."""
    return [line for step in steps for line in step["config"].strip("\n").split("\n")]


def ios_config(name: str, base, steps) -> str:
    """`nvram_config` of an IOL node with its steps: the configuration of the segments (or the same header the
    compiler gives a configured node) with the lines of the steps before the final `end`. Not checked for size."""
    lines = lines_of(steps)
    if base is None:
        return "\n".join([*iosconfig.head(name), *iosconfig.CONSOLE, *lines, "end"]) + "\n"
    assert base.endswith("\nend\n"), "a configuration of iosconfig ends with `end`"
    return base[:-len("end\n")] + "\n".join([*lines, "end"]) + "\n"


def vpcs_script(base: str, steps) -> str:
    """`startup_script` of a VPCS node with its steps: their lines after what the node already runs at start."""
    text = (base or "").rstrip("\n")
    return text + ("\n" if text else "") + "\n".join(lines_of(steps)) + "\n"


def by_node(definition: dict) -> dict:
    """Node name -> its steps in the order of `setup`."""
    out = {}
    for step in definition.get("setup") or []:
        out.setdefault(step["node"], []).append(step)
    return out


def total_timeout(definition: dict) -> int:
    """How much longer the first start of the lab may take: the timeouts of its `run` steps."""
    return sum(step.get("timeout", 0) for step in definition.get("setup") or [] if "run" in step)


def failure(text: str, definition: dict | None):
    """`setup_error` of a failed operation of the slot whose error text carries the marker of a step of this lab,
    else None: {"step", "node", "exit_code", "message"}."""
    found = None
    for found in MARKER_RE.finditer(text or ""):
        pass
    if found is None or not definition:
        return None
    step = next((s for s in definition.get("setup") or [] if s["id"] == found[1] and "run" in s), None)
    if step is None:
        return None
    return {"step": step["id"], "node": step["node"], "exit_code": int(found[2]),
            "message": (found[3] or "").strip()}


def prompt_fingerprint() -> dict:
    """What the prompt for a neural network must know of the format (plugin/plugin/prompts/setup.*.md).

    The prompt files carry a copy of it; tests/test_setup_prompt.py fails when the format moves on without them.
    """
    return {"format": "gns3lab.lab/2", "field": "setup", "step_fields": sorted(FIELDS),
            "run": sorted(RUN_FIELDS), "config": sorted(CONFIG_FIELDS),
            "kinds": {"run": "qemu with a guest agent", "config": ["iou", "vpcs"]},
            "shells": list(STAGE1_SHELLS),
            "limits": {"steps": MAX_STEPS, "title": MAX_TITLE, "run_bytes": MAX_RUN_BYTES,
                       "timeout": MAX_TIMEOUT, "timeout_default": DEFAULT_TIMEOUT,
                       "timeout_total": MAX_TOTAL_TIMEOUT, "ios_config": MAX_IOS_CONFIG,
                       "vpcs_script": MAX_VPCS_SCRIPT}}
