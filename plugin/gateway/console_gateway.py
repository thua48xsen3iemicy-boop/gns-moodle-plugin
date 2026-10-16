#!/usr/bin/env python3
"""
Шлюз консолей лаб: браузер (xterm.js) ⇄ этот сервис ⇄ GNS3 в LXC-слоте.

    браузер ──wss──> nginx ──ws──> шлюз ──ws──> http://<слот>:3080/v3/projects/{p}/nodes/{n}/console/ws?token=JWT
                                                     └─ GNS3 сам открывает telnet к консоли ноды внутри слота

Что делает шлюз:
  * пускает браузер только по одноразовому билету, который выдал менеджер лаб
    (токен GNS3 в браузер не попадает никогда);
  * сам логинится в GNS3 каждого слота и кэширует JWT до истечения срока;
  * вырезает из вывода согласование telnet (IAC …) — GNS3 пересылает его как есть, а xterm.js
    показал бы мусор;
  * пропускает от браузера только текст и сообщения о размере окна {"cols": N, "rows": M};
  * режим наблюдателя: вывод идёт, ввод и смена размера окна отбрасываются.

Запуск:
    pip install aiohttp
    GW_INTERNAL_KEY=секрет GW_ALLOWED_ORIGINS=https://moodle.example.edu \
    GW_SLOT_USERS='{"10.19.36.216": ["console-gw", "пароль"]}' python3 console_gateway.py

Билет (вызывает менеджер, не браузер):
    curl -s -X POST localhost:8080/internal/tickets -H 'X-Internal-Key: секрет' \
      -d '{"slot":"10.19.36.216","project_id":"…","nodes":["…"],"readonly":false}'
    → {"ticket": "…", "expires_in": 30}

Браузер подключается к  wss://<шлюз>/console/<node_id>?ticket=<билет>
"""
import asyncio
import base64
import hmac
import json
import logging
import os
import secrets
import time

from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

LISTEN_HOST = os.environ.get("GW_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("GW_PORT", "8080"))
GNS3_PORT = int(os.environ.get("GNS3_PORT", "3080"))
INTERNAL_KEY = os.environ.get("GW_INTERNAL_KEY", "")
ALLOWED_ORIGINS = {o for o in os.environ.get("GW_ALLOWED_ORIGINS", "").split(",") if o}
SLOT_USERS = json.loads(os.environ.get("GW_SLOT_USERS", "{}"))  # {"ip слота": ["user", "password"]}
TICKET_TTL = 30          # секунд; билет одноразовый
MAX_MSG = 64 * 1024      # максимум байт в одном сообщении от браузера
HEARTBEAT = 25           # ping каждые 25 с, чтобы прокси и NAT не рвали тихую консоль

log = logging.getLogger("console-gw")


# ---------------------------------------------------------------- telnet
IAC, SB, SE = 255, 250, 240
WILL, WONT, DO, DONT = 251, 252, 253, 254


class TelnetFilter:
    """Убирает telnet-команды из потока. Хранит состояние между кадрами:
    последовательность IAC может прийти разрезанной на два WebSocket-сообщения."""

    DATA, CMD, OPT, SUB, SUB_IAC = range(5)

    def __init__(self):
        self.state = self.DATA

    def feed(self, data: bytes) -> bytes:
        if self.state == self.DATA and IAC not in data:
            return data                                   # частый случай — без разбора
        out = bytearray()
        for b in data:
            st = self.state
            if st == self.DATA:
                if b == IAC:
                    self.state = self.CMD
                else:
                    out.append(b)
            elif st == self.CMD:
                if b == IAC:                              # IAC IAC — это сам байт 0xFF
                    out.append(IAC)
                    self.state = self.DATA
                elif b in (WILL, WONT, DO, DONT):
                    self.state = self.OPT
                elif b == SB:
                    self.state = self.SUB
                else:                                     # NOP, GA и прочие двухбайтовые
                    self.state = self.DATA
            elif st == self.OPT:
                self.state = self.DATA
            elif st == self.SUB:
                if b == IAC:
                    self.state = self.SUB_IAC
            elif st == self.SUB_IAC:
                self.state = self.DATA if b == SE else self.SUB
        return bytes(out)


# ---------------------------------------------------------------- билеты
class Tickets:
    """Заглушка в памяти. В бою — таблица в БД менеджера, чтобы шлюзов могло быть несколько."""

    def __init__(self):
        self._t = {}

    def issue(self, grant: dict) -> str:
        now = time.time()
        self._t = {k: v for k, v in self._t.items() if v["exp"] > now}
        t = secrets.token_urlsafe(24)
        self._t[t] = dict(grant, exp=now + TICKET_TTL)
        return t

    def take(self, t: str):
        g = self._t.pop(t, None)
        return g if g and g["exp"] > time.time() else None


# ---------------------------------------------------------------- токены GNS3
def jwt_exp(token: str) -> float:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except Exception:
        return time.time() + 600                          # не разобрали — считаем живым 10 минут


class SlotTokens:
    def __init__(self, http: ClientSession):
        self.http = http
        self.cache = {}
        self.locks = {}

    def invalidate(self, slot: str):
        self.cache.pop(slot, None)

    async def get(self, slot: str) -> str:
        hit = self.cache.get(slot)
        if hit and hit[1] - 60 > time.time():
            return hit[0]
        async with self.locks.setdefault(slot, asyncio.Lock()):
            hit = self.cache.get(slot)
            if hit and hit[1] - 60 > time.time():
                return hit[0]
            user, password = SLOT_USERS[slot]
            url = f"http://{slot}:{GNS3_PORT}/v3/access/users/authenticate"
            async with self.http.post(url, json={"username": user, "password": password}) as r:
                if r.status != 200:
                    raise web.HTTPBadGateway(text=f"GNS3 {slot}: вход не удался ({r.status})")
                token = (await r.json())["access_token"]
            self.cache[slot] = (token, jwt_exp(token))
            log.info("новый токен GNS3 для слота %s", slot)
            return token


def parse_resize(data: bytes):
    try:
        m = json.loads(data)
        c, r = m["cols"], m["rows"]
        if type(c) is int and type(r) is int and 2 <= c <= 500 and 2 <= r <= 300:
            return json.dumps({"cols": c, "rows": r}).encode()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- маршруты
async def issue_ticket(request: web.Request):
    if not INTERNAL_KEY or not hmac.compare_digest(request.headers.get("X-Internal-Key", ""), INTERNAL_KEY):
        raise web.HTTPForbidden()
    g = await request.json()
    if g.get("slot") not in SLOT_USERS or not g.get("project_id") or not g.get("nodes"):
        raise web.HTTPBadRequest(text="нужны slot (из GW_SLOT_USERS), project_id и nodes")
    grant = {"slot": g["slot"], "project_id": g["project_id"], "nodes": set(g["nodes"]),
             "readonly": bool(g.get("readonly")), "who": g.get("who", "")}
    return web.json_response({"ticket": request.app["tickets"].issue(grant), "expires_in": TICKET_TTL})


async def console(request: web.Request):
    origin = request.headers.get("Origin")
    if ALLOWED_ORIGINS and origin not in ALLOWED_ORIGINS:
        raise web.HTTPForbidden(text="чужой Origin")
    node_id = request.match_info["node_id"]
    g = request.app["tickets"].take(request.query.get("ticket", ""))
    if not g or node_id not in g["nodes"]:
        raise web.HTTPForbidden(text="билет недействителен")

    http: ClientSession = request.app["http"]
    tokens: SlotTokens = request.app["tokens"]
    slot = g["slot"]
    url = f"ws://{slot}:{GNS3_PORT}/v3/projects/{g['project_id']}/nodes/{node_id}/console/ws"
    up = await http.ws_connect(url, params={"token": await tokens.get(slot)},
                               heartbeat=HEARTBEAT, max_msg_size=1 << 20)

    browser = web.WebSocketResponse(heartbeat=HEARTBEAT, max_msg_size=MAX_MSG)
    await browser.prepare(request)
    log.info("консоль открыта: %s %s/%s%s", g["who"], slot, node_id, " (наблюдатель)" if g["readonly"] else "")
    tf = TelnetFilter()

    async def from_browser():
        async for m in browser:
            if g["readonly"]:
                continue
            if m.type == WSMsgType.TEXT:
                await up.send_str(m.data)
            elif m.type == WSMsgType.BINARY:              # только смена размера окна
                rs = parse_resize(m.data)
                if rs:
                    await up.send_bytes(rs)

    async def to_browser():
        async for m in up:
            if m.type == WSMsgType.BINARY:
                data = tf.feed(m.data)
                if data:
                    await browser.send_bytes(data)
            elif m.type == WSMsgType.TEXT:
                await browser.send_str(m.data)

    tasks = [asyncio.create_task(from_browser()), asyncio.create_task(to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if up.close_code == 1008:                         # GNS3 отверг токен — в следующий раз войдём заново
            tokens.invalidate(slot)
        upstream_code = up.close_code
        await up.close()
        if not browser.closed:
            # 4000 — консоль закрылась на стороне GNS3 (нода остановлена, перезапуск и т.п.)
            await browser.close(code=4000, message=f"upstream {upstream_code}".encode())
        log.info("консоль закрыта: %s/%s (GNS3: %s)", slot, node_id, upstream_code)
    return browser


async def on_startup(app):
    app["http"] = ClientSession(timeout=ClientTimeout(total=None, connect=10))
    app["tokens"] = SlotTokens(app["http"])
    app["tickets"] = Tickets()


async def on_cleanup(app):
    await app["http"].close()


def make_app():
    app = web.Application()
    app.router.add_post("/internal/tickets", issue_ticket)
    app.router.add_get("/console/{node_id}", console)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not INTERNAL_KEY:
        log.warning("GW_INTERNAL_KEY не задан — выдача билетов отключена")
    web.run_app(make_app(), host=LISTEN_HOST, port=LISTEN_PORT)
