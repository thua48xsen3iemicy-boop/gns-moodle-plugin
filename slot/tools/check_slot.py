"""Read-only diagnostics by default; deploy only with explicit --deploy."""

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from api.models import DeployTicket
    from release import VERSION

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "examples/cisco-3x2.json")
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument(
        "--key", help="New intentional deployment key; required with --deploy"
    )
    args = parser.parse_args()
    if args.deploy and not args.key:
        parser.error("--deploy requires --key")
    try:
        data = json.loads(args.input.read_text())
        DeployTicket.model_validate(data)
        token = os.environ.get("SLOT_API_TOKEN")
        if not token:
            raise ValueError("SLOT_API_TOKEN must be set")
        with httpx.Client(
            base_url="http://127.0.0.1:8080",
            timeout=15,
            trust_env=False,
            headers={"Authorization": "Bearer " + token},
        ) as client:
            health = client.get("/healthz")
            health.raise_for_status()
            ready = client.get("/v1/readyz")
            print(json.dumps(ready.json(), indent=2))
            ready.raise_for_status()
            if ready.json().get("version") != VERSION:
                raise ValueError("API version differs from diagnostic client")
            if args.deploy:
                response = client.post(
                    "/v1/labs/deploy", json=data, headers={"Idempotency-Key": args.key}
                )
                print(json.dumps(response.json(), indent=2))
                response.raise_for_status()
        return 0
    except (OSError, ValueError, httpx.HTTPError):
        # Do not print requests/headers or commands from the trusted manifest.
        print(
            "Slot check failed: check input JSON, token, version and readiness diagnostics",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
