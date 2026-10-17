"""Example invocation from Lab Manager, not installed on Proxmox nodes.

pip install requests
"""
import os
import requests

url = os.environ.get("LAB_AGENT_URL", "https://pve2.lab.local:8443")
cert = ("/etc/lab-manager/manager.crt", "/etc/lab-manager/manager.key")
server_ca = "/etc/lab-manager/node-server-ca.crt"

# If a POST times out, query /slots/{vmid}/status before retrying: the slot may be running.
response = requests.post(
    f"{url}/slots/2201/start",
    json={
        "student_key": "student42",
        "code_revision": None,
        "data_mode": "rw",
        "actions": ["init_slot"],
    },
    cert=cert,
    verify=server_ca,
    timeout=(3, 300),
)
response.raise_for_status()
print(response.json())
