"""Lab definitions from Moodle, formats gns3lab.lab/1 and /2 (plugin requests П11–П13, lab constructor stage 2).

A definition is untrusted: any teacher with the right capability in any course may send one.
check() verifies everything the slot would verify later, plus the image catalog and the
site's limits, and reports problems with a path the editor can highlight:

    {"path": "nodes[2].adapters", "code": "out_of_range", "message": "1..4"}

Only the manager hashes a definition (lab_def): sha256 of the canonical JSON of the normalized
definition without its title, so renaming a lab does not start a new workspace. Drawings,
labels and texts stay in Moodle; node parameters that are not the teacher's (image file,
disk interface, CLI prompt, provisioning) come from the image catalog only.

gns3lab.lab/2 is lab/1 plus `segments` (segments.py): the manager compiles them into the node setup of the ticket.
A lab/1 definition is normalized and hashed exactly as before.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass

from . import iosconfig, netconfig
from . import segments as segments_mod
from .images import PORTS_PER_ADAPTER, VPCS_MEMORY_MB

FORMAT = "gns3lab.lab/1"
FORMAT_2 = "gns3lab.lab/2"
FORMATS = (FORMAT, FORMAT_2)
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")  # NodeSpec.name of the slot
MAX_SIZE = 256 * 1024
MAX_NODES = 100  # DeployTicket of the slot
MAX_LINKS = 1000
COORD = 100_000
TOP_KEYS = {"format", "title", "nodes", "links"}
TOP_KEYS_2 = TOP_KEYS | {"segments"}
NODE_KEYS = {"name", "image", "x", "y", "ram_mb", "cpus", "adapters"}
ENDPOINT_KEYS = {"node", "adapter", "port"}
CODES = ("required", "unknown_field", "bad_type", "bad_format", "bad_name", "duplicate_name", "unknown_image",
         "image_not_allowed", "out_of_range", "unknown_node", "bad_port", "port_in_use", "self_link",
         "too_many_nodes", "limit_exceeded", "too_large",
         # lab/2
         "segment_not_connected", "segment_overlap", "member_in_two_segments", "gateway_not_router",
         "unsupported_image", "subnet_too_small", "bad_subnet", "vlan_out_of_range", "host_out_of_range",
         "host_in_use", "multiple_gateways", "duplicate_segment",
         # lab/2, stage 3
         "gateway_port_conflict")


class LabInvalid(ValueError):
    def __init__(self, problems):
        super().__init__("; ".join(f"{p['path']}: {p['code']}" for p in problems[:5]))
        self.problems = problems


@dataclass(frozen=True)
class LabDef:
    lab_def: str  # "sha256:<hex>"
    definition: dict  # Normalized, as stored with the workspace
    resources: dict  # {"cores", "memory_mb"} of the slot CT
    warnings: tuple = ()  # [{"path", "code", "message"}]: the lab starts, the teacher should know


def is_int(value):
    return type(value) is int  # bool is not a number here


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def lab_def_of(definition: dict) -> str:
    body = {k: v for k, v in definition.items() if k != "title"}
    return "sha256:" + hashlib.sha256(canonical(body).encode()).hexdigest()


def effective(node: dict, image) -> dict:
    """Node parameters with the image defaults filled in."""
    return {
        "ram_mb": node.get("ram_mb", image.ram_mb.default) if image.ram_mb else VPCS_MEMORY_MB,
        "cpus": node.get("cpus", image.cpus.default) if image.cpus else 1,
        "adapters": node.get("adapters", image.adapters.default) if image.adapters else 1,
    }


def resources_of(nodes, images, overhead_mb: int) -> dict:
    """CT size for a lab: node memory plus 10 % and a fixed overhead for GNS3 and the guest
    (rounded up to 256 MiB); cores: QEMU vCPUs, one per two IOL, one per eight VPCS, at least 2."""
    memory = cores_qemu = iou = vpcs = 0
    for node in nodes:
        image = images[node["image"]]
        values = effective(node, image)
        memory += values["ram_mb"]
        if image.kind == "qemu":
            cores_qemu += values["cpus"]
        elif image.kind == "iou":
            iou += 1
        else:
            vpcs += 1
    memory_mb = math.ceil((memory * 1.1 + overhead_mb) / 256) * 256
    return {"cores": max(2, cores_qemu + math.ceil(iou / 2) + math.ceil(vpcs / 8)), "memory_mb": memory_mb}


def check(lab, images: dict, limits, *, overhead_mb: int = 1024) -> LabDef:
    """Validate and normalize a definition. Raises LabInvalid with every problem found."""
    problems, warnings = [], []

    def problem(path, code, message=""):
        problems.append({"path": path, "code": code, "message": message})

    def warn(path, code, message=""):
        warnings.append({"path": path, "code": code, "message": message})

    def unknown(obj, allowed, path):
        for key in sorted(set(obj) - allowed):
            problem(f"{path}.{key}" if path else key, "unknown_field")

    if not isinstance(lab, dict):
        raise LabInvalid([{"path": "", "code": "bad_type", "message": "object expected"}])
    if len(canonical(lab).encode()) > MAX_SIZE:
        raise LabInvalid([{"path": "", "code": "too_large", "message": f"max {MAX_SIZE} bytes"}])
    fmt = lab.get("format")
    unknown(lab, TOP_KEYS_2 if fmt == FORMAT_2 else TOP_KEYS, "")
    if fmt not in FORMATS:
        problem("format", "required" if "format" not in lab else "bad_format", " or ".join(FORMATS))
    title = lab.get("title", "")
    if not isinstance(title, str) or len(title) > 200:
        problem("title", "bad_type", "string up to 200 characters")
    nodes, links = lab.get("nodes"), lab.get("links", [])
    if not isinstance(nodes, list) or not nodes:
        problem("nodes", "required" if nodes is None else "bad_type", "non-empty list")
        raise LabInvalid(problems)
    if not isinstance(links, list):
        problem("links", "bad_type", "list")
        links = []
    max_nodes = min(MAX_NODES, limits.max_nodes)
    if len(nodes) > max_nodes:
        problem("nodes", "too_many_nodes", f"max {max_nodes}")
    if len(links) > MAX_LINKS:
        problem("links", "too_large", f"max {MAX_LINKS} links")

    out_nodes, by_name = [], {}
    for i, node in enumerate(nodes[:MAX_NODES]):
        path = f"nodes[{i}]"
        if not isinstance(node, dict):
            problem(path, "bad_type", "object expected")
            continue
        unknown(node, NODE_KEYS, path)
        out, ok = {}, True
        name = node.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            problem(f"{path}.name", "required" if name is None else "bad_name", NAME_RE.pattern)
            ok = False
        elif name in by_name:
            problem(f"{path}.name", "duplicate_name", name)
            ok = False
        image_id = node.get("image")
        image = images.get(image_id) if isinstance(image_id, str) else None
        if image_id is None:
            problem(f"{path}.image", "required")
        elif image is None:
            problem(f"{path}.image", "unknown_image", str(image_id)[:64])
        elif not limits.allows(image.id):
            problem(f"{path}.image", "image_not_allowed", image.id)
            image = None
        for axis in ("x", "y"):
            value = node.get(axis, 0)
            if not is_int(value) or not -COORD <= value <= COORD:
                problem(f"{path}.{axis}", "out_of_range", f"integer -{COORD}..{COORD}")
            else:
                out[axis] = value
        if image is not None:
            for key in ("ram_mb", "cpus", "adapters"):
                if key not in node:
                    continue
                bounds = getattr(image, key)
                value = node[key]
                if bounds is None:
                    problem(f"{path}.{key}", "unknown_field", f"{image.id} has no {key}")
                elif not is_int(value) or not bounds.min <= value <= bounds.max:
                    problem(f"{path}.{key}", "out_of_range", f"{bounds.min}..{bounds.max}")
                else:
                    out[key] = value
        if ok and image is not None:
            out = {"name": name, "image": image.id, **out}
            by_name[name] = out
            out_nodes.append(out)

    out_links, used = [], set()
    for i, link in enumerate(links[:MAX_LINKS]):
        path = f"links[{i}]"
        if not isinstance(link, dict):
            problem(path, "bad_type", "object expected")
            continue
        unknown(link, {"a", "b"}, path)
        ends = []
        for side in ("a", "b"):
            ep, epath = link.get(side), f"{path}.{side}"
            if not isinstance(ep, dict):
                problem(epath, "required" if ep is None else "bad_type")
                continue
            unknown(ep, ENDPOINT_KEYS, epath)
            node_name, adapter, port = ep.get("node"), ep.get("adapter", 0), ep.get("port", 0)
            target = by_name.get(node_name) if isinstance(node_name, str) else None
            if target is None:
                # A node with its own problem is reported there, not again here.
                if not any(isinstance(n, dict) and n.get("name") == node_name for n in nodes):
                    problem(f"{epath}.node", "unknown_node", str(node_name)[:80])
                continue
            image = images[target["image"]]
            adapters = effective(target, image)["adapters"]
            if not (is_int(adapter) and is_int(port) and 0 <= adapter < adapters
                    and 0 <= port < PORTS_PER_ADAPTER[image.kind]):
                problem(epath, "bad_port", f"adapter 0..{adapters - 1}, port 0..{PORTS_PER_ADAPTER[image.kind] - 1}")
                continue
            key = (node_name, adapter, port)
            if key in used:
                problem(epath, "port_in_use", f"{node_name} {adapter}/{port}")
                continue
            used.add(key)
            ends.append({"node": node_name, "adapter": adapter, "port": port})
        if len(ends) == 2:
            if ends[0]["node"] == ends[1]["node"]:
                problem(path, "self_link", ends[0]["node"])
            else:
                out_links.append({"a": ends[0], "b": ends[1]})

    out_segments = None
    if fmt == FORMAT_2:
        def port_error(name, adapter, port):
            node = by_name[name]
            image = images[node["image"]]
            adapters = effective(node, image)["adapters"]
            if is_int(adapter) and is_int(port) and 0 <= adapter < adapters and 0 <= port < PORTS_PER_ADAPTER[image.kind]:
                return None
            return f"adapter 0..{adapters - 1}, port 0..{PORTS_PER_ADAPTER[image.kind] - 1}"

        out_segments = segments_mod.check(
            lab.get("segments", []), nodes=by_name, known={n.get("name") for n in nodes if isinstance(n, dict)},
            images=images, links=out_links, port_error=port_error, problem=problem, warn=warn,
            connected=not problems)

    if problems:
        raise LabInvalid(problems)
    resources = resources_of(out_nodes, images, overhead_mb)
    if limits.max_memory_mb is not None and resources["memory_mb"] > limits.max_memory_mb:
        problem("nodes", "limit_exceeded", f"memory {resources['memory_mb']} > {limits.max_memory_mb} MiB")
    if limits.max_cpus is not None and resources["cores"] > limits.max_cpus:
        problem("nodes", "limit_exceeded", f"cores {resources['cores']} > {limits.max_cpus}")
    if problems:
        raise LabInvalid(problems)
    definition = {"format": fmt, "title": title, "nodes": out_nodes, "links": out_links}
    if out_segments is not None:
        definition["segments"] = out_segments
        try:
            segments_mod.setup(definition, images)  # The scripts and configurations must fit the slot's ticket
        except (netconfig.ScriptTooLarge, iosconfig.ConfigTooLarge) as exc:
            raise LabInvalid([{"path": "segments", "code": "too_large", "message": str(exc)}]) from exc
        configured = segments_mod.ios_plans(definition, images)
        for i, node in enumerate(out_nodes):  # Indexes of `nodes` as sent: every node is sound when we get here
            if node["name"] in configured and not iosconfig.hostname_ok(node["name"]):
                warn(f"nodes[{i}].name", "hostname_not_set",
                     "not an IOS hostname (a letter first, then letters, digits and '-', 63 characters at most)")
    return LabDef(lab_def_of(definition), definition, resources, tuple(warnings))


def deploy_ticket(definition: dict, images: dict, *, project_id: str, project_name: str) -> dict:
    """DeployTicket (deploy/1) from a stored definition and the current image catalog.

    Raises KeyError if an image was removed from the catalog since the definition was checked.
    """
    nodes = []
    setup = segments_mod.setup(definition, images) if definition.get("segments") else {}
    for node in definition["nodes"]:
        image = images[node["image"]]
        values = effective(node, image)
        spec = {"name": node["name"], "kind": image.kind, "x": node.get("x", 0), "y": node.get("y", 0)}
        if image.kind != "vpcs":
            spec.update(image=image.file, ram_mb=values["ram_mb"])
        if image.kind == "qemu":
            spec.update(cpus=values["cpus"], adapters=values["adapters"])
        elif image.kind == "iou":
            spec["ethernet_adapters"] = values["adapters"]
        if image.console_type == "vnc":
            # Only vnc nodes carry the field: a telnet node stays byte for byte what it was, so the
            # canonical JSON and the lab_revision of existing labs do not change (CONTRACTS 4.2).
            spec["console"] = "vnc"
        options = image.slot.model_dump(exclude_none=True)
        preset = setup.get(node["name"], {})
        if "command" in preset:  # After the commands of the image
            options["provisioning"] = [*options.get("provisioning", []),
                                       {"command": preset["command"], "timeout_seconds": netconfig.TIMEOUT_SECONDS}]
        if "nvram_config" in preset:
            # Only a configured IOL node carries the field, so the canonical JSON and the lab_revision of the
            # nodes without it do not change (CONTRACTS 4.2).
            spec["nvram_config"] = preset["nvram_config"]
        if "startup_line" in preset:
            options["startup_script"] = options.get("startup_script", "").rstrip("\n")
            options["startup_script"] += ("\n" if options["startup_script"] else "") + preset["startup_line"] + "\n"
        if not options.get("provisioning"):
            options.pop("provisioning", None)
        spec.update(options)
        nodes.append(spec)
    return {"name": project_name, "project_id": project_id, "nodes": nodes,
            "links": [{"a": dict(link["a"]), "b": dict(link["b"])} for link in definition["links"]]}


def reference_topology(definition: dict, images: dict) -> dict:
    """What a lab.gns3 gives a catalog lab: node symbols by name (no drawings: they are Moodle's)."""
    return {"topology": {"nodes": [{"name": n["name"], "symbol": images[n["image"]].public()["symbol"]}
                                   for n in definition["nodes"] if n["image"] in images],
                         "drawings": []}}
