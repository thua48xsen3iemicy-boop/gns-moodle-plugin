"""Guest network setup for lab segments: one /bin/sh script per QEMU node (lab constructor README §4.1).

The script runs once as a `ProvisionCommand` through QGA, after the commands of the image. It
  1. checks that the network mechanism declared by the image (`net_config`) really runs in the guest,
  2. finds the interfaces by MAC (the guest's ethernet interfaces sorted by MAC; adapter N is the N-th,
     because GNS3 gives adapter N the base MAC + N), never by name,
  3. writes the files of the mechanism, removes what would override them, and applies them.

Nothing but validated addresses (ipaddress) and adapter numbers is put into the text: the guest's
MAC and interface names are read in the guest and checked there against a pattern. Set ROOT in the
environment to run the script against a fake tree (tests); in the guest it is empty.
"""

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Interface

MECHANISMS = ("ifupdown", "networkmanager", "netplan", "networkd")
MAX_SCRIPT = 8192  # ProvisionCommand.command of the slot
TIMEOUT_SECONDS = 120


class ScriptTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class Iface:
    adapter: int
    address: IPv4Interface
    gateway: IPv4Address | None = None  # Default route through this address, on this interface


PRELUDE = r'''set -eu
R="${ROOT-}"
die() { echo "$*" >&2; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }
active() { systemctl is-active --quiet "$1" 2>/dev/null; }
found=none
if has netplan; then found=netplan
elif active NetworkManager; then found=networkmanager
elif active systemd-networkd; then found=networkd
elif has ifup; then found=ifupdown; fi
'''

# The declared mechanism must be the one that works in the guest (lab constructor README §4.1).
CHECKS = {
    "ifupdown": "has ifup && ! active NetworkManager && ! active systemd-networkd",
    "networkmanager": "active NetworkManager",
    "netplan": "has netplan",
    "networkd": "active systemd-networkd",
}

FIND = r'''list=$(for p in "$R"/sys/class/net/*; do
  n=${p##*/}
  if [ "$n" = lo ]; then continue; fi
  case $(readlink -f "$p") in */virtual/*) continue;; esac
  t=1
  if [ -r "$p/type" ]; then read t < "$p/type"; fi
  if [ "$t" != 1 ]; then continue; fi
  read a < "$p/address" || continue
  echo "$(echo "$a" | tr A-F a-f) $n"
done | sort)
pick() {
  line=$(printf '%s\n' "$list" | sed -n "$(($1 + 1))p")
  if [ -z "$line" ]; then die "adapter $1 not found in the guest"; fi
  MAC=${line% *}
  IF=${line#* }
  case $MAC in *[!0-9a-f:]*) die "bad MAC of adapter $1";; esac
  if [ ${#MAC} -ne 17 ]; then die "bad MAC of adapter $1"; fi
  case $IF in *[!A-Za-z0-9_.-]*|'') die "bad interface name of adapter $1";; esac
  MACH=$(printf %s "$MAC" | tr -d :)
}
'''

# cfg ADAPTER ADDRESS/PREFIX NETMASK [GATEWAY] writes the config; finish applies it.
BODIES = {
    "ifupdown": r'''D="$R/etc/network"
F="$D/interfaces.d/gns3lab"
mkdir -p "$D/interfaces.d"
: > "$F"
NAMES=""
cfg() {
  pick "$1"
  { echo "auto $IF"; echo "iface $IF inet static"; echo "    address ${2%/*}"; echo "    netmask $3"
    if [ -n "${4-}" ]; then echo "    gateway $4"; fi; } >> "$F"
  NAMES="$NAMES $IF"
}
finish() {
  if ! grep -Eqs '^[[:space:]]*source(-directory)?[[:space:]]+/etc/network/interfaces\.d' "$D/interfaces"; then
    echo 'source /etc/network/interfaces.d/*' >> "$D/interfaces"
  fi
  for f in "$D/interfaces" "$D"/interfaces.d/*; do
    if [ ! -f "$f" ] || [ "$f" = "$F" ]; then continue; fi
    awk -v "names=$NAMES" 'BEGIN { n = split(names, a, " "); for (i = 1; i <= n; i++) mine[a[i]] = 1 }
/^[[:space:]]*(auto|allow-[a-z-]+|iface|mapping|source|source-directory|no-auto-down|no-scripts)[[:space:]]/ {
  skip = 0
  if ($1 == "iface" && ($2 in mine)) skip = 1
  else if ($1 == "auto" || $1 ~ /^allow-/) {
    out = $1; hit = 0; kept = 0
    for (i = 2; i <= NF; i++) { if ($i in mine) hit = 1; else { out = out " " $i; kept = 1 } }
    if (hit) { if (kept) print out; else print "# gns3lab: " $0; next }
  }
}
skip { print "# gns3lab: " $0; next }
{ print }' "$f" > "$f.gns3lab-tmp"
    cat "$f.gns3lab-tmp" > "$f"
    rm -f "$f.gns3lab-tmp"
  done
  for i in $NAMES; do
    ifdown --force "$i" || true
    ifup "$i"
  done
}
''',
    "networkmanager": r'''D="$R/etc/NetworkManager"
umask 077
mkdir -p "$D/conf.d" "$D/system-connections"
printf '[main]\nno-auto-default=*\n' > "$D/conf.d/90-gns3lab.conf"
IDS=""
cfg() {
  pick "$1"
  id=gns3lab-$MACH
  f="$D/system-connections/$id.nmconnection"
  { printf '[connection]\nid=%s\ntype=ethernet\nuuid=00000000-0000-4000-8000-%s\nautoconnect-priority=100\n\n' "$id" "$MACH"
    printf '[ethernet]\nmac-address=%s\n\n' "$MAC"
    printf '[ipv4]\nmethod=manual\naddress1=%s%s\n' "$2" "${4:+,$4}"; } > "$f"
  chmod 600 "$f"
  IDS="$IDS $id"
}
finish() {
  nmcli connection reload
  for id in $IDS; do nmcli connection up "$id"; done
}
''',
    "netplan": r'''D="$R/etc/netplan"
F="$D/90-gns3lab.yaml"
umask 077
mkdir -p "$D/gns3lab-disabled"
for f in "$D"/*.yaml; do
  if [ -f "$f" ] && [ "$f" != "$F" ]; then mv "$f" "$D/gns3lab-disabled/"; fi
done
printf 'network:\n  version: 2\n  ethernets:\n' > "$F"
cfg() {
  pick "$1"
  { printf '    gns3lab%s:\n      match:\n        macaddress: "%s"\n      dhcp4: false\n      addresses: [%s]\n' "$1" "$MAC" "$2"
    if [ -n "${4-}" ]; then printf '      routes:\n        - to: 0.0.0.0/0\n          via: %s\n' "$4"; fi; } >> "$F"
}
finish() {
  chmod 600 "$F"
  if [ -d "$R/etc/cloud/cloud.cfg.d" ]; then
    printf 'network: {config: disabled}\n' > "$R/etc/cloud/cloud.cfg.d/99-gns3lab-network.cfg"
  fi
  netplan apply
}
''',
    "networkd": r'''D="$R/etc/systemd/network"
mkdir -p "$D"
NAMES=""
cfg() {
  pick "$1"
  { printf '[Match]\nMACAddress=%s\n\n[Network]\nDHCP=no\nAddress=%s\n' "$MAC" "$2"
    if [ -n "${4-}" ]; then printf 'Gateway=%s\n' "$4"; fi; } > "$D/10-gns3lab-$MACH.network"
  NAMES="$NAMES $IF"
}
finish() {
  networkctl reload
  for i in $NAMES; do networkctl reconfigure "$i"; done
}
''',
}


def script(mechanism: str, ifaces: list[Iface]) -> str:
    """The provisioning script for `ifaces` of one node (non-empty, by ascending adapter)."""
    if mechanism not in MECHANISMS:
        raise ValueError(f"unknown net_config {mechanism!r}")
    parts = [PRELUDE,
             f'if ! {{ {CHECKS[mechanism]}; }}; then die "net_config mismatch: declared {mechanism}, found $found"; fi\n',
             FIND, BODIES[mechanism]]
    for iface in sorted(ifaces, key=lambda i: i.adapter):
        args = [str(int(iface.adapter)), str(iface.address), str(iface.address.network.netmask)]
        if iface.gateway is not None:
            args.append(str(iface.gateway))
        parts.append("cfg " + " ".join(args) + "\n")
    parts.append("finish\n")
    text = "".join(parts)
    if len(text) > MAX_SCRIPT:
        raise ScriptTooLarge(f"network script is {len(text)} characters, the slot takes {MAX_SCRIPT}")
    return text
