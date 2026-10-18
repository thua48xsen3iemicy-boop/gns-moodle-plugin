"""Configuration from /etc/lab-manager/manager.env and the secret files it points to.

Fail closed: a missing secret, an unreadable secrets file or an invalid value stops startup.
Secrets are kept out of repr() so they never end up in logs or tracebacks.
"""

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .images import IMAGE_ID_RE, ImageError, load_images

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")  # One path component, as the node agent requires
GUEST_ACTIONS = ("init_slot", "daemon_reload", "start_slot_service", "restart_slot_service")


class ConfigError(RuntimeError):
    pass


class SiteLimits(BaseModel):
    """What labs defined in this site's Moodle may use (plugin request П13)."""

    model_config = ConfigDict(extra="forbid")
    max_nodes: int = Field(default=100, ge=1, le=100)
    max_memory_mb: int | None = Field(default=None, ge=256)
    max_cpus: int | None = Field(default=None, ge=1)
    images: list[str] | None = None  # Allowed image ids; None (or "*" in the file) = all

    @field_validator("images", mode="before")
    @classmethod
    def star_means_all(cls, value):
        return None if value == "*" else value

    @field_validator("images")
    @classmethod
    def image_ids(cls, value):
        if value is not None and not all(re.fullmatch(IMAGE_ID_RE, i) for i in value):
            raise ValueError("images must be image ids or \"*\"")
        return value

    def allows(self, image_id: str) -> bool:
        return self.images is None or image_id in self.images

    def public(self) -> dict:
        return {"max_nodes": self.max_nodes, "max_memory_mb": self.max_memory_mb, "max_cpus": self.max_cpus,
                "images": "*" if self.images is None else list(self.images)}


class SiteConfig(BaseModel):
    """One Moodle site allowed to call the manager."""

    url: str = Field(min_length=1, max_length=255)  # Exactly $CFG->wwwroot, sent as X-Moodle-Site
    slug: str  # Short, path-safe name used for the student directory tree
    secret: str = Field(min_length=32, repr=False)
    limits: SiteLimits = Field(default_factory=SiteLimits)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("slug")
    @classmethod
    def safe_slug(cls, value: str) -> str:
        if not SLUG_RE.fullmatch(value):
            raise ValueError("slug must match " + SLUG_RE.pattern)
        return value


class SlotConfig(BaseModel):
    """One Lab Slot LXC. Non-secret fields are mirrored into the slots table.

    Slots are interchangeable executors: any slot runs any student's lab.
    """

    vmid: int = Field(ge=100, le=999_999_999)
    address: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")  # Host only, no scheme/port
    port: int = Field(default=8080, ge=1, le=65535)
    api_token: str = Field(min_length=32, repr=False)
    console_token: str = Field(default="", repr=False)


def _int(env, name, default, low=1, high=10**9):
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not low <= value <= high:
        raise ConfigError(f"{name} must be in [{low}, {high}]")
    return value


def _float(env, name, default, low, high):
    raw = env.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if not low <= value <= high:
        raise ConfigError(f"{name} must be in [{low}, {high}]")
    return value


def _read_secret_json(path: Path, check_mode: bool):
    try:
        st = path.stat()
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {type(exc).__name__}") from exc
    if check_mode and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"{path} must not be accessible by group/others (chmod 600)")
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Invalid JSON in {path}") from exc


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    sites: dict[str, SiteConfig] = field(repr=False)  # By normalized URL
    slots: dict[int, SlotConfig] = field(repr=False)  # By vmid
    labs_dir: Path
    pve_nodes: tuple[str, ...]
    pve_token_id: str
    pve_token_secret: str = field(repr=False)
    pve_ca_file: Path
    agent_url_template: str  # e.g. https://{node}.lab.local:8443, {node} = Proxmox node name
    agent_ca_file: Path
    agent_cert_file: Path
    agent_key_file: Path = field(repr=False)
    gateway_internal_url: str
    gateway_internal_key: str = field(repr=False)
    gateway_public_url: str
    agent_ceph_root: str = "/mnt/pve/cfs"  # LAB_CEPH_ROOT of the agents: expected mount sources
    agent_code_dir: str = "gns3-ro/slot"  # LAB_CODE_DIR of the agents, relative to the root
    agent_students_dir: str = "gns3-rw/students"  # LAB_STUDENTS_DIR of the agents, relative to the root
    slot_code_revision: str | None = None  # None: shared <ceph>/<code dir>, else <code dir>/<revision>
    agent_start_actions: tuple[str, ...] = ("init_slot",)
    placeholder_student_key: str | None = None  # Mounted read-only into a released slot
    node_memory_reserve_mb: int = 2048
    node_guest_memory_mb: int = 0  # Memory limits of all guests of a node, MiB; 0: no limit
    node_cpu_overcommit: float = 0.0  # vCPUs of all guests of a node per node CPU; 0: no limit
    listen_host: str = "127.0.0.1"
    listen_port: int = 8090
    pve_timeout: float = 10.0
    pve_retry_after: float = 60.0
    # Above the sum of the agent's subprocess timeouts (agent MANAGER_CONTRACT_REVIEW.md §5):
    # start 385 s with three actions, PUT mounts 85 s, graceful stop 110 s.
    agent_timeout: float = 30.0
    agent_start_timeout: float = 420.0
    agent_mounts_timeout: float = 120.0
    agent_stop_timeout: float = 130.0
    slot_timeout: float = 10.0
    slot_ready_timeout: float = 300.0
    lab_op_timeout: float = 1800.0
    queue_timeout: float = 600.0  # Seconds a session may wait for a slot; 0 = no limit
    images: dict | None = None  # Image catalog by id (IMAGES_FILE); None: no labs from Moodle
    lab_memory_overhead_mb: int = 1024  # Added to node memory when sizing a CT for a lab definition
    default_max_duration: int = 180  # Minutes
    max_duration_limit: int = 480  # Minutes; upper bound for options.max_duration from the plugin
    worker_interval: float = 2.0

    def site(self, url: str | None) -> SiteConfig | None:
        return self.sites.get((url or "").rstrip("/"))


def _key(env, name, default=""):
    value = env.get(name, default).strip()
    if value and not KEY_RE.fullmatch(value):
        raise ConfigError(f"{name} must match {KEY_RE.pattern}")
    return value or None


def _ceph_root(env):
    value = env.get("LAB_CEPH_ROOT", "/mnt/pve/cfs").strip().rstrip("/")
    if not re.fullmatch(r"(/[A-Za-z0-9_.-]+)+", value) or "/../" in value + "/" or "/./" in value + "/":
        raise ConfigError("LAB_CEPH_ROOT must be an absolute path without '..'")
    return value


def _ceph_subdir(env, name, default):
    """Same rule as the agent's ceph_subdir: relative path of safe directory names."""
    parts = env.get(name, default).strip().strip("/").split("/")
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", part) for part in parts):
        raise ConfigError(f"{name} must be a relative path of safe directory names")
    return "/".join(parts)


def load(env=None, check_mode=True) -> Settings:
    env = dict(os.environ if env is None else env)

    def required(name):
        value = env.get(name, "").strip()
        if not value:
            raise ConfigError(f"{name} is required (see manager.env.example)")
        return value

    sites_raw = _read_secret_json(Path(required("MANAGER_SITES_FILE")), check_mode)
    slots_raw = _read_secret_json(Path(required("MANAGER_SLOTS_FILE")), check_mode)
    if not isinstance(sites_raw, list) or not isinstance(slots_raw, list):
        raise ConfigError("Sites and slots files must contain JSON lists")
    try:
        sites = [SiteConfig.model_validate(s) for s in sites_raw]
        slots = [SlotConfig.model_validate(s) for s in slots_raw]
    except ValidationError as exc:
        # Report field locations only: input values may be secrets.
        where = "; ".join(
            ".".join(map(str, e["loc"])) + ": " + e["msg"]
            for e in exc.errors(include_input=False, include_url=False)
        )
        raise ConfigError(f"Invalid sites/slots file: {where}") from exc
    if not sites:
        raise ConfigError("MANAGER_SITES_FILE must list at least one Moodle site")
    if len({s.url for s in sites}) != len(sites) or len({s.slug for s in sites}) != len(sites):
        raise ConfigError("Duplicate site url or slug")
    if len({s.vmid for s in slots}) != len(slots) or len({s.address for s in slots}) != len(slots):
        raise ConfigError("Duplicate slot vmid or address")
    # Every CT clone needs its own slot.env (agent AGENT_REQUESTS §5); the slot cannot see this.
    tokens = [s.api_token for s in slots] + [s.console_token for s in slots if s.console_token]
    if len(set(tokens)) != len(tokens):
        raise ConfigError("Slot tokens must be unique per slot and differ between API and console")

    nodes = tuple(n.strip() for n in required("PVE_NODES").split(",") if n.strip())
    for node in nodes:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*(:\d{1,5})?", node):
            raise ConfigError("PVE_NODES must be host[:port] entries separated by commas")
    token_id = required("PVE_TOKEN_ID")
    if not re.fullmatch(r"[^@\s!]+@[^@\s!]+![A-Za-z0-9_-]+", token_id):
        raise ConfigError("PVE_TOKEN_ID must look like USER@REALM!TOKENID")

    template = required("AGENT_URL_TEMPLATE").rstrip("/")
    if not re.fullmatch(r"https://[A-Za-z0-9.{}-]*\{node\}[A-Za-z0-9.{}-]*(:\d{1,5})?", template):
        raise ConfigError("AGENT_URL_TEMPLATE must be https://...{node}...[:port] (mTLS only)")
    # agent/guest/lab-slot-init: daemon-reload, /run/lab-slot, restart lab-slot.service.
    actions = tuple(a.strip() for a in env.get("AGENT_START_ACTIONS", "init_slot").split(",") if a.strip())
    if any(a not in GUEST_ACTIONS for a in actions) or len(actions) > 3:
        raise ConfigError(f"AGENT_START_ACTIONS: up to three of {GUEST_ACTIONS}")
    # Every session continues the student's project (Lab Slot >= 0.4.0 keeps its lab record in the
    # student directory). The old fresh mode is gone; a leftover setting must not be silently ignored.
    if env.get("LAB_PERSISTENCE", "resume").strip() != "resume":
        raise ConfigError("LAB_PERSISTENCE was removed: a student's project always continues; delete the line")

    images = None
    if env.get("IMAGES_FILE", "").strip():
        try:
            images = load_images(Path(env["IMAGES_FILE"].strip()))
        except ImageError as exc:
            raise ConfigError(str(exc)) from exc
        for site in sites:
            unknown = sorted(set(site.limits.images or ()) - set(images))
            if unknown:
                raise ConfigError(f"Site {site.slug}: limits.images lists unknown images {unknown}")

    gateway_public = required("GATEWAY_PUBLIC_URL").rstrip("/")
    if not gateway_public.startswith(("wss://", "ws://")):
        raise ConfigError("GATEWAY_PUBLIC_URL must be a ws:// or wss:// URL")
    return Settings(
        database_url=required("DATABASE_URL"),
        sites={s.url: s for s in sites},
        slots={s.vmid: s for s in slots},
        labs_dir=Path(env.get("LABS_DIR", "/srv/gns3-ro/labs")),
        pve_nodes=nodes,
        pve_token_id=token_id,
        pve_token_secret=required("PVE_TOKEN_SECRET"),
        pve_ca_file=Path(required("PVE_CA_FILE")),
        agent_url_template=template,
        agent_ceph_root=_ceph_root(env),
        agent_code_dir=_ceph_subdir(env, "LAB_CODE_DIR", "gns3-ro/slot"),
        agent_students_dir=_ceph_subdir(env, "LAB_STUDENTS_DIR", "gns3-rw/students"),
        agent_ca_file=Path(required("AGENT_CA_FILE")),
        agent_cert_file=Path(required("AGENT_CERT_FILE")),
        agent_key_file=Path(required("AGENT_KEY_FILE")),
        gateway_internal_url=required("GATEWAY_INTERNAL_URL").rstrip("/"),
        gateway_internal_key=required("GATEWAY_INTERNAL_KEY"),
        gateway_public_url=gateway_public,
        slot_code_revision=_key(env, "SLOT_CODE_REVISION"),
        agent_start_actions=actions,
        placeholder_student_key=_key(env, "PLACEHOLDER_STUDENT_KEY", "placeholder"),
        node_memory_reserve_mb=_int(env, "NODE_MEMORY_RESERVE_MB", 2048, 0, 10**7),
        node_guest_memory_mb=_int(env, "NODE_GUEST_MEMORY_MB", 0, 0, 10**8),
        node_cpu_overcommit=_float(env, "NODE_CPU_OVERCOMMIT", 0, 0, 64),
        listen_host=env.get("MANAGER_LISTEN_HOST", "127.0.0.1"),
        listen_port=_int(env, "MANAGER_LISTEN_PORT", 8090, 1, 65535),
        pve_timeout=_int(env, "PVE_TIMEOUT", 10, 1, 300),
        pve_retry_after=_int(env, "PVE_RETRY_AFTER", 60, 1, 3600),
        agent_timeout=_int(env, "AGENT_TIMEOUT", 30, 5, 300),
        agent_start_timeout=_int(env, "AGENT_START_TIMEOUT", 420, 400, 3600),
        agent_mounts_timeout=_int(env, "AGENT_MOUNTS_TIMEOUT", 120, 90, 900),
        agent_stop_timeout=_int(env, "AGENT_STOP_TIMEOUT", 130, 120, 900),
        slot_timeout=_int(env, "SLOT_TIMEOUT", 10, 1, 300),
        slot_ready_timeout=_int(env, "SLOT_READY_TIMEOUT", 300, 10, 3600),
        lab_op_timeout=_int(env, "LAB_OPERATION_TIMEOUT", 1800, 30, 86400),
        queue_timeout=_int(env, "QUEUE_TIMEOUT", 600, 0, 86400),
        images=images,
        lab_memory_overhead_mb=_int(env, "LAB_MEMORY_OVERHEAD_MB", 1024, 0, 65536),
        default_max_duration=_int(env, "DEFAULT_MAX_DURATION", 180, 1, 7 * 24 * 60),
        max_duration_limit=_int(env, "MAX_DURATION_LIMIT", 480, 1, 7 * 24 * 60),
        worker_interval=float(_int(env, "WORKER_INTERVAL", 2, 1, 60)),
    )
