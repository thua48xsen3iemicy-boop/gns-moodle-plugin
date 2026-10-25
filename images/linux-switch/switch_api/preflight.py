"""Reject an incomplete guest installation before starting the API."""

import configparser
import sys
from pathlib import Path

NETWORK_FILES = ("10-slot-br0.netdev", "10-slot-br0.network", "20-slot-ports.network")


def check_network(directory):
    expected = (
        ("10-slot-br0.netdev", "NetDev", {"Name": "br0", "Kind": "bridge"}),
        ("10-slot-br0.network", "Match", {"Name": "br0"}),
        ("20-slot-ports.network", "Network", {"Bridge": "br0"}),
    )
    for name, section, values in expected:
        path = Path(directory) / name
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        try:
            with path.open() as stream:
                parser.read_file(stream)
            for key, value in values.items():
                if parser.get(section, key, fallback=None) != value:
                    raise ValueError(f"Expected [{section}] {key}={value}")
        except (OSError, ValueError, configparser.Error) as exc:
            raise RuntimeError(f"Incomplete switch network profile: {path}: {exc}") from exc


def main():
    try:
        check_network("/etc/systemd/network")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
