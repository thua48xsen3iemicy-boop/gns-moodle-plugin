"""Facts from the saved configuration of a Cisco IOL node (CONTRACTS.md 4.6, `ios.config/1`).

What counts for IOL is what the device boots with. Two files of the node directory of the project hold it:

* `startup-config.cfg`, the text GNS3 2.2.61 extracts from NVRAM (`IOUVM.save_configs`). GNS3 writes it
  only for a node that already has a non-empty one (`startup_config_content` given at creation); a node
  created without it never gets the file, whatever the student saves.
* `nvram_<application_id>`, the NVRAM of the device itself. Its text is taken out by `nvram_export` of
  the installed gns3server, not by a parser of ours.

This module reads those files and nothing else: no console, no QGA. The text holds passwords and hashes,
so it never leaves this module: only the fields of the contract are built from it, and error messages
name interfaces and fields, never lines.
"""

import importlib.util
import ipaddress
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "startup-config.cfg"
NVRAM = re.compile(r"nvram_\d{5}")
SIZE_LIMIT = 1 << 20  # a saved configuration is tens of KiB; more than this is not one
NVRAM_LIMIT = 4 << 20  # the slot creates IOL nodes with 256 KiB of it
EXPORT_PATH = "compute/iou/utils/iou_export.py"  # in the gns3server package
VLAN_IDS = range(1, 4095)
ALL_VLANS = frozenset(VLAN_IDS)
UNIQUE = re.compile(r"[0-9A-Za-z_-]+")

PHYSICAL = re.compile(r"(?:Ethernet|Serial)\d+(?:/\d+)*")
SUBIF = re.compile(r"((?:Ethernet|Serial)\d+(?:/\d+)*)\.\d+")
SVI = re.compile(r"Vlan(\d+)")
LOOPBACK = re.compile(r"Loopback\d+")
VLAN_LIST = re.compile(r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*")


class ConfigError(ValueError):
    """The saved configuration cannot be read or does not parse; the text says where, never what."""

    code = "failed"  # the live-state.md error code of the node


class Unreadable(ConfigError):
    """A file could not be read now. Unlike a file that does not parse, this may pass without a change of it."""


class Unsupported(Unreadable):
    """The slot cannot take the configuration out of NVRAM: the export code of gns3server is not there."""

    code = "unsupported"


@dataclass(frozen=True)
class Snapshot:
    """The outcome for one version of the source: `data` or, if it did not parse, the `error` message."""

    stamp: tuple  # what the outcome belongs to: the source (size, mtime) and the ports it was matched with
    data: dict | None = None
    error: str | None = None


# --- reading ----------------------------------------------------------------------------------


def node_directory(project_id, node):
    """Where GNS3 keeps the files of the node: its own word if it says one, else the 2.2.61 layout."""
    declared = node.get("node_directory")
    if isinstance(declared, str) and os.path.isabs(declared):
        return Path(declared)
    node_id = node.get("node_id")
    if not all(isinstance(v, str) and UNIQUE.fullmatch(v) for v in (project_id, node_id)):
        return None
    root = Path(os.getenv("SLOT_PROJECTS_DIR", "/opt/gns3/projects"))
    return root / project_id / "project-files" / "iou" / node_id


def regular_file(path, what):
    """lstat of a file GNS3 wrote, or None while there is none; a link or a directory is not followed."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise Unreadable(f"Cannot stat the {what}: {type(exc).__name__}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ConfigError(f"The {what} is not a regular file")
    return info


def find_nvram(directory, node):
    """The NVRAM file of the node: by its application id, else the only one there is."""
    application_id = (node.get("properties") or {}).get("application_id")
    if type(application_id) is int and 0 < application_id < 100000:
        names = [f"nvram_{application_id:05d}"]
    else:
        try:
            names = sorted(name for name in os.listdir(directory) if NVRAM.fullmatch(name))
        except OSError:
            return None
        if len(names) != 1:
            return None
    info = regular_file(directory / names[0], "NVRAM")
    return None if info is None else (directory / names[0], info)


def find_source(directory, node):
    """("config" | "nvram", path, stat) of what the device boots with, or None while there is nothing saved.

    GNS3 pushes a non-empty startup-config.cfg into NVRAM at every start and keeps it up to date, so it is
    what the node boots with. An empty one (GNS3 creates it for a node given an empty configuration) is never
    updated: the student's `write memory` is in NVRAM only.
    """
    path = directory / CONFIG_NAME
    info = regular_file(path, "saved configuration")
    if info is not None and info.st_size > 0:
        return "config", path, info
    nvram = find_nvram(directory, node)
    if nvram is not None and nvram[1].st_size > 0:
        return "nvram", nvram[0], nvram[1]
    return None


def read_bytes(path, limit, what):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as handle:
            raw = handle.read(limit + 1)
    except OSError as exc:
        raise Unreadable(f"Cannot read the {what}: {type(exc).__name__}") from exc
    if len(raw) > limit:
        raise ConfigError(f"The {what} is too large")
    return raw


def read_text(path, info):
    raw = read_bytes(path, SIZE_LIMIT, "saved configuration").rstrip(b"\x00")
    if b"\x00" in raw:
        raise ConfigError("The saved configuration is not text")
    return raw.decode("utf-8", errors="replace")


_exporters = {}  # path of iou_export.py -> its nvram_export


def exporter():
    """`nvram_export` of the iou_export.py of the installed gns3server.

    The module uses nothing but the standard library, and gns3server may well be installed for another
    interpreter than the slot's, so the file is loaded on its own instead of importing the package.
    `SLOT_IOU_EXPORT` names it; otherwise it is looked for in the gns3server this interpreter can see.
    """
    path = os.getenv("SLOT_IOU_EXPORT")
    if not path:
        try:
            package = importlib.util.find_spec("gns3server")
        except (ImportError, ValueError):
            package = None
        for root in (package.submodule_search_locations or []) if package else []:
            if os.path.isfile(os.path.join(root, EXPORT_PATH)):
                path = os.path.join(root, EXPORT_PATH)
                break
    if not path:
        raise Unsupported("gns3server's iou_export.py is not found (see SLOT_IOU_EXPORT)")
    if path not in _exporters:
        try:
            spec = importlib.util.spec_from_file_location("slot_iou_export", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _exporters[path] = module.nvram_export
        except (OSError, SyntaxError, AttributeError, ImportError) as exc:
            raise Unsupported(f"Cannot load iou_export.py: {type(exc).__name__}") from exc
    return _exporters[path]


def read_nvram(path):
    """The startup configuration inside an NVRAM file, or None if the device has saved none."""
    raw = read_bytes(path, NVRAM_LIMIT, "NVRAM")
    export = exporter()
    try:
        startup, _private = export(raw)  # the private config (keys) is not looked at
    except (ValueError, IndexError, TypeError) as exc:
        if str(exc) == "no startup config":  # the device has never saved one
            return None
        raise ConfigError(f"Cannot export the configuration from NVRAM: {type(exc).__name__}") from exc
    text = bytes(startup).rstrip(b"\x00")
    return text.decode("utf-8", errors="replace") if text.strip() else None


def port_map(node):
    """GNS3 port name -> {"adapter", "port"}; a name met twice is ambiguous and maps to nothing."""
    found, seen = {}, set()
    for port in node.get("ports") or []:
        name, adapter, number = port.get("name"), port.get("adapter_number"), port.get("port_number")
        if not isinstance(name, str) or type(adapter) is not int or type(number) is not int:
            continue
        if name in seen:
            found.pop(name, None)
            continue
        seen.add(name)
        found[name] = {"adapter": adapter, "port": number}
    return found


def collect(project_id, node, previous):
    """Snapshot of the saved configuration of an IOU node; `previous` is returned while the source is the same.

    None: nothing is saved (yet). A source is parsed again only when its size or modification time changed,
    or when the ports of the node did; a configuration that did not parse is not parsed again either.
    """
    directory = node_directory(project_id, node)
    source = find_source(directory, node) if directory is not None else None
    if source is None:
        return None
    kind, path, info = source
    ports = port_map(node)
    matched = tuple(sorted((name, port["adapter"], port["port"]) for name, port in ports.items()))
    stamp = (kind, str(path), info.st_size, info.st_mtime_ns, matched)
    if previous is not None and previous.stamp == stamp:
        return previous
    try:
        text = read_text(path, info) if kind == "config" else read_nvram(path)
        if text is None:
            return None
        return Snapshot(stamp, data=parse_ios_config(text, ports))
    except Unreadable:
        raise  # may pass without a change of the file: nothing to remember
    except ConfigError as exc:
        return Snapshot(stamp, error=str(exc))


# --- parsing ----------------------------------------------------------------------------------


def vlan_number(text, where):
    if not text.isdigit() or int(text) not in VLAN_IDS:
        raise ConfigError(f"{where}: bad VLAN number")
    return int(text)


def vlan_list(text, where):
    """`10,20,30-35` -> {10, 20, 30, ..., 35}."""
    if not VLAN_LIST.fullmatch(text):
        raise ConfigError(f"{where}: bad VLAN list")
    ids = set()
    for part in text.split(","):
        low, _, high = part.partition("-")
        first, last = int(low), int(high or low)
        if first > last or first not in VLAN_IDS or last not in VLAN_IDS:
            raise ConfigError(f"{where}: bad VLAN list")
        ids.update(range(first, last + 1))
    return ids


def prefix_length(mask, where):
    """Prefix length of a dotted netmask; a hostmask, holes or a stray word are a broken configuration."""
    try:
        value = int(ipaddress.IPv4Address(mask))
    except ValueError as exc:
        raise ConfigError(f"{where}: bad IPv4 mask") from exc
    holes = ~value & 0xFFFFFFFF
    if holes & (holes + 1):
        raise ConfigError(f"{where}: bad IPv4 mask")
    return 32 - holes.bit_length()


def ipv4_of(lines, where):
    """The addresses an interface is configured with; None when it takes one from the network (DHCP...)."""
    addresses, dynamic = [], False  # (interface, secondary)
    for line in lines:
        words = line.split()
        if words[:3] == ["no", "ip", "address"]:
            if len(words) == 3:
                addresses, dynamic = [], False
        elif words[:2] == ["ip", "address"]:
            if words[2:3] in (["dhcp"], ["negotiated"], ["pool"]):
                dynamic = True
                continue
            secondary = words[4:] == ["secondary"]
            if len(words) not in (4, 5) or (len(words) == 5 and not secondary):
                raise ConfigError(f"{where}: bad IPv4 address")
            try:
                address = ipaddress.IPv4Address(words[2])
            except ValueError as exc:
                raise ConfigError(f"{where}: bad IPv4 address") from exc
            interface = ipaddress.IPv4Interface(f"{address}/{prefix_length(words[3], where)}")
            if not secondary:  # a new primary address replaces the old one, secondaries stay
                addresses = [(i, s) for i, s in addresses if s]
            addresses.append((interface, secondary))
            dynamic = False
    if dynamic:
        return None
    return [str(i) for i in sorted({i for i, _ in addresses}, key=lambda i: (int(i.ip), i.network.prefixlen))]


def switchport_of(lines, where):
    """The L2 role of a switch port by its saved commands; None when the configuration does not say."""
    mode, access, native, allowed, routed = None, 1, 1, None, False
    for line in lines:
        words = line.split()
        if words == ["no", "switchport"]:
            routed = True
        elif words == ["switchport"]:
            routed = False
        elif words[:2] == ["switchport", "mode"]:
            mode = words[2] if len(words) > 2 else None
        elif words[:3] == ["switchport", "access", "vlan"]:
            access = vlan_number(" ".join(words[3:]), where)
        elif words[:4] == ["switchport", "trunk", "native", "vlan"]:
            native = vlan_number(" ".join(words[4:]), where)
        elif words[:4] == ["switchport", "trunk", "allowed", "vlan"]:
            allowed = allowed_vlans(allowed, words[4:], where)
    if routed:
        return None
    if mode == "trunk":
        # No `allowed` line: IOS allows everything. A full list is "all" as well.
        everything = allowed is None or allowed == ALL_VLANS
        return {"mode": "trunk", "allowed": "all" if everything else sorted(allowed), "native": native}
    if mode in (None, "access", "dynamic"):
        # Without `switchport mode` or with `dynamic ...` the port is what its neighbour makes of it (DTP);
        # until the owner decides (SLOT_REQUESTS.md 8), it is an access port.
        return {"mode": "access", "vlan": access}
    return None  # dot1q-tunnel, private VLANs...: not described by ios.config/1


def allowed_vlans(current, words, where):
    """Apply one `switchport trunk allowed vlan ...` line to the set so far (None: not set, everything)."""
    keyword = words[0] if words else ""
    base = ALL_VLANS if current is None else current
    if words == ["all"]:
        return ALL_VLANS
    if words == ["none"]:
        return set()
    if keyword in ("add", "remove", "except") and len(words) == 2:
        ids = vlan_list(words[1], where)
        if keyword == "add":
            return base | ids
        return base - ids if keyword == "remove" else ALL_VLANS - ids
    if len(words) == 1:
        return vlan_list(words[0], where)
    raise ConfigError(f"{where}: bad VLAN list")


def interface_entry(name, lines, ports, switch):
    """One `interface` block -> an interface of live-state.md 3.1, or None for kinds ios.config/1 does not describe."""
    where = f"Interface {name}"
    entry = {"name": name}
    if PHYSICAL.fullmatch(name):
        entry["kind"] = "physical"
        if name in ports:
            entry["port"] = ports[name]
    elif match := SUBIF.fullmatch(name):
        entry.update(kind="subif", parent=match.group(1))
        for line in lines:
            words = line.split()
            if words[:2] == ["encapsulation", "dot1Q"] and len(words) > 2:
                entry["dot1q"] = vlan_number(words[2], where)
    elif match := SVI.fullmatch(name):
        entry.update(kind="svi", vlan=vlan_number(match.group(1), where))
    elif LOOPBACK.fullmatch(name):
        entry["kind"] = "loopback"
    else:
        return None  # Tunnel, Port-channel, NVI...: live-state.md has no kind for them
    admin = "up"
    for line in lines:
        if line == "shutdown":
            admin = "down"
        elif line == "no shutdown":
            admin = "up"
    entry["admin"] = admin
    ipv4 = ipv4_of(lines, where)
    if ipv4 is not None:
        entry["ipv4"] = ipv4
    if switch and entry["kind"] == "physical" and (switchport := switchport_of(lines, where)) is not None:
        entry["switchport"] = switchport
    return entry


def skip_text(rest, lines):
    """Consume the lines of a multi-line text that begins on this line: it is not configuration."""
    delimiter = "^C" if rest.startswith("^C") else rest[:1]
    if not delimiter or delimiter in rest[len(delimiter) :]:
        return  # one-line banner, or none
    for line in lines:
        if delimiter in line:
            return


def blocks_of(text):
    """(interfaces, vlans, switch, vtp mode) by the structure of the text: blocks start in column 0, their
    commands are indented, `!` separates. Text of banners and macros is skipped: it may look like anything."""
    interfaces, vlans, switch, vtp = {}, {}, False, None
    current = None  # the commands of the open interface block, or the VLANs (dicts) of the open `vlan` block
    lines = iter(text.splitlines())
    for line in lines:
        stripped = line.strip()
        if not stripped or (stripped.startswith("!") and line[:1] not in " \t"):
            current = None if stripped else current
            continue
        if line[:1] in " \t":
            if stripped.startswith("!"):
                continue
            if isinstance(current, list):
                current.append(stripped)
            elif isinstance(current, tuple) and stripped.split()[:1] == ["name"]:
                for vlan in current:
                    vlan["name"] = stripped[len("name") :].strip()
            continue
        current = None
        words = stripped.split()
        if words[0] == "interface" and len(words) == 2:
            current = interfaces.setdefault(words[1], [])
        elif words[0] == "vlan" and len(words) == 2 and VLAN_LIST.fullmatch(words[1]):
            current = tuple(vlans.setdefault(vlan, {}) for vlan in sorted(vlan_list(words[1], "VLAN")))
        elif words[:2] == ["spanning-tree", "mode"]:
            switch = True
        elif words[:2] == ["vtp", "mode"] and len(words) > 2:
            vtp = words[2]
        elif words[0] == "banner" and len(words) > 2:
            skip_text(stripped.split(None, 2)[2], lines)
        elif words[:2] == ["macro", "name"]:
            for macro_line in lines:
                if macro_line.strip() == "@":
                    break
    return interfaces, vlans, switch, vtp


def natural(name):
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name))


def parse_ios_config(text, ports):
    """Turn a saved IOS configuration into the fragment of a live-state.md node that `ios.config/1` owns.

    Only what the configuration sets is kept (CONTRACTS.md 4.6): no `oper`, no routes, no OSPF. Comments and
    the order of the blocks do not matter, so the same saved configuration always gives the same `data`.
    """
    try:
        found, vlans, switch, vtp = blocks_of(text)
        interfaces = [
            entry
            for name in sorted(found, key=natural)
            if (entry := interface_entry(name, found[name], ports, switch)) is not None
        ]
        data = {"interfaces": interfaces}
        # The VLAN database sits in the configuration only in VTP transparent and off modes; in server and
        # client mode it is vlan.dat, which this collector does not read (SLOT_REQUESTS.md 8).
        if vtp in ("transparent", "off"):
            data["vlans"] = [
                {"id": vlan, **({"name": vlans[vlan]["name"]} if vlans[vlan].get("name") else {})}
                for vlan in sorted(vlans)
            ]
    except ConfigError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise ConfigError(f"Unexpected structure of the saved configuration: {type(exc).__name__}") from exc
    return data
