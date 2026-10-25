"""Проверка шлюза против поддельного Lab Slot: билеты, субпротокол с токеном, наблюдатель, коды закрытия,
консоль telnet и экран vnc (сырой RFB: двоичные кадры в обе стороны).
Запуск: pip install aiohttp && python3 manager/gateway/tests/check_gateway.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.update(GW_SLOT_SCHEME="ws", GW_ALLOW_PLAINTEXT_SLOTS="1",
                  GW_INTERNAL_KEY="k", GW_SLOTS=json.dumps({"127.0.0.1": "console-secret"}), GW_SLOT_PORT="38080",
                  GW_ALLOWED_ORIGINS="https://moodle.test")
import console_gateway as gw
from aiohttp import web, ClientSession, WSMsgType

P, N1, N2, OFF, VNC = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
                       "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444",
                       "55555555-5555-4555-8555-555555555555")

# --- поддельный Slot: /v1/console/{project_id}/{node_id}, токен в субпротоколе, вывод уже без telnet-команд
seen, seen_vnc, paths = [], [], []
BANNER = b"RFB 003.008\n\xff\x00\xfe"                        # начало потока RFB: 0xFF и 0x00 в данных
BIG = bytes(range(256)) * (2 * 4096)                      # 2 МиБ: больше, чем разрешено telnet-консоли от слота
async def vnc_cons(ws):
    await ws.send_bytes(BANNER)
    async for m in ws:
        seen_vnc.append((m.type, m.data))                 # настоящий слот закрыл бы сессию на текстовом кадре (1003)
        if m.type == WSMsgType.BINARY:
            if m.data == b"close":
                await ws.close(); break                   # TCP-соединение с VNC закрылось
            await ws.send_bytes(BIG if m.data == b"big" else b"ack:" + m.data)
    return ws
async def cons(r):
    paths.append(r.path)
    protocols = [x.strip() for x in r.headers.get("Sec-WebSocket-Protocol", "").split(",")]
    if "lab-slot" not in protocols or "bearer.console-secret" not in protocols or r.match_info["n"] == OFF:
        return web.Response(status=403)                  # как Starlette: close() до accept()
    ws = web.WebSocketResponse(protocols=("lab-slot",)); await ws.prepare(r)
    if r.match_info["n"] == VNC:
        return await vnc_cons(ws)
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
        async def ticket(ro, project=P, nodes=(N1, OFF, VNC), **extra):
            body = {"slot": "127.0.0.1", "project_id": project, "nodes": list(nodes), "readonly": ro, **extra}
            async with s.post("http://127.0.0.1:38081/internal/tickets", headers={"X-Internal-Key": "k"},
                              json=body) as r:
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
        # --- тип консоли в билете ---------------------------------------------------------------------------
        # поле необязательное; всё, кроме "telnet" и "vnc", — 400, как и vnc с readonly
        for bad in ("ssh", "VNC", "", None, 5, ["vnc"]):
            assert (await ticket(False, console=bad))[0] == 400, bad
        assert (await ticket(True, console="vnc"))[0] == 400
        assert (await ticket(True, console="telnet"))[0] == 200
        assert (await ticket(False, console="vnc"))[0] == 200 and (await ticket(False, console="telnet"))[0] == 200
        print("console field: default telnet, others 400, vnc+readonly 400 OK")
        # явный telnet — прежнее поведение: только текстовые кадры, бинарные отбрасываются
        seen.clear(); _, t = await ticket(False, console="telnet")
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{N1}?ticket={t}", headers=moodle)
        await prompt(ws)
        await ws.send_bytes(b"\x00\x01"); await ws.send_str("en\r")
        assert (await ws.receive()).data == b"en\r"
        await ws.close()
        assert [x[1] for x in seen if x[0] in (WSMsgType.TEXT, WSMsgType.BINARY)] == ["en\r"], seen
        print("explicit telnet OK")
        # vnc: двоичные кадры в обе стороны без изменений
        seen_vnc.clear(); _, t = await ticket(False, console="vnc")
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{VNC}?ticket={t}", headers=moodle)
        m = await ws.receive(); assert m.type == WSMsgType.BINARY and m.data == BANNER, m
        assert paths[-1] == f"/v1/console/{P}/{VNC}", paths
        rfb = bytes([0, 0, 0, 0xFF, 4, 0, 0, 0]) + bytes(range(256))   # SetEncodings-подобный кадр с 0x00 и 0xFF
        await ws.send_bytes(rfb)
        m = await ws.receive(); assert m.type == WSMsgType.BINARY and m.data == b"ack:" + rfb, m
        assert seen_vnc == [(WSMsgType.BINARY, rfb)], seen_vnc
        await ws.send_bytes(b"\x00" * 60 * 1024)                      # 60 КБ от браузера — в пределах 64 КБ
        m = await ws.receive(); assert m.type == WSMsgType.BINARY and len(m.data) == 4 + 60 * 1024, m
        # крупный кадр экрана от слота (2 МиБ, больше лимита telnet-консоли) доходит целиком
        await ws.send_bytes(b"big")
        m = await ws.receive(); assert m.type == WSMsgType.BINARY and m.data == BIG, (m.type, str(m.data)[:20])
        # текстовый кадр от браузера для vnc отбрасывается, поток при этом жив
        n = len(seen_vnc)
        await ws.send_str("hello"); await ws.send_bytes(b"after")
        m = await ws.receive(); assert m.type == WSMsgType.BINARY and m.data == b"ack:after", m
        assert [x for x in seen_vnc[n:] if x[0] == WSMsgType.TEXT] == [], seen_vnc[n:]
        assert seen_vnc[n:] == [(WSMsgType.BINARY, b"after")], seen_vnc[n:]
        await ws.close()
        print("vnc binary frames both ways, text frame dropped, 2 MiB frame OK")
        # сообщение от браузера больше 64 КБ закрывает соединение (1009), в слот не уходит
        _, t = await ticket(False, console="vnc"); seen_vnc.clear()
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{VNC}?ticket={t}", headers=moodle, max_msg_size=0)
        await ws.receive(); await ws.send_bytes(b"\x00" * (64 * 1024 + 1))
        m = await ws.receive(); assert m.type == WSMsgType.CLOSE and m.data == 1009, m
        assert seen_vnc == [], seen_vnc
        print("browser message over 64 KiB -> 1009 OK")
        # конец потока на стороне слота — 4000; слот не пустил — 4001; чужой Origin — 403; билет одноразовый
        _, t = await ticket(False, console="vnc")
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{VNC}?ticket={t}", headers=moodle)
        await ws.receive(); await ws.send_bytes(b"close")
        m = await ws.receive(); assert m.type == WSMsgType.CLOSE and m.data == 4000, m
        _, t = await ticket(False, console="vnc")
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{OFF}?ticket={t}", headers=moodle)
        m = await ws.receive(); assert m.type == WSMsgType.CLOSE and m.data == 4001, m
        _, t = await ticket(False, console="vnc")
        for headers in ({"Origin": "https://evil.test"}, {}):
            try:
                await s.ws_connect(f"http://127.0.0.1:38081/console/{VNC}?ticket={t}", headers=headers)
                raise SystemExit("FAIL: vnc пустил без Origin Moodle")
            except Exception as e:
                assert getattr(e, "status", None) == 403, e
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{VNC}?ticket={t}", headers=moodle)
        await ws.receive(); await ws.close()
        try:
            await s.ws_connect(f"http://127.0.0.1:38081/console/{VNC}?ticket={t}", headers=moodle)
            raise SystemExit("FAIL: билет vnc принят повторно")
        except Exception as e:
            assert getattr(e, "status", None) == 403, e
        print("vnc: upstream close -> 4000, refused -> 4001, Origin 403, one-time ticket OK")
    await r2.cleanup(); await r1.cleanup()
asyncio.run(main())
print("ALL OK")
