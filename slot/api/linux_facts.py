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

import fnmatch
import ipaddress
import json
import re
import shlex
from dataclasses import dataclass, field

MARK = "@@gns3lab"
MESSAGE_LIMIT = 200
TEXT_LIMIT = 65536  # resolv.conf is a few lines; a bigger one is cut, not refused
NAT_LIMIT = 1 << 20  # the ruleset of a router (VyOS) is tens of KiB; a cut one is not read, the hostname stays


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
n=; if command -v nft >/dev/null 2>&1; then
  n=1; o=$(nft -j list ruleset 2>/dev/null); r=$?; echo "$M nft $r"; printf '%s' "$o" | head -c {NAT_LIMIT}; echo
fi
if command -v iptables-save >/dev/null 2>&1; then
  case $(iptables-save --version 2>/dev/null) in
    *nf_tables*) i=; [ -z "$n" ] && i=1 ;;
    *) i=; grep -qx nat /proc/net/ip_tables_names 2>/dev/null && i=1 ;;
  esac
  if [ -n "$i" ]; then
    o=$(iptables-save -t nat 2>/dev/null); r=$?; echo "$M iptables $r"; printf '%s' "$o" | head -c {NAT_LIMIT}; echo
  fi
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
    snat = snat_of(parts)
    if snat is not None:
        data["snat"] = snat
    return data


# --- source NAT of a Linux router (linux.sys/1 `snat`) -------------------------------------------------------------
#
# What leaves the node translated: the rules of the nat chains on the postrouting hook, in the order the kernel walks
# them. nftables shows its own rules, those of iptables-nft and those VyOS compiles. iptables-save is read when it is
# the nf_tables one and there is no nft (the same rules, seen the other way), or when it is the legacy one and the
# legacy kernel tables hold a nat table (it is not run otherwise: it would load the module). A rule is
# {"action": "masquerade" | "snat" | "accept" | "return" | "other", "out": <interface> | None, "src": <net> | None,
#  "exact": bool}; `exact` is false when the rule has a condition this parser does not read (a protocol, a port, a
# negation, a wildcard interface, a mark...): the manager then gives it the benefit of the doubt.

TRANSLATE = ("masquerade", "snat")
XT_TARGETS = {"MASQUERADE": "masquerade", "SNAT": "snat", "ACCEPT": "accept", "RETURN": "return"}


def snat_rule(action, out=None, src=None, exact=True):
    return {"action": action, "out": out, "src": src, "exact": exact}


def nft_value(right):
    """The right side of an nft JSON match -> a network as text, or None when it is not one (sets, ranges...)."""
    if isinstance(right, str):
        return str(ipaddress.IPv4Network(right, strict=False))
    if isinstance(right, dict) and isinstance(right.get("prefix"), dict):
        prefix = right["prefix"]
        return str(ipaddress.IPv4Network(f"{prefix['addr']}/{int(prefix['len'])}", strict=False))
    return None


def nft_rule(expressions):
    out = src = None
    exact, action = True, None
    for expr in expressions:
        if not isinstance(expr, dict) or len(expr) != 1:
            exact = False
            continue
        (key, value), = expr.items()
        if key in ("counter", "log", "limit"):
            continue
        if key == "match" and isinstance(value, dict) and value.get("op") in ("==", "in"):
            left, right = value.get("left") or {}, value.get("right")
            meta = left.get("meta", {}).get("key") if isinstance(left.get("meta"), dict) else None
            payload = left.get("payload") if isinstance(left.get("payload"), dict) else {}
            if meta in ("oifname", "oif") and isinstance(right, str) and not right.endswith("*"):
                out = right
                continue
            if payload.get("protocol") == "ip" and payload.get("field") == "saddr":
                try:
                    src = nft_value(right)
                except (ValueError, KeyError, TypeError):
                    src = None
                if src is not None:
                    continue
            exact = False
        elif key in ("masquerade", "snat", "accept", "return"):
            action = key
        elif key == "xt" and isinstance(value, dict):  # iptables-nft: a target or match nft cannot show natively
            name = str(value.get("name", ""))
            if value.get("type") == "target":
                action = XT_TARGETS.get(name, "other")
            elif name != "comment":
                exact = False
        elif key in ("jump", "goto", "drop", "dnat", "redirect"):
            action = "other"
        else:
            exact = False
    if action is None:
        return None  # a rule without a verdict changes nothing (a counter): the walk goes on
    return snat_rule(action, out, src, exact)


def parse_nft(text):
    """`nft -j list ruleset` -> source NAT rules of the nat chains on the postrouting hook, by priority."""
    document = json.loads(text)
    items = document.get("nftables") if isinstance(document, dict) else None
    if not isinstance(items, list):
        raise ParseError("nft printed no ruleset")
    chains = {}
    for item in items:
        chain = item.get("chain") if isinstance(item, dict) else None
        if isinstance(chain, dict) and chain.get("type") == "nat" and chain.get("hook") == "postrouting" \
                and chain.get("family") in ("ip", "inet"):
            prio = chain.get("prio")
            chains[(chain["family"], chain.get("table"), chain.get("name"))] = prio if isinstance(prio, int) else 100
    rules = []
    for order, item in enumerate(items):
        rule = item.get("rule") if isinstance(item, dict) else None
        if not isinstance(rule, dict):
            continue
        key = (rule.get("family"), rule.get("table"), rule.get("chain"))
        if key in chains and (entry := nft_rule(rule.get("expr") or [])) is not None:
            rules.append((chains[key], order, entry))
    return [entry for _, _, entry in sorted(rules, key=lambda r: (r[0], r[1]))]


def iptables_rule(words):
    out = src = None
    exact, action = True, None
    k = 0
    while k < len(words):
        word = words[k]
        nxt = words[k + 1] if k + 1 < len(words) else None
        if word in ("-s", "--source") and nxt:
            try:
                src = str(ipaddress.IPv4Network(nxt, strict=False))
            except ValueError:
                exact = False
            k += 2
        elif word in ("-o", "--out-interface") and nxt:
            if nxt.endswith("+"):
                exact = False
            else:
                out = nxt
            k += 2
        elif word in ("-j", "--jump") and nxt:
            action = XT_TARGETS.get(nxt, "other")
            k += 2
        elif word == "-m" and nxt == "comment":
            k += 2
        elif word == "--comment" and nxt:
            k += 2
        elif word in ("--to-source", "--to-ports", "--random", "--random-fully", "--persistent"):
            k += 1 if word in ("--random", "--random-fully", "--persistent") else 2
        else:
            exact = False  # `!`, a protocol, a port, a match module...
            k += 1
    if action is None:
        return None
    return snat_rule(action, out, src, exact)


def parse_iptables(text):
    """`iptables-save -t nat` -> source NAT rules of the POSTROUTING chain, in order."""
    rules = []
    for line in text.splitlines():
        words = shlex.split(line) if line.startswith("-A POSTROUTING ") else []
        if words and (entry := iptables_rule(words[2:])) is not None:
            rules.append(entry)
    return rules


def snat_of(parts):
    """`snat` of linux.sys/1, or None when the guest could not tell (no tool answered, a cut or broken output)."""
    rules, told = [], False
    for name, parse in (("nft", parse_nft), ("iptables", parse_iptables)):
        if name not in parts:
            continue
        rc, lines = parts[name]
        if rc != "0":
            return None
        text = "\n".join(lines).strip()
        try:
            rules += parse(text) if text else []
        except (ParseError, ValueError, TypeError, KeyError, AttributeError):
            return None
        told = True
    if not told:
        return [] if "nft" not in parts and "iptables" not in parts else None
    return rules


# --- linux.config/1 -----------------------------------------------------------------------------------------------

# The mechanism is chosen as the manager's preconfiguration chooses it (manager netconfig.py, PRELUDE): that is where
# it writes the configuration. Each is read as it would be read at the next boot, and nothing in the guest changes:
#   ifupdown        by ifupdown itself (below);
#   networkd        the .network files and their drop-ins in /etc and /usr/lib: /run is gone after a reboot;
#   netplan         netplan's own generator on a copy of the YAML in a temporary root, which gives what the boot-time
#                   generator would write to /run/systemd/network, and the .network files as for networkd;
#   networkmanager  the keyfiles in /etc and /usr/lib and what netplan would generate for NetworkManager; nmcli tells
#                   which devices NetworkManager manages, `NetworkManager --print-config` its merged configuration.
# Those files are parsed in the slot: they may hold secrets, and only the fields of net_saved leave it. Every line of
# a file is printed after a `|`, so that nothing in a file can open a section of its own. `links` gives what the
# daemons compare a configuration against: the name, the MAC and the type of each interface of the guest.
#
# ifupdown itself resolves `source` and `source-directory`: `ifquery --list` gives the interfaces brought up at boot
# (`auto`, and `allow-hotplug`), `ifquery <iface>` their options, `ifup --no-act --force` whether they would come up
# (it checks what ifquery does not: required options of a method). `-v` names the files ifquery parsed; the method of
# an interface is not in ifquery's output, so it is taken from the `iface <name> inet <method>` lines of exactly
# those files. Mappings run scripts: --no-mappings. Nothing here locks or changes ifupdown's state.
LINUX_CONFIG_COMMAND = f"""M='{MARK}'
R="${{GNS3LAB_ROOT-}}"
set -f
active() {{ systemctl is-active --quiet "$1" 2>/dev/null; }}
has() {{ command -v "$1" >/dev/null 2>&1; }}
show() {{
  f=$1; shown=${{2-}}
  if [ -z "$shown" ]; then shown=${{1#"$R"}}; fi
  if [ -L "$f" ] && [ "$(readlink -f "$f")" = /dev/null ]; then echo "$M file 0 0 $shown"; return 0; fi
  if [ ! -f "$f" ]; then return 0; fi
  s=$(stat -L -c '%u %a' "$f" 2>/dev/null) || s='- -'
  echo "$M file $s $shown"
  head -c {TEXT_LIMIT} "$f" 2>/dev/null | sed 's/^/|/'; echo
}}
links() {{
  set +f
  for p in "$R"/sys/class/net/*; do
    if [ ! -e "$p" ]; then continue; fi
    n=${{p##*/}}
    echo "$M link $n"
    for k in address type addr_assign_type; do v=$(cat "$p/$k" 2>/dev/null); echo "$k=$v"; done
    echo "devtype=$(sed -n 's/^DEVTYPE=//p' "$p/uevent" 2>/dev/null)"
    if [ -e "$p/device" ]; then d=$(readlink "$p/device/driver" 2>/dev/null); echo "driver=${{d##*/}}"; fi
    ip -o link show dev "$n" 2>/dev/null | awk '{{for (i = 1; i < NF; i++) if ($i == "altname") print "altname=" $(i + 1)}}'
  done
  set -f
}}
networkd() {{
  set +f
  for d in /etc/systemd/network /usr/local/lib/systemd/network /usr/lib/systemd/network /lib/systemd/network; do
    for f in "$R$d"/*.network "$R$d"/*.network.d/*.conf; do show "$f"; done
  done
  set -f
}}
regen() {{
  g=
  for c in /usr/libexec/netplan/generate /usr/lib/netplan/generate /lib/netplan/generate; do
    if [ -x "$c" ]; then g=$c; break; fi
  done
  T=
  if [ -n "$g" ]; then T=$(mktemp -d 2>/dev/null) || T=; fi
  if [ -z "$T" ]; then echo "$M generate missing"; return 0; fi
  for d in lib etc run; do
    if [ -d "$R/$d/netplan" ]; then mkdir -p "$T/$d" && cp -pR "$R/$d/netplan" "$T/$d/"; fi
  done
  o=$("$g" --root-dir "$T" 2>&1); r=$?
  echo "$M generate $r"
  printf '%s\\n' "$o" | sed -e "s|$T||g" -e 's/^/|/'
  set +f
  for f in "$T"/run/systemd/network/*.network "$T"/run/systemd/network/*.network.d/*.conf; do
    show "$f" "/run/systemd/network/${{f#"$T"/run/systemd/network/}}"
  done
  for f in "$T"/run/NetworkManager/system-connections/*; do
    show "$f" "/run/NetworkManager/system-connections/${{f##*/}}"
  done
  set -f
  rm -rf -- "${{T:?}}"
}}
nm() {{
  echo "$M devices"; nmcli -t -e yes -f DEVICE,TYPE,STATE device 2>&1; echo "$M rc $?"
  c=$(NetworkManager --print-config 2>&1); r=$?
  echo "$M config $r"; printf '%s\\n' "$c" | sed 's/^/|/'
  set +f
  for f in "$R"/etc/NetworkManager/system-connections/* "$R"/usr/lib/NetworkManager/system-connections/*.nmconnection; do
    show "$f"
  done
  for f in "$R"/etc/sysconfig/network-scripts/ifcfg-*; do if [ -f "$f" ]; then echo "$M ifcfg ${{f##*/}}"; fi; done
  set -f
  show "$R/var/lib/NetworkManager/timestamps"
  show "$R/var/lib/NetworkManager/no-auto-default.state"
  if has netplan; then regen; fi
}}
if active NetworkManager; then echo "$M mechanism networkmanager"; links; nm
elif has netplan && active systemd-networkd; then echo "$M mechanism netplan"; links; regen; networkd
elif active systemd-networkd; then echo "$M mechanism networkd"; links; networkd
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
IFACE_NAME = re.compile(r"[^\s/:]{1,15}(?::[0-9A-Za-z_-]+)?")  # a kernel name, or an ifupdown alias eth0:1
ROUTE_KEYWORDS = {"add", "replace", "append"}


class Unsure(Exception):
    """The slot cannot tell what the guest would come up with: net_saved is `supported: false` with the reason."""


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
    return saved_of("ifupdown", interfaces, problems)


def saved_of(mechanism, interfaces, problems):
    saved = {"mechanism": mechanism, "ok": not problems}
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
    if mechanism == "ifupdown":
        return {"net_saved": parse_ifupdown(grouped)}
    try:
        if mechanism == "networkmanager":
            saved = read_networkmanager(grouped)
        else:
            saved = read_networkd(grouped, mechanism)
    except Unsure as exc:
        saved = {"mechanism": mechanism, "supported": False, "message": short(str(exc))}
    return {"net_saved": saved}


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


# --- linux.config/1: what every reader shares ---------------------------------------------------------------------

UNKNOWN = object()  # a property of an interface the slot cannot see (a match on it decides nothing)
ARPHRD = {"1": "ether", "772": "loopback", "65534": "none", "32": "infiniband", "768": "ipip", "776": "sit",
          "778": "gre", "823": "ip6gre"}


@dataclass(frozen=True)
class Link:
    """An interface of the guest as the network daemons compare it with a configuration."""

    name: str
    mac: str | None
    permanent: bool  # the MAC is the one of the hardware (addr_assign_type 0), not one set later
    kind: object  # systemd's link type: ether, loopback, wlan, bridge, …; UNKNOWN when the slot cannot tell
    driver: object  # the driver of a device; None for a virtual interface (it has no device)
    physical: bool
    altnames: tuple = ()


def mac_of(text):
    """A MAC address in lower case with colons, or None if the text is not one."""
    text = (text or "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{2}([:-][0-9a-f]{2}){5}", text):
        return text.replace("-", ":")
    if re.fullmatch(r"[0-9a-f]{4}(\.[0-9a-f]{4}){2}", text):
        digits = text.replace(".", "")
        return ":".join(digits[i : i + 2] for i in range(0, 12, 2))
    return None


def parse_links(parts):
    links = {}
    for name, lines in parts.get("link") or []:
        if not IFACE_NAME.fullmatch(name) or ":" in name or name in links:
            raise ParseError("bad interface name")
        values, altnames = {}, []
        for line in lines:
            key, sep, value = line.partition("=")
            if not sep:
                continue
            if key == "altname":
                altnames.append(value.strip())
            else:
                values[key] = value.strip()
        physical = "driver" in values
        kind = values.get("devtype") or ARPHRD.get(values.get("type", ""), UNKNOWN)
        links[name] = Link(name, mac_of(values.get("address")), values.get("addr_assign_type") == "0", kind,
                           (values["driver"] or UNKNOWN) if physical else None, physical, tuple(altnames))
    return links


def file_text(lines):
    """The lines of a file as the command printed them: each after a `|`."""
    return [line[1:] for line in lines if line.startswith("|")]


def parse_files(parts):
    """path -> (uid, mode, [lines]) of the `file` sections. A masked file (empty, or a link to /dev/null) has no lines."""
    files = {}
    for arg, lines in parts.get("file") or []:
        words = arg.split(" ", 2)
        if len(words) != 3 or not words[2].startswith("/"):
            raise ParseError("bad file header")
        files[words[2]] = (words[0], words[1], file_text(lines))
    return files


def conjunction(results):
    """True when every condition holds, False when one fails, None when the unknown ones decide."""
    if False in results:
        return False
    return None if None in results else True


def interface_entry(name, method, addresses, gateways, routes):
    """An interface of net_saved: the first default gateway is `gateway`, the other ones are routes to `default`."""
    entry = {"name": name, "method": method}
    entry["ipv4"] = [str(i) for i in sorted(set(addresses), key=lambda i: (int(i.ip), i.network.prefixlen))]
    unique = []
    for route in [{"dst": "default", "via": via} for via in gateways[1:]] + routes:
        if route not in unique and not (gateways and route == {"dst": "default", "via": gateways[0]}):
            unique.append(route)
    if gateways:
        entry["gateway"] = gateways[0]
    if unique:
        entry["routes"] = sorted(unique, key=lambda r: (r["dst"], r["via"]))
    return entry


def ipv4_of(value):
    """An IPv4 address, None for one of IPv6; ValueError for anything else."""
    if ":" in value:
        ipaddress.IPv6Address(value)
        return None
    return str(ipaddress.IPv4Address(value))


# --- linux.config/1: systemd-networkd and netplan -----------------------------------------------------------------

# networkd reads them in this order of precedence; a file of one name hides the files of that name further down.
# /run/systemd/network is listed for what netplan's generator would write there at boot (the slot runs it on a copy),
# the real one is not read: it is gone after a reboot.
NETWORKD_DIRS = ("/etc/systemd/network", "/run/systemd/network", "/usr/local/lib/systemd/network",
                 "/usr/lib/systemd/network", "/lib/systemd/network")
NETWORKD_MATCH = ("Name", "MACAddress", "PermanentMACAddress", "Type", "Driver", "Kind")
DHCP4 = {"yes": True, "true": True, "1": True, "on": True, "ipv4": True, "both": True, "v4": True,
         "no": False, "false": False, "0": False, "off": False, "ipv6": False, "v6": False, "none": False}


@dataclass
class NetworkUnit:
    path: str
    match: dict = field(default_factory=dict)  # key -> [(pattern, negated)], or a set of MACs
    unknown: list = field(default_factory=list)  # [Match] keys the slot cannot check
    managed: bool = True  # [Link] Unmanaged=no, and not ActivationPolicy=down
    dhcp4: bool = False
    addresses: list = field(default_factory=list)
    gateways: list = field(default_factory=list)  # default routes in the order of the file
    routes: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    unsure: str | None = None


def unit_sections(lines):
    """[(section, [(key, value)])] of a systemd unit-style file: comments dropped, `\\` continuation lines joined."""
    joined, buffer = [], None
    for raw in lines:
        line = raw.strip()
        if buffer is not None:
            if line.startswith(("#", ";")):
                continue
            line, buffer = f"{buffer} {line}", None
        if line.endswith("\\") and not line.startswith(("#", ";")):
            buffer = line[:-1].rstrip()
            continue
        joined.append(line)
    if buffer is not None:
        joined.append(buffer)
    result = []
    for line in joined:
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            result.append((line[1:-1].strip(), []))
            continue
        key, sep, value = line.partition("=")
        if sep and result:  # anything else networkd ignores with a warning
            result[-1][1].append((key.strip(), value.strip()))
    return result


def strv_add(entries, value):
    """A list setting of [Match]: an empty assignment clears it, a leading `!` inverts the whole assignment."""
    if not value:
        entries.clear()
        return
    negated = value.startswith("!")
    entries.extend((word, negated) for word in (value[1:] if negated else value).split())


def test_strv(entries, value):
    """systemd's net_condition_test_strv: no inverted pattern may match, and one plain pattern must if any exist."""
    if not entries:
        return True
    if value is UNKNOWN:
        return None
    plain = False
    matched = False
    for pattern, negated in entries:
        plain = plain or not negated
        if value is not None and fnmatch.fnmatchcase(value, pattern):
            if negated:
                return False
            matched = True
    return matched if plain else True


def networkd_unit(path, lines):
    unit = NetworkUnit(path)
    for section, items in unit_sections(lines):
        if section == "Match":
            for key, value in items:
                if key in ("MACAddress", "PermanentMACAddress"):
                    macs = unit.match.setdefault(key, set())
                    if not value:
                        macs.clear()
                    macs.update(mac for mac in map(mac_of, value.split()) if mac)
                elif key in NETWORKD_MATCH:
                    strv_add(unit.match.setdefault(key, []), value)
                else:
                    unit.unknown.append(key)
        elif section == "Link":
            for key, value in items:
                if key == "Unmanaged" and DHCP4.get(value.lower()) is True:
                    unit.managed = False
                elif key == "ActivationPolicy" and value in ("down", "always-down", "manual"):
                    unit.managed = False
        elif section == "Network":
            for key, value in items:
                if key == "DHCP":
                    if value.lower() in DHCP4:
                        unit.dhcp4 = DHCP4[value.lower()]
                    else:
                        unit.problems.append(f"{path}: bad DHCP={value}")
                elif key == "Address" and value:
                    networkd_address(unit, value)
                elif key == "Gateway" and value:
                    networkd_route(unit, {"Gateway": value})
        elif section == "Address":
            values = dict(items)
            if values.get("Address"):
                networkd_address(unit, values["Address"])
        elif section == "Route":
            networkd_route(unit, dict(items))
    return unit


def networkd_address(unit, value):
    if "/" not in value:
        unit.unsure = unit.unsure or f"{unit.path}: Address={value} has no prefix length"
        return
    try:
        address = ipaddress.ip_interface(value)
    except ValueError:
        unit.problems.append(f"{unit.path}: bad Address={value}")
        return
    if address.version == 4:
        if address.ip == ipaddress.IPv4Address("0.0.0.0"):
            unit.unsure = unit.unsure or f"{unit.path}: Address={value} lets networkd choose the address"
        else:
            unit.addresses.append(address)


def networkd_route(unit, values):
    """A route of [Route] (or `Gateway=` of [Network]) of the main table through a gateway; others are not routes
    for linux.config/1. A destination without a prefix length is not read either: its length is not certain."""
    if values.get("Table", "") not in ("", "main", "254") or values.get("Type", "unicast") != "unicast":
        return
    gateway = values.get("Gateway", "")
    if gateway in ("", "_dhcp4", "_ipv6ra", "_dhcp6"):
        return
    try:
        via = ipv4_of(gateway)
    except ValueError:
        unit.problems.append(f"{unit.path}: bad Gateway={gateway}")
        return
    if via is None or via == "0.0.0.0":
        return
    destination = values.get("Destination", "")
    if not destination:
        unit.gateways.append(via)
        return
    if "/" not in destination or ":" in destination:
        return
    try:
        network = ipaddress.IPv4Network(destination, strict=False)
    except ValueError:
        unit.problems.append(f"{unit.path}: bad Destination={destination}")
        return
    if network.prefixlen == 0:
        unit.gateways.append(via)
    elif {"dst": str(network), "via": via} not in unit.routes:
        unit.routes.append({"dst": str(network), "via": via})


def networkd_units(files):
    """The .network files networkd would read, in its order (by file name), drop-ins appended to their file."""
    mains, dropins = {}, {}
    for path, (_uid, _mode, lines) in files.items():
        directory, _, base = path.rpartition("/")
        if directory in NETWORKD_DIRS and base.endswith(".network"):
            rank = NETWORKD_DIRS.index(directory)
            if base not in mains or rank < mains[base][0]:
                mains[base] = (rank, path, lines)
            continue
        parent, _, folder = directory.rpartition("/")
        if parent in NETWORKD_DIRS and folder.endswith(".network.d") and base.endswith(".conf"):
            key, rank = (folder[: -len(".d")], base), NETWORKD_DIRS.index(parent)
            if key not in dropins or rank < dropins[key][0]:
                dropins[key] = (rank, lines)
    units = []
    for base in sorted(mains):
        _rank, path, lines = mains[base]
        if not lines:
            continue  # masked: empty, or a link to /dev/null
        extra = [dropins[key][1] for key in sorted(dropins) if key[0] == base]
        units.append(networkd_unit(path, lines + [line for more in extra for line in ["[]"] + more]))
    return units


def networkd_matches(unit, link):
    results = []
    for key, entries in unit.match.items():
        if key == "Name":
            results.append(any(test_strv(entries, name) for name in (link.name, *link.altnames)))
        elif key == "MACAddress":
            results.append(not entries or link.mac in entries)
        elif key == "PermanentMACAddress":
            results.append(not entries or (link.mac in entries if link.permanent else None))
        elif key == "Type":
            results.append(test_strv(entries, link.kind))
        elif key == "Driver":
            results.append(test_strv(entries, link.driver if link.physical else UNKNOWN))
        elif key == "Kind":  # hardware and the loopback have no kind; a virtual one has, the slot does not read it
            results.append(test_strv(entries, None if link.physical or link.kind == "loopback" else UNKNOWN))
    results.extend(None for _ in unit.unknown)
    return conjunction(results)


def generate_problems(parts):
    """Problems of the YAML, as netplan's generator reports them; [] when it would write its files at boot."""
    found = parts.get("generate") or []
    if len(found) != 1:
        raise ParseError("netplan generator result missing")
    arg, lines = found[0]
    if arg == "missing":
        raise Unsure("netplan's generator cannot be run in the guest")
    rc = rc_of(arg)
    if rc == 0:
        return []
    text = [line.strip() for line in file_text(lines) if line.strip()]
    errors = [line for line in text if "Error" in line or "error" in line]
    return [errors[0] if errors else f"netplan generate exited with {rc}"]


def read_networkd(parts, mechanism):
    links = parse_links(parts)
    problems = generate_problems(parts) if mechanism == "netplan" else []
    units = networkd_units(parse_files(parts))
    interfaces = []
    for link in links.values():
        for unit in units:
            result = networkd_matches(unit, link)
            if result is None:
                raise Unsure(f"{unit.path}: its [Match] cannot be checked for {link.name}")
            if result:
                break
        else:
            continue  # no file: networkd leaves the interface alone
        if not unit.managed:
            continue
        if unit.unsure:
            raise Unsure(unit.unsure)
        problems.extend(problem for problem in unit.problems if problem not in problems)
        method = "dhcp" if unit.dhcp4 else "static" if unit.addresses else "manual"
        interfaces.append(interface_entry(link.name, method, unit.addresses, unit.gateways, unit.routes))
    return saved_of(mechanism, interfaces, problems)


# --- linux.config/1: NetworkManager -------------------------------------------------------------------------------

# Keyfiles in the order NetworkManager prefers a profile of one UUID; /run holds only what netplan would generate.
NM_DIRS = ("/etc/NetworkManager/system-connections", "/run/NetworkManager/system-connections",
           "/usr/lib/NetworkManager/system-connections")
NM_SKIPPED = (".bak", ".orig", ".rej", ".swp", ".tmp", "~", ".nmmeta")  # names the keyfile plugin does not read
NM_TYPES = {"802-3-ethernet": "ethernet", "802-11-wireless": "wifi"}
# Profiles of these types put no address on an interface the lab checks; any other type with autoconnect (a bridge,
# a bond, a VLAN, …) does, on an interface it creates, and is not read yet.
NM_PASSIVE = {"ethernet", "wifi", "loopback", "gsm", "cdma", "bluetooth", "olpc-mesh", "wimax", "vpn", "wpan",
              "6lowpan", "generic"}
NM_METHODS = {"auto": "dhcp", "manual": "static"}


class KeyfileError(ValueError):
    pass


@dataclass
class Profile:
    path: str
    uuid: str
    type: str
    iface: str = ""
    mac: str | None = None
    denied: tuple = ()
    match_names: tuple = ()
    match_other: bool = False  # match.driver, .path, .kernel-command-line: not checked by the slot
    autoconnect: bool = True
    priority: int = 0
    timestamp: int = 0
    private: bool = False  # connection.permissions: a user's profile, not brought up at boot
    controller: str = ""
    multi: bool = False
    method: str = "dhcp"
    addresses: tuple = ()
    gateways: tuple = ()
    routes: tuple = ()

    def entry(self, name):
        return interface_entry(name, self.method, self.addresses, list(self.gateways), list(self.routes))


def keyfile(lines):
    """{group: {key: value}} of a GLib key file; the last of a repeated key wins, as NetworkManager reads it."""
    groups, current = {}, None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = groups.setdefault(line[1:-1].strip(), {})
            continue
        key, sep, value = line.partition("=")
        if not sep or current is None:
            raise KeyfileError(line)
        current[key.strip()] = value.strip()
    return groups


def kf_list(value):
    """A list value of a key file: `;`-separated, `\\;` is a literal `;`."""
    items = re.split(r"(?<!\\);", value or "")
    return [item.replace("\\;", ";").strip() for item in items if item.strip()]


def kf_bool(value, default):
    return {"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False}.get(
        (value or "").strip().lower(), default)


def kf_int(value, default=0):
    try:
        return int((value or "").strip())
    except ValueError:
        return default


def nm_mac(value):
    """A MAC of a keyfile: `52:54:00:…`, or the old list of bytes `82;84;0;…;`."""
    value = (value or "").strip()
    if ";" in value:
        octets = kf_list(value)
        if len(octets) == 6 and all(o.isdigit() and int(o) < 256 for o in octets):
            return ":".join(f"{int(o):02x}" for o in octets)
        return None
    return mac_of(value)


def nm_address(value, route=False):
    """(network or interface, next hop or gateway or None) of `addressN` / `routeN`. No prefix length: /24, as
    NetworkManager assumes for both. ValueError when NetworkManager could not read it."""
    value = value.strip().rstrip(";")
    if ";" in value and "," not in value:  # the old form: address;prefix;gateway
        words = value.split(";")
        value = f"{words[0]}/{words[1]}" + (f",{words[2]}" if len(words) > 2 else "") if len(words) > 1 else words[0]
    words = [word.strip() for word in value.split(",")]
    text = words[0] if "/" in words[0] else words[0] + "/24"
    target = ipaddress.IPv4Network(text, strict=False) if route else ipaddress.IPv4Interface(text)
    hop = None
    if len(words) > 1 and words[1]:
        hop = str(ipaddress.IPv4Address(words[1]))
        hop = None if hop == "0.0.0.0" else hop
    return target, hop


def numbered(group, names):
    """Keys `name`, `name1`, `name2`, … of a group in their order (the plain one first)."""
    found = []
    for key in group:
        for name in names:
            if key == name or (key.startswith(name) and key[len(name) :].isdigit()):
                found.append((kf_int(key[len(name) :], -1), key))
    return [key for _n, key in sorted(found)]


def nm_profile(path, lines):
    """(Profile, problem): no Profile when NetworkManager would not load the file."""
    try:
        groups = keyfile(lines)
    except KeyfileError:
        return None, f"{path}: NetworkManager cannot read the file"
    con = groups.get("connection", {})
    kind = con.get("type") or ("ethernet" if "ethernet" in groups or "802-3-ethernet" in groups else "")
    kind = NM_TYPES.get(kind, kind)
    if not kind:
        return None, f"{path}: NetworkManager does not load the profile: it has no type"
    eth = {**groups.get("802-3-ethernet", {}), **groups.get("ethernet", {})}
    match = groups.get("match", {})
    profile = Profile(path=path, uuid=con.get("uuid") or path, type=kind)
    profile.iface = con.get("interface-name", "")
    profile.mac = nm_mac(eth.get("mac-address"))
    profile.denied = tuple(filter(None, (nm_mac(v) for key in ("mac-address-blacklist", "mac-address-denylist")
                                         for v in kf_list(eth.get(key)))))
    profile.match_names = tuple(kf_list(match.get("interface-name")))
    profile.match_other = any(kf_list(match.get(key)) for key in ("driver", "path", "kernel-command-line"))
    profile.autoconnect = kf_bool(con.get("autoconnect"), True)
    profile.priority = kf_int(con.get("autoconnect-priority"))
    profile.timestamp = kf_int(con.get("timestamp"))
    profile.private = bool(kf_list(con.get("permissions")))
    profile.controller = con.get("controller") or con.get("master") or ""
    profile.multi = con.get("multi-connect", "").strip() in ("3", "multiple")
    ipv4 = groups.get("ipv4", {})
    method = ipv4.get("method", "auto")
    addresses, gateways, routes = [], [], []
    try:
        for key in numbered(ipv4, ("address", "addresses")):
            address, gateway = nm_address(ipv4[key])
            addresses.append(address)
            if gateway and not gateways:
                gateways.append(gateway)
        if ipv4.get("gateway"):
            gateways = [str(ipaddress.IPv4Address(ipv4["gateway"]))]
    except (ValueError, IndexError):
        return None, f"{path}: NetworkManager does not load the profile: a bad IPv4 address or gateway"
    if method == "manual" and not addresses:
        return None, f"{path}: NetworkManager does not load the profile: method=manual without an address"
    if method in ("disabled", "link-local") and addresses:
        return None, f"{path}: NetworkManager does not load the profile: addresses with method={method}"
    if kf_bool(ipv4.get("never-default"), False):
        gateways = []
    for key in numbered(ipv4, ("route", "routes")):
        options = dict(o.split("=", 1) for o in ipv4.get(f"{key}_options", "").split(",") if "=" in o)
        if options.get("table", "0").strip() not in ("0", "254") or options.get("type", "unicast") != "unicast":
            continue
        try:
            network, hop = nm_address(ipv4[key], route=True)
        except (ValueError, IndexError):
            continue  # NetworkManager drops a route it cannot read and keeps the profile
        if hop is None:
            continue
        if network.prefixlen == 0:
            gateways.append(hop)
        elif {"dst": str(network), "via": hop} not in routes:
            routes.append({"dst": str(network), "via": hop})
    profile.method = NM_METHODS.get(method, method)
    profile.addresses, profile.gateways, profile.routes = tuple(addresses), tuple(gateways), tuple(routes)
    return profile, None


def nm_wildcards(patterns, name):
    """match.interface-name: every mandatory element (`&`, `!`) must hold, one optional (plain, `|`) if any exist."""
    optional, matched = False, False
    for pattern in patterns:
        mandatory = pattern[:1] == "&"
        if pattern[:1] in "&|" and pattern:
            pattern = pattern[1:]
        inverted = pattern[:1] == "!"
        if inverted:
            pattern, mandatory = pattern[1:], True
        if pattern[:1] == "\\":
            pattern = pattern[1:]
        hit = fnmatch.fnmatchcase(name, pattern) != inverted
        if mandatory and not hit:
            return False
        if not mandatory:
            optional, matched = True, matched or hit
    return matched or not optional


def nm_compatible(profile, link):
    """Whether NetworkManager may bring the profile up on this ethernet device; None when the slot cannot tell."""
    if profile.type != "ethernet" or (profile.iface and profile.iface != link.name):
        return False
    results = []
    if profile.mac:
        results.append(profile.mac == link.mac if link.permanent else None)
    if profile.denied and link.mac in profile.denied:
        results.append(False if link.permanent else None)
    if profile.match_names:
        results.append(nm_wildcards(profile.match_names, link.name))
    if profile.match_other:
        results.append(None)
    return conjunction(results)


def nm_devices(parts):
    """name -> (type, state) from `nmcli -t -e yes -f DEVICE,TYPE,STATE device`."""
    found = parts.get("devices") or []
    if len(found) != 1 or not found[0][1] or not found[0][1][-1].startswith(MARK):
        raise ParseError("nmcli result missing")
    lines = found[0][1]
    if rc_of(lines[-1].split()[-1]) != 0:
        raise Unsure("nmcli does not answer")
    devices = {}
    for line in lines[:-1]:
        if not line.strip():
            continue
        fields = [f.replace("\\:", ":").replace("\\\\", "\\") for f in re.split(r"(?<!\\):", line)]
        if len(fields) != 3:
            raise ParseError("bad nmcli line")
        devices[fields[0]] = (fields[1], fields[2])
    return devices


def nm_config(parts):
    found = parts.get("config") or []
    if len(found) != 1:
        raise ParseError("NetworkManager configuration missing")
    if rc_of(found[0][0]) != 0:
        raise Unsure("NetworkManager --print-config failed")
    try:
        return keyfile(file_text(found[0][1]))
    except KeyfileError:
        raise ParseError("bad NetworkManager configuration") from None


def no_auto_default(specs, link):
    """Whether NetworkManager's no-auto-default (device specs) covers the device."""
    if "*" in specs:
        return True
    for spec in specs:
        if spec.startswith("mac:") or mac_of(spec):
            if mac_of(spec.removeprefix("mac:")) == link.mac:
                return True
        elif spec.startswith("interface-name:"):
            pattern = spec[len("interface-name:") :]
            if pattern[:1] == "=":
                hit = pattern[1:] == link.name
            else:
                hit = fnmatch.fnmatchcase(link.name, pattern.removeprefix("~"))
            if hit:
                return True
        else:
            raise Unsure(f"no-auto-default {spec[:40]} is not read")
    return False


def read_networkmanager(parts):
    links = parse_links(parts)
    files = parse_files(parts)
    devices = nm_devices(parts)
    config = nm_config(parts)
    main = config.get("main", {})
    plugins = [p.strip() for p in re.split(r"[,;]", main.get("plugins", ""))]
    if "ifcfg-rh" in plugins and any(arg != "ifcfg-lo" for arg, _lines in parts.get("ifcfg") or []):
        raise Unsure("profiles of the ifcfg-rh plugin are not read")
    problems = generate_problems(parts) if "generate" in parts else []
    try:  # when each profile was last up: of two with one priority, NetworkManager takes the latest
        stamps = keyfile(files.get("/var/lib/NetworkManager/timestamps", ("0", "0", []))[2]).get("timestamps", {})
    except KeyfileError:
        stamps = {}
    specs = [s.strip() for s in re.split(r"[,;]", main.get("no-auto-default", "")) if s.strip()]
    state = files.get("/var/lib/NetworkManager/no-auto-default.state")
    if state:
        specs += [s.strip() for line in state[2] for s in re.split(r"[,;]", line) if s.strip()]

    profiles, seen = [], set()
    def order(path):
        directory, _, base = path.rpartition("/")
        return NM_DIRS.index(directory), base
    for path in sorted((p for p in files if p.rpartition("/")[0] in NM_DIRS), key=order):
        directory, _, base = path.rpartition("/")
        if directory != NM_DIRS[0] and not base.endswith(".nmconnection"):
            continue
        if base.startswith(".") or base.endswith(NM_SKIPPED) or ".dpkg-" in base or ".rpm" in base:
            continue
        uid, mode, lines = files[path]
        if not lines:
            continue
        if uid != "0" or not re.fullmatch(r"[0-7]{1,4}", mode) or int(mode, 8) & 0o077:
            problems.append(f"{path}: NetworkManager ignores the file: it must belong to root and have mode 600")
            continue
        profile, problem = nm_profile(path, lines)
        if problem:
            problems.append(problem)
        if profile is None or profile.uuid in seen:
            continue
        seen.add(profile.uuid)
        profile.timestamp = kf_int(stamps.get(profile.uuid), profile.timestamp)
        profiles.append(profile)
    for profile in profiles:
        if profile.type not in NM_PASSIVE and profile.autoconnect and not profile.private:
            raise Unsure(f"{profile.path}: profiles of type {profile.type[:20]} are not read")

    interfaces, chosen = [], {}
    for name, (kind, state) in devices.items():
        link = links.get(name)
        if kind != "ethernet" or link is None or not link.physical:
            continue
        if state == "unmanaged":
            raise Unsure(f"NetworkManager does not manage {name}: what configures it is not read")
        compatible = []
        for profile in profiles:
            result = nm_compatible(profile, link)
            if result is None:
                raise Unsure(f"{profile.path}: whether it fits {name} cannot be checked")
            if result:
                compatible.append(profile)
        ready = [p for p in compatible if p.autoconnect and not p.private]
        if not ready:
            if not compatible and not no_auto_default(specs, link):
                interfaces.append({"name": name, "method": "dhcp", "ipv4": []})  # "Wired connection N"
            continue
        ready.sort(key=lambda p: (-p.priority, -p.timestamp))
        best = ready[0]
        rivals = [p for p in ready[1:] if (p.priority, p.timestamp) == (best.priority, best.timestamp)]
        if any(p.entry(name) != best.entry(name) or p.controller != best.controller for p in rivals):
            raise Unsure(f"profiles for {name} are equally likely: {best.path}, {rivals[0].path}")
        if best.controller:
            raise Unsure(f"{best.path}: a port of {best.controller[:40]} is not read")
        chosen.setdefault(best.uuid, []).append(name)
        if len(chosen[best.uuid]) > 1 and not (best.iface or best.mac or best.multi):
            raise Unsure(f"{best.path} fits several devices and comes up on one of them")
        interfaces.append(best.entry(name))
    return saved_of("networkmanager", interfaces, problems)
