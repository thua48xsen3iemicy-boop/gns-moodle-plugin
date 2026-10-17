"""Start the manager from the fixed read-only CephFS path (same scheme as slot/run.py)."""

import sys
from pathlib import Path

ROOT = Path("/srv/gns3-ro/manager")


def main(root=ROOT):
    if Path(__file__).resolve().parent != root.resolve():
        raise SystemExit("MANAGER_PATH_MISMATCH: run from /srv/gns3-ro/manager")
    if Path(sys.prefix).resolve() != (root / "venv").resolve():
        raise SystemExit("MANAGER_VENV_MISMATCH: use /srv/gns3-ro/manager/venv/bin/python")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    from lab_manager.preflight import run

    result = run()
    if not result["ok"]:
        raise SystemExit(str(result))
    from lab_manager.__main__ import main as serve

    serve()


if __name__ == "__main__":
    main()
