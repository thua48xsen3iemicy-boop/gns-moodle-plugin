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

    # Loopback by default; a non-loopback address must be firewalled to Manager and console gateway.
    host = os.environ.get("SLOT_LISTEN_HOST", "127.0.0.1")
    cert, key = os.getenv("SLOT_TLS_CERT_FILE"), os.getenv("SLOT_TLS_KEY_FILE")
    if bool(cert) != bool(key):
        raise SystemExit("SLOT_TLS_CERT_FILE and SLOT_TLS_KEY_FILE must be configured together")
    tls = {"ssl_certfile": cert, "ssl_keyfile": key} if cert else {}
    uvicorn.run("main:app", host=host, port=8080, workers=1, **tls)


if __name__ == "__main__":
    main()
