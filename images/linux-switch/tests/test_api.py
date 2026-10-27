"""Guest behavior without privileged interfaces or a running GNS3 lab."""

import asyncio
import copy
import json
import struct
import sys

import pytest

from switch_api.capture import Captures, decode
from switch_api.core import ApiError, Linux, Vlans, validate_policy
from switch_api.server import Service


class FakeLinux:
    def __init__(self):
        self.current = [
            {"ifname": "eth0", "address": "02:00:00:00:00:01", "ifindex": 2},
            {"ifname": "eth1", "address": "02:00:00:00:00:02", "ifindex": 3},
        ]
        self.commands = []
        self.fail = False

    async def ports(self):
        return copy.deepcopy(self.current)

    async def run(self, *args):
        self.commands.append(args)
        if self.fail:
            raise ApiError("COMMAND_FAILED", "simulated kernel error")
        return b""

    async def query(self, *args):
        if "fdb" in args:
            return [{"mac": f"02:00:00:00:00:{i:02x}", "dev": "eth0"} for i in range(5)]
        if "vlan" in args:
            return [{"ifname": p["ifname"], "vlans": [{"vlan": 1}]} for p in self.current]
        return [{"ifname": "br0"}]


def policy():
    return {"ports": {"eth0": {"mode": "access", "vlan": 10},
                      "eth1": {"mode": "trunk", "allowed": [10, 20], "native": 20}}}


def arp_frame(tags=()):
    source = bytes.fromhex("020000000001")
    frame = bytes.fromhex("ffffffffffff") + source
    for tpid, vid in tags:
        frame += struct.pack("!HH", tpid, vid)
    frame += struct.pack("!H", 0x0806)
    frame += struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1)
    frame += source + bytes([10, 0, 0, 1]) + bytes(6) + bytes([10, 0, 0, 2])
    return frame


def pcap(frames, endian="<", linktype=1):
    header = struct.pack(endian + "IHHIIII", 0xa1b2c3d4, 2, 4, 0, 0, 128, linktype)
    return header + b"".join(struct.pack(endian + "IIII", 123, 500000, len(f), len(f)) + f
                              for f in frames)


@pytest.mark.parametrize("tags", [(), ((0x8100, 10),), ((0x88a8, 100), (0x8100, 20))])
def test_decode_arp_and_vlan_headers(tags):
    result = decode(arp_frame(tags))
    assert result["source"] == "02:00:00:00:00:01"
    assert result["arp"]["sender_ip"] == "10.0.0.1"
    assert result["arp"]["target_ip"] == "10.0.0.2"
    assert [t["vid"] for t in result["tags"]] == [vid for _, vid in tags]
    assert result["ether_type"] == 0x0806


def test_truncated_frames_do_not_invent_headers():
    assert decode(b"short") is None
    assert decode(bytes(12) + bytes.fromhex("810000")) is None
    assert "arp" not in decode(arp_frame()[:20])


@pytest.mark.parametrize("bad", [
    {"ports": {}},
    {"ports": {"eth0": {"mode": "access", "vlan": True}, "eth1": {"mode": "access", "vlan": 1}}},
    {"ports": {"eth0": {"mode": "access", "vlan": 4095}, "eth1": {"mode": "access", "vlan": 1}}},
    {"ports": {"eth0": {"mode": "access", "vlan": 1}, "eth1": {"mode": "trunk", "allowed": [10], "native": 20}}},
    {"ports": {"eth0": {"mode": "access", "vlan": 1}, "eth1": {"mode": "trunk", "allowed": [10, 10]}}},
])
def test_vlan_validation_rejects_before_mutation(bad):
    with pytest.raises(ApiError, match="."):
        validate_policy(bad, {"eth0": "a", "eth1": "b"})


def test_vlan_persistence_tagging_and_unknown_port_isolation(tmp_path):
    async def scenario():
        linux = FakeLinux()
        state = tmp_path / "vlans.json"
        v = Vlans(linux, state)
        assert await v.configure(policy()) == {"applied": False, "state": "pending"}
        assert not linux.commands
        assert state.stat().st_mode & 0o777 == 0o600
        await v.reconcile()
        bridge_command = linux.commands[0]
        assert bridge_command[bridge_command.index("stp_state") + 1] == "1"
        assert ("bridge", "vlan", "add", "dev", "eth0", "vid", "10", "pvid", "untagged") in linux.commands
        assert ("bridge", "vlan", "add", "dev", "eth1", "vid", "10") in linux.commands
        assert ("bridge", "vlan", "add", "dev", "eth1", "vid", "20", "pvid", "untagged") in linux.commands
        count = len(linux.commands)
        await v.reconcile()
        assert len(linux.commands) == count
        linux.current.append({"ifname": "eth2", "address": "02:00:00:00:00:03", "ifindex": 4})
        linux.current[0]["address"] = "02:00:00:00:00:ff"
        linux.commands.clear()
        restored = Vlans(linux, state)
        await restored.reconcile()
        added = [c for c in linux.commands if c[:3] == ("bridge", "vlan", "add")]
        assert all(c[4] == "eth1" for c in added)
        assert restored.applied is not None
    asyncio.run(scenario())


def test_default_vlan_and_failed_policy_retry(tmp_path):
    async def scenario():
        linux = FakeLinux()
        v = Vlans(linux, tmp_path / "vlans.json")
        await v.reconcile()
        assert any(c[-3:] == ("1", "pvid", "untagged") for c in linux.commands)
        await v.configure(policy())
        linux.fail = True
        with pytest.raises(ApiError) as error:
            await v.reconcile()
        assert error.value.code == "VLAN_APPLY_FAILED"
        assert v.last_error and v.applied is None
        linux.fail = False
        await v.reconcile()
        assert v.last_error is None and v.applied is not None
    asyncio.run(scenario())


def test_single_port_rules_persist_preserve_other_ports_and_isolation(tmp_path):
    async def scenario():
        linux = FakeLinux()
        state = tmp_path / "vlans.json"
        v = Vlans(linux, state)
        assert (await v.configure_port({"port": "eth0", "mode": "trunk", "allowed": [10, 20]}))["state"] == "pending"
        assert v.desired["eth1"]["vlan"] == 1
        await v.configure_port({"port": "eth1", "mode": "access", "vlan": 20})
        linux.current.append({"ifname": "eth2", "address": "02:00:00:00:00:03", "ifindex": 4})
        await v.configure_port({"port": "eth0", "mode": "trunk", "allowed": [10, 20], "native": 10})
        assert "eth2" not in v.desired
        assert v.desired["eth1"]["vlan"] == 20
        await v.configure_port({"port": "eth0", "mode": "trunk", "allowed": [10, 20]})
        assert "native" not in v.desired["eth0"]
        restored = Vlans(linux, state)
        await restored.reconcile()
        assert restored.desired == v.desired
        added = [c for c in linux.commands if c[:3] == ("bridge", "vlan", "add")]
        assert all(c[4] != "eth2" for c in added)
        assert ("bridge", "vlan", "add", "dev", "eth0", "vid", "10") in added
        assert ("bridge", "vlan", "add", "dev", "eth1", "vid", "20", "pvid", "untagged") in added
        original = state.read_bytes()
        for invalid in ({"port": "missing", "mode": "access", "vlan": 10},
                        {"port": "eth0", "mode": "trunk", "allowed": [10], "native": 20}):
            with pytest.raises(ApiError):
                await restored.configure_port(invalid)
            assert state.read_bytes() == original
        async with restored.lock:
            with pytest.raises(ApiError) as error:
                await restored.configure_port({"port": "eth0", "mode": "access", "vlan": 10})
            assert error.value.code == "BUSY"
    asyncio.run(scenario())


class FakeProcess:
    def __init__(self, data, eof=True):
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(data)
        if eof:
            self.stdout.feed_eof()
        self.returncode = None
        self.terminated = False

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    async def wait(self):
        return self.returncode


@pytest.mark.parametrize("endian", ["<", ">"])
def test_capture_packet_limit_grouping_and_fixed_filter(monkeypatch, endian):
    async def scenario():
        frames = [arp_frame(), arp_frame()]
        proc = FakeProcess(pcap(frames, endian))
        seen = []
        async def spawn(*args, **kwargs):
            seen.append(args)
            assert kwargs["stderr"] == asyncio.subprocess.DEVNULL
            return proc
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        c = Captures(FakeLinux())
        start = await c.start({"port": "eth0", "template": "arp", "packets": 2})
        await c.sessions[start["id"]]["task"]
        result = c.read(start["id"])
        assert result["reason"] == "packet_limit" and result["seen"] == 2
        assert len(result["events"]) == 1 and result["events"][0]["count"] == 2
        assert result["events"][0]["first_at"] == 123.5
        assert seen[0][seen[0].index("-w") + 1] == "-"
        assert "-Q" in seen[0] and "in" in seen[0] and "-p" in seen[0]
        assert proc.terminated
    asyncio.run(scenario())


def test_capture_bounds_stop_expiry_and_no_implicit_start(monkeypatch):
    async def scenario():
        spawned = []
        async def spawn(*args, **kwargs):
            proc = FakeProcess(pcap([]), eof=False)
            spawned.append(proc)
            return proc
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        c = Captures(FakeLinux())
        for args in [
            {"port": "eth0", "template": "arp", "seconds": 16},
            {"port": "eth0", "template": "arp", "packets": True},
            {"port": "br0", "template": "arp"},
            {"port": "eth0", "template": "arp", "filter": "anything"},
        ]:
            with pytest.raises(ApiError):
                await c.start(args)
        assert not spawned
        first = await c.start({"port": "eth0", "template": "arp"})
        with pytest.raises(ApiError) as error:
            await c.start({"port": "eth0", "template": "vlan"})
        assert error.value.code == "CAPTURE_BUSY"
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        c.read(first["id"])
        assert len(spawned) == 1
        assert (await c.stop(first["id"]))["reason"] == "stopped"
        assert spawned[0].terminated
        assert (await c.stop(first["id"]))["state"] == "done"
        c.sessions[first["id"]]["completed"] -= 121
        with pytest.raises(ApiError) as expired:
            c.read(first["id"])
        assert expired.value.code == "CAPTURE_NOT_FOUND"
    asyncio.run(scenario())


def test_capture_timeout_and_spawn_failure(monkeypatch):
    async def scenario():
        async def spawn(*args, **kwargs):
            return FakeProcess(pcap([]), eof=False)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        c = Captures(FakeLinux())
        s = await c.start({"port": "eth0", "template": "arp", "seconds": 1})
        await c.sessions[s["id"]]["task"]
        assert c.read(s["id"])["reason"] == "timeout"
        async def broken(*args, **kwargs):
            raise FileNotFoundError("tcpdump missing")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", broken)
        s = await c.start({"port": "eth1", "template": "arp"})
        await c.sessions[s["id"]]["task"]
        assert c.read(s["id"])["state"] == "failed"
        await c.close()
    asyncio.run(scenario())


def test_stream_output_limit():
    async def scenario():
        stream = asyncio.StreamReader()
        stream.feed_data(b"12345")
        stream.feed_eof()
        with pytest.raises(ApiError) as error:
            await Linux.bounded(stream, 4)
        assert error.value.code == "RESULT_TOO_LARGE"
    asyncio.run(scenario())


def test_command_output_is_bounded_and_child_reaped():
    async def scenario():
        linux = Linux()
        assert await linux.run(sys.executable, "-c", "print('ok')") == b"ok\n"
        for fd in (1, 2):
            with pytest.raises(ApiError) as error:
                await asyncio.wait_for(linux.run(
                    sys.executable, "-c", f"import os; os.write({fd}, b'x' * 5000000)"
                ), 3)
            assert error.value.code == "RESULT_TOO_LARGE"
    asyncio.run(scenario())


def test_capture_rejects_oversized_record_and_limits_events(monkeypatch):
    async def scenario():
        proc = FakeProcess(pcap([]) + struct.pack("<IIII", 1, 0, 65536, 65536))
        async def spawn(*args, **kwargs):
            return proc
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        c = Captures(FakeLinux())
        start = await c.start({"port": "eth0", "template": "ethernet"})
        await c.sessions[start["id"]]["task"]
        assert c.read(start["id"])["state"] == "failed"
        assert proc.terminated
        session = {"events": []}
        for i in range(128):
            c.add_event(session, {"source": str(i)}, i, 60, 60)
        assert len(session["events"]) == 64
        c.add_event(session, {"source": "0"}, 200, 60, 60)
        assert session["events"][0]["count"] == 2
    asyncio.run(scenario())


def test_capture_immediate_stop_and_retention_bound():
    async def scenario():
        c = Captures(FakeLinux())
        first = None
        for _ in range(10):
            started = await c.start({"port": "eth0", "template": "arp"})
            first = first or started["id"]
            result = await c.stop(started["id"])
            assert result["state"] == "done" and result["reason"] == "stopped"
        assert len(c.sessions) == 8
        assert first not in c.sessions
    asyncio.run(scenario())


def test_rpc_and_cli_over_real_unix_socket(tmp_path):
    async def scenario():
        service = Service(FakeLinux(), tmp_path / "state.json")
        path = str(tmp_path / "api.sock")
        server = await asyncio.start_unix_server(service.handle, path=path, limit=65536)
        async def rpc(payload):
            reader, writer = await asyncio.open_unix_connection(path)
            writer.write(payload + b"\n")
            await writer.drain()
            result = json.loads(await reader.readline())
            writer.close()
            await writer.wait_closed()
            return result
        try:
            reply = await rpc(json.dumps({"version": 1, "id": "a", "method": "fdb",
                                          "params": {"offset": 1, "limit": 2}}).encode())
            assert reply["ok"] and len(reply["result"]["entries"]) == 2
            assert reply["result"]["next_offset"] == 3
            assert not (await rpc(b"not json"))["ok"]
            assert not (await rpc(b"[]"))["ok"]
            assert not (await rpc(json.dumps({"version": 2, "id": "a", "method": "ports", "params": {}}).encode()))["ok"]
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "switch_api.cli", "--socket", path, "ports",
                stdout=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            assert proc.returncode == 0
            assert len(json.loads(stdout)["result"]["ports"]) == 2
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "switch_api.cli", "--socket", path,
                "capture.start", "--params", '{"port":"eth0","template":"arp","filter":"x"}',
                stdout=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            assert proc.returncode == 1
            assert json.loads(stdout)["error"]["code"] == "INVALID_ARGUMENT"
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "switch_api.cli", "--socket", path,
                "port.configure", "--params", '{"port":"eth0","mode":"trunk","allowed":[10,20]}',
                stdout=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            assert proc.returncode == 0 and json.loads(stdout)["result"]["state"] == "pending"
            assert service.vlans.desired["eth0"]["allowed"] == [10, 20]
            assert service.vlans.desired["eth1"]["vlan"] == 1
        finally:
            server.close()
            await server.wait_closed()
            await service.captures.close()
    asyncio.run(scenario())
