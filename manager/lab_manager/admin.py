"""Operator commands, run as the service user with the service environment:

    sudo -u lab-manager /srv/gns3-ro/manager/lab-manager-admin <command>

Commands never print secrets.
"""

import argparse
import asyncio
import json
import sys

from . import config
from .db import latest_version, migrate


def _settings():
    try:
        return config.load()
    except config.ConfigError as exc:
        raise SystemExit(f"CONFIG_ERROR: {exc}") from exc


async def _with_services(fn):
    from .app import Services

    services = Services(_settings())
    await services.pool.open(wait=True, timeout=10)
    try:
        await services.store.sync_slots(list(services.settings.slots.values()))
        return await fn(services)
    finally:
        await services.close()


def cmd_migrate(args):
    applied = migrate(_settings().database_url)
    print(json.dumps({"applied": applied, "version": latest_version()}))


def cmd_check_config(args):
    s = _settings()
    print(json.dumps({
        "sites": sorted(x.slug for x in s.sites.values()),
        "slots": sorted(s.slots),
        "pve_nodes": list(s.pve_nodes),
        "agent_url_template": s.agent_url_template,
        "lab_persistence": s.lab_persistence,
        "slot_code_revision": s.slot_code_revision,
        "agent_start_actions": list(s.agent_start_actions),
        "labs_dir": str(s.labs_dir),
    }, ensure_ascii=False))


def cmd_labs(args):
    from .labs import Catalog

    print(json.dumps(Catalog(_settings().labs_dir).check_all(), ensure_ascii=False, indent=1))


def cmd_slots(args):
    async def run(services):
        rows = await services.store.slots()
        for r in rows:
            print(json.dumps({k: r[k] for k in ("vmid", "address", "state", "generation",
                                                "session_id", "student_key", "in_config", "state_message")},
                             ensure_ascii=False, default=str))

    asyncio.run(_with_services(run))


def cmd_slot_state(args):
    """enable: maintenance/failed -> free, after the operator checked the CT and its lab."""

    async def run(services):
        if args.state == "free":
            node = await services.pve.find_ct_node(args.vmid)
            state = await services.agent.status(node, args.vmid)
            if state != "stopped":
                raise SystemExit(f"CT {args.vmid} is {state} on {node}; stop it first")
        await services.store.set_slot_admin_state(args.vmid, args.state, args.message)
        print(json.dumps({"vmid": args.vmid, "state": args.state}))

    asyncio.run(_with_services(run))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="lab_manager.admin")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    sub.add_parser("check-config").set_defaults(fn=cmd_check_config)
    sub.add_parser("labs").set_defaults(fn=cmd_labs)
    sub.add_parser("slots").set_defaults(fn=cmd_slots)
    for name, state in (("slot-enable", "free"), ("slot-maintenance", "maintenance")):
        p = sub.add_parser(name)
        p.add_argument("vmid", type=int)
        p.add_argument("--message", default="")
        p.set_defaults(fn=cmd_slot_state, state=state)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
