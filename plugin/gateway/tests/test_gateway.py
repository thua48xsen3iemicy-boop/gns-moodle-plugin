"""Проверка шлюза против поддельного GNS3: фильтр telnet, билеты, режим наблюдателя.
Запуск: pip install aiohttp && python3 gateway/tests/test_gateway.py
"""
import asyncio, base64, json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.update(GW_INTERNAL_KEY="k", GW_SLOT_USERS=json.dumps({"127.0.0.1": ["gw", "pw"]}), GNS3_PORT="38080")
import console_gateway as gw
from aiohttp import web, ClientSession, WSMsgType

# --- проверка фильтра на разрезанных кадрах
tf = gw.TelnetFilter()
parts = [b"\xff\xfb\x01\xff", b"\xfb\x03\xff\xfd\x1fR1", b">\xff\xfa\x1f\x00\x50", b"\x00\x18\xff\xf0 ok \xff\xff!"]
assert b"".join(tf.feed(p) for p in parts) == b"R1> ok \xff!", "filter"
print("filter OK")

# --- поддельный GNS3
exp = int(time.time()) + 3600
TOKEN = "h." + base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=") + ".s"
seen = []
async def auth(r):
    b = await r.json(); assert b == {"username": "gw", "password": "pw"}
    return web.json_response({"access_token": TOKEN})
async def cons(r):
    ws = web.WebSocketResponse(); await ws.prepare(r)
    if r.query.get("token") != TOKEN: await ws.close(code=1008); return ws
    await ws.send_bytes(b"\xff\xfb\x01\xff\xfb"); await ws.send_bytes(b"\x03\r\nR1>")
    async for m in ws:
        seen.append((m.type, m.data))
        if m.type == WSMsgType.TEXT: await ws.send_bytes(m.data.encode())
    return ws
fake = web.Application()
fake.router.add_post("/v3/access/users/authenticate", auth)
fake.router.add_get("/v3/projects/{p}/nodes/{n}/console/ws", cons)

async def main():
    r1 = web.AppRunner(fake); await r1.setup(); await web.TCPSite(r1, "127.0.0.1", 38080).start()
    r2 = web.AppRunner(gw.make_app()); await r2.setup(); await web.TCPSite(r2, "127.0.0.1", 38081).start()
    async with ClientSession() as s:
        async def ticket(ro):
            async with s.post("http://127.0.0.1:38081/internal/tickets", headers={"X-Internal-Key": "k"},
                              json={"slot": "127.0.0.1", "project_id": "p1", "nodes": ["n1"], "readonly": ro}) as r:
                return (await r.json())["ticket"]
        # обычный студент
        t = await ticket(False)
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/n1?ticket={t}")
        out = b""
        while b"R1>" not in out: out += (await ws.receive()).data
        assert out == b"\r\nR1>", out
        await ws.send_str("en\r"); await ws.send_bytes(b'{"cols":120,"rows":40}'); await ws.send_bytes(b"junk")
        echo = (await ws.receive()).data; assert echo == b"en\r", echo
        await ws.close()
        # повторное использование билета
        try:
            await s.ws_connect(f"http://127.0.0.1:38081/console/n1?ticket={t}"); raise SystemExit("FAIL: билет принят повторно")
        except Exception as e: print("reuse rejected:", type(e).__name__)
        # чужая нода
        t2 = await ticket(False)
        try:
            await s.ws_connect(f"http://127.0.0.1:38081/console/n2?ticket={t2}"); raise SystemExit("FAIL: пустил к чужой ноде")
        except Exception as e: print("other node rejected:", type(e).__name__)
        # наблюдатель
        got = [x[1] for x in seen if x[0] in (WSMsgType.TEXT, WSMsgType.BINARY)]
        assert got == ["en\r", b'{"cols": 120, "rows": 40}'], got   # мусорный бинарный кадр отброшен
        print("student input + resize OK"); seen.clear(); t3 = await ticket(True)
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/n1?ticket={t3}")
        out = b""
        while b"R1>" not in out: out += (await ws.receive()).data
        await ws.send_str("conf t\r"); await ws.send_bytes(b'{"cols":80,"rows":24}')
        await asyncio.sleep(0.3); await ws.close(); await asyncio.sleep(0.2)
        leaked = [x for x in seen if x[0] in (WSMsgType.TEXT, WSMsgType.BINARY)]
        assert not leaked, leaked
        print("observer is read-only OK")
    await r2.cleanup(); await r1.cleanup()
asyncio.run(main())
print("ALL OK")
