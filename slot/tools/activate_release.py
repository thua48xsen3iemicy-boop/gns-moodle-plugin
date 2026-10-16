"""Controlled CephFS alias switch. The operator must stop ALL dependent Slot services first."""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path


def activate(release, alias, all_stopped):
    if not all_stopped:
        raise ValueError(
            "Stop ALL Slot API services using this alias; GNS3/VMs may keep running"
        )
    release = release.resolve(strict=True)
    if alias.exists() and not alias.is_symlink():
        raise ValueError(
            "Alias is a directory: perform the documented first migration while all services are stopped"
        )
    python = release / "venv/bin/python"
    if not python.is_file():
        raise ValueError("Build venv at its final physical path before activation")
    subprocess.run(
        [str(python), str(release / "release.py"), "check"],
        cwd=release,
        check=True,
        timeout=30,
    )
    subprocess.run(
        [str(python), "-m", "pip", "check"], cwd=release, check=True, timeout=30
    )
    # Reserve a unique name in the same directory; replace only the alias atomically.
    with tempfile.TemporaryDirectory(
        prefix=".slot-switch-", dir=alias.parent
    ) as staging:
        candidate = Path(staging) / "slot"
        candidate.symlink_to(release, target_is_directory=True)
        os.replace(candidate, alias)
    return str(release)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path)
    parser.add_argument("--alias", type=Path, default=Path("/srv/gns3-ro/slot"))
    parser.add_argument("--all-services-stopped", action="store_true")
    args = parser.parse_args()
    try:
        print(activate(args.release, args.alias, args.all_services_stopped))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Activation refused: {exc}\n")


if __name__ == "__main__":
    main()
