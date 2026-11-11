"""Random guest interface names (CONTRACTS 2.6, NodeSpec provisioning in 4.2).

Students get used to `ens3`. A node with `random_ifnames` gets other names that real machines have: onboard ports
(`eno1`), PCI slots (`ens18`, `ens192`), PCI paths (`enp3s0`, `enp0s8`), ports of multi-port cards (`enp4s0f0`,
`enp65s0f1np1`, `ens2f3`), USB adapters named by MAC (`enx0c1a2b3c4d00`) and biosdevname's `em1` / `p2p1`.

names() picks them from a seed (project id and node name): the same student project always gets the same names, and
another project of the same lab gets others. script() is the `/bin/sh` provisioning command that gives the guest
these names. It runs once through QGA when the project is created, before the network setup of the segments:
  1. checks the mechanism declared by the image, like netconfig, and that the guest has `ip` and udev,
  2. finds the interfaces by MAC (adapter N is the N-th by ascending MAC, netconfig.FIND),
  3. writes /etc/systemd/network/10-gns3lab-<mac>.link (Name= by MACAddress=), so udev names them so on every boot,
  4. renames them now (through temporary names, so two adapters may swap names), carries the old names over in the
     network files of the guest, switches cloud-init's network part off and lets the mechanism see the new names.

Names come from this module only (checked against NAME_RE); `@mac` is turned into `enx<mac>` in the guest.
"""

import hashlib
import random
import re

from . import netconfig

MAC_NAME = "@mac"  # enx + the guest's MAC, made in the guest
NAME_RE = re.compile(r"^[a-z][a-z0-9]{1,14}$")  # IFNAMSIZ - 1, and nothing the shell would read
TIMEOUT_SECONDS = 120

# Slot numbers that machines show (`ens<slot>`): QEMU, Proxmox (18, 19), VMware (33, 160, 192, 224, 256), servers.
SLOTS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 16, 17, 18, 19, 20, 33, 34, 35, 36, 160, 161, 192, 193, 224, 256)
# PCI buses of add-in and onboard devices (`enp<bus>s<slot>`).
BUSES = (1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 24, 59, 65, 94, 129, 130, 175, 216)
ROOT_SLOTS = (3, 8, 9, 10, 17, 18, 19, 20, 25, 31)  # Devices on bus 0 (`enp0s3` of VirtualBox, `enp0s25` of laptops)

# Kinds of devices; a node is built from them until it has a name for every adapter.
SYSTEMD = (("onboard", 2), ("slot", 4), ("path", 4), ("root", 2), ("multi", 3), ("multi_np", 2), ("multi_slot", 2),
           ("usb", 1))
BIOSDEVNAME = (("em", 1), ("pci", 3))
FAMILIES = ((SYSTEMD, 5), (BIOSDEVNAME, 1))


def pick(rng, weighted):
    items, weights = zip(*weighted)
    return rng.choices(items, weights=weights)[0]


def device(rng, kind):
    """(what it takes in the machine, names of its ports) for one device of `kind`."""
    if kind in ("onboard", "em"):
        prefix = "eno" if kind == "onboard" else "em"
        first = rng.choice((1, 1, 3, 5, 49)) if kind == "onboard" else 1  # eno49...: HPE servers
        return ("onboard",), [f"{prefix}{n}" for n in range(first, first + rng.choice((2, 2, 4)))]
    if kind == "usb":
        return ("usb",), [MAC_NAME]
    if kind in ("slot", "multi_slot"):
        slot = rng.choice(SLOTS if kind == "slot" else SLOTS[:10])
        ports = [f"ens{slot}"] if kind == "slot" else [f"ens{slot}f{p}" for p in range(rng.choice((2, 4)))]
        return ("slot", slot), ports
    if kind in ("path", "multi", "multi_np"):
        bus = rng.choice(BUSES)
        if kind == "path":
            return ("bus", bus), [f"enp{bus}s0"]
        if kind == "multi":
            return ("bus", bus), [f"enp{bus}s0f{p}" for p in range(rng.choice((2, 4)))]
        return ("bus", bus), [f"enp{bus}s0f{p}np{p}" for p in range(2)]  # Mellanox, Broadcom: the port in the name
    if kind == "root":
        slot = rng.choice(ROOT_SLOTS)
        return ("root", slot), [f"enp0s{slot}f6" if slot == 31 else f"enp0s{slot}"]  # enp0s31f6: Intel I219
    if kind == "pci":
        slot = rng.choice((1, 2, 3, 4, 5, 6))
        return ("pci", slot), [f"p{slot}p{p}" for p in range(1, rng.choice((1, 2, 4)) + 1)]
    raise ValueError(kind)


def names(seed: str, count: int) -> list[str]:
    """`count` distinct interface names for the adapters of one node, by ascending adapter."""
    if not 1 <= count <= 16:
        raise ValueError("count must be 1..16")
    rng = random.Random(hashlib.sha256(seed.encode()).digest())
    family = pick(rng, FAMILIES)
    devices, taken, draws = [], set(), 0
    while sum(map(len, devices)) < count:
        draws += 1
        if draws > 64:
            family = SYSTEMD  # Biosdevname runs out of slots on a node with many adapters
        where, ports = device(rng, pick(rng, family))
        if where in taken:
            continue  # One onboard controller and one USB adapter per machine, a bus or a slot holds one card
        taken.add(where)
        devices.append(ports)
    rng.shuffle(devices)  # Cards come in any order, the ports of a card stay in theirs
    result = [p for ports in devices for p in ports][:count]
    assert all(n == MAC_NAME or NAME_RE.fullmatch(n) for n in result) and len(set(result)) == count
    return result


def node_names(project_id: str, node: str, count: int) -> list[str]:
    return names(f"{project_id}\0{node}", count)


BODY = r'''has ip || die "random_ifnames: no ip command in the guest"
has udevadm || die "random_ifnames: no udev in the guest"
L="$R/etc/systemd/network"
mkdir -p "$L"
rm -f "$L"/10-gns3lab-*.link
PAIRS=""
rn() {
  pick "$1"
  N=$2
  if [ "$N" = @mac ]; then N=enx$MACH; fi
  printf '[Match]\nMACAddress=%s\n\n[Link]\nName=%s\n' "$MAC" "$N" > "$L/10-gns3lab-$MACH.link"
  PAIRS="$PAIRS $1:$IF:$N"
}
carry() {
  o=$(printf %s "$1" | sed 's/\./\\./g')
  for f in "$R"/etc/network/interfaces "$R"/etc/network/interfaces.d/* "$R"/etc/netplan/*.yaml \
      "$R"/etc/NetworkManager/system-connections/* "$R"/etc/systemd/network/*.network; do
    if [ -f "$f" ] && grep -q -- "$1" "$f"; then
      e="s/(^|[^A-Za-z0-9_.-])$o([^A-Za-z0-9_.-]|\$)/\1$2\2/g"
      sed -E -e "$e" -e "$e" "$f" > "$f.gns3lab-tmp" && cat "$f.gns3lab-tmp" > "$f" && rm -f "$f.gns3lab-tmp"
    fi
  done
}
renamed() {
  UP=""
  for p in $PAIRS; do
    a=${p%%:*}; r=${p#*:}; o=${r%%:*}; n=${r#*:}
    if [ "$o" = "$n" ]; then continue; fi
    if has ifquery && ifquery --state "$o" >/dev/null 2>&1; then UP="$UP $n"; fi
    if has ifdown; then ifdown --force "$o" >/dev/null 2>&1 || true; fi
    ip link set dev "$o" down
    ip link set dev "$o" name "gns3lab$a"
    carry "$o" "gns3lab$a"
  done
  for p in $PAIRS; do
    a=${p%%:*}; r=${p#*:}; o=${r%%:*}; n=${r#*:}
    if [ "$o" = "$n" ]; then continue; fi
    ip link set dev "gns3lab$a" name "$n"
    carry "gns3lab$a" "$n"
    ip link set dev "$n" up
  done
  cloud_off
  case $found in
    networkmanager) nmcli connection reload || true;;
    netplan) netplan apply || true;;
    networkd) networkctl reload || true
      for p in $PAIRS; do networkctl reconfigure "${p##*:}" || true; done;;
    ifupdown) for n in $UP; do
        if has timeout; then timeout 30 ifup "$n" >/dev/null 2>&1 || true; else ifup "$n" >/dev/null 2>&1 || true; fi
      done;;
  esac
}
'''


def script(mechanism: str, adapters: dict[int, str]) -> str:
    """The provisioning command that gives adapter N the name adapters[N] (adapters left out keep theirs)."""
    if mechanism not in netconfig.MECHANISMS:
        raise ValueError(f"random_ifnames needs a /bin/sh net_config, not {mechanism!r}")
    parts = [netconfig.PRELUDE,
             f'if ! {{ {netconfig.CHECKS[mechanism]}; }}; then '
             f'die "net_config mismatch: declared {mechanism}, found $found"; fi\n',
             netconfig.FIND, BODY]
    for adapter in sorted(adapters):
        name = adapters[adapter]
        if not (name == MAC_NAME or NAME_RE.fullmatch(name)) or not 0 <= int(adapter) < 16:
            raise ValueError(f"bad interface name {name!r} of adapter {adapter!r}")
        parts.append(f"rn {int(adapter)} {name}\n")
    parts.append("renamed\n")
    text = "".join(parts)
    if len(text) > netconfig.MAX_SCRIPT:
        raise netconfig.ScriptTooLarge(f"interface names script is {len(text)} characters")
    return text
