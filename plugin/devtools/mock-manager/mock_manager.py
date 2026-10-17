"""Mock-менеджер лаб для разработки плагина: API SPEC §3 в памяти и поддельная WS-консоль Lab Slot.

Ведёт себя как настоящий менеджер (manager/ в ветке менеджера) там, где это видит плагин:
- авторизация Bearer + X-Moodle-Site, ошибки {"error", "message"};
- у студента одна живая лаба на сайт: та же активность и lab_ref — 200 с ней, другая — 409 session_conflict;
- GET /sessions?user_id=&instance_id= — живая сессия или последняя завершённая, 404 — если не было;
- сессия идёт queued → starting → running за ~5 с, stop — stopping → stopped за ~2 с;
- у запущенной сессии topology (properties нод пустые) и nodes со статусами;
- билеты консолей выдаёт настоящий шлюз (gateway/console_gateway.py) через /internal/tickets.

Эталонные лабы — fixtures/*.gns3 (lab_ref = имя файла без .gns3), статусы нод — fixtures/<lab>.live.json.

Поддельный Lab Slot слушает MOCK_SLOT_PORT: /v1/console/{project_id}/{node_id} с субпротоколами
["lab-slot", "bearer.<токен>"], отвечает приглашением "<имя ноды>#" и эхом. Команда "reload" закрывает
консоль (шлюз отдаст браузеру 4000). Остановленная нода не пускает в консоль.

Управление для ручных проверок:
    curl -X POST localhost:8090/mock/nodes/<node_id>/stop    # нода остановлена: консоль закроется
    curl -X POST localhost:8090/mock/nodes/<node_id>/start

Запуск — devtools/README.md.
"""
import asyncio
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import ClientSession, WSMsgType, web

FIXTURES = Path(os.environ.get("MOCK_FIXTURES", Path(__file__).resolve().parents[2] / "fixtures"))
PORT = int(os.environ.get("MOCK_PORT", "8090"))
SLOT_PORT = int(os.environ.get("MOCK_SLOT_PORT", "8091"))
SECRET = os.environ.get("MOCK_SECRET", "dev-secret")
SLOT_ADDRESS = os.environ.get("MOCK_SLOT_ADDRESS", "127.0.0.1")  # ключ в GW_SLOTS шлюза
CONSOLE_TOKEN = os.environ.get("MOCK_CONSOLE_TOKEN", "dev-console-token")
GW_INTERNAL_URL = os.environ.get("GW_INTERNAL_URL", "http://127.0.0.1:8080")
GW_INTERNAL_KEY = os.environ.get("GW_INTERNAL_KEY", "dev-internal-key")
GW_PUBLIC_URL = os.environ.get("GW_PUBLIC_URL", "ws://127.0.0.1:8080")
QUEUED_FOR, STARTING_FOR, STOPPING_FOR, MAX_DURATION = 1.0, 4.0, 2.0, 90 * 60

log = logging.getLogger("mock-manager")
MESSAGES = {
    "queued": "Ожидание свободного слота…",
    "starting": "Запуск устройств…",
    "running": "Лаба запущена",
    "stopping": "Остановка…",
    "stopped": "Лаба остановлена",
}


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None


def error(status, code, message):
    return web.json_response({"error": code, "message": message}, status=status)


def load_labs():
    labs = {}
    for path in sorted(FIXTURES.glob("*.gns3")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        live = path.with_suffix(".live.json")
        statuses = json.loads(live.read_text(encoding="utf-8")).get("node_status", {}) if live.exists() else {}
        labs[path.stem] = {"doc": doc, "statuses": statuses}
    return labs


class Session:
    def __init__(self, site, user_id, instance_id, lab_ref):
        self.id = "s-" + secrets.token_hex(8)
        self.site, self.user_id, self.instance_id, self.lab_ref = site, user_id, instance_id, lab_ref
        self.project_id = str(uuid.uuid4())
        self.created = time.time()
        self.stop_requested = None

    def status(self):
        now = time.time()
        if self.stop_requested is not None:
            return "stopped" if now - self.stop_requested >= STOPPING_FOR else "stopping"
        age = now - self.created
        if age < QUEUED_FOR:
            return "queued"
        if age < QUEUED_FOR + STARTING_FOR:
            return "starting"
        if age > QUEUED_FOR + STARTING_FOR + MAX_DURATION:
            return "stopped"
        return "running"

    @property
    def started(self):
        return self.created + QUEUED_FOR + STARTING_FOR


class Manager:
    def __init__(self):
        self.labs = load_labs()
        self.sessions: list[Session] = []
        self.stopped_nodes: set[str] = set()

    def live(self, site, user_id):
        return next((s for s in self.sessions if s.site == site and s.user_id == user_id
                     and s.status() not in ("stopped", "failed")), None)

    def latest(self, site, user_id, instance_id):
        mine = [s for s in self.sessions if (s.site, s.user_id, s.instance_id) == (site, user_id, instance_id)]
        return mine[-1] if mine else None

    def node_status(self, lab, node_id):
        if node_id in self.stopped_nodes:
            return "stopped"
        return lab["statuses"].get(node_id, "started")

    def present(self, s):
        status = s.status()
        body = {
            "session_id": s.id,
            "status": status,
            "status_message": MESSAGES[status],
            "started_at": iso(s.started) if status in ("running", "stopping", "stopped") else None,
            "expires_at": iso(s.started + MAX_DURATION) if status != "stopped" else None,
        }
        if status == "running":
            lab = self.labs[s.lab_ref]
            doc = json.loads(json.dumps(lab["doc"]))
            doc["name"] = f"{s.lab_ref}-{s.id}"
            for n in doc["topology"]["nodes"]:
                n["properties"] = {}  # как у настоящего менеджера: внутреннее слота не уходит наружу
                n.pop("console", None)
            body["topology"] = {"name": doc["name"], "topology": doc["topology"]}
            body["nodes"] = {
                n["node_id"]: {
                    "status": self.node_status(lab, n["node_id"]),
                    "console_type": n.get("console_type") if n.get("console_type") in ("telnet", "vnc") else "none",
                }
                for n in doc["topology"]["nodes"]
            }
        return body

    def node_name(self, node_id):
        for lab in self.labs.values():
            for n in lab["doc"]["topology"]["nodes"]:
                if n["node_id"] == node_id:
                    return n["name"], lab
        return None, None


def site_of(request):
    site = request.headers.get("X-Moodle-Site", "").rstrip("/")
    if not site or request.headers.get("Authorization") != f"Bearer {SECRET}":
        raise web.HTTPUnauthorized(text=json.dumps({"error": "unauthorized", "message": "Invalid credentials"}),
                                   content_type="application/json")
    return site


async def list_labs(request):
    site_of(request)
    m: Manager = request.app["m"]
    return web.json_response([{"lab_ref": ref, "title": lab["doc"].get("name", ref), "description": ""}
                              for ref, lab in m.labs.items()])


async def lab_topology(request):
    site_of(request)
    lab = request.app["m"].labs.get(request.match_info["ref"])
    return web.json_response(lab["doc"]) if lab else error(404, "lab_not_found", "Unknown lab_ref")


async def find_session(request):
    site = site_of(request)
    try:
        user_id, instance_id = int(request.query["user_id"]), int(request.query["instance_id"])
    except (KeyError, ValueError):
        return error(400, "invalid_request", "Invalid fields: user_id, instance_id")
    m: Manager = request.app["m"]
    s = m.latest(site, user_id, instance_id)
    return web.json_response(m.present(s)) if s else error(404, "session_not_found", "No session")


async def create_session(request):
    site = site_of(request)
    m: Manager = request.app["m"]
    try:
        body = await request.json()
        user_id, instance_id, lab_ref = int(body["user_id"]), int(body["instance_id"]), str(body["lab_ref"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return error(400, "invalid_request", "Invalid fields: user_id, instance_id, lab_ref")
    live = m.live(site, user_id)
    if live:
        if (live.instance_id, live.lab_ref) != (instance_id, lab_ref):
            return error(409, "session_conflict", "У студента уже запущена другая лаба; сначала остановите её")
        return web.json_response(m.present(live), status=200)
    if lab_ref not in m.labs:
        return error(404, "lab_not_found", "Unknown lab_ref")
    s = Session(site, user_id, instance_id, lab_ref)
    m.sessions.append(s)
    log.info("session %s: user %s, activity %s, lab %s", s.id, user_id, instance_id, lab_ref)
    return web.json_response(m.present(s), status=201)


def own(request, site):
    s = next((x for x in request.app["m"].sessions if x.id == request.match_info["sid"] and x.site == site), None)
    if not s:
        raise web.HTTPNotFound(text=json.dumps({"error": "session_not_found", "message": "Unknown session"}),
                               content_type="application/json")
    return s


async def get_session(request):
    return web.json_response(request.app["m"].present(own(request, site_of(request))))


async def stop_session(request):
    s = own(request, site_of(request))
    if s.status() not in ("stopped", "failed") and s.stop_requested is None:
        s.stop_requested = time.time()
    return web.json_response(request.app["m"].present(s))


async def console_ticket(request):
    s = own(request, site_of(request))
    m: Manager = request.app["m"]
    body = await request.json()
    if s.status() != "running":
        return error(409, "session_not_running", "Session is not running")
    node_id = body.get("node_id")
    if node_id not in {n["node_id"] for n in m.labs[s.lab_ref]["doc"]["topology"]["nodes"]}:
        return error(404, "node_not_found", "Node does not belong to this session")
    readonly = bool(body.get("readonly"))
    grant = {"slot": SLOT_ADDRESS, "project_id": s.project_id, "nodes": [node_id], "readonly": readonly,
             "who": f"{s.id}:{'observer' if readonly else 'owner'}"}
    try:
        async with request.app["http"].post(f"{GW_INTERNAL_URL}/internal/tickets", json=grant,
                                            headers={"X-Internal-Key": GW_INTERNAL_KEY}) as resp:
            if resp.status != 200:
                return error(502, "gateway_unavailable", f"Gateway answered {resp.status}")
            ticket = await resp.json()
    except Exception as exc:  # noqa: BLE001 - any transport problem is "gateway unavailable"
        return error(502, "gateway_unavailable", f"Gateway: {type(exc).__name__}")
    return web.json_response({"ticket": ticket["ticket"], "gateway_url": GW_PUBLIC_URL,
                              "expires_in": ticket.get("expires_in", 30)})


async def mock_node(request):
    m: Manager = request.app["m"]
    node_id, action = request.match_info["node"], request.match_info["action"]
    if action == "stop":
        m.stopped_nodes.add(node_id)
        for ws in list(request.app["consoles"].get(node_id, ())):
            await ws.close()  # консоль на стороне слота закрылась
    else:
        m.stopped_nodes.discard(node_id)
    return web.json_response({"node_id": node_id, "status": "stopped" if action == "stop" else "started"})


# ---------------------------------------------------------------- поддельный Lab Slot
async def slot_console(request):
    m: Manager = request.app["m"]
    protocols = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")]
    node_id = request.match_info["node"]
    name, _ = m.node_name(node_id)
    if "lab-slot" not in protocols or f"bearer.{CONSOLE_TOKEN}" not in protocols or not name \
            or node_id in m.stopped_nodes:
        return web.Response(status=403)
    ws = web.WebSocketResponse(protocols=("lab-slot",))
    await ws.prepare(request)
    request.app["consoles"].setdefault(node_id, set()).add(ws)
    prompt = f"{name}#".encode()
    line = ""
    await ws.send_bytes(b"\r\n" + prompt)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue  # размер окна и прочие бинарные кадры слот не понимает
            for ch in msg.data:
                if ch in "\r\n":
                    cmd, line = line.strip(), ""
                    if cmd == "reload":
                        await ws.close()
                        return ws
                    out = f"\r\n% mock {name}: {cmd}" if cmd else ""
                    await ws.send_bytes(out.encode() + b"\r\n" + prompt)
                elif ch in "\x7f\b":
                    if line:
                        line = line[:-1]
                        await ws.send_bytes(b"\b \b")
                elif ch == "\x1a":
                    line = ""
                    await ws.send_bytes(b"^Z\r\n" + prompt)
                elif ch >= " ":
                    line += ch
                    await ws.send_bytes(ch.encode())
    finally:
        request.app["consoles"][node_id].discard(ws)
    return ws


def make_apps():
    manager = Manager()
    consoles = {}
    api = web.Application()
    api["m"], api["consoles"] = manager, consoles
    api.router.add_get("/api/v1/labs", list_labs)
    api.router.add_get("/api/v1/labs/{ref}/topology", lab_topology)
    api.router.add_get("/api/v1/sessions", find_session)
    api.router.add_post("/api/v1/sessions", create_session)
    api.router.add_get("/api/v1/sessions/{sid}", get_session)
    api.router.add_post("/api/v1/sessions/{sid}/stop", stop_session)
    api.router.add_post("/api/v1/sessions/{sid}/console-tickets", console_ticket)
    api.router.add_post("/mock/nodes/{node}/{action:stop|start}", mock_node)

    async def http_session(app):
        app["http"] = ClientSession()
        yield
        await app["http"].close()

    api.cleanup_ctx.append(http_session)
    slot = web.Application()
    slot["m"], slot["consoles"] = manager, consoles
    slot.router.add_get("/v1/console/{project}/{node}", slot_console)
    return api, slot


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    api, slot = make_apps()
    runners = [web.AppRunner(api), web.AppRunner(slot)]
    for runner, port in zip(runners, (PORT, SLOT_PORT)):
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("API on :%s, fake Lab Slot on :%s, labs: %s", PORT, SLOT_PORT, ", ".join(api["m"].labs) or "none")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
