# Lab uplink guest image 0.1.0

The "Internet" node of a lab: its first adapter goes to the outside through a PPPoE client, the other
adapters are LAN ports that give the lab's routers an address, a default gateway and DNS, and NAT their
traffic. Base: Debian 13 (trixie) minimal, amd64, `pppd` with `rp-pppoe.so`, `dnsmasq`, `nftables`,
systemd-networkd and qemu-guest-agent. These files configure the guest; they do not build a QCOW2 image.
The PPPoE server (BRAS) is not part of this component. Task: `docs/lab-uplink/TASK.md`.

```
lab: R1 ─┐
     R2 ─┼─ adapters 1..N (LAN, 198.18.N.1/24, DHCP+DNS, NAT) ── this node ── adapter 0 ── PPPoE ── slot eth1 ── BRAS
     R3 ─┘
```

## Ports

Roles follow the adapter numbers, which follow the MACs (the slot gives the adapters ascending MACs; the
manager's guest setup orders interfaces the same way):

| Adapter | Role |
|---|---|
| 0 | outer port (`uplink_port: 0` in the lab); no IP address; PPPoE client `ppp0` on top of it |
| N = 1…15 | LAN port: `198.18.N.1/24`, DHCP range `.100–.250`, router and DNS = the port's own address |

The number of LAN ports is the number of Ethernet adapters at boot minus one: nothing is edited in the image.
`198.18.0.0/15` is the benchmarking range (RFC 2544); it does not overlap the private pools of the lab
constructor. One adapter only: no LAN, no `dnsmasq`.

## What runs at boot

`lab-uplink-setup.service` (before systemd-networkd) finds the real Ethernet cards (`uplink_net/setup.py`) and writes,
from `uplink_net/config.py`: networkd files in `/run/systemd/network`, and `/run/lab-uplink/{pppd.options,
dnsmasq.conf, nftables.nft}`. Then `lab-uplink-nft`, `lab-uplink-pppoe` and `lab-uplink-dnsmasq` start.

* **pppd** (`persist`, `maxfail 0`, `holdoff 5`, `defaultroute`, `usepeerdns`, `ifname ppp0`, MTU/MRU 1492, LCP echo).
  User and password come from `/etc/lab-uplink/pppoe.options` (root, 0600), written by the image build. If the
  server is not there yet, pppd keeps trying.
* **dnsmasq**: DHCP on the LAN ports only; DNS forwards to the servers PPPoE hands out (`/etc/ppp/resolv.conf`).
* **nftables**: masquerade into `ppp0`; forwarding only LAN → `ppp0` and the replies; LAN ↔ LAN is dropped; the node
  itself answers the LAN with DHCP and DNS only (everything else to it is dropped); TCP MSS is clamped to the path MTU
  (`rt mtu`: PPPoE 1492 against the routers' 1500).
* sysctl: `ip_forward=1`, IPv6 off.

## Build

Inside a new Debian 13 guest during image preparation, never on the slot host or an existing shared image:

```sh
apt-get update && apt-get full-upgrade -y
apt-get install --no-install-recommends python3 ppp dnsmasq-base nftables iproute2 qemu-guest-agent systemd systemd-sysv
python3 tools/install_guest.py prepare --pppoe-user USER --pppoe-password PASSWORD
```

`prepare` copies the files and writes the credentials; it enables nothing and does not touch live networking.
After checking QGA (`guest-ping` from the slot) and a `ttyS0` login (console authentication is configured
separately; do not publish credentials with the recipe):

```sh
python3 tools/install_guest.py activate
```

`activate` enables `systemd-networkd`, `qemu-guest-agent`, `serial-getty@ttyS0` and the four units and masks the other
network managers. Remove SSH and temporary network access last, as in `images/linux-switch/`.

The credentials are the same for every lab at this stage: isolation comes from the bridge filter
(`install/lab-uplink-filter`) and the BRAS, not from the password. Per-slot credentials are a later step. The
repository holds only `ppp/pppoe.options.example` with placeholders.

## Use in a lab

Put the image into the image catalog (`manager/examples/images.json`, id `lab-uplink`, `console_type: none`: nobody gets
a console), add a node with it to the lab and set `uplink_port: 0`. Do not link that port: the slot connects it to its
`eth1`. Link the routers to adapters 1…N. A router on an adapter that sets `ip address dhcp` gets
`198.18.N.x/24`, the default gateway `198.18.N.1` and DNS `198.18.N.1`. Different LAN ports are different networks
and do not reach each other.

## Tests

```sh
PYTHONPATH=images/lab-uplink python3 -m pytest -q -p no:cacheprovider images/lab-uplink/tests
```

They check the generated `dnsmasq`, `nftables` (also with `nft -c` when the environment allows it), `pppd` and networkd
files for 1 and 4 LAN ports, the discovery of cards in a fake `/sys/class/net`, and the installer in a temporary root.
There is **no end-to-end test**: the development environment has no `ip`, `pppd` or `pppoe-server`; check on a stand
(`docs/lab-uplink/TASK.md`, the owner's list).
