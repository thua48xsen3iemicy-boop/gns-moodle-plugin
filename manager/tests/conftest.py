"""Shared fixtures. Async tests run through asyncio.run (no extra pytest plugins).

Tests marked by the `dsn` fixture need PostgreSQL: set DATABASE_URL to a THROWAWAY database
(its public schema is dropped and recreated for every test). Without it they are skipped.
"""

import asyncio
import inspect
import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeAgent, FakeGateway, FakePve, FakeSlot, MultiTransport  # noqa: E402

from lab_manager.config import Settings, SiteConfig, SlotConfig  # noqa: E402

SITE_URL = "https://moodle.example.edu"
SITE_SECRET = "s" * 40
SLOT_TOKEN = "a" * 40


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        kwargs = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(pyfuncitem.obj(**kwargs))
        return True
    return None


@pytest.fixture
def dsn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL is not set: PostgreSQL tests skipped (use a throwaway database)")
    import psycopg

    from lab_manager.db import migrate

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    migrate(url)
    return url


def make_settings(tmp_path, dsn="postgresql:///unused", *, vmids=(201, 202), **overrides):
    labs = tmp_path / "labs"
    if not labs.exists():
        shutil.copytree(ROOT / "examples" / "labs", labs)
    values = dict(
        database_url=dsn,
        sites={SITE_URL: SiteConfig(url=SITE_URL, slug="main", secret=SITE_SECRET),
               "https://other.example.edu": SiteConfig(url="https://other.example.edu", slug="other",
                                                       secret="o" * 40)},
        slots={v: SlotConfig(vmid=v, address=f"10.0.0.{v - 200}", api_token=SLOT_TOKEN,
                             console_token="c" * 40) for v in vmids},
        labs_dir=labs,
        pve_nodes=("pve1:8006", "pve2:8006", "pve3:8006"),
        pve_token_id="lab-manager@pve!manager",
        pve_token_secret="pve-secret",
        pve_ca_file=tmp_path / "ca.pem",
        agent_url_template="https://{node}.agent:8443",
        agent_ca_file=tmp_path / "node-server-ca.crt",
        agent_cert_file=tmp_path / "manager.crt",
        agent_key_file=tmp_path / "manager.key",
        gateway_internal_url="http://gateway.internal:8080",
        gateway_internal_key="gw-key",
        gateway_public_url="wss://gateway.example.edu",
        placeholder_student_key="placeholder",
        pve_retry_after=60,
        slot_ready_timeout=60,
        worker_interval=1,
    )
    values.update(overrides)
    return Settings(**values)


class Env:
    pass


@asynccontextmanager
async def make_env(tmp_path, dsn, *, vmids=(201, 202), nodes_of=None, student_state=True, **overrides):
    """Services wired to fakes, pool opened, slots synced and enabled.

    nodes_of: {vmid: node}; by default slots are spread over pve2, pve3, pve1, ...
    """
    import httpx

    from lab_manager.app import Services, create_app

    env = Env()
    env.timeline = []
    env.settings = make_settings(tmp_path, dsn, vmids=vmids, **overrides)
    env.pve = FakePve(env.timeline)
    for i, vmid in enumerate(vmids):
        env.pve.add_ct(vmid, node=(nodes_of or {}).get(vmid, env.pve.nodes[(i + 1) % 3]))
    env.agent = FakeAgent(env.pve)
    env.projects = {}
    env.slots = {vmid: FakeSlot(s.address, env.timeline, env.projects, student_state=student_state)
                 for vmid, s in env.settings.slots.items()}
    env.gateway = FakeGateway()
    env.services = Services(
        env.settings, pve_transport=env.pve.transport(), agent_transport=env.agent.transport(),
        slot_transport=MultiTransport(env.slots.values()), gateway_transport=env.gateway.transport(),
    )
    env.services.pve.task_interval = 0.001
    await env.services.pool.open(wait=True)
    await env.services.store.sync_slots(list(env.settings.slots.values()))
    for vmid in vmids:
        await env.services.store.set_slot_admin_state(vmid, "free")
    env.app = create_app(env.services, run_orchestrator=False)
    env.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://manager",
                                   headers={"Authorization": "Bearer " + SITE_SECRET, "X-Moodle-Site": SITE_URL})
    env.orch = env.services.orchestrator

    async def run(rounds=30):
        """Drive the orchestrator until nothing moves (bounded)."""
        for _ in range(rounds):
            await env.orch.tick()
            await env.orch.drain()

    env.run = run
    try:
        yield env
    finally:
        await env.client.aclose()
        await env.services.close()
