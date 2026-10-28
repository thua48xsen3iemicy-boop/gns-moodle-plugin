"""L1 pair recovery, guest gating, fencing and real Unix datagram framing."""

import asyncio
import logging
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from api import l1


def node(name, app=None):
    return {"node_id": name, "node_type": "iou" if app else "qemu", "status": "started",
            "properties": {"application_id": app, "l1_keepalives": True}}


def side(name, adapter=0, port=0):
    return {"node_id": name, "adapter_number": adapter, "port_number": port}


async def harness(monkeypatch, directory, nodes=None, links=None):
    sent, disabled = [], set()

    class Channel:
        def __init__(self, directory, app):
            self.app = app

        async def exchange(self, ports, timeout, allowed, max_age=None):
            assert allowed()
            ends = {(self.app, port) for port in ports}
            sent.extend(sorted(ends))
            return {port for app, port in ends if (app, port) not in disabled}

        def close(self):
            pass

    monkeypatch.setattr(l1, "Channel", Channel)
    lab = {"project_id": "p", "state": "READY"}
    topology = {"nodes": nodes or [node("a", 1), node("b", 2)],
                "links": links if links is not None else [{"nodes": [side("a"), side("b")]}]}

    async def api(method, path):
        return topology[path.rsplit("/", 1)[1]]

    lc = SimpleNamespace(claimed=True, get=lambda *args: lab, all=lambda kind: [lab], api=api,
                         facts=SimpleNamespace(results={}))
    service = l1.Keepalives(lc)
    service.task.cancel()
    await asyncio.gather(service.task, return_exceptions=True)
    service.directory = directory / "sockets"
    return service, sent, disabled, topology, lab


def test_pair_shutdown_wait_recovery_and_other_end_changed(monkeypatch, tmp_path):
    async def scenario():
        service, sent, disabled, _, _ = await harness(monkeypatch, tmp_path)
        try:
            await service.cycle()
            assert set(sent) == {(1, 0), (2, 0)}
            disabled.add((1, 0))
            for _ in range(2):
                await service.cycle()
            sent.clear()
            await service.cycle()
            assert sent == [(1, 0)]  # healthy neighbor no longer receives carrier
            # B can change while silent. Recovering A must not assume B stayed enabled.
            disabled.add((2, 0))
            disabled.remove((1, 0))
            sent.clear()
            await service.cycle()
            assert sent == [(1, 0), (2, 0)]
            sent.clear()
            await service.cycle()
            assert sent == [(2, 0)]  # wait moved to the newly discovered unavailable end
            disabled.clear()
            await service.cycle()
            sent.clear()
            await service.cycle()
            assert set(sent) == {(1, 0), (2, 0)}
        finally:
            await service.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("disabled_ends", [set(), {(1, 0)}, {(2, 0)}, {(1, 0), (2, 0)}])
def test_four_initial_states_and_single_missed_reply(monkeypatch, tmp_path, disabled_ends):
    async def scenario():
        service, sent, disabled, _, _ = await harness(monkeypatch, tmp_path)
        try:
            disabled.update(disabled_ends)
            await service.cycle()
            assert next(iter(service.pairs.values())).waiting is None
            await service.cycle()
            pair = next(iter(service.pairs.values()))
            assert (pair.waiting is None) == (not disabled_ends)
            disabled.clear()
            await service.cycle()
            assert pair.waiting is None
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_parallel_links_and_multiple_switches_are_independent(monkeypatch, tmp_path):
    async def scenario():
        links = [{"nodes": [side("a"), side("b", 2)]},
                 {"nodes": [side("a", 1), side("b", 3)]},
                 {"nodes": [side("b", 0, 1), side("c", 1, 1)]}]
        service, sent, disabled, _, _ = await harness(
            monkeypatch, tmp_path, [node("a", 1), node("b", 2), node("c", 3)], links
        )
        try:
            disabled.add((1, 0))
            await service.cycle()
            await service.cycle()
            sent.clear()
            await service.cycle()
            assert set(sent) == {(1, 0), (1, 1), (2, 3), (2, 16), (3, 17)}
        finally:
            await service.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("collector", ["linux.net", "windows.net", "switch.net"])
def test_guest_uses_fresh_admin_facts_and_exact_port(monkeypatch, tmp_path, collector):
    async def scenario():
        service, sent, _, _, _ = await harness(
            monkeypatch, tmp_path, [node("a", 1), node("guest")],
            [{"nodes": [side("a"), side("guest", 2)]}]
        )
        try:
            await service.cycle()
            assert not sent
            interface = {"port": {"adapter": 2, "port": 0}, "admin": "up", "oper": "down"}
            record = {"ok": True, "observed_at": datetime.now(timezone.utc).isoformat(),
                      "data": {"interfaces": [interface]}}
            service.lc.facts.results = {"p": {"guest": {"collectors": {collector: record}}}}
            await service.cycle()
            assert sent == [(1, 0)]  # oper=down must not create a carrier bootstrap deadlock
            for change in (lambda: interface.update(admin="down"),
                           lambda: interface.update(admin="up", port={"adapter": 1, "port": 0}),
                           lambda: record.update(ok=False),
                           lambda: record.update(ok=True, observed_at="2000-01-01T00:00:00Z")):
                change()
                sent.clear()
                await service.cycle()
                assert not sent
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_suspension_stop_disabled_keepalives_and_no_owner(monkeypatch, tmp_path):
    async def scenario():
        service, sent, _, topology, lab = await harness(monkeypatch, tmp_path)
        try:
            await service.cycle()
            topology["links"][0]["suspend"] = True
            sent.clear()
            await service.cycle()
            assert not sent and not service.pairs
            topology["links"][0]["suspend"] = False
            topology["nodes"][1]["status"] = "stopped"
            await service.cycle()
            assert not sent
            service.lc.claimed = False
            await service.cycle()
            assert not service.channels
            service.lc.claimed = True
            lab["state"] = "STOPPING"
            await service.cycle()
            assert not sent
            lab["state"] = "READY"
            topology["nodes"][1]["status"] = "started"
            topology["nodes"][1]["properties"]["l1_keepalives"] = False
            await service.cycle()
            assert not sent  # mixed IOL pair cannot infer the peer's readiness
            for n in topology["nodes"]:
                n["properties"]["l1_keepalives"] = False
            await service.cycle()
            assert not service.channels
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_owner_changes_during_topology_read_do_not_send(monkeypatch, tmp_path):
    async def scenario():
        service, sent, _, topology, _ = await harness(monkeypatch, tmp_path)

        async def api(method, path):
            service.reset()
            return topology[path.rsplit("/", 1)[1]]

        service.lc.api = api
        try:
            await service.cycle()
            assert not sent and not service.channels
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_unix_packets_validation_and_owned_cleanup():
    async def scenario(directory):
        remote = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        remote.setblocking(False)
        remote.bind(str(directory / "L11"))
        channel = l1.Channel(directory, 1)

        async def responder():
            for _ in range(2):
                packet, sender = await asyncio.get_running_loop().sock_recvfrom(remote, 4096)
                dest, source, dest_if, source_if, kind, number = l1.HEADER.unpack(packet)
                assert (dest, source, kind, number) == (1, 513, 3, 0)
                remote.sendto(b"bad", sender)
                remote.sendto(l1.HEADER.pack(source, dest, source_if, dest_if, 4, 0), sender)
                remote.sendto(l1.HEADER.pack(source, dest, source_if, dest_if, kind, number), sender)

        task = asyncio.create_task(responder())
        try:
            assert await channel.exchange({0, 16}, 1, lambda: True) == {0, 16}
            await task
            assert await channel.exchange({0}, 0.01, lambda: True) == set()
            assert await channel.exchange({0}, 0.01, lambda: False) == set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            channel.close()
            remote.close()
        assert not (directory / "L1513").exists()
        assert (directory / "L11").exists()

    with tempfile.TemporaryDirectory(prefix="l1-") as directory:
        asyncio.run(scenario(Path(directory)))


def test_existing_socket_is_not_removed():
    with tempfile.TemporaryDirectory(prefix="l1-") as directory:
        path = Path(directory)
        owner = l1.Channel(path, 1)
        try:
            with pytest.raises(l1.L1Error, match="Cannot bind"):
                l1.Channel(path, 1)
            assert owner.local.exists()
        finally:
            owner.close()


@pytest.mark.parametrize("app", [1, 2])
def test_late_reply_is_consumed_next_round_once(app):
    async def scenario(directory):
        remote = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        remote.bind(str(directory / f"L1{app}"))
        channel = l1.Channel(directory, app)
        try:
            assert await channel.exchange({0}, 0.01, lambda: True, max_age=1) == set()
            request, sender = remote.recvfrom(4096)
            assert l1.HEADER.unpack(request) == (app, app + 512, 0, 0, 3, 0)
            # The real IOL trace shows RX after the short receive window, before
            # the next send. The previous implementation discarded precisely this RX.
            remote.sendto(l1.HEADER.pack(app + 512, app, 0, 0, 3, 0), sender)
            assert await channel.exchange({0}, 0.01, lambda: True, max_age=1) == {0}
            # No further replies: a previously acknowledged packet cannot mask shutdown forever.
            assert await channel.exchange({0}, 0.01, lambda: True, max_age=1) == set()
        finally:
            channel.close()
            remote.close()

    with tempfile.TemporaryDirectory(prefix="l1-") as directory:
        asyncio.run(scenario(Path(directory)))


def test_conditional_probe_preserves_another_ports_late_reply():
    async def scenario(directory):
        remote = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        remote.bind(str(directory / "L11"))
        channel = l1.Channel(directory, 1)
        try:
            assert await channel.exchange({0}, 0.01, lambda: True, max_age=1) == set()
            _, sender = remote.recvfrom(4096)
            remote.sendto(l1.HEADER.pack(513, 1, 0, 0, 3, 0), sender)
            # A peer recovery on a parallel link must not eat port 0's response.
            assert await channel.exchange({16}, 0.01, lambda: True, max_age=1) == set()
            assert await channel.exchange({0}, 0.01, lambda: True, max_age=1) == {0}
        finally:
            channel.close()
            remote.close()

    with tempfile.TemporaryDirectory(prefix="l1-") as directory:
        asyncio.run(scenario(Path(directory)))


def test_reply_queued_before_long_silence_does_not_restore_peer():
    async def scenario(directory):
        remote = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        remote.bind(str(directory / "L11"))
        channel = l1.Channel(directory, 1)
        try:
            assert await channel.exchange({0}, 0.01, lambda: True, max_age=0.02) == set()
            _, sender = remote.recvfrom(4096)
            remote.sendto(l1.HEADER.pack(513, 1, 0, 0, 3, 0), sender)
            await asyncio.sleep(0.03)
            assert await channel.exchange({0}, 0.01, lambda: True, max_age=0.02) == set()
        finally:
            channel.close()
            remote.close()

    with tempfile.TemporaryDirectory(prefix="l1-") as directory:
        asyncio.run(scenario(Path(directory)))


def test_invalid_and_colliding_application_ids_fail_closed(monkeypatch, tmp_path):
    async def scenario():
        service, sent, _, topology, _ = await harness(monkeypatch, tmp_path)
        try:
            for app in (None, 1, 513, 65535):
                topology["nodes"][1]["properties"]["application_id"] = app
                with pytest.raises(l1.L1Error):
                    await service.cycle()
                assert not sent
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_unsafe_directory_is_rejected(monkeypatch, tmp_path):
    async def scenario():
        service, sent, _, _, _ = await harness(monkeypatch, tmp_path)
        try:
            service.directory.symlink_to(tmp_path, target_is_directory=True)
            with pytest.raises(l1.L1Error, match="Unsafe socket directory"):
                await service.cycle()
            assert not sent
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_background_failure_closes_carrier_retries_and_logs_recovery(monkeypatch, tmp_path, caplog):
    async def scenario():
        service, _, _, _, _ = await harness(monkeypatch, tmp_path)
        closed = []
        service.channels["a"] = SimpleNamespace(close=lambda: closed.append(True))
        recovered = asyncio.Event()
        attempts = 0

        async def cycle():
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                raise OSError("GNS3 unavailable")
            recovered.set()

        service.cycle = cycle
        service.interval = 0.01
        service.task = asyncio.create_task(service.run())
        try:
            await asyncio.wait_for(recovered.wait(), 1)
            assert closed == [True] and not service.channels
            assert sum("L1_POLL_FAILED" in r.message for r in caplog.records) == 1
            assert any("L1_SERVICE_RECOVERED" in r.message for r in caplog.records)
        finally:
            await service.stop()

    with caplog.at_level(logging.INFO, logger=l1.LOG.name):
        asyncio.run(scenario())


def test_duplicate_links_and_unencodable_ports_fail_closed(monkeypatch, tmp_path):
    async def scenario():
        service, sent, _, topology, _ = await harness(monkeypatch, tmp_path)
        try:
            topology["links"].append({"nodes": [side("a"), side("b", 1)]})
            with pytest.raises(l1.L1Error, match="multiple links"):
                await service.cycle()
            assert not sent
            topology["links"].pop()
            topology["links"][0]["nodes"][0]["adapter_number"] = 16
            with pytest.raises(l1.L1Error, match="one byte"):
                await service.cycle()
            assert not sent
        finally:
            await service.stop()
    asyncio.run(scenario())


def test_socket_cleanup_preserves_replaced_path():
    with tempfile.TemporaryDirectory(prefix="l1-") as directory:
        channel = l1.Channel(Path(directory), 1)
        channel.local.unlink()
        channel.local.write_text("replacement")
        channel.close()
        assert channel.local.read_text() == "replacement"
