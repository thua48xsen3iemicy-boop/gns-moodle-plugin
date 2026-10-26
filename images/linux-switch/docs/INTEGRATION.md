# Integration requests for later work

This image is an independent component. No Slot, Manager or Plugin API changes
are part of this implementation. Do not assume these guest methods are already
exposed through those services.

## Slot

Use the existing Lifecycle.qga broker to execute switchctl, never open a QGA
socket directly. Read stdout as the guest API envelope and check both guest exit
code and envelope ok. Use METRICS priority and wait=False or bounded max_age for
background status reads. Use the appropriate command priority for writes.
Do not cache capture.start/stop or vlan.apply and do not blindly retry them.
Capture lifetime is independent of the short QGA invocation that starts it.

When constructing a shell command for the existing QGA exec path, use proper
shell quoting of a strictly serialized JSON object, not string interpolation of
user input. Port names must be discovered from guest ports. Map topology NICs to
guest ports by MAC, never assume eth0 ordering. Capture/packet decoding, VLAN
semantics and Linux command construction remain inside the image.

Slot 0.7.0 reads `ports` and `vlans` (read-only, METRICS priority) in its `switch.net/1` facts collector,
see manager/docs/CONTRACTS.md section 4.6; no other guest method is used yet.

## Manager and Plugin

Future integration must add its own documented contracts before implementation.
Trigger observations by a button or an explicit lesson step. Poll a capture ID,
show observed ARP/tags, and label the observation window and missed-data limits.
Do not interpret timeout/no packets as an interface-down indication. A captured
ingress event cannot prove that a specific egress port transmitted the frame.

Configured VLANs come from the teaching topology; actual VLAN membership and
observed tags are separate data. Wait for VLAN application before marking the
configuration ready. Show that access traffic is normally untagged and that
changing VLAN membership can interrupt an exercise. UI coloring for a remote
interface stays in the platform; the image does not probe remote link state.
