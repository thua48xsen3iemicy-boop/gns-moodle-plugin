"""Проверка шлюза против поддельного Lab Slot: билеты, субпротокол с токеном, наблюдатель, коды закрытия,
консоль telnet и экран vnc (сырой RFB: двоичные кадры в обе стороны), размер окна слоту lab-slot.2, пределы консолей и билетов,
срок рукопожатия со слотом, закрытие соединения со слотом при любом сбое, активность консолей владельца для наблюдателя.
Запуск: pip install aiohttp && python3 manager/gateway/tests/check_gateway.py
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.update(GW_SLOT_SCHEME="ws", GW_ALLOW_PLAINTEXT_SLOTS="1",
                  GW_INTERNAL_KEY="k", GW_SLOTS=json.dumps({"127.0.0.1": "console-secret", "127.0.0.2": "hang"}), GW_SLOT_PORT="38080",
                  GW_ALLOWED_ORIGINS="https://moodle.test")
import console_gateway as gw
from aiohttp import web, ClientSession, WSMsgType

P, N1, N2, OFF, VNC = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
                       "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444",
                       "55555555-5555-4555-8555-555555555555")
V2 = "99999999-9999-4999-8999-999999999999"              # нода нового слота: он выбирает lab-slot.2

# --- поддельный Slot: /v1/console/{project_id}/{node_id}, токен в субпротоколе, вывод уже без telnet-команд
seen, seen_vnc, seen2, paths, offered, queries = [], [], [], [], [], []
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
    paths.append(r.path); queries.append(r.query_string)
    protocols = [x.strip() for x in r.headers.get("Sec-WebSocket-Protocol", "").split(",")]
    offered.append(protocols)
    if "lab-slot" not in protocols or "bearer.console-secret" not in protocols or r.match_info["n"] == OFF:
        return web.Response(status=403)                  # как Starlette: close() до accept()
    if r.match_info["n"] == V2 and "lab-slot.2" in protocols:
        ws = web.WebSocketResponse(protocols=("lab-slot.2",)); await ws.prepare(r)
        await ws.send_bytes(b"\r\nR1> ")
        async for m in ws:                                # ввод — двоичные кадры, текстовые — управление
            seen2.append((m.type, m.data))
            if m.type == WSMsgType.BINARY:
                await ws.send_bytes(m.data)
        return ws
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

# --- зависший слот 127.0.0.2: принимает TCP и молчит; hang_open — его открытые соединения
hang_open = set()
async def hang(reader, writer):
    hang_open.add(writer)
    try:
        await reader.read()                              # до EOF: шлюз закрыл соединение
    finally:
        hang_open.discard(writer); writer.close()

async def until(cond, timeout=3.0):
    for _ in range(int(timeout / 0.05)):
        if cond(): return True
        await asyncio.sleep(0.05)
    return cond()

async def main():
    r1 = web.AppRunner(fake); await r1.setup(); await web.TCPSite(r1, "127.0.0.1", 38080).start()
    hang_srv = await asyncio.start_server(hang, "127.0.0.2", 38080)
    app = gw.make_app()
    r2 = web.AppRunner(app); await r2.setup(); await web.TCPSite(r2, "127.0.0.1", 38081).start()
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
        # новый слот (lab-slot.2): ввод уходит двоичными кадрами, размер окна — текстовым {"resize": …}
        assert all(p[:2] == ["lab-slot.2", "lab-slot"] for p in offered), offered
        seen2.clear(); _, t = await ticket(False, nodes=(V2,))
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{V2}?ticket={t}", headers=moodle)
        await prompt(ws)
        await ws.send_bytes(b'{"cols":120,"rows":40}')
        for bad in (b'{"cols":0,"rows":40}', b'{"cols":120,"rows":1001}', b'{"cols":true,"rows":40}',
                    b'{"cols":"120","rows":40}', b'{"cols":120}', b'[120,40]', b'\xff\x00', b'{"cols":80.5,"rows":24}'):
            await ws.send_bytes(bad)
        await ws.send_str("ip a\r"); await ws.send_bytes(b'{"cols":1000,"rows":1,"x":5}')
        assert (await ws.receive()).data == b"ip a\r"
        await asyncio.sleep(0.2); await ws.close()
        assert seen2 == [(WSMsgType.TEXT, '{"resize": {"cols": 120, "rows": 40}}'), (WSMsgType.BINARY, b"ip a\r"),
                         (WSMsgType.TEXT, '{"resize": {"cols": 1000, "rows": 1}}')], seen2
        # наблюдатель новому слоту не шлёт ни ввод, ни размер
        seen2.clear(); _, t = await ticket(True, nodes=(V2,))
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{V2}?ticket={t}", headers=moodle)
        await prompt(ws)
        await ws.send_bytes(b'{"cols":120,"rows":40}'); await ws.send_str("conf t\r")
        await asyncio.sleep(0.3); await ws.close(); await asyncio.sleep(0.2)
        assert seen2 == [], seen2
        print("lab-slot.2: input as binary, window size as resize, bad sizes dropped, observer silent OK")
        # история: ?history=1 нового терминала доходит до слота, у наблюдателя тоже; иначе и у vnc — нет
        for ro, q, want in ((False, "&history=1", "history=1"), (True, "&history=1", "history=1"),
                            (False, "", ""), (False, "&history=yes", "")):
            _, t = await ticket(ro, nodes=(V2,))
            ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{V2}?ticket={t}{q}", headers=moodle)
            await prompt(ws); await ws.close()
            assert queries[-1] == want, (ro, q, queries[-1])
        print("history flag passed to the slot OK")
        # vnc: двоичные кадры в обе стороны без изменений
        seen_vnc.clear(); _, t = await ticket(False, console="vnc")
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{VNC}?ticket={t}&history=1", headers=moodle)
        m = await ws.receive(); assert m.type == WSMsgType.BINARY and m.data == BANNER, m
        assert paths[-1] == f"/v1/console/{P}/{VNC}" and queries[-1] == "", (paths, queries)
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
        # --- пределы и сбои: одна лаба не занимает весь шлюз ----------------------------------------------
        assert await until(lambda: app["limits"].total == 0), app["limits"].__dict__
        # обычный HTTP-запрос с билетом — 400 до траты билета и до похода в слот
        _, t = await ticket(False); n = len(paths)
        async with s.get(f"http://127.0.0.1:38081/console/{N1}?ticket={t}", headers=moodle) as r:
            assert r.status == 400, r.status
        assert len(paths) == n and app["limits"].total == 0
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{N1}?ticket={t}", headers=moodle)
        await prompt(ws); await ws.close()
        print("plain HTTP -> 400, ticket kept, slot not called OK")
        # предел на ноду и на проект: сверх него 4001, после закрытия — снова можно
        gw.MAX_PER_NODE, gw.MAX_PER_PROJECT = 2, 3
        async def open_console(node=N1, project=P, nodes=(N1, N2, OFF, VNC)):
            _, t = await ticket(False, project=project, nodes=nodes)
            return await s.ws_connect(f"http://127.0.0.1:38081/console/{node}?ticket={t}", headers=moodle)
        a, b = await open_console(), await open_console()
        await prompt(a); await prompt(b)
        c = await open_console()
        m = await c.receive(); assert m.type == WSMsgType.CLOSE and m.data == 4001, m
        d = await open_console(VNC)                       # другая нода того же проекта — третья консоль проекта
        assert (await d.receive()).data == BANNER
        e = await open_console(VNC)                       # четвёртая консоль проекта — сверх предела
        m = await e.receive(); assert m.type == WSMsgType.CLOSE and m.data == 4001, m
        P2 = "66666666-6666-4666-8666-666666666666"      # другая лаба не страдает
        f = await open_console(project=P2)
        assert paths[-1] == f"/v1/console/{P2}/{N1}"; await prompt(f)
        assert app["limits"].total == 4, app["limits"].__dict__
        await a.close()
        assert await until(lambda: app["limits"].total == 3)
        g2 = await open_console(); await prompt(g2)
        for w in (b, d, f, g2): await w.close()
        assert await until(lambda: app["limits"].total == 0), app["limits"].__dict__
        gw.MAX_PER_NODE, gw.MAX_PER_PROJECT = 6, 40
        print("per-node and per-project limits -> 4001, other lab unaffected, released on close OK")
        # общий предел шлюза
        gw.MAX_CONSOLES = 1
        a = await open_console(); await prompt(a)
        b = await open_console(project=P2)
        m = await b.receive(); assert m.type == WSMsgType.CLOSE and m.data == 4001, m
        await a.close(); assert await until(lambda: app["limits"].total == 0)
        gw.MAX_CONSOLES = 400
        print("gateway-wide limit -> 4001 OK")
        # неиспользованные билеты одной лабы ограничены (429), у другой лабы выдаются
        gw.MAX_TICKETS_PER_PROJECT = 3
        P3 = "77777777-7777-4777-8777-777777777777"
        assert [(await ticket(False, project=P3))[0] for _ in range(4)] == [200, 200, 200, 429]
        assert (await ticket(False, project="88888888-8888-4888-8888-888888888888"))[0] == 200
        gw.MAX_TICKETS_PER_PROJECT = 40
        print("unused tickets per lab -> 429 OK")
        # зависший слот: рукопожатие ограничено сроком, соединение со слотом закрыто, место освобождено
        gw.HANDSHAKE_TIMEOUT = 0.5
        async def ticket_for_hang():
            body = {"slot": "127.0.0.2", "project_id": P, "nodes": [N1], "readonly": False}
            async with s.post("http://127.0.0.1:38081/internal/tickets", headers={"X-Internal-Key": "k"},
                              json=body) as r:
                return (await r.json())["ticket"]
        ws = await s.ws_connect(f"http://127.0.0.1:38081/console/{N1}?ticket={await ticket_for_hang()}", headers=moodle)
        m = await ws.receive(timeout=5); assert m.type == WSMsgType.CLOSE and m.data == 4001, m
        assert await until(lambda: not hang_open), hang_open
        assert app["limits"].total == 0, app["limits"].__dict__
        print("hanging slot -> 4001 after the handshake timeout, slot connection closed OK")
        # браузер ушёл во время рукопожатия: aiohttp не отменяет обработчик, его останавливает срок рукопожатия;
        # затем соединение со слотом и место освобождаются, ответ браузеру не нужен
        gw.HANDSHAKE_TIMEOUT = 1.0
        url = f"http://127.0.0.1:38081/console/{N1}?ticket={await ticket_for_hang()}"
        pending = asyncio.create_task(s.ws_connect(url, headers=moodle))
        assert await until(lambda: len(hang_open) == 1 and app["limits"].total == 1)
        pending.cancel(); await asyncio.gather(pending, return_exceptions=True)
        assert await until(lambda: not hang_open and app["limits"].total == 0, 4), (hang_open, app["limits"].__dict__)
        gw.HANDSHAKE_TIMEOUT = 15
        print("browser gone during the handshake -> slot connection closed, limit released OK")
        # --- активность консолей владельца для наблюдателя (CONTRACTS.md §6.3) -------------------------------
        async def activity_ticket(**body):
            body = {"slot": "127.0.0.1", "project_id": P, **body}
            async with s.post("http://127.0.0.1:38081/internal/tickets", headers={"X-Internal-Key": "k"},
                              json=body) as r:
                return r.status, (await r.json())["ticket"] if r.status == 200 else None
        async def watch():
            _, t = await activity_ticket(activity=True, who="s1:observer")
            return await s.ws_connect(f"http://127.0.0.1:38081/console/activity?ticket={t}", headers=moodle)
        async def frame(ws):
            m = await ws.receive(timeout=3); assert m.type == WSMsgType.TEXT, m
            return json.loads(m.data)
        async def silent(ws, wait=0.3):
            try:
                m = await ws.receive(timeout=wait)
            except asyncio.TimeoutError:
                return True
            raise SystemExit(f"FAIL: unexpected activity frame {m}")
        assert (await activity_ticket(activity="yes"))[0] == 400
        assert (await activity_ticket(activity=True, project_id="../x"))[0] == 400
        _, t = await activity_ticket(activity=True)          # билет активности не открывает консоль ноды
        try:
            await s.ws_connect(f"http://127.0.0.1:38081/console/{N1}?ticket={t}", headers=moodle); raise SystemExit("FAIL: activity ticket opened a console")
        except Exception as e: assert getattr(e, "status", None) == 403, e
        _, t = await ticket(True)                             # билет консоли не открывает активность
        try:
            await s.ws_connect(f"http://127.0.0.1:38081/console/activity?ticket={t}", headers=moodle); raise SystemExit("FAIL: console ticket opened the activity")
        except Exception as e: assert getattr(e, "status", None) == 403, e
        _, t = await activity_ticket(activity=True)
        try:
            await s.ws_connect(f"http://127.0.0.1:38081/console/activity?ticket={t}"); raise SystemExit("FAIL: activity without Origin")
        except Exception as e: assert getattr(e, "status", None) == 403, e
        print("activity tickets: validated, not interchangeable with console tickets, Origin checked OK")
        w = await watch()
        assert await frame(w) == {"type": "snapshot", "open": [], "input": {}}
        owner = await open_console(); await prompt(owner)
        assert await frame(w) == {"type": "open", "node": N1}
        observer = await s.ws_connect(f"http://127.0.0.1:38081/console/{N1}?ticket={(await ticket(True))[1]}", headers=moodle)
        await prompt(observer); await observer.send_str("observer\r")
        assert await silent(w)                                # консоль наблюдателя и его ввод не в счёт
        await owner.send_bytes(b'{"cols":100,"rows":30}')     # размер окна — не ввод
        assert await silent(w)
        await owner.send_str("e"); await owner.send_str("n")
        assert await frame(w) == {"type": "input", "node": N1}
        assert await silent(w)                                # второй символ в пределах ACTIVITY_INPUT_EVERY не шлётся
        await asyncio.sleep(gw.ACTIVITY_INPUT_EVERY)
        await owner.send_str("\r")
        assert await frame(w) == {"type": "input", "node": N1}
        w2 = await watch()                                    # новый наблюдатель сразу видит, где студент
        snap = await frame(w2)
        assert snap["type"] == "snapshot" and snap["open"] == [N1] and list(snap["input"]) == [N1], snap
        assert 0 <= snap["input"][N1] < 2, snap
        second = await open_console(); await prompt(second)   # вторая консоль той же ноды: open уже был
        await second.close()
        assert await silent(w)
        await owner.close()
        assert await frame(w) == {"type": "close", "node": N1}
        assert await frame(w2) == {"type": "close", "node": N1}
        await observer.close()
        print("activity: open/input/close of the owner only, throttled, snapshot for a new watcher OK")
        # экран vnc: ввод — сообщения RFB с клавишей или нажатой кнопкой мыши
        _, t = await ticket(False, nodes=(VNC,), console="vnc")
        d = await s.ws_connect(f"http://127.0.0.1:38081/console/{VNC}?ticket={t}", headers=moodle)
        assert (await d.receive(timeout=3)).data == BANNER
        assert await frame(w) == {"type": "open", "node": VNC}
        await d.send_bytes(b"\x03\x01\x00\x00\x00\x00\x04\x00\x03\x00"); await d.receive()  # запрос экрана
        await d.send_bytes(b"\x05\x00\x00\x10\x00\x10"); await d.receive()                  # мышь без кнопки
        assert await silent(w)
        await d.send_bytes(b"\x04\x01\x00\x00\x00\x00\x00\x61"); await d.receive()          # клавиша
        assert await frame(w) == {"type": "input", "node": VNC}
        await d.close()
        assert await frame(w) == {"type": "close", "node": VNC}
        print("activity: vnc keys count, screen requests and bare mouse moves do not OK")
        # пределы наблюдателей
        gw.MAX_WATCHERS_PER_PROJECT = 2
        w3 = await watch()
        m = await w3.receive(); assert m.type == WSMsgType.CLOSE and m.data == 4001, m
        gw.MAX_WATCHERS_PER_PROJECT = 10
        await w2.close()
        assert await until(lambda: app["activity"].watchers == 1)
        w3 = await watch(); assert (await frame(w3))["type"] == "snapshot"
        # отстающий наблюдатель получает снимок вместо потерянных кадров
        lag = gw.Activity(); proj = ("127.0.0.1", P)
        q = lag.watch(proj); lag.opened(proj, N1)
        for _ in range(gw.WATCH_QUEUE + 5):
            lag._send(proj, {"type": "input", "node": N1})
        assert q.qsize() <= gw.WATCH_QUEUE
        got = [q.get_nowait() for _ in range(q.qsize())]
        assert {"type": "snapshot", "open": [N1], "input": {}} in got, got[:3]
        gw_act = app["activity"]
        await w.close(); await w3.close()
        assert await until(lambda: gw_act.watchers == 0 and not gw_act.projects), gw_act.__dict__
        assert await until(lambda: app["limits"].total == 0), app["limits"].__dict__
        print("activity: watcher limit -> 4001, overflow -> snapshot, state released OK")
    await r2.cleanup(); await r1.cleanup(); hang_srv.close()
asyncio.run(main())
print("ALL OK")
