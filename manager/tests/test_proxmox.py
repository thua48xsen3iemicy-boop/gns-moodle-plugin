import httpx
import pytest
from fakes import FakePve

from lab_manager.proxmox import (PveApiError, PveClient, PveTaskFailed, PveTaskUnknown, PveUncertain,
                                 PveUnavailable, upid_node)

NODES = ("pve1:8006", "pve2:8006", "pve3:8006")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def client(fake, clock=None, **kw):
    c = PveClient(NODES, "lab-manager@pve!manager", "secret", transport=fake.transport(),
                  clock=clock or Clock(), **kw)
    c.task_interval = 0
    return c


def hosts(fake):
    return [h for h, _, _ in fake.requests]


async def test_first_node_down_uses_second_and_sticks_to_it():
    fake = FakePve()
    fake.add_ct(201, node="pve3")
    fake.down_hosts = {"pve1"}
    pve = client(fake)
    assert (await pve.ct_status(201))["status"] == "stopped"
    assert hosts(fake)[:2] == ["pve1", "pve2"]
    fake.requests.clear()
    await pve.ct_status(201)
    assert "pve1" not in hosts(fake)  # Marked down, last good node is used first
    assert hosts(fake)[0] == "pve2"
    await pve.close()


async def test_ct_is_addressed_by_its_node_not_by_entry_point():
    fake = FakePve()
    fake.add_ct(201, node="pve3")
    pve = client(fake)
    await pve.ct_status(201)
    assert fake.requests[-1] == ("pve1", "GET", "/nodes/pve3/lxc/201/status/current")
    await pve.close()


async def test_4xx_does_not_switch_node():
    fake = FakePve()
    fake.responses[("GET", "/cluster/resources")] = (403, {"data": None, "message": "Permission check failed"})
    pve = client(fake)
    with pytest.raises(PveApiError) as exc:
        await pve.request("GET", "/cluster/resources")
    assert exc.value.status == 403
    assert hosts(fake) == ["pve1"]
    assert not pve.node_is_down("pve1:8006")
    await pve.close()


async def start(pve, vmid, node):
    return await pve.request("POST", f"/nodes/{node}/lxc/{vmid}/status/start")


async def test_application_500_is_returned_not_failed_over():
    fake = FakePve()
    fake.add_ct(201, node="pve1", status="running")
    pve = client(fake)
    with pytest.raises(PveApiError) as exc:
        await start(pve, 201, "pve1")
    assert "already running" in str(exc.value)
    assert set(hosts(fake)) == {"pve1"}
    await pve.close()


async def test_503_on_get_switches_node():
    fake = FakePve()
    fake.responses[("GET", "/version")] = (503, {"data": None})
    pve = client(fake)
    with pytest.raises(PveApiError):  # pve2 answers 501 for an unknown path: a real answer
        await pve.request("GET", "/version")
    assert hosts(fake) == ["pve1", "pve2"]
    assert pve.node_is_down("pve1:8006")
    await pve.close()


async def test_node_comes_back_after_retry_period():
    fake = FakePve()
    fake.add_ct(201, node="pve2")
    clock = Clock()
    pve = client(fake, clock=clock, retry_after=60)
    fake.down_hosts = {"pve1"}
    await pve.ct_status(201)
    assert pve.node_is_down("pve1:8006")
    # pve1 is repaired, pve2 and pve3 go for maintenance: after the retry period pve1 works again.
    fake.down_hosts = {"pve2", "pve3"}
    clock.t += 61
    fake.requests.clear()
    pve.forget_ct_node(201)
    await pve.ct_status(201)
    assert not pve.node_is_down("pve1:8006")
    assert hosts(fake) == ["pve2", "pve3", "pve1", "pve1"]
    fake.requests.clear()
    await pve.ct_status(201)
    assert hosts(fake) == ["pve1"]  # Now the last working entry point
    await pve.close()


async def test_all_nodes_down_raises_unavailable():
    fake = FakePve()
    fake.down_hosts = {"pve1", "pve2", "pve3"}
    pve = client(fake)
    with pytest.raises(PveUnavailable):
        await pve.request("GET", "/cluster/resources")
    # Even when all are marked down they are still tried (oldest failure first).
    fake.down_hosts = {"pve1", "pve2"}
    assert await pve.request("GET", "/cluster/resources", params={"type": "vm"}) == []
    await pve.close()


async def test_mutating_request_with_unknown_outcome_is_not_repeated():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        raise httpx.ReadTimeout("slow", request=request)

    pve = PveClient(NODES, "u@pve!t", "s", transport=httpx.MockTransport(handler))
    with pytest.raises(PveUncertain):
        await pve.request("POST", "/nodes/pve1/lxc/201/status/start")
    assert calls == ["pve1"]
    await pve.close()


async def test_token_header_and_tls_required():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        seen["scheme"] = request.url.scheme
        return httpx.Response(200, json={"data": []})

    pve = PveClient(NODES, "root@pam!mgr", "abc-123", transport=httpx.MockTransport(handler))
    await pve.request("GET", "/cluster/resources")
    assert seen == {"auth": "PVEAPIToken=root@pam!mgr=abc-123", "scheme": "https"}
    await pve.close()
    with pytest.raises(ValueError):
        PveClient(NODES, "u@pve!t", "s")  # No CA file and no test transport: refuse
    assert "secret" not in repr(client(FakePve()).__dict__.get("_http"))


async def test_wait_task_until_stopped_ok():
    fake = FakePve()
    fake.add_ct(201, node="pve2")
    fake.task_polls = 3
    pve = client(fake)
    upid = await start(pve, 201, "pve2")
    assert upid_node(upid) == "pve2"
    status = await pve.wait_task(upid, timeout=10)
    assert status["exitstatus"] == "OK"
    assert (await pve.ct_status(201))["status"] == "running"
    await pve.close()


async def test_wait_task_failed_exitstatus():
    fake = FakePve()
    fake.add_ct(201, node="pve2", status="running")
    fake.exit_status["vzshutdown"] = "command 'lxc-stop -n 201 --nokill --timeout 60' failed: exit code 1"
    pve = client(fake)
    upid = await pve.request("POST", "/nodes/pve2/lxc/201/status/shutdown", data={"timeout": 60})
    with pytest.raises(PveTaskFailed) as exc:
        await pve.wait_task(upid)
    assert "exit code 1" in exc.value.exitstatus
    assert (await pve.ct_status(201))["status"] == "running"
    await pve.close()


async def test_wait_task_on_unreachable_node_is_unknown_not_success():
    fake = FakePve()
    fake.add_ct(201, node="pve2")
    pve = client(fake)
    upid = await start(pve, 201, "pve2")
    fake.task_node_down = {"pve2"}
    with pytest.raises(PveTaskUnknown):
        await pve.wait_task(upid)
    await pve.close()


async def test_wait_task_timeout_is_unknown():
    fake = FakePve()
    fake.add_ct(201, node="pve2")
    fake.task_polls = 10**6
    clock = Clock()
    pve = client(fake, clock=clock)

    async def no_sleep(_):
        clock.t += 1

    import lab_manager.proxmox as mod

    original = mod.asyncio.sleep
    mod.asyncio.sleep = no_sleep
    try:
        upid = await start(pve, 201, "pve2")
        with pytest.raises(PveTaskUnknown):
            await pve.wait_task(upid, timeout=5)
    finally:
        mod.asyncio.sleep = original
    await pve.close()


async def test_ct_migration_is_followed():
    fake = FakePve()
    fake.add_ct(201, node="pve2")
    pve = client(fake)
    await pve.ct_status(201)
    fake.cts[201]["node"] = "pve3"  # Migrated
    with pytest.raises(PveApiError):
        await pve.ct_status(201)  # Cached node is stale once, then forgotten
    assert (await pve.ct_status(201))["node"] == "pve3"
    await pve.close()


async def test_cluster_resources_lists_nodes_and_fills_node_cache():
    fake = FakePve()
    fake.add_ct(201, node="pve3")
    pve = client(fake)
    resources = await pve.cluster_resources()
    assert {r["node"] for r in resources if r["type"] == "node"} == {"pve1", "pve2", "pve3"}
    fake.requests.clear()
    assert await pve.find_ct_node(201) == "pve3"
    assert fake.requests == []  # Served from the cache filled by cluster_resources
    await pve.close()


async def test_config_put_uses_form_and_delete():
    fake = FakePve()
    fake.add_ct(201, node="pve1", config={"hostname": "slot201", "cores": 2, "swap": 512})
    pve = client(fake)
    await pve.ct_set_config(201, {"cores": "8", "memory": "16384"}, delete=["swap"])
    config = await pve.ct_config(201)
    assert config == {"hostname": "slot201", "cores": "8", "memory": "16384"}
    await pve.close()
