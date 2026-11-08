"""Collectors `linux.sys/1` and `linux.config/1` of a Linux guest (CONTRACTS.md 4.6).

`linux.sys/1` reads what the kernel and the resolver say about the node itself: the hostname, whether IPv4 is
forwarded, the DNS servers. `linux.config/1` reads the network configuration the guest keeps on disk, the one it
would come up with after a reboot (autochecks README 4.4, `persistence: saved`). Both are one `/bin/sh` exec each
and only read: no interface is touched, `ifup --no-act` only prints what it would do.

They are two collectors, not one, because the manager journals the whole `data` of a collector whenever it changes,
and because the saved configuration is a heavier read (a dozen short processes): a broken `ifquery` must not cost
the node its hostname. The guest's output is parsed here and never leaves the slot: only the fields below do.

The output of both commands is a sequence of sections, each opened by a line `@@gns3lab <name> [<arg>]`.
"""

import ipaddress
import re

MARK = "@@gns3lab"
MESSAGE_LIMIT = 200
TEXT_LIMIT = 65536  # resolv.conf is a few lines; a bigger one is cut, not refused


class ParseError(ValueError):
    """The output does not have the expected structure; the message names the part, never the guest's text."""


def sections(stdout):
    """[(name, arg, [lines])] in the order the command printed them."""
    found = []
    for line in stdout.splitlines():
        if line.startswith(MARK + " "):
            words = line[len(MARK) + 1 :].split(None, 1)
            if not words:
                raise ParseError("empty section mark")
            found.append((words[0], words[1].strip() if len(words) > 1 else "", []))
        elif found:
            found[-1][2].append(line)
        elif line.strip():
            raise ParseError("text before the first section")
    return found


def section_map(stdout):
    """name -> (arg, lines) of sections that occur once."""
    result = {}
    for name, arg, lines in sections(stdout):
        if name in result:
            raise ParseError(f"section {name} twice")
        result[name] = (arg, lines)
    return result


# --- linux.sys/1 --------------------------------------------------------------------------------------------------

# The kernel's own files, not `hostname`/`sysctl`: they exist in every Linux guest, busybox ones included.
# resolv.conf of systemd-resolved points at its stub (127.0.0.53); the servers it really asks are in
# /run/systemd/resolve/resolv.conf. A section is printed only for a file that can be read.
LINUX_SYS_COMMAND = f"""M='{MARK}'
h=$(cat /proc/sys/kernel/hostname) && f=$(cat /proc/sys/net/ipv4/ip_forward) || exit 1
printf '%s hostname\\n%s\\n%s ip_forward\\n%s\\n' "$M" "$h" "$M" "$f"
if [ -r /etc/resolv.conf ]; then echo "$M resolv"; head -c {TEXT_LIMIT} /etc/resolv.conf; echo; fi
if [ -r /run/systemd/resolve/resolv.conf ]; then
  echo "$M upstream"; head -c {TEXT_LIMIT} /run/systemd/resolve/resolv.conf; echo
fi
"""

RESOLVED_STUBS = ("127.0.0.53", "127.0.0.54")


def resolver_config(lines):
    """(servers, search) of a resolv.conf: `nameserver` lines in their order without repeats, and the last
    `search` or `domain` line (glibc takes the last of them). Comments start with `#` or `;`."""
    servers, search = [], []
    for line in lines:
        words = re.split(r"[#;]", line, maxsplit=1)[0].split()
        if not words:
            continue
        if words[0] == "nameserver" and len(words) >= 2:
            try:
                server = str(ipaddress.ip_address(words[1]))
            except ValueError:
                continue  # glibc skips a server it cannot parse as well
            if server not in servers:
                servers.append(server)
        elif words[0] in ("search", "domain"):
            search = words[1:]
    return servers, search


def parse_linux_sys(stdout):
    """`hostname`, `ip_forward` and `dns` of a live-state.md node from the output of LINUX_SYS_COMMAND."""
    parts = section_map(stdout)
    if "hostname" not in parts or "ip_forward" not in parts:
        raise ParseError("no hostname or ip_forward")
    hostname = "\n".join(parts["hostname"][1]).strip()
    if not hostname or "\n" in hostname:
        raise ParseError("bad hostname")
    forward = "\n".join(parts["ip_forward"][1]).strip()
    if not forward.isdigit():
        raise ParseError("bad ip_forward")
    data = {"hostname": hostname, "ip_forward": int(forward) != 0}
    servers, search = resolver_config(parts["resolv"][1]) if "resolv" in parts else ([], [])
    dns = {"servers": servers, "search": search}
    if servers and all(server in RESOLVED_STUBS for server in servers) and "upstream" in parts:
        # systemd-resolved: the stub is the guest asking itself; the upstream list is what it forwards to.
        dns["servers"] = resolver_config(parts["upstream"][1])[0]
        dns["resolver"] = "systemd-resolved"
    data["dns"] = dns
    return data


# --- linux.config/1 -----------------------------------------------------------------------------------------------

# The mechanism is chosen as the manager's preconfiguration chooses it (manager netconfig.py, PRELUDE): that is where
# it writes the configuration. Only ifupdown is read so far; for the others the name is reported and nothing else.
#
# ifupdown itself resolves `source` and `source-directory`: `ifquery --list` gives the interfaces brought up at boot
# (`auto`, and `allow-hotplug`), `ifquery <iface>` their options, `ifup --no-act --force` whether they would come up
# (it checks what ifquery does not: required options of a method). `-v` names the files ifquery parsed; the method of
# an interface is not in ifquery's output, so it is taken from the `iface <name> inet <method>` lines of exactly
# those files. Mappings run scripts: --no-mappings. Nothing here locks or changes ifupdown's state.
LINUX_CONFIG_COMMAND = f"""M='{MARK}'
set -f
active() {{ systemctl is-active --quiet "$1" 2>/dev/null; }}
has() {{ command -v "$1" >/dev/null 2>&1; }}
if active NetworkManager; then echo "$M mechanism networkmanager"
elif has netplan && active systemd-networkd; then echo "$M mechanism netplan"
elif active systemd-networkd; then echo "$M mechanism networkd"
elif has ifquery && has ifup; then
  echo "$M mechanism ifupdown"
  q="ifquery --no-mappings"
  a=$($q --list -a 2>&1); ra=$?
  h=$($q --list --allow=hotplug 2>&1); rh=$?
  printf '%s list %s\\n%s\\n%s list %s\\n%s\\n' "$M" "$ra" "$a" "$M" "$rh" "$h"
  echo "$M methods"
  {{ echo /etc/network/interfaces; $q -v --list -a 2>&1 >/dev/null | sed -n 's/^ifquery: parsing file //p'; }} |
    while IFS= read -r f; do awk '$1 == "iface" && $3 == "inet" {{ print $2, $4 }}' "$f" 2>/dev/null; done
  n=$(printf '%s\\n%s\\n' "$a" "$h" | awk 'NF == 1 && !/^ifquery:/ && !s[$0]++')
  for i in $n; do echo "$M iface $i"; $q "$i" 2>&1; echo "$M rc $?"; done
  echo "$M apply"
  if [ -n "$n" ]; then ifup --no-act --force --no-mappings --no-scripts $n 2>&1 >/dev/null; echo "$M rc $?"
  else echo "$M rc 0"; fi
else echo "$M mechanism none"
fi
"""

MECHANISMS = ("ifupdown", "networkmanager", "netplan", "networkd", "none")
READ_MECHANISMS = ("ifupdown", "none")  # the others are named, their files are not read yet
IFACE_NAME = re.compile(r"[^\s/:]{1,15}(?::[0-9A-Za-z_-]+)?")  # a kernel name, or an ifupdown alias eth0:1
ROUTE_KEYWORDS = {"add", "replace", "append"}


def short(text):
    return text.strip()[:MESSAGE_LIMIT]


def ipv4_prefix(mask):
    """A netmask as ifquery prints it (dotted, or a prefix length) -> prefix length; None if not one."""
    if mask.isdigit():
        return int(mask) if int(mask) <= 32 else None
    try:
        value = int(ipaddress.IPv4Address(mask))
    except ValueError:
        return None
    holes = ~value & 0xFFFFFFFF
    if holes & (holes + 1):
        return None
    return 32 - holes.bit_length()


def network_of(text):
    if text == "default":
        return "default"
    network = ipaddress.IPv4Network(text if "/" in text else text + "/32", strict=False)
    return "default" if network.prefixlen == 0 else str(network)


def strip_or_true(command):
    """`cmd || true` and `cmd || :` are the usual way to keep ifup going: the route is still `cmd`."""
    head, sep, tail = command.partition("||")
    if sep and tail.strip() in ("true", ":", "/bin/true"):
        return head.strip()
    return command.strip()


def route_of(command):
    """A static route from one `up` command, or None when it is not one this parser is sure about.

    Understood: `ip [-4] route add|replace|append <dst> via <ip> [dev <if>] [metric N] [onlink] [proto P]` and
    net-tools `route add [-net|-host] <dst> [netmask M] gw <ip> [dev <if>] [metric N]` (`<dst>` may be `default`).
    Anything else (other words, shell syntax, variables) is not a route for linux.config/1.
    """
    words = strip_or_true(command).split()
    if words and words[0] in ("/sbin/ip", "/bin/ip", "/usr/sbin/ip", "/usr/bin/ip"):
        words[0] = "ip"
    if words and words[0] in ("/sbin/route", "/usr/sbin/route"):
        words[0] = "route"
    try:
        if words[:1] == ["ip"]:
            rest = words[1:]
            if rest[:1] == ["-4"]:
                rest = rest[1:]
            if rest[:1] not in (["route"], ["r"], ["ro"], ["rou"], ["rout"]) or rest[1] not in ROUTE_KEYWORDS:
                return None
            dst, options = network_of(rest[2]), rest[3:]
            known = {"via": 1, "dev": 1, "metric": 1, "proto": 1, "onlink": 0, "src": 1}
        elif words[:2] == ["route", "add"]:
            rest = words[2:]
            kind = None
            if rest[:1] in (["-net"], ["-host"]):
                kind, rest = rest[0], rest[1:]
            target, options = rest[0], rest[1:]
            if options[:1] == ["netmask"]:
                prefix = ipv4_prefix(options[1])
                if prefix is None or "/" in target:
                    return None
                target, options = f"{target}/{prefix}", options[2:]
            elif kind == "-host" and "/" not in target and target != "default":
                target += "/32"
            dst = network_of(target)
            options = ["via" if word == "gw" else word for word in options]
            known = {"via": 1, "dev": 1, "metric": 1}
        else:
            return None
        values, index = {}, 0
        while index < len(options):
            word = options[index]
            if word not in known or word in values:
                return None
            values[word] = options[index + 1] if known[word] else ""
            index += 1 + known[word]
    except (IndexError, ValueError):
        return None
    if "via" not in values:
        return None  # a route without a next hop is a device route: not what the gateway rules read
    try:
        route = {"dst": dst, "via": str(ipaddress.IPv4Address(values["via"]))}
    except ValueError:
        return None
    if "dev" in values:
        if not IFACE_NAME.fullmatch(values["dev"]):
            return None
        route["dev"] = values["dev"]
    return route


def ifupdown_interface(name, method, lines):
    """(interface of `net_saved`, problem or None) from the `key: value` lines of `ifquery <name>`.

    Options of all stanzas of the interface come in one run, inet6 ones included, each stanza in the order of its
    file, the values ifupdown computes (`netmask` of `address A/N`, `broadcast`) after it. So a `netmask` belongs to
    the address before it that has none yet, or else to the next address. ifquery prints `address A/N` as
    `address A` + `netmask M`; an address it could not split (not a valid one) is a problem of the configuration.
    """
    entry = {"name": name}
    if method:
        entry["method"] = method
    addresses, gateway, routes, problem = [], None, [], None
    current, pending = None, None  # the last address still without a mask; a mask seen before its address
    for line in lines:
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key == "address":
            text, slash, prefix = value.partition("/")
            try:
                address = ipaddress.ip_address(text)
            except ValueError:
                address = None
            if address is None or (slash and not (prefix.isdigit() and int(prefix) <= address.max_prefixlen)):
                problem = problem or f"{name}: bad address {value}"
                current, pending = None, None
                continue
            item = [address, int(prefix) if slash else None]
            if item[1] is None and pending is not None:
                item[1], pending = pending, None
            current = item if item[1] is None else None
            if address.version == 4:
                addresses.append(item)
        elif key == "netmask":
            if current is not None:
                current[1], current = value, None
            else:
                pending = value
        elif key == "gateway" and gateway is None:
            try:
                gateway = str(ipaddress.IPv4Address(value))
            except ValueError:
                if ":" not in value:  # an inet6 gateway is not ours, anything else is broken
                    problem = problem or f"{name}: bad gateway {value}"
        elif key == "up" and (route := route_of(value)) is not None and route not in routes:
            routes.append(route)
    ipv4 = set()
    for address, mask in addresses:
        prefix = 32 if mask is None else mask if isinstance(mask, int) else ipv4_prefix(mask)
        if prefix is None:
            problem = problem or f"{name}: bad netmask {mask}"
            continue
        ipv4.add(ipaddress.IPv4Interface(f"{address}/{prefix}"))
    entry["ipv4"] = [str(i) for i in sorted(ipv4, key=lambda i: (int(i.ip), i.network.prefixlen))]
    if gateway is not None:
        entry["gateway"] = gateway
    if routes:
        entry["routes"] = sorted(routes, key=lambda r: (r["dst"], r["via"], r.get("dev", "")))
    return entry, problem


def rc_of(arg):
    if not arg.lstrip("-").isdigit():
        raise ParseError("bad exit status")
    return int(arg)


def errors_of(lines, tool):
    """The messages ifupdown printed about itself (`ifquery: …`, `ifup: …`), without its prefix."""
    prefix = tool + ": "
    return [line[len(prefix) :].strip() for line in lines if line.startswith(prefix)]


def parse_ifupdown(parts):
    names, problems = [], []
    lists = parts.get("list") or []
    if len(lists) != 2:
        raise ParseError("ifquery lists missing")
    for arg, lines in lists:
        rc = rc_of(arg)
        errors = errors_of(lines, "ifquery")
        for line in lines:
            line = line.strip()
            if line and not line.startswith("ifquery:") and line not in names:
                if not IFACE_NAME.fullmatch(line):
                    raise ParseError("bad interface name")
                names.append(line)
        if rc != 0:
            problems.append(errors[0] if errors else f"ifquery --list exited with {rc}")
    methods = {}
    for line in (parts.get("methods") or [("", [])])[0][1]:
        words = line.split()
        if len(words) == 2 and words[0] not in methods:
            methods[words[0]] = words[1]
    queried = {}
    stanzas = parts.get("iface") or []
    for arg, lines in stanzas:
        if not lines or not lines[-1].startswith(MARK):  # the rc line is folded into the section by sections()
            raise ParseError("ifquery result without exit status")
        queried[arg] = lines
    interfaces = []
    for name in names:
        lines = queried.get(name)
        if lines is None:
            raise ParseError("an interface was not queried")
        method = methods.get(name) or ("loopback" if name == "lo" else None)
        rc = rc_of(lines[-1].split()[-1])
        body = lines[:-1]
        if rc != 0:
            errors = errors_of(body, "ifquery")
            problems.append(errors[0] if errors else f"ifquery {name} exited with {rc}")
            continue  # ifupdown does not know it: it will not come up, there is nothing to describe
        entry, problem = ifupdown_interface(name, method, [line for line in body if not line.startswith("ifquery:")])
        if problem:
            problems.append(problem)
        interfaces.append(entry)
    apply = parts.get("apply") or []
    if len(apply) != 1 or not apply[0][1] or not apply[0][1][-1].startswith(MARK):
        raise ParseError("ifup --no-act result missing")
    lines = apply[0][1]
    rc = rc_of(lines[-1].split()[-1])
    if rc != 0:
        errors = [e for e in errors_of(lines[:-1], "ifup") if not e.startswith("failed to bring up")]
        problems.append("; ".join(errors) if errors else f"ifup --no-act exited with {rc}")
    saved = {"mechanism": "ifupdown", "ok": not problems}
    if problems:
        saved["message"] = short(problems[0])
    saved["interfaces"] = sorted(interfaces, key=lambda entry: natural(entry["name"]))
    return saved


def natural(name):
    return tuple((0, int(part), "") if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", name))


def parse_linux_config(stdout):
    """`net_saved` of a live-state.md node from the output of LINUX_CONFIG_COMMAND."""
    grouped = {}
    for name, arg, lines in fold_rc(sections(stdout)):
        grouped.setdefault(name, []).append((arg, lines))
    mechanisms = grouped.get("mechanism") or []
    if len(mechanisms) != 1 or mechanisms[0][0] not in MECHANISMS:
        raise ParseError("unknown mechanism")
    mechanism = mechanisms[0][0]
    if mechanism == "none":
        return {"net_saved": {"mechanism": "none", "ok": True, "interfaces": []}}
    if mechanism not in READ_MECHANISMS:
        return {"net_saved": {"mechanism": mechanism, "supported": False}}
    return {"net_saved": parse_ifupdown(grouped)}


def fold_rc(found):
    """`@@gns3lab rc N` closes the section before it: keep it as the last line of that section."""
    result = []
    for name, arg, lines in found:
        if name == "rc":
            if not result:
                raise ParseError("exit status without a command")
            if any(line.strip() for line in lines):
                raise ParseError("text after an exit status")
            result[-1][2].append(f"{MARK} rc {arg}")
            continue
        result.append((name, arg, lines))
    return result
