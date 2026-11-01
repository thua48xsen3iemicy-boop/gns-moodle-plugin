"""Offline checks; no guest services or networking are started."""

import json
import ipaddress
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

from build_cloud_init import render, write_private


class CloudInitTests(unittest.TestCase):
    def test_credentials_and_startup(self):
        cloud, network = render(user='lab"user', password='secret\\"value')
        files = {item["path"]: item for item in cloud["write_files"]}
        secret = files["/etc/ppp/lab-bras-secrets"]
        self.assertEqual(secret["permissions"], "0600")
        self.assertEqual(secret["content"], '"lab\\"user" * "secret\\\\\\"value" *\n')
        self.assertNotIn("packages", cloud)
        self.assertNotIn("password", network)
        script = files["/usr/local/sbin/lab-bras-init"]["content"]
        subprocess.run(["sh", "-n"], input=script, text=True, check=True)
        # JSON round trip preserves multiline files and shell argv for cloud-init.
        self.assertEqual(json.loads(json.dumps(cloud)), cloud)

    def test_distinct_networks_and_nat(self):
        cloud, network = render(user="lab", password="example", wan="enp6s0", access="enp7s0",
                                pool="172.20.0.0/24")
        self.assertTrue(network["ethernets"]["enp6s0"]["dhcp4"])
        self.assertFalse(network["ethernets"]["enp7s0"]["dhcp4"])
        files = {item["path"]: item["content"] for item in cloud["write_files"]}
        self.assertIn("gw-ip-address=172.20.0.1", files["/etc/accel-ppp.conf"])
        section = files["/etc/accel-ppp.conf"].split("[ip-pool]\n", 1)[1].split("\n\n", 1)[0]
        addresses = {str(address) for line in section.splitlines() if "=" not in line
                     for address in ipaddress.IPv4Network(line)}
        self.assertEqual(addresses, {f"172.20.0.{n}" for n in range(2, 255)})
        self.assertIn('oifname "enp6s0" ip saddr 172.20.0.0/24 masquerade', files["/etc/lab-bras.nft"])
        self.assertNotIn("flush ruleset", files["/etc/lab-bras.nft"])

    def test_invalid_settings(self):
        for overrides in ({"password": "bad\nvalue"}, {"user": ""}, {"wan": "ens19"},
                          {"access": "ens19;reboot"}, {"pool": "198.18.0.0/24"},
                          {"pool": "10.0.0.0/31"}, {"dns": ["bad", "1.1.1.1"]},
                          {"binary": "/tmp/accel-pppd -d"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                render(**({"user": "lab", "password": "example"} | overrides))

    def test_secret_output_is_private_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "user-data.yaml"
            write_private(target, "example")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                write_private(target, "replacement")
            self.assertEqual(target.read_text(), "example")
            link = Path(directory) / "link"
            link.symlink_to(target)
            with self.assertRaises(FileExistsError):
                write_private(link, "replacement")


if __name__ == "__main__":
    unittest.main()
