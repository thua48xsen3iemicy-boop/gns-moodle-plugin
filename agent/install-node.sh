#!/usr/bin/env bash
set -euo pipefail
[[ "$EUID" == 0 ]] || { echo 'Run as root on a Proxmox node' >&2; exit 1; }
cd "$(dirname "$(readlink -f "$0")")"
command -v pct >/dev/null || { echo 'pct not found: install on a Proxmox host' >&2; exit 1; }
command -v findmnt >/dev/null || exit 1
[[ -f /etc/lab-node-agent/server.crt && -f /etc/lab-node-agent/server.key && -f /etc/lab-node-agent/manager-ca.crt ]] || {
  echo 'Install mTLS certificates under /etc/lab-node-agent first. See README.' >&2; exit 1;
}
for cert in server.crt server.key manager-ca.crt; do
  [[ ! -L "/etc/lab-node-agent/$cert" ]] || { echo 'Certificate symlinks are forbidden' >&2; exit 1; }
done
install -d -m 0700 /opt/lab-node-agent /etc/lab-node-agent
install -m 0644 agent.py requirements.txt /opt/lab-node-agent/
install -m 0644 ../constraints-tested.txt /opt/lab-node-agent/constraints-tested.txt
python3 -m venv /opt/lab-node-agent/venv
/opt/lab-node-agent/venv/bin/pip install --disable-pip-version-check -r /opt/lab-node-agent/requirements.txt -c /opt/lab-node-agent/constraints-tested.txt
install -m 0644 lab-node-agent.service /etc/systemd/system/lab-node-agent.service
chown -R root:root /opt/lab-node-agent /etc/lab-node-agent
chmod 0700 /etc/lab-node-agent
chmod 0600 /etc/lab-node-agent/server.key
chmod 0644 /etc/lab-node-agent/server.crt /etc/lab-node-agent/manager-ca.crt
systemctl daemon-reload
systemctl enable lab-node-agent
systemctl restart lab-node-agent
systemctl --no-pager status lab-node-agent
