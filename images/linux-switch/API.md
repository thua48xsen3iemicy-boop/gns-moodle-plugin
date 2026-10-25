# Switch API 0.1.0

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
install -d -m 0755 /opt/switch-api/switch_api
install -m 0644 switch_api/*.py /opt/switch-api/switch_api/
install -m 0755 bin/switchctl /usr/local/sbin/switchctl
install -d -m 0700 /etc/switch-api
install -m 0644 system/switch-api.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable switch-api.service
```

Install the network profile from README.md as well: in this version the bridge
starts VLAN-filtered with no default membership. The daemon assigns access VLAN 1
on an unconfigured image. Ports remain isolated until the daemon has reconciled
them. Do not restart networkd or start the API over the temporary SSH network;
activate on the final reboot after verifying QGA and serial access.

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
the unit tests. Live guest verification has not yet been performed.
