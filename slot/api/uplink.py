"""The "Internet" node of a lab: its outer port and the GNS3 cloud node on eth1 (CONTRACTS.md 4.2, 7).

The manifest only marks the QEMU node (`NodeSpec.uplink_port`). The slot adds the `cloud` node bound to its own
eth1 and the link to it itself: they describe the slot, not the lab, so lifecycle, facts, L1 and the console views
leave them out (`visible_nodes`, `visible_links`).
"""

import os
import re
from pathlib import Path

CLOUD_NAME = "_uplink"  # NodeSpec.name cannot start with "_", so no lab node can take it
INTERFACE = "eth1"
NET_DIR = Path("/sys/class/net")
MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}$")


def configured_mac():
    """SLOT_UPLINK_MAC as 48-bit int, or None: unset, malformed, multicast or not locally administered."""
    text = os.getenv("SLOT_UPLINK_MAC", "").strip()
    if not MAC_RE.match(text):
        return None
    value = int(text.replace(":", ""), 16)
    first = value >> 40
    return value if first & 1 == 0 and first & 2 == 2 else None


def enabled():
    """The `uplink` feature: a usable SLOT_UPLINK_MAC and an eth1 to bind the cloud node to."""
    return configured_mac() is not None and (NET_DIR / INTERFACE).exists()


def base_mac(port, adapters):
    """The `mac_address` of the QEMU node that gives adapter `port` the SLOT_UPLINK_MAC.

    GNS3 numbers the adapters upwards from the base (facts.adapter_macs). Every adapter must keep the
    first byte, so the base never wraps into another address class.
    """
    mac = configured_mac()
    if mac is None:
        raise ValueError("SLOT_UPLINK_MAC is not set or not a locally administered unicast address")
    base = mac - port
    if base < 0 or (base + adapters - 1) >> 40 != mac >> 40 or base >> 40 != mac >> 40:
        raise ValueError("SLOT_UPLINK_MAC leaves no room for the adapters of the node")
    raw = f"{base:012x}"
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


def spec_of(ticket):
    """The node spec with `uplink_port` (dict of the stored ticket or NodeSpec), or None."""
    for node in ticket.get("nodes", []) if isinstance(ticket, dict) else ticket.nodes:
        port = node.get("uplink_port") if isinstance(node, dict) else node.uplink_port
        if port is not None:
            return node
    return None


def cloud_payload(node_id):
    return {
        "name": CLOUD_NAME,
        "node_id": node_id,
        "node_type": "cloud",
        "compute_id": "local",
        "x": 0,
        "y": 0,
        "properties": {
            "ports_mapping": [
                {"interface": INTERFACE, "name": INTERFACE, "port_number": 0, "type": "ethernet"}
            ]
        },
    }


def visible_nodes(ticket, nodes):
    """The nodes of the lab: without the cloud node when the ticket owns one. Without an uplink in the
    ticket a node with that name is not ours and stays visible, so inventory checks reject it."""
    if spec_of(ticket) is None:
        return nodes
    return [n for n in nodes if n.get("name") != CLOUD_NAME]


def visible_links(ticket, nodes, links):
    if spec_of(ticket) is None:
        return links
    hidden = {n["node_id"] for n in nodes if n.get("name") == CLOUD_NAME}
    return [l for l in links if not any(e.get("node_id") in hidden for e in l.get("nodes", []))]


def check_attached(ticket, nodes, links):
    """The cloud node and its link must exist, once, as the slot built them. RuntimeError otherwise."""
    spec = spec_of(ticket)
    if spec is None:
        return
    name = spec["name"] if isinstance(spec, dict) else spec.name
    port = spec["uplink_port"] if isinstance(spec, dict) else spec.uplink_port
    clouds = [n for n in nodes if n.get("name") == CLOUD_NAME]
    qemu = [n for n in nodes if n.get("name") == name]
    if len(clouds) != 1 or clouds[0].get("node_type") != "cloud" or len(qemu) != 1:
        raise RuntimeError("Uplink cloud node is missing or foreign; operator reconciliation required")
    wanted = sorted([(qemu[0]["node_id"], port, 0), (clouds[0]["node_id"], 0, 0)])
    own = [
        l for l in links
        if sorted((e.get("node_id"), e.get("adapter_number"), e.get("port_number")) for e in l.get("nodes", []))
        == wanted
    ]
    touching = [
        l for l in links if any(e.get("node_id") == clouds[0]["node_id"] for e in l.get("nodes", []))
    ]
    if len(own) != 1 or len(touching) != 1:
        raise RuntimeError("Uplink link differs from manifest; refusing automatic rebuild")
