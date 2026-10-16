"""Pin all imports and the interpreter to one immutable release directory."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    if Path(sys.prefix).resolve() != (ROOT / "venv").resolve():
        raise SystemExit("RELEASE_VENV_MISMATCH: use this release's venv")
    from release import preflight

    result = preflight()
    if not result["ok"]:
        raise SystemExit(str(result))
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8080, workers=1)
