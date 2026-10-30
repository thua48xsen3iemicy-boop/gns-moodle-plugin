# Integration

This image is an independent component; it changes no Slot, Manager or Plugin contract.

* Slot: the node is a QEMU node like any other (QGA is required: `qga` in the ticket). The outer adapter is marked by
  `NodeSpec.uplink_port` (manager/docs/CONTRACTS.md 4.2); the slot gives it `SLOT_UPLINK_MAC` and links it to its own
  cloud node on `eth1`. The image must not assume anything about the other end except PPPoE frames.
* Manager: image `lab-uplink` in `images.json` with `"console_type": "none"`; labs with the node run only on slots that
  offer the `uplink` feature (CONTRACTS 7).
* Install: `install/lab-uplink-filter` keeps slots from seeing each other in the shared VLAN.
* Nothing in the guest is queried by the slot: no collector reads this node.
