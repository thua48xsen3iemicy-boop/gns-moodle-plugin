# Switch API 0.1.1

Python standard-library daemon, local Unix socket and a CLI for QGA. Runtime
dependencies inside Debian 13: python3, iproute2, tcpdump, systemd and
qemu-guest-agent. No pip packages, HTTP service or guest network address required.

See [the wire contract](docs/CONTRACTS.md) and
[future integration boundaries](docs/INTEGRATION.md).

## Install into the dedicated guest

Run from this image recipe directory, while SSH and package access still work:

```sh
apt-get update
apt-get install --no-install-recommends python3 iproute2 tcpdump qemu-guest-agent
python3 tools/install_guest.py prepare
```

Preparation installs the API and stages all three bridge profile files under
/opt/switch-api/network-profile. It does not run systemctl, activate networking,
enable the new service or stop SSH. The script resolves its source directory
relative to itself, so it does not depend on the current working directory.

Verify QGA command execution from the platform and console login. Then, as the
last deployment step inside the dedicated guest:

```sh
python3 tools/install_guest.py activate
reboot
```

Activation backs up existing .network/.netdev files under /var/backups/switch-network,
installs and verifies all three staged files in /etc/systemd/network, disables
cloud-init's future execution if installed, enables networkd/API/serial getty and
masks the previous network managers. SSH service/socket are masked last, without
--now. The sshd alias is deliberately not masked. The script never reboots or
restarts networking; the administrator runs reboot after it succeeds.

At final boot the bridge starts VLAN-filtered with no default membership. The
daemon assigns access VLAN 1 on an unconfigured image. Ports remain isolated until
the daemon reconciles them. A service preflight now rejects missing or malformed
bridge profiles instead of leaving the API running against a nonexistent bridge.
Do not restart networkd or start the API over the temporary SSH network.

The production daemon is root with NET_ADMIN/NET_RAW and SETUID/SETGID capabilities
(tcpdump drops to nobody after opening its capture socket), a private
runtime directory, read-only system filesystem except its state/runtime paths,
32-task and 64-MiB memory limits. Logging remains disabled by the appliance profile.
These budgets need verification with the final guest image under traffic load.
The startup command does not wait for all NICs: maintenance reconciles discovery.

## QGA-callable examples

```sh
switchctl status
switchctl ports
switchctl fdb --params '{"limit":100}'
switchctl vlans
switchctl vlan.apply --params '{"ports":{"eth0":{"mode":"access","vlan":10},"eth1":{"mode":"trunk","allowed":[10,20]}}}'
switchctl capture.start --params '{"port":"eth0","template":"arp","seconds":10,"packets":8}'
switchctl capture.read --params '{"id":"ID_FROM_START"}'
switchctl capture.stop --params '{"id":"ID_FROM_START"}'
```

Use real port names from ports; VLAN writes must include all current ports.
Poll vlans for applied=true and last_error=null. The capture result is parsed
headers, not a PCAP download. The daemon never captures until asked explicitly.

## Development validation

From the repository root:

```sh
PYTHONPATH=images/linux-switch python3 -B -m pytest -q -p no:cacheprovider images/linux-switch/tests
python3 -m compileall -q images/linux-switch/switch_api
systemd-analyze verify --man=no images/linux-switch/system/switch-api.service
```

Tests use fake Linux commands/PCAP streams and real temporary Unix sockets; no
privileged networking, VM or GNS3 environment is started.

## Guest acceptance checklist

1. Through the existing QGA broker, run status/ports/fdb/vlans after final reboot.
   Confirm socket permissions, no IP listeners and all NICs in access VLAN 1.
2. Apply access VLANs 10 and 20 and a trunk. Verify equal-VLAN forwarding,
   cross-VLAN isolation, tag insertion/removal and untagged trunk behavior.
3. Reboot: saved VLAN policy returns. Add a NIC: with a saved policy it has no
   VLAN membership; without one it joins VLAN 1. Removed/replaced cards do not
   inherit another card's saved policy. Ports remain administratively UP.
4. Trigger ARP with two guests; capture only when requested. Check request/reply
   fields, port attribution, timeout without traffic, explicit stop and limits.
5. Send known tagged frames, including double tagging; compare observations with
   the selected QEMU NIC's offload behavior. Do not equate missing tags with VLAN 0.
6. Exercise command failures, busy channels, policy application errors and daemon
   restart. Confirm captures stop and saved policy survives. Check peak RAM and
   CPU at two simultaneous captures under representative load.

These checks require the owner's GNS3 test environment and are not replaced by
the unit tests. The owner verified bridge creation and status/ports/vlans in a
Debian guest after installing the three network files. End-to-end VLAN forwarding,
capture behavior and the new installer still require guest acceptance testing.
