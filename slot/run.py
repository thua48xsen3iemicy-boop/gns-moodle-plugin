"""Start the supplied Slot code at the fixed, read-only LXC mount point."""

import os
import sys
from pathlib import Path

ROOT = Path("/srv/gns3-ro/slot")


def main(root=ROOT):
    # Manager owns the mount source; Slot checks only the code and local interpreter.
    if Path(__file__).resolve().parent != root.resolve():
        raise SystemExit("SLOT_PATH_MISMATCH: run from /srv/gns3-ro/slot")
    if Path(sys.prefix).resolve() != (root / "venv").resolve():
        raise SystemExit("RELEASE_VENV_MISMATCH: use /srv/gns3-ro/slot/venv/bin/python")
    sys.dont_write_bytecode = True
    os.chdir(root)
    sys.path.insert(0, str(root))
    from release import preflight

    result = preflight()
    if not result["ok"]:
        raise SystemExit(str(result))
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8080, workers=1)


if __name__ == "__main__":
    main()
