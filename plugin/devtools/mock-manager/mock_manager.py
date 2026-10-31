"""Mock-менеджер лаб для разработки плагина: API SPEC §3 в памяти и поддельная WS-консоль Lab Slot.

Ведёт себя как настоящий менеджер 0.3 (manager/ в ветке менеджера, CONTRACTS §2) там, где это видит плагин:
- авторизация Bearer + X-Moodle-Site, ошибки {"error", "message"};
- GET /api/v1/info: options ["max_duration"], features ["consoles", "observer", "resume", …] (MOCK_RESUME=0 убирает
  "resume" — так вёл себя менеджер до 0.5.1);
- один проект на студента × активность × лабу: имя проекта постоянное (<лаба>-w<номер>), node_id те же;
- неизвестное поле в теле запроса на любом уровне — 400 unsupported_field;
- у студента одна живая лаба на сайт: та же активность и lab_ref — 200 с ней, другая — 409 session_conflict
  с объектом conflict;
- GET /sessions?user_id=&instance_id= — живая сессия или последняя завершённая, 404 — если не было;
  GET /sessions?instance_id= — список по одной записи на студента (для наблюдателя);
- сессия идёт queued → starting → running за ~5 с, со stage и progress; stop — stopping → stopped за ~2 с;
  options.max_duration задаёт expires_at; у failed-сессии error {"code", "retryable"};
- у запущенной сессии topology (properties нод пустые), nodes со статусами и nodes_stale, если включено;
- билеты консолей выдаёт настоящий шлюз (manager/gateway/console_gateway.py в ветке менеджера) через /internal/tickets;
  к остановленной ноде — 409 node_not_running (если статусы не устарели).

Лабы из Moodle (CONTRACTS §2.6 менеджера 0.5, ветка claude/determined-brown-lfgnxy):
- GET /api/v1/images — каталог образов IMAGES; info: features "images", "lab_definitions", lab_formats, limits;
- POST /api/v1/labs/validate {"lab"} — 200 {lab_def, resources, warnings} или 422 lab_invalid с problems;
- POST /api/v1/sessions с "lab" вместо "lab_ref": сессия по определению, в ней lab_def и lab_ref null.

Эталонные лабы — fixtures/*.gns3 (lab_ref = имя файла без .gns3), статусы нод — fixtures/<lab>.live.json.

Факты о нодах (CONTRACTS §2.7 менеджера 0.8, формат plugin/docs/live-state.md):
- у running-сессии features ["consoles", "facts"];
- GET /api/v1/sessions/{id}/state[?since_rev=N] — fixtures/<lab>.facts.json, ноды переведены на node_id живого проекта
  по имени, с name, status из живых статусов, collected_at; rev, generated_at, ttl FACTS_TTL; 304, если rev равен N.
  Сбор «идёт» каждые FACTS_PERIOD с: generated_at сдвигается, rev — нет (как у менеджера: время — не изменение).
  Сессия ещё не запускалась — rev 0, nodes {}, без generated_at;
- POST /api/v1/sessions/{id}/state/refresh — 202 без тела (409 session_not_running у неживой); не чаще раза в 10 с
  действительно «собирает»: rev растёт, снимок переключается между facts.json и facts-broken.json.

Поддельный Lab Slot слушает MOCK_SLOT_PORT: /v1/console/{project_id}/{node_id} с субпротоколами
["lab-slot", "bearer.<токен>"], отвечает приглашением "<имя ноды>#" и эхом. Команда "reload" закрывает
консоль (шлюз отдаст браузеру 4000). Остановленная нода не пускает в консоль. Режимы IOS (enable, configure terminal,
interface, router, exit, end, Ctrl+Z, Ctrl+U) меняют приглашение — для клавиатуры консоли.

Графическая консоль (CONTRACTS §2.4, §2.6, §6 менеджера): у ноды с console_type "vnc" вместо терминала экран.
- билет на такую ноду получает только владелец сессии; readonly: true — 403 console_disabled, шлюз не вызывается;
  в тело /internal/tickets для vnc-ноды добавляется "console": "vnc" (для остальных поля нет, как у менеджера);
- поддельный Lab Slot на такой ноде отдаёт сырой поток RFB 3.8 двоичными кадрами: security None, экран 320×200
  (цветные полосы и буквы VNC), на FramebufferUpdateRequest — raw-прямоугольник. Нажатие клавиши рисует цветной
  квадрат внизу, движение мыши — жёлтый перекрёстный маркер, щелчок — красный квадрат, ClientCutText — голубой;
- GET /mock/vnc/<node_id> — что дошло до экрана: счётчики и последние события (проверка без глаз).
Образ debian12-desktop каталога — с console_type "vnc"; в эталонной лабе ospf-multi-area это нода PVE1.

Управление для ручных проверок:
    curl -X POST localhost:8090/mock/nodes/<node_id>/stop    # нода остановлена: консоль закроется
    curl -X POST localhost:8090/mock/nodes/<node_id>/start
    curl -X POST localhost:8090/mock/fail-next -d '{"code": "SLOT_START_FAILED", "retryable": true}'
    curl -X POST localhost:8090/mock/stale/on                 # статусы нод «устарели» (nodes_stale)
    curl -X POST localhost:8090/mock/stale/off
    curl -X POST localhost:8090/mock/facts/nodes/<node_id>/fail -d '{"code": "timeout"}'  # сбор с ноды не удался
    curl -X POST localhost:8090/mock/facts/nodes/<node_id>/ok
    curl -X POST localhost:8090/mock/facts/stall/on           # сбор встал: generated_at стоит, через ttl — «устарели»
    curl -X POST localhost:8090/mock/facts/stall/off

Запуск — devtools/README.md.
"""
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import secrets
import struct
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
RESUME = os.environ.get("MOCK_RESUME", "1") != "0"  # менеджер 0.5.1: работа сохраняется всегда
QUEUED_FOR, STARTING_FOR, STOPPING_FOR = 1.0, 4.0, 2.0
FACTS_SCHEMA, FACTS_TTL, FACTS_PERIOD, FACTS_REFRESH_EVERY = "gns3lab.facts/1", 90, 30, 10  # seconds
DEFAULT_MAX_DURATION, MAX_DURATION_LIMIT = 90, 480  # minutes

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


def error(status, code, message, **extra):
    return web.json_response({"error": code, "message": message, **extra}, status=status)


class Unsupported(Exception):
    pass


def check_fields(body, allowed, where=""):
    """Strict bodies like the manager (CONTRACTS §1.2): the first unknown field is an error."""
    if not isinstance(body, dict):
        raise Unsupported(where or "body")
    for key, value in body.items():
        if key not in allowed:
            raise Unsupported(where + key)
        if isinstance(allowed[key], dict):
            check_fields(value, allowed[key], f"{where}{key}.")


SESSION_FIELDS = {"user_id": 1, "instance_id": 1, "library_lab_id": 1, "lab_ref": 1, "lab": 1, "course_id": 1,
                  "options": {"max_duration": 1, "mode": 1}, "client": {"plugin_version": 1, "moodle_release": 1}}


# Каталог образов (CONTRACTS §2.6 менеджера 0.5): у настоящего менеджера ещё файл образа, cli_prompt, qga и т. п.
# Имена портов — как у GNS3 2.2.61: iou Ethernet{adapter}/{port}, qemu Ethernet{adapter}, vpcs Ethernet0.
IMAGES = [
    {"id": "cisco-iou-l3", "title": "Cisco IOU L3 (маршрутизатор)", "kind": "iou", "category": "router",
     "symbol": ":/symbols/router.svg", "console_type": "telnet",
     "ram_mb": {"default": 1024, "min": 512, "max": 2048}, "cpus": None,
     "adapters": {"default": 2, "min": 1, "max": 4}, "port_name": "Ethernet{adapter}/{port}", "ports_per_adapter": 4},
    {"id": "cisco-iou-l2", "title": "Cisco IOU L2 (коммутатор)", "kind": "iou", "category": "switch",
     "symbol": ":/symbols/multilayer_switch.svg", "console_type": "telnet",
     "ram_mb": {"default": 1024, "min": 512, "max": 2048}, "cpus": None,
     "adapters": {"default": 1, "min": 1, "max": 4}, "port_name": "Ethernet{adapter}/{port}", "ports_per_adapter": 4},
    {"id": "debian12", "title": "Debian 12", "kind": "qemu", "category": "host",
     "symbol": ":/symbols/computer.svg", "console_type": "telnet",
     "ram_mb": {"default": 768, "min": 512, "max": 4096}, "cpus": {"default": 1, "min": 1, "max": 2},
     "adapters": {"default": 1, "min": 1, "max": 4}, "port_name": "Ethernet{adapter}", "ports_per_adapter": 1},
    {"id": "debian12-server", "title": "Debian 12 (сервер)", "kind": "qemu", "category": "server",
     "symbol": ":/symbols/server.svg", "console_type": "telnet",
     "ram_mb": {"default": 2048, "min": 1024, "max": 8192}, "cpus": {"default": 2, "min": 1, "max": 4},
     "adapters": {"default": 2, "min": 1, "max": 8}, "port_name": "Ethernet{adapter}", "ports_per_adapter": 1},
    {"id": "debian12-desktop", "title": "Debian 12 (рабочий стол)", "kind": "qemu", "category": "host",
     "symbol": ":/symbols/computer.svg", "console_type": "vnc",
     "ram_mb": {"default": 2048, "min": 1024, "max": 4096}, "cpus": {"default": 2, "min": 1, "max": 4},
     "adapters": {"default": 1, "min": 1, "max": 4}, "port_name": "Ethernet{adapter}", "ports_per_adapter": 1},
    {"id": "vpcs", "title": "VPCS", "kind": "vpcs", "category": "host",
     "symbol": ":/symbols/vpcs_guest.svg", "console_type": "telnet",
     "ram_mb": None, "cpus": None, "adapters": None, "port_name": "Ethernet{adapter}", "ports_per_adapter": 1},
]
IMAGES.extend([
    dict(next(i for i in IMAGES if i['id'] == 'debian12'), id='linux-switch', title='Linux switch',
         category='switch', capture=False, console_type='none', adapters={'default': 8, 'min': 1, 'max': 16}),
    dict(next(i for i in IMAGES if i['id'] == 'debian12'), id='lab-uplink', title='Internet',
         category='cloud', capture=False, console_type='none'),
])
LIMITS = {"max_nodes": 30, "max_memory_mb": 32768, "max_cpus": 16, "images": "*"}
LAB_FORMAT = "gns3lab.lab/1"
# Проверка определения — перенос manager/lab_manager/labdef.py (ветка менеджера claude/determined-brown-lfgnxy):
# те же правила, коды и пути замечаний, хэш без title, тот же размер CT.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
MAX_LAB_BYTES, MAX_NODES, MAX_LINKS, COORD = 256 * 1024, 100, 1000, 100_000
VPCS_MEMORY_MB, OVERHEAD_MB = 32, 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def is_int(value):
    return type(value) is int


def effective(node, image):
    rng = lambda key, fallback: node.get(key, image[key]["default"]) if image[key] else fallback  # noqa: E731
    return {"ram_mb": rng("ram_mb", VPCS_MEMORY_MB), "cpus": rng("cpus", 1), "adapters": rng("adapters", 1)}


def resources_of(nodes, images):
    memory = cores_qemu = iou = vpcs = 0
    for node in nodes:
        image = images[node["image"]]
        values = effective(node, image)
        memory += values["ram_mb"]
        if image["kind"] == "qemu":
            cores_qemu += values["cpus"]
        elif image["kind"] == "iou":
            iou += 1
        else:
            vpcs += 1
    return {"cores": max(2, cores_qemu + math.ceil(iou / 2) + math.ceil(vpcs / 8)),
            "memory_mb": math.ceil((memory * 1.1 + OVERHEAD_MB) / 256) * 256}


def lab_problems(lab):
    """Checks a definition like the manager. Returns (problems, normalised definition with _resources)."""
    problems = []
    images = {i["id"]: i for i in IMAGES if LIMITS["images"] == "*" or i.get("derived", {}).get("base", i["id"]) in LIMITS["images"]}

    def bad(path, code, message=""):
        problems.append({"path": path, "code": code, "message": message})

    def unknown(obj, allowed, path):
        for key in sorted(set(obj) - allowed):
            bad(f"{path}.{key}" if path else key, "unknown_field")

    if not isinstance(lab, dict):
        return [{"path": "", "code": "bad_type", "message": "object expected"}], None
    if len(canonical(lab).encode()) > MAX_LAB_BYTES:
        return [{"path": "", "code": "too_large", "message": f"max {MAX_LAB_BYTES} bytes"}], None
    unknown(lab, {"format", "title", "nodes", "links"}, "")
    if lab.get("format") != LAB_FORMAT:
        bad("format", "required" if "format" not in lab else "bad_format", LAB_FORMAT)
    title = lab.get("title", "")
    if not isinstance(title, str) or len(title) > 200:
        bad("title", "bad_type", "string up to 200 characters")
    nodes, links = lab.get("nodes"), lab.get("links", [])
    if not isinstance(nodes, list) or not nodes:
        bad("nodes", "required" if nodes is None else "bad_type", "non-empty list")
        return problems, None
    if not isinstance(links, list):
        bad("links", "bad_type", "list")
        links = []
    max_nodes = min(MAX_NODES, LIMITS["max_nodes"])
    if len(nodes) > max_nodes:
        bad("nodes", "too_many_nodes", f"max {max_nodes}")
    if len(links) > MAX_LINKS:
        bad("links", "too_large", f"max {MAX_LINKS} links")
    out_nodes, by_name = [], {}
    for i, node in enumerate(nodes[:MAX_NODES]):
        path = f"nodes[{i}]"
        if not isinstance(node, dict):
            bad(path, "bad_type", "object expected")
            continue
        unknown(node, {"name", "image", "x", "y", "ram_mb", "cpus", "adapters"}, path)
        out, ok = {}, True
        name = node.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            bad(f"{path}.name", "required" if name is None else "bad_name", NAME_RE.pattern)
            ok = False
        elif name in by_name:
            bad(f"{path}.name", "duplicate_name", name)
            ok = False
        image_id = node.get("image")
        image = images.get(image_id) if isinstance(image_id, str) else None
        if image_id is None:
            bad(f"{path}.image", "required")
        elif image is None:
            known = any(i["id"] == image_id for i in IMAGES)
            bad(f"{path}.image", "image_not_allowed" if known else "unknown_image", str(image_id)[:64])
        for axis in ("x", "y"):
            value = node.get(axis, 0)
            if not is_int(value) or not -COORD <= value <= COORD:
                bad(f"{path}.{axis}", "out_of_range", f"integer -{COORD}..{COORD}")
            else:
                out[axis] = value
        if image is not None:
            for key in ("ram_mb", "cpus", "adapters"):
                if key not in node:
                    continue
                rng, value = image[key], node[key]
                if rng is None:
                    bad(f"{path}.{key}", "unknown_field", f"{image['id']} has no {key}")
                elif not is_int(value) or not rng["min"] <= value <= rng["max"]:
                    bad(f"{path}.{key}", "out_of_range", f"{rng['min']}..{rng['max']}")
                else:
                    out[key] = value
        if ok and image is not None:
            out = {"name": name, "image": image["id"], **out}
            by_name[name] = out
            out_nodes.append(out)
    out_links, used = [], set()
    for i, link in enumerate(links[:MAX_LINKS]):
        path = f"links[{i}]"
        if not isinstance(link, dict):
            bad(path, "bad_type", "object expected")
            continue
        unknown(link, {"a", "b"}, path)
        ends = []
        for side in ("a", "b"):
            ep, epath = link.get(side), f"{path}.{side}"
            if not isinstance(ep, dict):
                bad(epath, "required" if ep is None else "bad_type")
                continue
            unknown(ep, {"node", "adapter", "port"}, epath)
            node_name, adapter, port = ep.get("node"), ep.get("adapter", 0), ep.get("port", 0)
            target = by_name.get(node_name) if isinstance(node_name, str) else None
            if target is None:
                if not any(isinstance(n, dict) and n.get("name") == node_name for n in nodes):
                    bad(f"{epath}.node", "unknown_node", str(node_name)[:80])
                continue
            image = images[target["image"]]
            adapters, per = effective(target, image)["adapters"], image["ports_per_adapter"]
            if not (is_int(adapter) and is_int(port) and 0 <= adapter < adapters and 0 <= port < per):
                bad(epath, "bad_port", f"adapter 0..{adapters - 1}, port 0..{per - 1}")
                continue
            key = (node_name, adapter, port)
            if key in used:
                bad(epath, "port_in_use", f"{node_name} {adapter}/{port}")
                continue
            used.add(key)
            ends.append({"node": node_name, "adapter": adapter, "port": port})
        if len(ends) == 2:
            if ends[0]["node"] == ends[1]["node"]:
                bad(path, "self_link", ends[0]["node"])
            else:
                out_links.append({"a": ends[0], "b": ends[1]})
    if problems:
        return problems, None
    resources = resources_of(out_nodes, images)
    if LIMITS["max_memory_mb"] is not None and resources["memory_mb"] > LIMITS["max_memory_mb"]:
        bad("nodes", "limit_exceeded", f"memory {resources['memory_mb']} > {LIMITS['max_memory_mb']} MiB")
    if LIMITS["max_cpus"] is not None and resources["cores"] > LIMITS["max_cpus"]:
        bad("nodes", "limit_exceeded", f"cores {resources['cores']} > {LIMITS['max_cpus']}")
    if problems:
        return problems, None
    return [], {"format": LAB_FORMAT, "title": title, "nodes": out_nodes, "links": out_links, "_resources": resources}


def lab_hash(normal):
    """sha256 of the normalised definition without its title and without the mock's own fields."""
    body = {k: v for k, v in normal.items() if k != "title" and not k.startswith("_")}
    return "sha256:" + hashlib.sha256(canonical(body).encode()).hexdigest()


def lab_document(normal, lab_def):
    """The .gns3-like document the slot would report for a lab built from a definition."""
    images = {i["id"]: i for i in IMAGES}
    ids = {n["name"]: str(uuid.uuid5(uuid.NAMESPACE_URL, lab_def + "/" + n["name"])) for n in normal["nodes"]}
    nodes = []
    for n in normal["nodes"]:
        image = images[n["image"]]
        ports = []
        for adapter in range(effective(n, image)["adapters"]):
            for port in range(image["ports_per_adapter"]):
                name = image["port_name"].replace("{adapter}", str(adapter)).replace("{port}", str(port))
                short = "e" + name[8:] if name.startswith("Ethernet") else name
                ports.append({"adapter_number": adapter, "port_number": port, "name": name, "short_name": short})
        nodes.append({"node_id": ids[n["name"]], "name": n["name"], "node_type": image["kind"],
                      "symbol": image["symbol"], "console_type": image["console_type"], "x": n["x"], "y": n["y"],
                      "width": 60, "height": 45, "ports": ports, "properties": {}})
    links = [{"link_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{lab_def}/link/{i}")),
              "nodes": [{"node_id": ids[e["node"]], "adapter_number": e["adapter"], "port_number": e["port"]}
                        for e in (link["a"], link["b"])]}
             for i, link in enumerate(normal["links"])]
    return {"name": normal["title"], "topology": {"nodes": nodes, "links": links, "drawings": []}}


def load_labs():
    labs = {}
    for path in sorted(FIXTURES.glob("*.gns3")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        live = path.with_suffix(".live.json")
        statuses = json.loads(live.read_text(encoding="utf-8")).get("node_status", {}) if live.exists() else {}
        facts = {}
        for variant, suffix in (("ok", ".facts.json"), ("broken", ".facts-broken.json")):
            file = path.with_name(path.stem + suffix)
            if file.exists():
                facts[variant] = json.loads(file.read_text(encoding="utf-8"))
        labs[path.stem] = {"doc": doc, "statuses": statuses, "facts": facts}
    return labs


class Session:
    def __init__(self, site, user_id, instance_id, lab_ref, course_id, max_duration, fail=None, lab_def=None):
        self.id = "s-" + secrets.token_hex(8)
        self.mode = None
        self.library_lab_id = None
        self.definition = None
        self.capture = None
        self.node_statuses = {}
        self.site, self.user_id, self.instance_id, self.lab_ref = site, user_id, instance_id, lab_ref
        self.lab_def = lab_def  # sha256:… for a lab from Moodle, then lab_ref is None
        self.key = lab_def or lab_ref  # key in Manager.labs
        self.workspace = 0  # number of the student's project, set by the manager
        self.course_id = course_id
        self.max_duration = max_duration * 60
        self.fail = fail  # {"code", "retryable"}: the session fails half way through starting
        self.project_id = str(uuid.uuid4())
        self.created = time.time()
        self.stop_requested = None
        self.stopped_by_teacher = False
        self.facts_rev = 1  # rev of the facts once the lab runs; 0 is reported before that
        self.facts_variant = "ok"  # fixture shown: ok or broken, switched by a refresh
        self.facts_refreshed = None  # time of the last refresh that really collected

    def status(self):
        return self.state()[0]

    def state(self):
        """(status, stage, progress or None)."""
        now = time.time()
        if self.stop_requested is not None:
            if now - self.stop_requested >= STOPPING_FOR:
                return "stopped", "stopped", None
            return "stopping", "lab_stop", None
        age = now - self.created
        if age < QUEUED_FOR:
            return "queued", "wait_slot", None
        if age < QUEUED_FOR + STARTING_FOR:
            part = (age - QUEUED_FOR) / STARTING_FOR
            if self.fail and part >= 0.5:
                return "failed", "failed", None
            if part < 0.4:
                return "starting", "ct_start", None
            return "starting", "lab_wait", int((part - 0.4) / 0.6 * 100)
        if self.fail:
            return "failed", "failed", None
        if age > QUEUED_FOR + STARTING_FOR + self.max_duration:
            return "stopped", "stopped", None
        return "running", "ready", None

    @property
    def started(self):
        return self.created + QUEUED_FOR + STARTING_FOR


class Manager:
    def __init__(self):
        self.labs = load_labs()
        self.sessions: list[Session] = []
        self.started_nodes = {}
        self.shutdown_errors = {}
        self.shutdown_supported = True
        self.workspaces: dict = {}  # (site, user, activity, lab) -> project number
        self.stopped_nodes: set[str] = set()
        self.fail_next = None
        self.capture_fail = os.environ.get('MOCK_CAPTURE_FAIL') == '1'
        self.stale = False
        self.fact_errors: dict = {}  # node_id -> (code, since): the collection from the node fails
        self.fact_changes = 0  # changes of fact_errors: they move rev of every session
        self.facts_stalled = None  # time the collection stopped, None while it works

    def live(self, site, user_id, library_lab_id=None):
        return next((s for s in self.sessions if s.site == site and s.user_id == user_id and s.library_lab_id == library_lab_id
                     and s.status() not in ("stopped", "failed")), None)

    def latest(self, site, user_id, instance_id):
        mine = [s for s in self.sessions if (s.site, s.user_id, s.instance_id) == (site, user_id, instance_id)]
        return mine[-1] if mine else None

    def node_status(self, lab, node_id):
        if node_id in self.stopped_nodes:
            return "stopped"
        return lab["statuses"].get(node_id, "started")

    def observe(self, s):
        if s.mode == 'authoring' and s.status() == 'running':
            s.node_statuses = {n['name']: self.node_status(self.labs[s.key], n['node_id'])
                               for n in self.labs[s.key]['doc']['topology']['nodes']}
            self.started_nodes.setdefault(s.workspace, set()).update(name for name, status in s.node_statuses.items()
                                                                    if status == 'started')

    def summary(self, s):
        self.observe(s)
        status, stage, progress = s.state()
        message = MESSAGES.get(status, "Лаба не запустилась")
        if status in ("stopping", "stopped") and s.stopped_by_teacher:
            message = "Лаба остановлена преподавателем"
        body = {
            "schema": "gns3lab.session/1",
            "session_id": s.id,
            "lab_ref": s.lab_ref,
            "lab_def": s.lab_def,
            "status": status,
            "stage": stage,
            "status_message": message + (f" {progress}%" if progress is not None else ""),
            "error": dict(s.fail) if status == "failed" else None,
            "started_at": iso(s.started) if status in ("running", "stopping", "stopped") else None,
            "expires_at": iso(s.started + s.max_duration) if status in ("running", "stopping") else None,
        }
        if progress is not None:
            body["progress"] = progress
        if s.mode:
            body['mode'] = s.mode
            body['capture_nodes'] = [{'name': n['name'], 'status': s.node_statuses.get(n['name'], 'unknown')}
                                     for n in (s.definition or {}).get('nodes', [])
                                     if n['name'] in self.started_nodes.get(s.workspace, set())
                                     and next((i for i in IMAGES if i['id'] == n['image']), {}).get('kind') == 'qemu'
                                     and next((i for i in IMAGES if i['id'] == n['image']), {}).get('capture') is not False]
        return body

    def present(self, s):
        body = self.summary(s)
        status = body["status"]
        body["features"] = ["consoles", "facts"] if status == "running" else []
        if status == "running":
            lab = self.labs[s.key]
            doc = json.loads(json.dumps(lab["doc"]))
            doc["name"] = f"{s.lab_ref or 'lab-' + s.lab_def[7:15]}-w{s.workspace}"
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
            if self.stale:
                body["nodes_stale"] = True
        return body

    def facts(self, s):
        """The gns3lab.facts/1 snapshot of a session (CONTRACTS §2.7), like build_state of the manager."""
        lab = self.labs[s.key]
        now = time.time()
        if not lab["facts"] or now < s.started or s.fail:
            return {"schema": FACTS_SCHEMA, "rev": 0, "ttl": FACTS_TTL, "nodes": {}}
        # The slot collects every FACTS_PERIOD seconds while the lab runs and the collection is not stalled.
        end = min(x for x in (now, s.stop_requested, self.facts_stalled) if x is not None)
        generated = s.started + max(0, (end - s.started) // FACTS_PERIOD) * FACTS_PERIOD
        if s.facts_refreshed and s.facts_refreshed <= end:
            generated = max(generated, s.facts_refreshed)
        fixture = lab["facts"].get(s.facts_variant) or lab["facts"]["ok"]
        names = {n["node_id"]: n["name"] for n in lab["doc"]["topology"]["nodes"]}  # reference project
        live = {n["name"]: n["node_id"] for n in lab["doc"]["topology"]["nodes"]}  # running project
        running = s.status() == "running"
        nodes = {}
        for ref_id, facts in fixture["nodes"].items():
            node_id = live.get(names.get(ref_id))
            if node_id is None:
                continue
            node = json.loads(json.dumps(facts))
            node["name"] = names[ref_id]
            node["status"] = self.node_status(lab, node_id) if running else "stopped"
            node["collected_at"] = iso(generated)
            if node_id in self.fact_errors:
                code, since = self.fact_errors[node_id]
                node["error"] = {"code": code, "message": "mock: collection failed"}
                node["collected_at"] = iso(min(since, generated))
            nodes[node_id] = node
        return {"schema": FACTS_SCHEMA, "rev": s.facts_rev + self.fact_changes, "generated_at": iso(generated),
                "ttl": FACTS_TTL, "nodes": nodes}

    def node_name(self, node_id):
        for lab in self.labs.values():
            for n in lab["doc"]["topology"]["nodes"]:
                if n["node_id"] == node_id:
                    return n["name"], lab
        return None, None

    def node_console(self, node_id):
        """console_type of a node in the lab documents: telnet, vnc, or none for everything else."""
        for lab in self.labs.values():
            for n in lab["doc"]["topology"]["nodes"]:
                if n["node_id"] == node_id:
                    return n.get("console_type") if n.get("console_type") in ("telnet", "vnc") else "none"
        return "none"


def site_of(request):
    site = request.headers.get("X-Moodle-Site", "").rstrip("/")
    if not site or request.headers.get("Authorization") != f"Bearer {SECRET}":
        raise web.HTTPUnauthorized(text=json.dumps({"error": "unauthorized", "message": "Invalid credentials"}),
                                   content_type="application/json")
    return site


async def info(request):
    site_of(request)
    features = ["consoles", "observer", "images", "lab_definitions", "image_capture"] + (["resume"] if RESUME else [])
    return web.json_response({"schema": "gns3lab.info/1", "version": "0.14.0-mock",
                              "session_schemas": ["gns3lab.session/1"], "options": ["max_duration", "mode"],
                              "features": features, "lab_formats": [LAB_FORMAT], "limits": LIMITS})


async def images(request):
    site_of(request)
    allowed = LIMITS["images"]
    return web.json_response({"images": [i for i in IMAGES if allowed == "*" or i.get("derived", {}).get("base", i["id"]) in allowed]})


async def validate_lab(request):
    site_of(request)
    try:
        body = await read_body(request, {"lab": 1})
    except Unsupported as exc:
        return error(400, "unsupported_field", f"Unsupported field: {exc}")
    except json.JSONDecodeError:
        return error(400, "invalid_request", "Body is not JSON")
    problems, normal = lab_problems(body.get("lab"))
    if problems:
        return error(422, "lab_invalid", f"{len(problems)} problem(s) in the lab", problems=problems)
    return web.json_response({"lab_def": lab_hash(normal), "resources": normal["_resources"], "warnings": []})


async def read_body(request, allowed):
    """JSON body checked like the manager does; an empty body is an empty object."""
    raw = await request.text()
    body = json.loads(raw) if raw.strip() else {}
    check_fields(body, allowed)
    return body


async def list_labs(request):
    site_of(request)
    m: Manager = request.app["m"]
    return web.json_response([{"lab_ref": ref, "title": lab["doc"].get("name", ref), "description": ""}
                              for ref, lab in m.labs.items() if not ref.startswith("sha256:")])


async def lab_topology(request):
    site_of(request)
    ref = request.match_info["ref"]
    lab = None if ref.startswith("sha256:") else request.app["m"].labs.get(ref)
    return web.json_response(lab["doc"]) if lab else error(404, "lab_not_found", "Unknown lab_ref")


async def find_session(request):
    site = site_of(request)
    m: Manager = request.app["m"]
    try:
        if 'library_lab_id' in request.query:
            library = int(request.query['library_lab_id'])
            user = int(request.query['user_id'])
            sessions = [s for s in m.sessions if s.site == site and s.user_id == user and s.library_lab_id == library]
            return web.json_response(m.present(sessions[-1])) if sessions else error(404, 'session_not_found', 'No authoring session')
        instance_id = int(request.query["instance_id"])
        user_id = int(request.query["user_id"]) if "user_id" in request.query else None
    except (KeyError, ValueError):
        return error(400, "invalid_request", "Invalid fields: user_id, instance_id")
    if user_id is None:
        # Observer page: one entry per student, the live session or the last one.
        users = {s.user_id for s in m.sessions if (s.site, s.instance_id) == (site, instance_id)}
        rows = []
        for user in sorted(users):
            live = m.live(site, user)
            rows.append(live if live and live.instance_id == instance_id else m.latest(site, user, instance_id))
        return web.json_response([dict(m.summary(s), user_id=s.user_id) for s in rows])
    s = m.latest(site, user_id, instance_id)
    return web.json_response(m.present(s)) if s else error(404, "session_not_found", "No session")


async def create_session(request):
    site = site_of(request)
    m: Manager = request.app["m"]
    try:
        body = await read_body(request, SESSION_FIELDS)
        mode = (body.get('options') or {}).get('mode')
        if mode not in (None, 'authoring'):
            raise ValueError('Unknown mode')
        authoring = mode == 'authoring'
        library = int(body['library_lab_id']) if authoring else None
        if (authoring and ('instance_id' in body or 'lab' not in body or library <= 0)) or (not authoring and 'library_lab_id' in body):
            raise ValueError('Invalid authoring identity')
        user_id, instance_id = int(body['user_id']), -library if authoring else int(body['instance_id'])
        if ("lab_ref" in body) == ("lab" in body):
            raise KeyError("lab_ref or lab")
        lab_ref = str(body["lab_ref"]) if "lab_ref" in body else None
    except Unsupported as exc:
        return error(400, "unsupported_field", f"Unsupported field: {exc}")
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return error(400, "invalid_request", "Invalid fields: user_id, instance_id, lab_ref or lab")
    if authoring and any(x.capture and x.capture['state'] == 'running' for x in m.sessions
                         if x.site == site and x.user_id == user_id and x.library_lab_id == library):
        return error(409, 'capture_in_progress', 'Workspace capture is running')
    lab_def = None
    if lab_ref is None:
        problems, normal = lab_problems(body["lab"])
        if problems:
            return error(422, "lab_invalid", f"{len(problems)} problem(s) in the lab", problems=problems)
        lab_def = lab_hash(normal)
        m.labs.setdefault(lab_def, {"doc": lab_document(normal, lab_def), "statuses": {}, "facts": {}})
    live = m.live(site, user_id, library)
    if live:
        if (live.instance_id, live.key) != (instance_id, lab_def or lab_ref):
            return error(409, "session_conflict", "У студента уже запущена другая лаба; сначала остановите её",
                         conflict={"instance_id": live.instance_id, "course_id": live.course_id,
                                   "lab_ref": live.lab_ref, "lab_def": live.lab_def, "status": live.status()})
        return web.json_response(m.present(live), status=200)
    if lab_ref is not None and lab_ref not in m.labs:
        return error(404, "lab_not_found", "Unknown lab_ref")
    requested = (body.get("options") or {}).get("max_duration") or 0
    max_duration = min(int(requested or 240), 240) if authoring else (min(int(requested), MAX_DURATION_LIMIT) if requested else DEFAULT_MAX_DURATION)
    s = Session(site, user_id, instance_id, lab_ref, body.get("course_id"), max_duration, m.fail_next, lab_def)
    s.mode = 'authoring' if authoring else None
    s.library_lab_id = library
    s.definition = body.get('lab')
    m.fail_next = None
    s.workspace = m.workspaces.setdefault((site, user_id, instance_id, s.key), len(m.workspaces) + 1)
    for node in m.labs[s.key]['doc']['topology']['nodes']:
        m.stopped_nodes.discard(node['node_id'])
    m.sessions.append(s)
    log.info("session %s: user %s, activity %s, lab %s, %s min, client %s", s.id, user_id, instance_id, s.key,
             max_duration, body.get("client"))
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
    try:
        body = await read_body(request, {"requested_by_user_id": 1})
    except Unsupported as exc:
        return error(400, "unsupported_field", f"Unsupported field: {exc}")
    if s.status() not in ("stopped", "failed") and s.stop_requested is None:
        request.app['m'].observe(s)
        s.stop_requested = time.time()
        by = body.get("requested_by_user_id")
        s.stopped_by_teacher = by is not None and by != s.user_id
    return web.json_response(request.app["m"].present(s))


async def image_capture(request):
    site = site_of(request)
    session = own(request, site)
    if request.method == 'GET':
        return web.json_response(session.capture) if session.capture else error(404, 'capture_not_found', 'No capture')
    if session.mode != 'authoring':
        return error(409, 'not_authoring', 'Authoring session required')
    if session.capture and session.capture['state'] in ('running', 'done'):
        code = 'capture_in_progress' if session.capture['state'] == 'running' else 'capture_done'
        return error(409, code, code)
    if session.status() not in ('running', 'stopped'):
        return error(409, 'session_not_running', 'Session must run or be stopped')
    body = await request.json()
    force = body.get('force', False)
    if type(force) is not bool:
        return error(400, 'invalid_request', 'force must be boolean')
    manager = request.app['m']
    manager.observe(session)
    selected = body.get('nodes')
    names = {n['name']: n for n in (session.definition or {}).get('nodes', [])}
    if (not isinstance(selected, list) or not 1 <= len(selected) <= 10 or
            len({n.get('name') for n in selected}) != len(selected)):
        return error(400, 'invalid_request', 'Invalid node selection')
    for n in selected:
        image = next((i for i in IMAGES if i['id'] == names.get(n.get('name'), {}).get('image')), None)
        if not image or image['kind'] != 'qemu' or image.get('capture') is False:
            return error(422, 'lab_invalid', 'Node cannot be captured')
        if n['name'] not in manager.started_nodes.get(session.workspace, set()):
            return error(422, 'lab_invalid', 'Node never started', problems=[{'code': 'never_started'}])
    unsafe = [n['name'] for n in selected if session.node_statuses.get(n['name']) != 'stopped'] if session.status() == 'stopped' else []
    if unsafe and not force:
        return error(409, 'guest_not_stopped', 'Start the lab again or save as is',
                     nodes=[{'name': name, 'reason': 'SESSION_STOPPED'} for name in unsafe])
    session.capture = {'schema': 'gns3lab.capture/1', 'state': 'running', 'stage': 'quiesce', 'error': None}

    async def finish():
        forced = list(unsafe)
        if session.status() == 'running':
            await asyncio.sleep(0.15)
            errors = []
            ids = {n['name']: n['node_id'] for n in manager.labs[session.key]['doc']['topology']['nodes']}
            manager.observe(session)
            for item in selected:
                name = item['name']
                if session.node_statuses.get(name) == 'stopped':
                    continue
                reason = manager.shutdown_errors.get(name) if manager.shutdown_supported else 'GUEST_SHUTDOWN_UNSUPPORTED'
                if reason:
                    errors.append({'name': name, 'reason': reason})
                else:
                    manager.stopped_nodes.add(ids[name])
                    for ws in list(request.app['consoles'].get(ids[name], ())):
                        await ws.close()
            manager.observe(session)
            if errors and not force:
                session.capture = {'schema': 'gns3lab.capture/1', 'state': 'failed', 'stage': 'quiesce',
                                   'error': {'code': 'GUEST_NOT_STOPPED', 'retryable': True, 'nodes': errors}}
                return
            forced = [item['name'] for item in errors]
        session.capture['stage'] = 'stopping'
        session.stop_requested = time.time()
        await asyncio.sleep(float(os.environ.get('MOCK_CAPTURE_DELAY', '3')) + STOPPING_FOR)
        if request.app['m'].capture_fail:
            session.capture = {'schema': 'gns3lab.capture/1', 'state': 'failed',
                               'error': {'code': 'CAPTURE_NODE_MISSING', 'retryable': False, 'node': selected[-1]['name']}}
            return
        staged, result = [], {}
        for n in selected:
            parent = next(i for i in IMAGES if i['id'] == names[n['name']]['image'])
            base_id = parent.get('derived', {}).get('base', parent['id'])
            base = next(i for i in IMAGES if i['id'] == base_id)
            identity = 'd-' + secrets.token_hex(6)
            image = dict(base, id=identity, title=n['title'], featured=False, retired=False,
                         derived={'base': base_id, 'base_sha256': 'sha256:' + 'a' * 64,
                                  'sha256': 'sha256:' + secrets.token_hex(32), 'size': 1048576,
                                  'author_user_id': session.user_id, 'published_at': iso(time.time()), 'forced': n['name'] in forced,
                                  'description': n.get('description', ''),
                                  'source': {'library_lab_id': session.library_lab_id, 'node': n['name']},
                                  'supersedes': parent['id'] if parent.get('derived', {}).get('author_user_id') == session.user_id and parent['derived']['source'] == {'library_lab_id': session.library_lab_id, 'node': n['name']} else None})
            previous = [i for i in IMAGES if i.get('derived', {}).get('author_user_id') == session.user_id and i['derived']['source'] == {'library_lab_id': session.library_lab_id, 'node': n['name']}]
            image['derived']['supersedes'] = previous[-1]['id'] if previous else None
            staged.append(image)
            result[n['name']] = identity
        IMAGES.extend(staged)
        session.capture = {'schema': 'gns3lab.capture/1', 'state': 'done', 'images': result, 'forced': forced, 'error': None}
    task = asyncio.create_task(finish())
    request.app['capture_tasks'].add(task)
    task.add_done_callback(request.app['capture_tasks'].discard)
    return web.json_response(session.capture, status=202)


async def mock_capture_failure(request):
    request.app['m'].capture_fail = request.match_info['mode'] == 'on'
    return web.json_response({'fail': request.app['m'].capture_fail})


async def mock_shutdown_failure(request):
    body = await request.json()
    request.app['m'].shutdown_errors = body.get('nodes', {})
    request.app['m'].shutdown_supported = body.get('supported', True)
    return web.json_response({'nodes': request.app['m'].shutdown_errors})


async def image_patch(request):
    site_of(request)
    image = next((i for i in IMAGES if i['id'] == request.match_info['image']), None)
    if image is None:
        return error(404, 'not_found', 'Unknown image')
    if not image.get('derived'):
        return error(409, 'image_static', 'Static image')
    body = await read_body(request, {'title': 1, 'description': 1, 'featured': 1, 'retired': 1})
    if not body:
        return error(400, 'invalid_request', 'Empty image patch')
    for key, value in body.items():
        if key == 'description':
            image['derived'][key] = value
        else:
            image[key] = value
    return web.json_response(image)


async def console_ticket(request):
    s = own(request, site_of(request))
    m: Manager = request.app["m"]
    try:
        body = await read_body(request, {"node_id": 1, "readonly": 1, "issued_for_user_id": 1})
    except Unsupported as exc:
        return error(400, "unsupported_field", f"Unsupported field: {exc}")
    if s.status() != "running":
        return error(409, "session_not_running", "Session is not running")
    node_id = body.get("node_id")
    if node_id not in {n["node_id"] for n in m.labs[s.key]["doc"]["topology"]["nodes"]}:
        return error(404, "node_not_found", "Node does not belong to this session")
    if not m.stale and node_id in m.stopped_nodes:
        return error(409, "node_not_running", "Node is not running")
    readonly = bool(body.get("readonly"))
    screen = m.node_console(node_id) == "vnc"
    if screen and readonly:
        # The manager does not show the screen of a student to the teacher (CONTRACTS §2.4).
        return error(403, "console_disabled", "The screen of a node is shown to the owner of the lab only")
    grant = {"slot": SLOT_ADDRESS, "project_id": s.project_id, "nodes": [node_id], "readonly": readonly,
             "who": f"{s.id}:{'observer' if readonly else 'owner'}"}
    if screen:
        grant["console"] = "vnc"  # CONTRACTS §6: only for vnc, the tickets of text consoles stay as they were
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


async def session_facts(request):
    s = own(request, site_of(request))
    doc = request.app["m"].facts(s)
    since = request.query.get("since_rev")
    if since is not None and since.isdigit() and int(since) == doc["rev"]:
        return web.Response(status=304)
    return web.json_response(doc)


async def refresh_facts(request):
    s = own(request, site_of(request))
    if s.status() != "running":
        return error(409, "session_not_running", "Session is not running")
    now = time.time()
    # More often than every 10 s a refresh does not start a new collection, the answer is 202 all the same.
    if s.facts_refreshed is None or now - s.facts_refreshed >= FACTS_REFRESH_EVERY:
        s.facts_refreshed = now
        s.facts_rev += 1
        s.facts_variant = "broken" if s.facts_variant == "ok" else "ok"
        log.info("session %s: facts refreshed, rev %s, %s", s.id, s.facts_rev, s.facts_variant)
    return web.Response(status=202)


async def mock_facts_node(request):
    m: Manager = request.app["m"]
    node_id, action = request.match_info["node"], request.match_info["action"]
    body = await request.json() if (await request.text()).strip() else {}
    if action == "fail":
        m.fact_errors[node_id] = (str(body.get("code", "unreachable")), time.time())
    else:
        m.fact_errors.pop(node_id, None)
    m.fact_changes += 1
    return web.json_response({"node_id": node_id, "error": m.fact_errors.get(node_id, (None,))[0]})


async def mock_facts_stall(request):
    m: Manager = request.app["m"]
    m.facts_stalled = time.time() if request.match_info["mode"] == "on" else None
    return web.json_response({"stalled": m.facts_stalled is not None})


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


async def mock_fail_next(request):
    body = await request.json() if (await request.text()).strip() else {}
    m: Manager = request.app["m"]
    m.fail_next = {"code": str(body.get("code", "SLOT_START_FAILED")), "retryable": bool(body.get("retryable", True))}
    return web.json_response({"fail_next": m.fail_next})


async def mock_stale(request):
    request.app["m"].stale = request.match_info["mode"] == "on"
    return web.json_response({"nodes_stale": request.app["m"].stale})


# ---------------------------------------------------------------- поддельный экран VNC (RFB 3.8)
class ProtocolError(Exception):
    pass


class FakeRfb:
    """A minimal RFB 3.8 server for the graphical console: security None, a small screen, raw rectangles.

    feed() takes the bytes that came from the browser and returns the frames to send back. The picture is made to be
    recognised on a screenshot, and input shows on it: a key press adds a coloured square along the bottom, the pointer
    is a yellow cross, a click leaves a red square, ClientCutText a cyan one. Everything that arrived is also kept in
    `events` (GET /mock/vnc/<node_id>).
    """
    W, H = 320, 200
    # noVNC asks for 32 bits per pixel, little endian, red shift 0, green 8, blue 16: the bytes of a pixel are R, G, B, 0.
    FONT = {  # 5x7 letters of the logo
        "V": ["X...X", "X...X", "X...X", "X...X", "X...X", ".X.X.", "..X.."],
        "N": ["X...X", "XX..X", "X.X.X", "X..XX", "X...X", "X...X", "X...X"],
        "C": [".XXX.", "X...X", "X....", "X....", "X....", "X...X", ".XXX."],
    }
    BARS = [(235, 235, 235), (235, 235, 16), (16, 235, 235), (16, 235, 16), (235, 16, 235), (235, 16, 16), (16, 16, 235),
            (16, 16, 16)]

    def __init__(self, name):
        self.name = name
        self.stage = "version"
        self.inbuf = bytearray()
        self.base = bytearray(self.W * self.H * 4)
        self.want = False  # an incremental request waits for a change
        self.dirty = None  # [x0, y0, x1, y1] of what changed since the last update
        self.cross = None
        self.keys = 0
        self.counts = {"key": 0, "pointer": 0, "click": 0, "cut": 0}
        self.events = []
        self.mask = 0
        self.draw_logo()
        self.fb = bytearray(self.base)

    # -------- drawing
    def fill(self, buf, x, y, w, h, rgb):
        x0, y0, x1, y1 = max(0, x), max(0, y), min(self.W, x + w), min(self.H, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes((rgb[0], rgb[1], rgb[2], 0)) * (x1 - x0)
        for yy in range(y0, y1):
            buf[(yy * self.W + x0) * 4:(yy * self.W + x1) * 4] = row

    def draw_logo(self):
        for yy in range(self.H):  # a dark blue gradient
            self.fill(self.base, 0, yy, self.W, 1, (10, 24 + yy // 8, 48 + yy // 4))
        bar = self.W // len(self.BARS)
        for i, rgb in enumerate(self.BARS):
            self.fill(self.base, i * bar, 0, bar, 60, rgb)
        scale, gap, top = 8, 8, 76
        left = (self.W - (3 * 5 * scale + 2 * gap)) // 2
        for n, letter in enumerate("VNC"):
            for row, line in enumerate(self.FONT[letter]):
                for col, ch in enumerate(line):
                    if ch == "X":
                        self.fill(self.base, left + n * (5 * scale + gap) + col * scale, top + row * scale, scale, scale,
                                  (250, 250, 250))
        self.fill(self.base, 0, 0, self.W, 2, (255, 140, 0))  # the frame
        self.fill(self.base, 0, self.H - 2, self.W, 2, (255, 140, 0))
        self.fill(self.base, 0, 0, 2, self.H, (255, 140, 0))
        self.fill(self.base, self.W - 2, 0, 2, self.H, (255, 140, 0))

    def touch(self, x0, y0, x1, y1):
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(self.W, x1), min(self.H, y1)
        d = self.dirty
        self.dirty = [x0, y0, x1, y1] if d is None else [min(d[0], x0), min(d[1], y0), max(d[2], x1), max(d[3], y1)]

    def mark(self, x, y, size, rgb):
        """A square that stays on the picture."""
        self.fill(self.base, x, y, size, size, rgb)
        self.fill(self.fb, x, y, size, size, rgb)
        self.touch(x, y, x + size, y + size)

    def move_cross(self, x, y):
        if self.cross:  # put back what the old cross covered
            ox, oy = self.cross
            for yy in range(max(0, oy - 7), min(self.H, oy + 8)):
                a, b = (yy * self.W + max(0, ox - 7)) * 4, (yy * self.W + min(self.W, ox + 8)) * 4
                self.fb[a:b] = self.base[a:b]
            self.touch(ox - 7, oy - 7, ox + 8, oy + 8)
        self.cross = (x, y)
        self.fill(self.fb, x - 6, y - 1, 13, 3, (255, 230, 0))
        self.fill(self.fb, x - 1, y - 6, 3, 13, (255, 230, 0))
        self.touch(x - 7, y - 7, x + 8, y + 8)

    # -------- protocol
    def record(self, kind, **fields):
        self.counts[kind] += 1
        self.events.append({"type": kind, **fields})
        del self.events[:-200]

    def update(self, x, y, w, h):
        x, y = max(0, min(x, self.W)), max(0, min(y, self.H))
        w, h = min(w, self.W - x), min(h, self.H - y)
        head = struct.pack(">BxH", 0, 1) + struct.pack(">HHHHi", x, y, w, h, 0)
        rows = b"".join(bytes(self.fb[(yy * self.W + x) * 4:(yy * self.W + x + w) * 4]) for yy in range(y, y + h))
        self.dirty, self.want = None, False
        return head + rows

    def feed(self, data):
        self.inbuf += data
        out = []
        while True:
            buf = self.inbuf
            if self.stage == "version":
                if len(buf) < 12:
                    break
                if bytes(buf[:12]) != b"RFB 003.008\n":
                    raise ProtocolError(f"unexpected protocol version {bytes(buf[:12])!r}")
                del buf[:12]
                out.append(bytes([1, 1]))  # one security type: None
                self.stage = "security"
            elif self.stage == "security":
                if len(buf) < 1:
                    break
                if buf[0] != 1:
                    raise ProtocolError(f"security type {buf[0]} chosen")
                del buf[:1]
                out.append(struct.pack(">I", 0))  # SecurityResult: ok
                self.stage = "init"
            elif self.stage == "init":
                if len(buf) < 1:
                    break
                del buf[:1]  # the shared flag
                name = f"Mock {self.name}".encode()
                pixel_format = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 0, 8, 16)
                out.append(struct.pack(">HH", self.W, self.H) + pixel_format + struct.pack(">I", len(name)) + name)
                self.stage = "ready"
            else:
                if not buf:
                    break
                size = {0: 20, 3: 10, 4: 8, 5: 6}.get(buf[0])
                if buf[0] == 2 and len(buf) >= 4:
                    size = 4 + 4 * struct.unpack(">H", bytes(buf[2:4]))[0]
                elif buf[0] == 6 and len(buf) >= 8:
                    size = 8 + struct.unpack(">I", bytes(buf[4:8]))[0]
                elif buf[0] not in (2, 6) and size is None:
                    raise ProtocolError(f"client message {buf[0]} is not supported")
                if size is None or len(buf) < size:
                    break
                msg = bytes(buf[:size])
                del buf[:size]
                out.extend(self.message(msg))
        return out

    def message(self, msg):
        kind = msg[0]
        if kind == 3:  # FramebufferUpdateRequest
            incremental, x, y, w, h = struct.unpack(">BHHHH", msg[1:10])
            if not incremental:
                return [self.update(x, y, w, h)]
            if self.dirty:
                x0, y0, x1, y1 = self.dirty
                return [self.update(x0, y0, x1 - x0, y1 - y0)]
            self.want = True
        elif kind == 4:  # KeyEvent
            down, keysym = struct.unpack(">B2xI", msg[1:8])
            self.record("key", down=bool(down), keysym=keysym)
            log.info("vnc %s: key %#x %s", self.name, keysym, "down" if down else "up")
            if down:
                i = self.keys % 28
                self.keys += 1
                self.mark(6 + i * 11, 170, 10, ((keysym * 37) % 200 + 55, (keysym * 91) % 200 + 55, (keysym * 53) % 200 + 55))
        elif kind == 5:  # PointerEvent
            mask, x, y = struct.unpack(">BHH", msg[1:6])
            self.record("pointer", mask=mask, x=x, y=y)
            if mask & 1 and not self.mask & 1:
                self.counts["click"] += 1
                log.info("vnc %s: click at %d,%d", self.name, x, y)
                self.mark(x - 6, y - 6, 12, (230, 30, 30))
            self.mask = mask
            self.move_cross(x, y)
        elif kind == 6:  # ClientCutText
            text = msg[8:].decode("latin-1")
            self.record("cut", text=text)
            log.info("vnc %s: clipboard %r", self.name, text)
            self.mark(self.W - 18, 66, 12, (0, 220, 220))
        if self.want and self.dirty:
            x0, y0, x1, y1 = self.dirty
            return [self.update(x0, y0, x1 - x0, y1 - y0)]
        return []


async def vnc_screen(request, ws, node_id, name):
    """The console of a node with console_type vnc: the raw RFB stream in binary frames, both ways."""
    rfb = FakeRfb(name)
    request.app["vnc"][node_id] = rfb
    await ws.send_bytes(b"RFB 003.008\n")
    async for msg in ws:
        if msg.type != WSMsgType.BINARY:
            continue  # RFB has no text frames
        try:
            frames = rfb.feed(msg.data)
        except ProtocolError as exc:
            log.warning("vnc %s: %s", name, exc)
            await ws.close(code=1002)
            break
        for frame in frames:
            await ws.send_bytes(frame)
    return ws


async def mock_vnc(request):
    rfb = request.app["vnc"].get(request.match_info["node"])
    if rfb is None:
        return error(404, "node_not_found", "No screen was opened on this node yet")
    return web.json_response({"stage": rfb.stage, "size": [rfb.W, rfb.H], "keys": rfb.keys, "counts": rfb.counts,
                              "events": rfb.events[-50:]})


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
    if m.node_console(node_id) == "vnc":
        try:
            return await vnc_screen(request, ws, node_id, name)
        finally:
            request.app["consoles"][node_id].discard(ws)
    # Режимы IOS настолько, насколько их видит клавиатура консоли: приглашение меняется, команды — эхо.
    modes = [""]  # "", "config", "config-if", "config-router"...
    priv = [True]

    def prompt():
        mode = f"({modes[-1]})" if modes[-1] else ""
        return f"{name}{mode}{'#' if priv[0] else '>'}".encode()

    def run(cmd):
        word = cmd.split()[0].lower() if cmd else ""
        if word == "enable":
            priv[0] = True
        elif word == "disable":
            priv[0], modes[:] = False, [""]
        elif cmd.lower() in ("configure terminal", "conf t") and priv[0] and not modes[-1]:
            modes.append("config")
        elif word in ("interface", "int") and modes[-1].startswith("config"):
            modes[1:] = ["config", "config-if"]
        elif word == "router" and modes[-1].startswith("config"):
            modes[1:] = ["config", "config-router"]
        elif word == "exit" and len(modes) > 1:
            modes.pop()
        elif word == "end":
            modes[:] = [""]
        elif cmd.lower() in ("write memory", "write", "wr", "copy running-config startup-config") and priv[0]:
            return "\r\nBuilding configuration...\r\n[OK]"
        elif cmd:
            return f"\r\n% mock {name}: {cmd}"
        return ""

    line = ""
    await ws.send_bytes(b"\r\n" + prompt())
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue  # размер окна и прочие бинарные кадры слот не понимает
            for ch in msg.data:
                if ch in "\r\n":
                    cmd, line = line.strip(), ""
                    if cmd == "poweroff":
                        m.stopped_nodes.add(node_id)
                        await ws.close()
                        return ws
                    if cmd == "reload":
                        await ws.close()
                        return ws
                    await ws.send_bytes(run(cmd).encode() + b"\r\n" + prompt())
                elif ch in "\x7f\b":
                    if line:
                        line = line[:-1]
                        await ws.send_bytes(b"\b \b")
                elif ch == "\x15":
                    await ws.send_bytes(b"\b \b" * len(line))
                    line = ""
                elif ch == "\x1a":
                    line = ""
                    modes[:] = [""]
                    await ws.send_bytes(b"^Z\r\n" + prompt())
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
    vnc = {}  # node_id → FakeRfb of the last screen opened on it
    api["m"], api["consoles"], api["vnc"] = manager, consoles, vnc
    api['capture_tasks'] = set()
    api.router.add_post('/mock/shutdown-fail', mock_shutdown_failure)
    api.router.add_post('/mock/capture-fail/{mode}', mock_capture_failure)
    api.router.add_post('/api/v1/sessions/{sid}/capture', image_capture)
    api.router.add_get('/api/v1/sessions/{sid}/capture', image_capture)
    api.router.add_patch('/api/v1/images/{image}', image_patch)
    api.router.add_get("/api/v1/info", info)
    api.router.add_get("/api/v1/labs", list_labs)
    api.router.add_get("/api/v1/images", images)
    api.router.add_post("/api/v1/labs/validate", validate_lab)
    api.router.add_get("/api/v1/labs/{ref}/topology", lab_topology)
    api.router.add_get("/api/v1/sessions", find_session)
    api.router.add_post("/api/v1/sessions", create_session)
    api.router.add_get("/api/v1/sessions/{sid}", get_session)
    api.router.add_post("/api/v1/sessions/{sid}/stop", stop_session)
    api.router.add_post("/api/v1/sessions/{sid}/console-tickets", console_ticket)
    api.router.add_get("/api/v1/sessions/{sid}/state", session_facts)
    api.router.add_post("/api/v1/sessions/{sid}/state/refresh", refresh_facts)
    api.router.add_post("/mock/nodes/{node}/{action:stop|start}", mock_node)
    api.router.add_post("/mock/fail-next", mock_fail_next)
    api.router.add_post("/mock/stale/{mode:on|off}", mock_stale)
    api.router.add_post("/mock/facts/nodes/{node}/{action:fail|ok}", mock_facts_node)
    api.router.add_post("/mock/facts/stall/{mode:on|off}", mock_facts_stall)
    api.router.add_get("/mock/vnc/{node}", mock_vnc)

    async def http_session(app):
        app["http"] = ClientSession()
        yield
        tasks = list(app['capture_tasks'])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await app["http"].close()

    api.cleanup_ctx.append(http_session)
    slot = web.Application()
    slot["m"], slot["consoles"], slot["vnc"] = manager, consoles, vnc
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
