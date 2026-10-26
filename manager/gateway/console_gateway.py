#!/usr/bin/env python3
"""
Шлюз консолей лаб: браузер (xterm.js) ⇄ этот сервис ⇄ Lab Slot в LXC-слоте (GNS3 2.2.61).

Работает в LXC менеджера, рядом с ним, отдельным процессом (manager/README.md §1.1). Код перешёл
от плагина (plugin/gateway, шлюз 0.4.5); договорённости с браузерной частью плагина —
plugin/docs/MANAGER_REQUESTS.md §12, менять их только через manager/docs/PLUGIN_REQUESTS.md.

    браузер ──wss──> nginx ──ws──> шлюз ──ws──> http://<слот>:8080/v1/console/{project_id}/{node_id}
                                                     └─ Slot сам открывает telnet к консоли ноды внутри слота

Что делает шлюз:
  * пускает браузер только по одноразовому билету, который выдал менеджер лаб
    (токен слота в браузер не попадает никогда);
  * подключается к WebSocket консоли Slot с консольным токеном этого слота
    (субпротоколы ["lab-slot", "bearer.<токен>"]; см. slot/README.md);
  * тип консоли задаёт билет (поле `console`, его ставит менеджер по живому типу ноды):
      - `telnet` (по умолчанию):
          · пересылает вывод как есть: Slot уже вырезал согласование telnet (IAC …),
            повторная фильтрация испортила бы байт 0xFF в данных;
          · пропускает от браузера только текстовые кадры (ввод с клавиатуры). Бинарные кадры,
            в том числе {"cols": N, "rows": M}, отбрасываются: Slot пока не передаёт размер окна (telnet NAWS);
          · режим наблюдателя: вывод идёт, ввод отбрасывается;
      - `vnc` (экран QEMU, в браузере noVNC): по соединению идёт сырой RFB. Двоичные кадры идут в обе стороны
        без изменений и без разбора, текстовые кадры браузера отбрасываются. Наблюдателя у экрана нет
        (его видит только владелец сессии): билет с `console: "vnc"` и `readonly: true` не выдаётся (400);
  * коды закрытия для браузера: 4000 — консоль закрылась на стороне слота, 4001 — слот не пустил к консоли
    (нода не готова, слот недоступен, неверный токен в GW_SLOTS);
  * WebSocket только с сайтов из GW_ALLOWED_ORIGINS: без этой настройки шлюз не запускается.

Запуск в бою — lab-console-gateway.service, переменные — /etc/lab-manager/gateway.env, его пишет
`lab-manager-admin gateway-env` из конфига менеджера. Вручную (разработка):
    pip install aiohttp
    GW_INTERNAL_KEY=секрет GW_ALLOWED_ORIGINS=https://moodle.example.edu \
    GW_SLOTS='{"10.19.36.216": "SLOT_CONSOLE_TOKEN этого слота"}' python3 console_gateway.py

Билет (вызывает менеджер, не браузер):
    curl -s -X POST localhost:8080/internal/tickets -H 'X-Internal-Key: секрет' \
      -d '{"slot":"10.19.36.216","project_id":"<uuid>","nodes":["<uuid>"],"readonly":false,"console":"telnet"}'
    → {"ticket": "…", "expires_in": 30}
`console` необязательно: "telnet" (по умолчанию) или "vnc"; любое другое значение, как и "vnc" вместе
с "readonly": true, — 400.

Браузер подключается к  wss://<шлюз>/console/<node_id>?ticket=<билет>
"""
import asyncio
import base64
import hmac
import json
import logging
import os
import secrets
import ssl
import time
import uuid

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType, web

LISTEN_HOST = os.environ.get("GW_HOST", "127.0.0.1")  # Наружу — только через nginx, только /console/
LISTEN_PORT = int(os.environ.get("GW_PORT", "8080"))
SLOT_PORT = int(os.environ.get("GW_SLOT_PORT", "8080"))
SLOT_SCHEME = os.environ.get("GW_SLOT_SCHEME", "wss")
SLOT_CA_FILE = os.environ.get("GW_SLOT_CA_FILE") or None
SLOT_CA_B64 = os.environ.get("GW_SLOT_CA_B64") or None  # PEM of the slot CA, base64 (gateway-env writes it)
ALLOW_PLAINTEXT = os.environ.get("GW_ALLOW_PLAINTEXT_SLOTS") == "1"
INTERNAL_KEY = os.environ.get("GW_INTERNAL_KEY", "")
ALLOWED_ORIGINS = {o for o in os.environ.get("GW_ALLOWED_ORIGINS", "").split(",") if o}
SLOTS = json.loads(os.environ.get("GW_SLOTS", "{}"))  # {"адрес слота": "консольный токен Slot"}
TICKET_TTL = 30          # секунд; билет одноразовый
CONSOLES = ("telnet", "vnc")
MAX_MSG = 64 * 1024      # максимум байт в одном сообщении от браузера (нажатия, мышь, буфер обмена RFB — с запасом)
MAX_MSG_SLOT = {         # максимум байт в одном сообщении от слота; больше — слот закрывается с 1009
    "telnet": 1 << 20,
    # Кадр экрана без сжатия: 2560x1440 по 4 байта = 14,1 МиБ. 16 МиБ вмещает такой кадр одним сообщением,
    # даже если слот не режет поток RFB на куски (размер куска в контракте не задан).
    "vnc": 16 << 20,
}
HEARTBEAT = 25           # ping каждые 25 с, чтобы прокси и NAT не рвали тихую консоль

log = logging.getLogger("console-gw")


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


def canonical_uuid(value):
    """ID попадают в путь запроса к Slot — пропускаем только UUID в каноническом виде."""
    try:
        return str(uuid.UUID(value))
    except (TypeError, ValueError, AttributeError):
        return None


# ---------------------------------------------------------------- маршруты
async def issue_ticket(request: web.Request):
    if not INTERNAL_KEY or not hmac.compare_digest(request.headers.get("X-Internal-Key", ""), INTERNAL_KEY):
        raise web.HTTPForbidden()
    g = await request.json()
    project_id = canonical_uuid(g.get("project_id"))
    nodes = g.get("nodes")
    node_ids = {canonical_uuid(n) for n in nodes} if isinstance(nodes, list) else set()
    if g.get("slot") not in SLOTS or not project_id or not node_ids or None in node_ids:
        raise web.HTTPBadRequest(text="нужны slot (из GW_SLOTS), project_id и nodes (UUID)")
    console_type = g.get("console", "telnet")
    if console_type not in CONSOLES:  # Не только незнакомое имя: null и другой регистр тоже
        raise web.HTTPBadRequest(text='console: "telnet" или "vnc"')
    readonly = bool(g.get("readonly"))
    if console_type == "vnc" and readonly:
        raise web.HTTPBadRequest(text="у экрана vnc нет режима просмотра: readonly недопустим")
    grant = {"slot": g["slot"], "project_id": project_id, "nodes": node_ids, "console": console_type,
             "readonly": readonly, "who": g.get("who", "")}
    return web.json_response({"ticket": request.app["tickets"].issue(grant), "expires_in": TICKET_TTL})


async def console(request: web.Request):
    origin = request.headers.get("Origin")
    if origin not in ALLOWED_ORIGINS:
        raise web.HTTPForbidden(text="чужой Origin")
    node_id = canonical_uuid(request.match_info["node_id"])
    g = request.app["tickets"].take(request.query.get("ticket", ""))
    if not g or node_id not in g["nodes"]:
        raise web.HTTPForbidden(text="билет недействителен")

    http: ClientSession = request.app["http"]
    slot = g["slot"]
    vnc = g["console"] == "vnc"
    url = f"{SLOT_SCHEME}://{slot}:{SLOT_PORT}/v1/console/{g['project_id']}/{node_id}"
    try:
        up = await http.ws_connect(url, protocols=("lab-slot", "bearer." + SLOTS[slot]), heartbeat=HEARTBEAT,
                                   max_msg_size=MAX_MSG_SLOT[g["console"]], ssl=request.app["slot_tls"])
    except (ClientError, asyncio.TimeoutError) as exc:
        # Slot отказывает до рукопожатия: нода не запущена, нет telnet-консоли, неверный токен.
        up = None
        upstream_error = type(exc).__name__

    browser = web.WebSocketResponse(heartbeat=HEARTBEAT, max_msg_size=MAX_MSG)
    await browser.prepare(request)
    if up is None:
        # 4001, а не 4000: соединение открылось и сразу закрылось, клиент не должен считать его удачным.
        await browser.close(code=4001, message=b"upstream unavailable")
        log.info("консоль недоступна: %s %s/%s (%s)", g["who"], slot, node_id, upstream_error)
        return browser
    log.info("консоль открыта: %s %s/%s%s%s", g["who"], slot, node_id,
             " (vnc)" if vnc else "", " (наблюдатель)" if g["readonly"] else "")

    async def from_browser():
        async for m in browser:
            if g["readonly"]:
                continue  # У vnc такого билета нет (400 при выдаче); проверка остаётся на случай ошибки в коде
            if vnc:
                if m.type == WSMsgType.BINARY:  # RFB: байты клиента как есть
                    await up.send_bytes(m.data)
            elif m.type == WSMsgType.TEXT:
                await up.send_str(m.data)

    async def to_browser():
        async for m in up:
            if m.type == WSMsgType.BINARY:
                await browser.send_bytes(m.data)
            elif m.type == WSMsgType.TEXT and not vnc:  # RFB двоичный: текстового кадра от слота в нём не бывает
                await browser.send_bytes(m.data.encode())

    tasks = [asyncio.create_task(from_browser()), asyncio.create_task(to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        upstream_code = up.close_code
        await up.close()
        if not browser.closed:
            # 4000 — консоль закрылась на стороне слота (нода остановлена, перезапуск и т.п.)
            await browser.close(code=4000, message=f"upstream {upstream_code}".encode())
        log.info("консоль закрыта: %s/%s (Slot: %s)", slot, node_id, upstream_code)
    return browser


def slot_tls_context() -> ssl.SSLContext:
    """Trust for the slots' certificates: GW_SLOT_CA_B64 and/or GW_SLOT_CA_FILE, else the system store.

    The unit hides /etc/lab-manager from the gateway's dynamic user, so `gateway-env` inlines the CA
    into gateway.env instead of pointing at SLOT_CA_FILE there.
    """
    try:
        cadata = base64.b64decode(SLOT_CA_B64, validate=True).decode("ascii") if SLOT_CA_B64 else None
        return ssl.create_default_context(cafile=SLOT_CA_FILE, cadata=cadata)
    except ValueError as exc:
        raise RuntimeError(f"GW_SLOT_CA_B64 is not base64 of a PEM certificate: {exc}") from exc
    except ssl.SSLError as exc:
        raise RuntimeError(f"slot CA is not a valid PEM certificate: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"cannot read GW_SLOT_CA_FILE={SLOT_CA_FILE} as the gateway user: {exc}; "
                           "regenerate gateway.env with `lab-manager-admin gateway-env`") from exc


async def on_startup(app):
    if SLOT_SCHEME not in ("wss", "ws") or (SLOT_SCHEME == "ws" and not ALLOW_PLAINTEXT):
        raise RuntimeError("Use WSS or explicitly approve isolated plaintext slots")
    app["slot_tls"] = slot_tls_context()
    app["http"] = ClientSession(timeout=ClientTimeout(total=None, connect=10))
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
    if not ALLOWED_ORIGINS:
        raise SystemExit("GW_ALLOWED_ORIGINS не задан: укажите origin сайта Moodle, например https://moodle.example.edu")
    if not INTERNAL_KEY:
        log.warning("GW_INTERNAL_KEY не задан — выдача билетов отключена")
    web.run_app(make_app(), host=LISTEN_HOST, port=LISTEN_PORT)
