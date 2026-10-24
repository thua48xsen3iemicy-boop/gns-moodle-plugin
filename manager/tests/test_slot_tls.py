"""Actual TLS handshake for manager REST and gateway WSS; no Proxmox needed."""
import base64
import importlib.util
import ssl
import subprocess
from pathlib import Path

import pytest
from aiohttp import web, ClientSession, WSMsgType

from lab_manager.slot_client import SlotClient, SlotUnavailable


@pytest.mark.parametrize("ca_via", ["GW_SLOT_CA_B64", "GW_SLOT_CA_FILE"])
async def test_verified_slot_rest_and_gateway_wss(tmp_path, monkeypatch, ca_via):
    root = Path(__file__).resolve().parents[2]
    subprocess.run(["bash", str(root / "agent/make-demo-pki.sh"), "localhost=127.0.0.1"],
                   cwd=tmp_path, capture_output=True, check=True, timeout=30)
    pki = tmp_path / "pki"
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(str(pki / "localhost.crt"), str(pki / "localhost.key"))
    node = "22222222-2222-4222-8222-222222222222"
    project = "11111111-1111-4111-8111-111111111111"
    seen = []
    async def health(request):
        assert request.headers["Authorization"] == "Bearer dummy-token"
        return web.json_response({"status": "ok"})
    async def console(request):
        assert "bearer.console-secret" in request.headers["Sec-WebSocket-Protocol"]
        seen.append(request.secure)
        ws = web.WebSocketResponse(protocols=("lab-slot",))
        await ws.prepare(request)
        await ws.send_bytes(b"R1#")
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                await ws.send_bytes(message.data.encode())
        return ws
    fake = web.Application()
    fake.router.add_get("/healthz", health)
    fake.router.add_get("/v1/console/{project}/{node}", console)
    runner = web.AppRunner(fake)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    good = SlotClient("127.0.0.1", port, "dummy-token", ca_file=pki / "node-server-ca.crt")
    bad = SlotClient("127.0.0.1", port, "dummy-token", ca_file=pki / "manager-client-ca.crt")
    gateway_runner = None
    try:
        assert await good.healthz()
        with pytest.raises(SlotUnavailable):
            await bad._call("GET", "/healthz")
        monkeypatch.setenv("GW_SLOT_SCHEME", "wss")
        ca = pki / "node-server-ca.crt"
        # B64 is what `gateway-env` writes (the unit hides the CA file's directory from the gateway)
        monkeypatch.setenv(ca_via, base64.b64encode(ca.read_bytes()).decode() if ca_via.endswith("B64") else str(ca))
        monkeypatch.setenv("GW_SLOT_PORT", str(port))
        monkeypatch.setenv("GW_SLOTS", '{"127.0.0.1":"console-secret"}')
        monkeypatch.setenv("GW_ALLOWED_ORIGINS", "https://moodle.test")
        monkeypatch.setenv("GW_INTERNAL_KEY", "dummy-key")
        spec = importlib.util.spec_from_file_location("tls_gateway_test", root / "manager/gateway/console_gateway.py")
        gateway = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gateway)
        gateway_runner = web.AppRunner(gateway.make_app())
        await gateway_runner.setup()
        public = web.TCPSite(gateway_runner, "127.0.0.1", 0)
        await public.start()
        address = f"http://127.0.0.1:{public._server.sockets[0].getsockname()[1]}"
        async with ClientSession() as c:
            response = await c.post(address + "/internal/tickets", headers={"X-Internal-Key": "dummy-key"},
                                    json={"slot": "127.0.0.1", "project_id": project, "nodes": [node]})
            ticket = (await response.json())["ticket"]
            ws = await c.ws_connect(address + f"/console/{node}?ticket={ticket}",
                                    headers={"Origin": "https://moodle.test"})
            assert (await ws.receive(timeout=2)).data == b"R1#"
            await ws.send_str("show version\r")
            assert (await ws.receive(timeout=2)).data == b"show version\r"
            await ws.close()
        assert seen == [True]
    finally:
        await good.close()
        await bad.close()
        if gateway_runner:
            await gateway_runner.cleanup()
        await runner.cleanup()
