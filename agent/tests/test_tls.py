"""Exercise actual TLS without Proxmox or system installation."""
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path
import pytest
import uvicorn
import agent


def test_mtls_transport(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'make-demo-pki.sh'
    subprocess.run(['bash', str(script), 'localhost=127.0.0.1'], cwd=tmp_path,
                   check=True, capture_output=True, timeout=120)
    pki = tmp_path / 'pki'
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(agent.app, log_level='error',
        ssl_certfile=str(pki / 'localhost.crt'), ssl_keyfile=str(pki / 'localhost.key'),
        ssl_ca_certs=str(pki / 'manager-client-ca.crt'), ssl_cert_reqs=ssl.CERT_REQUIRED,
        proxy_headers=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        def request(cert=None, trusted=True, hostname='localhost'):
            ctx = ssl.create_default_context(cafile=str(pki / (
                'node-server-ca.crt' if trusted else 'manager-client-ca.crt')))
            if cert:
                ctx.load_cert_chain(str(pki / (cert + '.crt')), str(pki / (cert + '.key')))
            with socket.create_connection(('127.0.0.1', port), timeout=3) as raw:
                with ctx.wrap_socket(raw, server_hostname=hostname) as tls:
                    tls.sendall(b'GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                    data = b''
                    while chunk := tls.recv(4096):
                        data += chunk
                    return data
        assert b'200 OK' in request('manager')
        for cert in (None, 'localhost'):
            try:
                result = request(cert)
            except (ssl.SSLError, ConnectionError):
                pass
            else:
                assert b'200 OK' not in result
        with pytest.raises(ssl.SSLCertVerificationError):
            request('manager', trusted=False)
        with pytest.raises(ssl.SSLCertVerificationError):
            request('manager', hostname='wrong.example')
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


def test_service_enforces_mtls():
    import shlex
    unit = (Path(__file__).resolve().parents[1] / 'lab-node-agent.service').read_text()
    command = next(line.partition('=')[2] for line in unit.splitlines() if line.startswith('ExecStart='))
    args = shlex.split(command)
    assert args[args.index('--ssl-cert-reqs') + 1] == '2'
    assert args[args.index('--ssl-ca-certs') + 1] == '/etc/lab-node-agent/manager-ca.crt'
    assert '--no-proxy-headers' in args
