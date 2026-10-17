"""Lab catalog on RO CephFS: LABS_DIR/<lab_ref>/{lab.gns3, ticket.json, meta.json, checks/}.

lab.gns3    reference topology for the preview (served as is)
ticket.json DeployTicket for Lab Slot (slot/api/models.py); the slot validates it fully,
            the manager checks that its nodes match lab.gns3 and sets name/project_id
meta.json   {"title", "description", "profile"?}; profile picks the slot resource profile
checks/     auto-check scripts (later)
"""

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path

LAB_REF_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_FILE = 8 * 1024 * 1024


class LabError(RuntimeError):
    pass


class LabNotFound(LabError):
    pass


@dataclass(frozen=True)
class Lab:
    lab_ref: str
    title: str
    description: str
    profile: str
    topology: dict
    ticket: dict


def _load_json(path: Path):
    try:
        if path.is_symlink() or not path.is_file():
            raise LabError(f"{path.name} is missing")
        if path.stat().st_size > MAX_FILE:
            raise LabError(f"{path.name} is too large")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LabError(f"{path.name} is unreadable or not JSON") from exc


def validate(lab_ref: str, topology: dict, ticket: dict, meta: dict) -> list[str]:
    """Consistency checks between lab.gns3 and ticket.json. Returns problems (empty = ok)."""
    problems = []
    try:
        gns3_names = [n["name"] for n in topology["topology"]["nodes"]]
    except (KeyError, TypeError):
        return ["lab.gns3 has no topology.nodes[].name"]
    nodes = ticket.get("nodes") if isinstance(ticket, dict) else None
    if not isinstance(nodes, list) or not nodes:
        return ["ticket.json has no nodes"]
    try:
        ticket_names = [n["name"] for n in nodes]
    except (KeyError, TypeError):
        return ["ticket.json nodes must have names"]
    if len(set(ticket_names)) != len(ticket_names):
        problems.append("ticket.json has duplicate node names")
    if set(ticket_names) != set(gns3_names):
        missing = sorted(set(gns3_names) - set(ticket_names))
        extra = sorted(set(ticket_names) - set(gns3_names))
        problems.append(f"Node names differ: only in lab.gns3 {missing}, only in ticket.json {extra}")
    if not isinstance(meta.get("title"), str) or not meta["title"].strip():
        problems.append("meta.json needs a title")
    profile = meta.get("profile", "default")
    if not isinstance(profile, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", profile):
        problems.append("meta.json profile is invalid")
    return problems


class Catalog:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, lab_ref: str) -> Path:
        if not LAB_REF_RE.fullmatch(lab_ref or "") or ".." in lab_ref:
            raise LabNotFound("Unknown lab_ref")
        path = self.root / lab_ref
        if path.is_symlink() or not path.is_dir():
            raise LabNotFound("Unknown lab_ref")
        return path

    def get(self, lab_ref: str) -> Lab:
        path = self._dir(lab_ref)
        meta = _load_json(path / "meta.json")
        topology = _load_json(path / "lab.gns3")
        ticket = _load_json(path / "ticket.json")
        if not isinstance(meta, dict) or not isinstance(topology, dict):
            raise LabError("meta.json and lab.gns3 must be JSON objects")
        problems = validate(lab_ref, topology, ticket, meta)
        if problems:
            raise LabError("; ".join(problems))
        return Lab(lab_ref, meta["title"].strip(), str(meta.get("description", "")),
                   meta.get("profile", "default"), topology, ticket)

    def list(self) -> list[dict]:
        labs = []
        try:
            entries = sorted(self.root.iterdir())
        except OSError as exc:
            raise LabError("Lab catalog is unavailable") from exc
        for entry in entries:
            if not LAB_REF_RE.fullmatch(entry.name):
                continue
            try:
                lab = self.get(entry.name)
            except LabError:
                continue  # Broken labs are not offered; `admin labs` shows why
            labs.append({"lab_ref": lab.lab_ref, "title": lab.title, "description": lab.description})
        return labs

    def check_all(self) -> dict[str, str]:
        result = {}
        for entry in sorted(self.root.iterdir()):
            if entry.is_dir() and LAB_REF_RE.fullmatch(entry.name):
                try:
                    self.get(entry.name)
                    result[entry.name] = "ok"
                except LabError as exc:
                    result[entry.name] = str(exc)
        return result


def deploy_ticket(lab: Lab, *, project_id: str, project_name: str) -> dict:
    ticket = copy.deepcopy(lab.ticket)
    ticket["name"] = project_name
    ticket["project_id"] = project_id
    ticket.pop("auto_start", None)  # Slot only accepts the default (true)
    return ticket


def project_name(lab_ref: str, workspace_id: int) -> str:
    return f"{lab_ref}-w{workspace_id}"[:120]
