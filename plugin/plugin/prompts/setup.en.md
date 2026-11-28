<!-- gns3lab-setup-format: {"format": "gns3lab.lab/2", "field": "setup", "step_fields": ["config", "id", "node", "run", "shell", "timeout", "title"], "run": ["id", "node", "run", "shell", "timeout", "title"], "config": ["config", "id", "node", "title"], "kinds": {"run": "qemu with a guest agent", "config": ["iou", "vpcs"]}, "shells": ["sh", "routeros"], "limits": {"steps": 30, "title": 120, "run_bytes": 8000, "timeout": 120, "timeout_default": 60, "timeout_total": 600, "ios_config": 65536, "vpcs_script": 4096}} -->
<!-- DRAFT. The text below these lines is shown to a teacher in the lab editor ("Setup" block, "Prompt for a neural
     network") and copied into a neural network. The text is written together with the owner (docs/lab-setup/README.md §6a).
     [[LAB]] — devices (kind, OS, shell, what a step is made of), links and segments of the lab with addresses (JSON),
     [[SETUP]] — the current setup steps, [[TASK]] — what to set up.
     When the format of the setup field changes (manager/docs/CONTRACTS.md §2.6), update the text and the
     gns3lab-setup-format line above: manager/tests/test_setup_prompt.py compares it. -->
You help a teacher prepare a GNS3 networking lab: write its setup — steps the system runs on the lab devices once, when a
student gets the lab. The answer is JSON for the "Setup" field.

## Format

Answer with one JSON object, no explanations and no Markdown: {"setup": [ ... ]}. Each item is a step:

- "id" — lowercase Latin letters, digits, "-" and "_", up to 32 characters, unique.
- "title" — what the step does, up to 120 characters. The teacher and the observer see it, the student does not.
- "node" — the name of a lab device. Only devices that may carry a step in the lab below.
- On Linux and MikroTik RouterOS (virtual machines with a guest agent):
  - "run" — the script. Up to 8000 bytes. Runs as root. Linux: POSIX sh (/bin/sh, not bash); RouterOS: a RouterOS script;
  - "shell" — "sh" (Linux) or "routeros" (MikroTik RouterOS). Always give it, by the device's image;
  - "timeout" — seconds, 1…120, 60 by default. All steps together — at most 600.
- On Cisco IOL (switches and routers) and VPCS:
  - "config" — lines: startup-config lines on IOL, VPCS commands on VPCS, one per line. Printable ASCII and line feeds
    only. They have no "run", "shell" or "timeout".

At most 30 steps. No other fields.

## Rules

- Steps are **repeatable**: running one again gives the same result. Append to a file only if the line is not there yet;
  create a user only if there is none.
- Devices are set up one by one from top to bottom in the order of the lab, the steps of a device in the order of the
  list. A step may rely only on devices above it: their steps have already run.
- IOL "config" is startup-config lines ("interface …", "router …", "ip route …" blocks), not console commands: no
  "enable", "configure terminal", "write memory" or "end". IOS silently skips wrong lines — be exact.
- The addresses of the segments are already set (except segments "configured by the student"): do not repeat them,
  change them only on purpose, when the task says so.
- The Internet only through the lab's "Internet" device, and only if there is one. What the image already has is more
  reliable: do not install packages you can do without.
- A script that exits with a non-zero code stops the lab: the student does not get it. Do not hide errors that matter,
  and do not fail on ones that do not.

## The lab

[[LAB]]

## Already there

[[SETUP]]

If there are steps already, return the whole new list: keep the fitting ones, fix or add what is needed. The task names
the exceptions: if it asks only to add steps, return only the new ones, with ids not taken yet; if it asks to fix one
step, return only that step, with the same id.

## What to set up

[[TASK]]
