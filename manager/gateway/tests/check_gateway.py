"""Проверка шлюза против поддельного Lab Slot: билеты, субпротокол с токеном, наблюдатель, коды закрытия.
Запуск: pip install aiohttp && python3 manager/gateway/tests/check_gateway.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.update(GW_INTERNAL_KEY="k", GW_SLOTS=json.dumps({"127.0.0.1": "console-secret"}), GW_SLOT_PORT="38080",
                  GW_ALLOWED_ORIGINS="https://moodle.test")
import console_gateway as gw
from aiohttp import web, ClientSession, WSMsgType

P, N1, N2, OFF = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
                  "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444")

# --- поддельный Slot: /v1/console/{project_id}/{node_id}, токен в субпротоколе, вывод уже без telnet-команд
seen, paths = [], []
async def cons(r):
    paths.append(r.path)
    protocols = [x.strip() for x in r.headers.get("Sec-WebSocket-Protocol", "").split(",")]
    if "lab-slot" not in protocols or "bearer.console-secret" not in protocols or r.match_info["n"] == OFF:
        return web.Response(status=403)                  # как Starlette: close() до accept()
    ws = web.WebSocketResponse(protocols=("lab-slot",)); await ws.prepare(r)
    await ws.send_bytes(b"\r\nR1> \xff")                 # 0xFF в данных должен дойти без изменений
    async for m in ws:
        seen.append((m.type, m.data))
        if m.type == WSMsgType.TEXT:
            if m.data == "reload\r":
                await ws.close(); break                   # telnet ноды закрылся
            await ws.send_bytes(m.data.encode())
    return ws
fake = web.Application()
fake.router.add_get("/v1/console/{p}/{n}", cons)

async def main():
    r1 = web.AppRunner(fake); await r1.setup(); await web.TCPSite(r1, "127.0.0.1", 38080).start()
    r2 = web.AppRunner(gw.make_app()); await r2.setup(); await web.TCPSite(r2, "127.0.0.1", 38081).start()
    async with ClientSession() as s:
        moodle = {"Origin": "https://moodle.test"}
        async def ticket(ro, project=P, nodes=(N1, OFF)):
            async with s.post("http://127.0.0.1:38081/internal/tickets", headers={"X-Internal-Key": "k"},
                              json={"slot": "127.0.0.1", "project_id": project, "nodes": list(nodes), "readonly": ro}) as r:
                return r.status, (await r.json())["ticket"] if r.status == 200 else None
        async def prompt(ws):
            out = b""
            while b"R1>" not in out: out += (await ws.receive()).data
            return out
        # ID уходят в путь запроса к слоту — без UUID билет не выдаётся
        assert (await ticket(False, project="../v1/labs"))[0] == 400
        assert (await ticket(False, nodes=["n1"]))[0] == 400
        print("non-UUID ids rejected OK")
        # обычный студент
        _, t = await ticket(False)
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{N1}?ticket={t}", headers=moodle)
        assert await prompt(ws) == b"\r\nR1> \xff"
        assert paths[-1] == f"/v1/console/{P}/{N1}", paths
        await ws.send_str("en\r"); await ws.send_bytes(b'{"cols":120,"rows":40}')
        assert (await ws.receive()).data == b"en\r"
        await ws.close()
        got = [x[1] for x in seen if x[0] in (WSMsgType.TEXT, WSMsgType.BINARY)]
        assert got == ["en\r"], got                       # бинарные кадры (в т.ч. размер окна) в слот не идут
        print("student input OK, binary frames dropped")
        # повторное использование билета
        try:
            await s.ws_connect(f"http://127.0.0.1:38081/console/{N1}?ticket={t}", headers=moodle); raise SystemExit("FAIL: билет принят повторно")
        except Exception as e: print("reuse rejected:", type(e).__name__)
        # чужая нода
        _, t2 = await ticket(False)
        try:
            await s.ws_connect(f"http://127.0.0.1:38081/console/{N2}?ticket={t2}", headers=moodle); raise SystemExit("FAIL: пустил к чужой ноде")
        except Exception as e: print("other node rejected:", type(e).__name__)
        # консоль на стороне слота закрылась — браузер получает 4000
        _, t = await ticket(False)
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{N1}?ticket={t}", headers=moodle)
        await prompt(ws); await ws.send_str("reload\r")
        m = await ws.receive(); assert m.type == WSMsgType.CLOSE and m.data == 4000, m
        print("upstream close -> 4000 OK")
        # слот отказал (нода не запущена) — 4001 «консоль недоступна», без трассировки
        _, t = await ticket(False)
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{OFF}?ticket={t}", headers=moodle)
        m = await ws.receive(); assert m.type == WSMsgType.CLOSE and m.data == 4001, m
        print("upstream refused -> 4001 OK")
        # неверный токен слота — то же поведение
        gw.SLOTS["127.0.0.1"] = "wrong"
        _, t = await ticket(False)
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{N1}?ticket={t}", headers=moodle)
        m = await ws.receive(); assert m.type == WSMsgType.CLOSE and m.data == 4001, m
        gw.SLOTS["127.0.0.1"] = "console-secret"
        print("wrong slot token -> 4001 OK")
        # чужой сайт и запрос без Origin — 403, билет не тратится
        _, t = await ticket(False)
        for headers in ({"Origin": "https://evil.test"}, {}):
            try:
                await s.ws_connect(f"http://127.0.0.1:38081/console/{N1}?ticket={t}", headers=headers)
                raise SystemExit("FAIL: пустил без Origin Moodle")
            except Exception as e:
                assert getattr(e, "status", None) == 403, e
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{N1}?ticket={t}", headers=moodle)
        await prompt(ws); await ws.close()
        print("foreign or missing Origin -> 403 OK")
        # наблюдатель
        seen.clear(); _, t3 = await ticket(True)
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{N1}?ticket={t3}", headers=moodle)
        await prompt(ws)
        await ws.send_str("conf t\r"); await ws.send_bytes(b'{"cols":80,"rows":24}')
        await asyncio.sleep(0.3); await ws.close(); await asyncio.sleep(0.2)
        leaked = [x for x in seen if x[0] in (WSMsgType.TEXT, WSMsgType.BINARY)]
        assert not leaked, leaked
        print("observer is read-only OK")
    await r2.cleanup(); await r1.cleanup()
asyncio.run(main())
print("ALL OK")
