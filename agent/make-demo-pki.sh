#!/usr/bin/env bash
# Development/testing certificate generator. Run ONCE on a trusted administration machine.
# Usage: ./make-demo-pki.sh pve1.lab.local=10.0.0.11 pve2.lab.local=10.0.0.12
set -euo pipefail
umask 077
[[ $# -ge 1 ]] || { echo 'Usage: make-demo-pki.sh fqdn=ip [fqdn=ip ...]' >&2; exit 2; }
command -v openssl >/dev/null
DEST="$(pwd)/pki"
[[ ! -e "$DEST" ]] || { echo "Refusing to overwrite $DEST" >&2; exit 1; }
mkdir -m 0700 "$DEST"
cd "$DEST"
# Isolated client and server CAs. Their private keys NEVER go to a node.
for ca in manager-client-ca node-server-ca; do
  openssl req -new -x509 -newkey rsa:3072 -nodes -sha256 -days 3650 \
    -keyout "$ca.key" -out "$ca.crt" -subj "/CN=Lab $ca" \
    -addext 'basicConstraints=critical,CA:TRUE' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' >/dev/null 2>&1
done
openssl req -new -newkey rsa:3072 -nodes -sha256 \
  -keyout manager.key -out manager.csr -subj '/CN=lab-manager' >/dev/null 2>&1
cat > manager.ext <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=clientAuth
EOF
openssl x509 -req -sha256 -days 365 -in manager.csr \
  -CA manager-client-ca.crt -CAkey manager-client-ca.key -CAcreateserial \
  -extfile manager.ext -out manager.crt >/dev/null 2>&1
for arg in "$@"; do
  FQDN="${arg%%=*}"
  IP="${arg#*=}"
  [[ "$arg" == *=* && "$FQDN" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ && "$IP" =~ ^[0-9.]+$ ]] || {
    echo "Invalid fqdn=ip: $arg" >&2; exit 2;
  }
  openssl req -new -newkey rsa:3072 -nodes -sha256 \
    -keyout "$FQDN.key" -out "$FQDN.csr" -subj "/CN=$FQDN" >/dev/null 2>&1
  cat > "$FQDN.ext" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:$FQDN,IP:$IP
EOF
  openssl x509 -req -sha256 -days 365 -in "$FQDN.csr" \
    -CA node-server-ca.crt -CAkey node-server-ca.key -CAcreateserial \
    -extfile "$FQDN.ext" -out "$FQDN.crt" >/dev/null 2>&1
done
rm -f ./*.csr ./*.ext ./*.srl
printf '%s\n' "Generated under $DEST" \
  'Each node: its FQDN.crt -> /etc/lab-node-agent/server.crt' \
  'Each node: its FQDN.key -> /etc/lab-node-agent/server.key' \
  'Each node: manager-client-ca.crt -> /etc/lab-node-agent/manager-ca.crt' \
  'Manager: manager.crt, manager.key, node-server-ca.crt' \
  'DO NOT copy *-ca.key to any Proxmox node.'
