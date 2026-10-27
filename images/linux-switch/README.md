# Linux switch guest image 0.2.0

Base: Debian 13 (trixie) minimal, amd64, Linux bridge, systemd-networkd and
qemu-guest-agent from the maintained Debian repositories. These files configure
the guest; they do not build a QCOW2 image or change the slot service.
The guest-local service and CLI are described in [API.md](API.md), with a
versioned [contract](docs/CONTRACTS.md). Install that service as part of the image.

## Network ownership

Every QEMU PCI Ethernet card is a switch port. No Ethernet management port is
reserved. Management uses QGA over virtio-serial and the ttyS0 console, as already
configured by the slot. There are no IP addresses, DHCP clients or IPv6 router
advertisement clients on the bridge or its ports.

systemd-networkd creates br0 and attaches matching cards without maintaining a
saved interface count or list of interface names. The PCI path match covers the
slot's e1000 cards and QEMU virtio-net PCI cards. Loopback, bridge, VLAN and tunnel
devices are not switch ports. Non-PCI NIC models are outside this image profile.

At each boot, only the cards actually present are configured. Additional cards
are attached when discovered, including hot-added cards if the hypervisor supports
them. Removing a card removes its live bridge membership through the kernel; it
does not leave a generated per-port configuration behind. Renamed cards continue
to match by device type and path.

ActivationPolicy=always-up keeps the administrative state UP and reverses a local
`ip link set ... down` while networkd runs. It does not force carrier UP. Ports
are attached even without carrier. Topology indicators and remote interface state
remain the responsibility of the platform; no heartbeat or remote-state detection
is implemented in this guest.

The initial profile is one learning bridge, with a 300-second FDB ageing time.
VLAN filtering is enabled with no automatic PVID. switch-api assigns access VLAN 1
to all ports until a saved policy exists; otherwise it applies the saved access/
trunk policy. Ports have no VLAN connectivity until the service configures them.
Classic STP is enabled to block redundant paths. The same spanning tree applies
to all VLANs; this is not RSTP/PVST/MSTP. Ports can take about 30 seconds to enter
forwarding at startup, and blocking does not change the administrative UP flag.

## Prepare a dedicated guest

Run these commands inside a new Debian 13 guest during image preparation, never
on the slot host or against an existing shared image. Install packages while the
guest still has temporary network access:

```sh
apt-get update
apt-get full-upgrade -y
apt-get install --no-install-recommends python3 iproute2 tcpdump qemu-guest-agent systemd
```

Install the API and stage its network profile using the same installer:

```sh
python3 tools/install_guest.py prepare
```

This installs all API files and stages all three network files together without
enabling the API, invoking systemctl or changing the running network. Do not use
the earlier manual API-only installation sequence.

Reboot after the upgrade while the original network and SSH configuration are
still intact. Prepare the image over SSH; leave SSH and the current network
manager running until the final activation step below. Do not start or restart
systemd-networkd, reload its configuration, apply the appliance sysctls, or flush
addresses during SSH preparation. If networkd already owns the temporary SSH
network, stage the new network files outside /etc/systemd/network until the final
step. If SSH uses IPv6, applying the appliance sysctls would disconnect it.

At final activation the installer backs up existing .network/.netdev files in
/etc/systemd/network, installs the complete profile, archives ifupdown's interfaces
file and includes and replaces them with a loopback-only configuration without
includes. It masks ifupdown hotplug units, previous network managers and standalone
DHCP/IPv4LL services, and disables cloud-init if present. Masking networking.service
alone is insufficient because allow-hotplug can start ifupdown separately.
systemd-networkd must be the sole
owner of the switch interfaces. It uses the first matching .network file; check
/run/systemd/network and /usr/lib/systemd/network for any additional custom
profiles that could override this dedicated guest's configuration.

Enable guest management during preparation:

```sh
systemctl enable qemu-guest-agent.service
systemctl enable serial-getty@ttyS0.service
```

If the guest-agent unit is static, retain its packaged device activation instead
of adding a replacement unit. Ensure the QGA virtio-serial channel is present.
Verify QGA command execution from the slot and a ttyS0 console login before
final activation. Configure console authentication separately during image
preparation. Do not publish credentials with the image recipe.

## Minimal appliance profile

The guest has no network management endpoint: no SSH, HTTP API, DNS responder,
DHCP client/server, LLDP advertisements, mDNS or LLMNR. Install only the minimal
base system, Python, iproute2, tcpdump and QGA. Ethernet ports and br0 have no IPv4 or IPv6
addresses. Keep the standard IPv4 loopback address 127.0.0.1: it is internal to
the guest and is not reachable through the bridge. QGA uses virtio-serial and
does not need an IP stack on the switch ports.

Install the additional profile files inside the dedicated guest:

```sh
install -D -m 0644 journald/90-slot-switch.conf \
    /etc/systemd/journald.conf.d/90-slot-switch.conf
install -D -m 0644 sysctl/90-slot-switch.conf \
    /etc/sysctl.d/90-slot-switch.conf
install -D -m 0644 system/var-log.mount /etc/systemd/system/var-log.mount
install -D -m 0644 system/90-slot-switch-limits.conf \
    /etc/systemd/system.conf.d/90-slot-switch-limits.conf
systemctl daemon-reload
systemctl enable var-log.mount
```

The journal retains no messages and forwards none. Leave journald and its
sockets available so programs using logging APIs can still run normally.
Kernel ring-buffer messages can still exist in RAM; this profile prevents
journald from collecting them. Other programs writing under /var/log are
restricted to a shared 8 MiB tmpfs with 1024 inodes, which is cleared on reboot.
This is an upper limit, not a preallocated 8 MiB buffer. Remove any rsyslog or
syslog-ng package from the final image. QGA must not have custom log-file or
verbose/debug options; check its packaged defaults and /etc/qemu/qemu-ga.conf.
No durable diagnostic history will be available after reboot.

Disable installed background jobs and discovery services during preparation.
Keep SSH, its sockets, the current network manager and DNS service running until
the final step, so package installation and file transfer continue to work:

```sh
for unit in \
    avahi-daemon.service avahi-daemon.socket \
    systemd-networkd-wait-online.service \
    systemd-timesyncd.service chrony.service chronyd.service ntpsec.service \
    rsyslog.service syslog-ng.service syslog.socket \
    apt-daily.timer apt-daily.service apt-daily-upgrade.timer apt-daily-upgrade.service \
    unattended-upgrades.service man-db.timer man-db.service \
    logrotate.timer logrotate.service cron.service atd.service \
    systemd-pstore.service systemd-coredump.socket \
    getty@tty1.service
do
    systemctl mask --now "$unit"
done
```

Use a minimal installation without cloud-init; if a cloud image was used,
disable cloud-init and remove its generated network configuration at final
activation. Keep udev,
D-Bus, networkd, QGA, ttyS0 getty, tmpfiles and the kernel's network stack.
Remove swap entries from /etc/fstab and disable any installed zram/swap generator
in the image. Do not shrink RAM until packet forwarding and QGA have been
measured under load. IP forwarding is off; Ethernet bridging remains enabled.

### Final activation: disable SSH last

Finish all package installation and file transfer first. Verify QGA and the
serial console, then activate the complete staged installation:

```sh
python3 tools/install_guest.py activate
reboot
```

The final masks deliberately omit --now: the running SSH service and current
network remain available until reboot. Do not manually flush addresses over SSH.
SSH is masked only after the three installed profile files are validated. Do not
mask sshd.service: it can already be an alias symlink to ssh.service on Debian.
After reboot the old temporary addresses are absent, networkd applies the bridge
profile, and guest management uses QGA and the serial console.

Activation also installs the IPv6-disable sysctl profile, independently of the
optional logging settings. Recheck `ip -br address` after reboot: no IPv4 link-local,
DHCP or IPv6 addresses should remain on the switch ports or br0. For an earlier
prototype, rerun prepare then activate to replace the old ifupdown configuration;
a service restart alone does not remove old clients or their address state.

The reboot also applies the sysctl, mount and manager settings. IPv6 is disabled on
current and future interfaces. Check /etc/fstab has no other /var/log mount.
Remove old disk-backed logs in the offline image before final compression:
mounting tmpfs hides existing disk files but does not reclaim their space.
Also remove APT lists/cache and temporary build files after package installation.
For an ext4 image, trim free space on the build VM and compact the shut-down
QCOW2 into a new file. Never modify an existing shared image in place. These
steps reduce the final disk footprint; disabling logs alone does not.

Maintenance and security updates happen by preparing and testing a new image,
not by automatic upgrades during a lab. Re-enable journal storage temporarily
in a diagnostic clone if boot or networkd troubleshooting is needed.

## Read data through the existing QGA broker

Install the service using [API.md](API.md) before final activation. No HTTP
service is needed. Use switchctl through the broker for structured requests:

```sh
switchctl status
switchctl ports
switchctl fdb
switchctl vlans
```

For direct administrator diagnostics, Linux commands are also available:

```sh
ip -j -s link show
bridge -j link show master br0
bridge -j fdb show br br0
bridge -j vlan show
```

FDB includes local/permanent entries as well as dynamically learned MACs; a
consumer must distinguish them. Background reads use Lifecycle.qga with METRICS
priority and wait=False or a bounded max_age, never a direct socket connection.

## Guest acceptance checks

These checks require the actual image and GNS3/QEMU environment. They have not
been executed by adding these configuration files.

1. Boot with five Ethernet cards: br0 has exactly five member ports, all with
   the UP administrative flag. `networkctl status` shows the intended profile.
2. Repeat with two cards and then seven; membership matches the current cards
   without editing guest files. Repeat a boot to verify the same result.
3. On a port, run `ip link set dev <port> down`; networkd restores its UP flag.
   Check a port without carrier is still a bridge member.
4. If hotplug is available, add and remove a card and check current membership.
5. Send traffic between two attached guests: frames pass and dynamic source MACs
   appear on the correct ports in FDB. Confirm counters increase.
6. Confirm no IPv4/IPv6 addresses on br0 or its members, QGA commands execute
   through the broker, and the serial console works after reboot.
7. During initial validation, keep journal storage enabled and check for networkd
   configuration warnings. Then apply the minimal profile and repeat the checks.
   Record Debian, kernel, systemd, iproute2 and qemu-guest-agent package versions.
8. After final reboot, `findmnt /var/log` shows tmpfs limited to 8 MiB,
   `journalctl --disk-usage` reports no retained journals, `swapon --show` is empty,
   and `ss -lntup` shows no IP listeners. `ip -j address show` contains only IPv4
   loopback addresses, and still does so after changing the NIC count.
9. Verify QGA and FDB/counter reads still work with logging disabled. Verify ttyS0
   access and representative traffic under load before choosing the RAM budget.
