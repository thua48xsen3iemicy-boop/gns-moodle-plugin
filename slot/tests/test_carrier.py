"""QEMU NIC carrier follows the far end of the cable: IOL silence, a guest's admin=down, a stopped node."""

import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from api import carrier, l1


def iol(name, app):
    return {"node_id": name, "name": name, "node_type": "iou", "status": "started",
            "properties": {"application_id": app, "l1_keepalives": True}}


def qemu(name, monitor=4000, adapters=(0, 1)):
    netdevs = " ".join(f"-device e1000,netdev=gns3-{a} -netdev socket,id=gns3-{a}" for a in adapters)
    return {"node_id": name, "name": name, "node_type": "qemu", "status": "started", "properties": {},
            "command_line": f"/usr/bin/qemu-system-x86_64 -name {name} -monitor tcp:127.0.0.1:{monitor},server,nowait {netdevs}"}


def side(name, adapter=0, port=0):
    return {"node_id": name, "adapter_number": adapter, "port_number": port}


def facts(admin):
    return {"collectors": {"linux.net": {"ok": True, "observed_at": datetime.now(timezone.utc).isoformat(),
            "data": {"interfaces": [{"port": {"adapter": 0, "port": 0}, "admin": admin}]}}}}


async def harness(monkeypatch, nodes, links):
    disabled, calls = set(), []

    class Channel:
        def __init__(self, directory, app):
            self.app = app

        async def exchange(self, ports, timeout, allowed, max_age=None):
            return {port for port in ports if (self.app, port) not in disabled}

        def close(self):
            pass

    monkeypatch.setattr(l1, "Channel", Channel)
    lab = {"project_id": "p", "state": "READY"}
    topology = {"nodes": nodes, "links": links}

    async def api(method, path):
        return topology[path.rsplit("/", 1)[1]]

    refreshes = []
    lc = SimpleNamespace(claimed=True, get=lambda *args: lab, all=lambda kind: [lab], api=api,
                         facts=SimpleNamespace(results={"p": {}}, collect_soon=lambda pid, node: refreshes.append((pid, node))))
    service = l1.Keepalives(lc)
    service.task.cancel()
    await asyncio.gather(service.task, return_exceptions=True)

    async def send(port, netdev, up):
        calls.append((port, netdev, "on" if up else "off"))

    service.carrier.send = send
    return service, calls, disabled, topology, lab, refreshes


def test_iol_silence_drops_the_carrier_of_the_guest_and_restores_it(monkeypatch, tmp_path):
    async def scenario():
        service, calls, disabled, topology, _, refreshes = await harness(
            monkeypatch, [iol("sw", 1), qemu("pc")], [{"nodes": [side("sw", 0, 1), side("pc")]}])
        service.directory = tmp_path / "sockets"
        service.lc.facts.results["p"]["pc"] = facts("up")
        try:
            await service.cycle()
            assert calls == [(4000, "gns3-0", "on")]  # first sight: the known state is set once
            assert refreshes == []  # ... and it changes nothing in the guest
            calls.clear()
            await service.cycle()
            assert calls == []  # unchanged: nothing is sent again
            disabled.add((1, 0x10))  # an unsaved shutdown on the IOL port
            await service.cycle()
            assert calls == []  # one miss is not enough
            await service.cycle()
            assert calls == [(4000, "gns3-0", "off")] and refreshes == [("p", "pc")]  # the guest is read again now
            calls.clear()
            await service.cycle()
            assert calls == []
            service.carrier.applied[("pc", 0)] = (False, time.monotonic() - carrier.REASSERT - 1)
            await service.cycle()
            assert calls == [(4000, "gns3-0", "off")]  # an off NIC is told so again now and then
            calls.clear()
            disabled.clear()
            await service.cycle()
            assert calls == [(4000, "gns3-0", "on")]
            # The guest's own admin=down stops the keepalives; that is not a reason to drop its carrier.
            calls.clear()
            service.lc.facts.results["p"]["pc"] = facts("down")
            await service.cycle()
            await service.cycle()
            assert calls == []
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_guest_admin_down_and_stopped_peer_drop_the_carrier(monkeypatch, tmp_path):
    async def scenario():
        service, calls, _, topology, _, _ = await harness(
            monkeypatch, [qemu("a", 4001), qemu("b", 4002)], [{"nodes": [side("a"), side("b")]}])
        results = service.lc.facts.results["p"]
        try:
            await service.cycle()
            assert sorted(calls) == [(4001, "gns3-0", "on"), (4002, "gns3-0", "on")]
            calls.clear()
            results["a"] = facts("down")  # ip link set dev ens4 down on a
            await service.cycle()
            assert calls == [(4002, "gns3-0", "off")]  # b loses the carrier, a keeps its own
            calls.clear()
            results["b"] = facts("up")
            results["a"] = facts("up")
            await service.cycle()
            assert calls == [(4002, "gns3-0", "on")]
            calls.clear()
            results["a"]["collectors"]["linux.net"]["observed_at"] = "2000-01-01T00:00:00Z"
            results["a"]["collectors"]["linux.net"]["data"]["interfaces"][0]["admin"] = "down"
            await service.cycle()
            assert calls == []  # stale facts never invent a cable fault
            topology["nodes"][0]["status"] = "stopped"
            await service.cycle()
            assert calls == [(4002, "gns3-0", "off")]
            calls.clear()
            topology["links"][0]["suspend"] = True
            topology["nodes"][0]["status"] = "started"
            await service.cycle()
            assert calls == [] and not service.carrier.applied  # GNS3 owns a suspended link
            topology["links"][0]["suspend"] = False
            results["a"] = facts("up")
            await service.cycle()
            assert sorted(calls) == [(4001, "gns3-0", "on"), (4002, "gns3-0", "on")]
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_unknown_monitor_failures_and_switch_off(monkeypatch, tmp_path):
    async def scenario():
        pc = qemu("pc")
        pc["command_line"] = "/usr/bin/qemu-system-x86_64 -name pc"  # no monitor, no netdev
        service, calls, _, topology, _, _ = await harness(
            monkeypatch, [qemu("a", 4001), pc], [{"nodes": [side("a"), side("pc")]}])
        monkeypatch.setattr(carrier, "command_line", lambda node: node["command_line"])
        failures = []

        async def broken(port, netdev, up):
            failures.append(port)
            raise ConnectionRefusedError("busy")

        try:
            await service.cycle()
            assert calls == [(4001, "gns3-0", "on")]  # the other NIC is left alone
            service.carrier.applied.clear()
            service.carrier.send = broken
            await service.cycle()
            await service.cycle()
            assert failures == [4001]  # retried later, not every round, and the L1 cycle goes on
            service.carrier.retry.clear()
            monkeypatch.setenv("SLOT_L1_CARRIER", "0")
            await service.cycle()
            assert failures == [4001]
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_monitor_of_reads_the_gns3_command_line():
    node = qemu("pc", 35477, adapters=(0, 2))
    assert carrier.monitor_of(node, 0) == (35477, "gns3-0")
    assert carrier.monitor_of(node, 2) == (35477, "gns3-2")
    assert carrier.monitor_of(node, 1) is None


@pytest.mark.parametrize("reply, error", [(b"", None), (b"Device 'gns3-9' not found\r\n", "not found")])
def test_set_link_speaks_the_hmp_monitor(reply, error):
    async def scenario():
        received = []

        async def monitor(reader, writer):
            writer.write(b"QEMU 8.2.2 monitor - type 'help' for more information\r\n(qemu) ")
            await writer.drain()
            line = await reader.readline()
            received.append(line)
            writer.write(line.rstrip(b"\n") + b"\r\n" + reply + b"(qemu) ")  # HMP echoes the command
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(monitor, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            if error:
                with pytest.raises(RuntimeError, match=error):
                    await carrier.set_link(port, "gns3-9", False)
            else:
                await carrier.set_link(port, "gns3-0", False)
            with pytest.raises(ValueError):
                await carrier.set_link(port, "gns3-0; quit", True)
        finally:
            server.close()
            await server.wait_closed()
        assert received[0] == (b"set_link gns3-9 off\n" if error else b"set_link gns3-0 off\n")
    asyncio.run(scenario())


def test_collect_soon_makes_only_that_node_due_and_wakes_the_loop():
    from api.facts import Facts

    service = Facts.__new__(Facts)
    service.next_due = {("p", "pc", "linux.net"): 1e12, ("p", "other", "linux.net"): 1e12, ("q", "pc", "linux.net"): 1e12}
    service.wake = asyncio.Event()
    service.collect_soon("p", "pc")
    assert service.next_due == {("p", "pc", "linux.net"): 0, ("p", "other", "linux.net"): 1e12,
                                ("q", "pc", "linux.net"): 1e12}
    assert service.wake.is_set()
