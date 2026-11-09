# Integration

This image is an independent component; it changes no Slot, Manager or Plugin contract.

* Slot: the node is a QEMU node like any other (QGA is required: `qga` in the ticket). The outer adapter is marked by
  `NodeSpec.uplink_port` (manager/docs/CONTRACTS.md 4.2); the slot gives it `SLOT_UPLINK_MAC` and links it to its own
  cloud node on `eth1`. The image must not assume anything about the other end except PPPoE frames.
* Manager: image `lab-uplink` in `images.json` with `"console_type": "none"`; labs with the node run only on slots that
  offer the `uplink` feature (CONTRACTS 7).
* Install: `install/lab-uplink-filter` keeps slots from seeing each other in the shared VLAN.
* The slot reads only `linux.net` of the guest (read-only) and publishes only the LAN ports that have a cable in the
  lab, with their routes (slot 0.17.2, manager/docs/CONTRACTS.md 4.2). Those facts keep the carrier and the IOL
  keepalives of the cables to the lab's routers. The outer port, ppp0 and spare LAN ports are not shown.
  The catalog entry has no `role`: the slot never asks it the switch collectors. Do not build this image on top of
  `images/linux-switch` without removing switch-api (see that image's docs/INTEGRATION.md).
* Optional LAN routing is an image-local build setting (`--routed-lan-ports`), persisted in
  `/etc/lab-uplink/routing.json`. Slot and Manager do not inject this policy. Use a separate image
  variant for each policy and provide all selected adapters. Adapter 0 remains the PPPoE port;
  routing between selected LAN adapters does not traverse the BRAS and does not use NAT.
