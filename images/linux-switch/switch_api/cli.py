"""One short request per QGA invocation; stdout is always one JSON envelope."""

import argparse
import json
import socket
import sys
import uuid

from .server import MAX_REQUEST, MAX_RESPONSE, Service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default="/run/switch-api/api.sock")
    parser.add_argument("method", choices=["status", "ports", "fdb", "vlans", "vlan.apply", "port.configure",
                                           "capture.start", "capture.read", "capture.stop"])
    parser.add_argument("--params", default="{}", help="JSON object, or - to read stdin")
    args = parser.parse_args()
    ident = uuid.uuid4().hex
    try:
        payload = sys.stdin.read(MAX_REQUEST + 1) if args.params == "-" else args.params
        params = json.loads(payload)
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        request = {"version": 1, "id": ident, "method": args.method, "params": params}
        wire = json.dumps(request).encode() + b"\n"
        if len(wire) > MAX_REQUEST:
            raise ValueError("request too large")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(30)
            sock.connect(args.socket)
            sock.sendall(wire)
            with sock.makefile("rb") as stream:
                data = stream.readline(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE or not data.endswith(b"\n"):
                raise ValueError("invalid response framing")
            response = json.loads(data)
            if not isinstance(response, dict) or type(response.get("ok")) is not bool:
                raise ValueError("invalid response envelope")
            valid_id = response.get("id") == ident or (
                response.get("ok") is False and response.get("id") is None)
            if not valid_id or response.get("version") != 1:
                raise ValueError("invalid response envelope")
    except (OSError, ValueError) as exc:
        response = Service.error(ident, "CLIENT_ERROR", str(exc)[:256])
    print(json.dumps(response, separators=(",", ":")))
    return 0 if response.get("ok") is True else 1


if __name__ == "__main__":
    sys.exit(main())
