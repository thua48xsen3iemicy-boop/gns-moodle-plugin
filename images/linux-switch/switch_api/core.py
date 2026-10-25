"""Linux bridge inspection and persisted VLAN policy."""

import asyncio
import json
import os
import tempfile
from pathlib import Path


class ApiError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def require(condition, message):
    if not condition:
        raise ApiError("INVALID_ARGUMENT", message)


def integer(value, low, high):
    return type(value) is int and low <= value <= high


class Linux:
    @staticmethod
    async def bounded(stream, limit):
        data = bytearray()
        while True:
            chunk = await stream.read(min(65536, limit + 1 - len(data)))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if len(data) > limit:
                raise ApiError("RESULT_TOO_LARGE", "Linux command output exceeds limit")

    async def run(self, *args):
        try:
            proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except OSError as exc:
            raise ApiError("COMMAND_FAILED", str(exc)) from exc
        try:
            async def collect():
                readers = [asyncio.create_task(self.bounded(proc.stdout, 1024 * 1024)),
                           asyncio.create_task(self.bounded(proc.stderr, 4096))]
                try:
                    out, err = await asyncio.gather(*readers)
                    await proc.wait()
                    return out, err
                finally:
                    for task in readers:
                        task.cancel()
                    await asyncio.gather(*readers, return_exceptions=True)

            out, err = await asyncio.wait_for(collect(), 5)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            # Drain pipes after killing: wait() alone can block on a full pipe.
            await proc.communicate()
            raise
        if proc.returncode:
            raise ApiError("COMMAND_FAILED", err.decode(errors="replace")[:512])
        return out

    async def query(self, *args):
        return json.loads(await self.run(*args))

    async def ports(self):
        return await self.query("ip", "-j", "-s", "link", "show", "master", "br0")


def validate_policy(value, ports):
    require(isinstance(value, dict) and set(value) == {"ports"}, "Expected ports map")
    rules = value["ports"]
    require(isinstance(rules, dict) and set(rules) == set(ports),
            "Supply exactly all current bridge port names")
    require(len(rules) <= 64, "At most 64 ports")
    result = {}
    for name, rule in rules.items():
        require(isinstance(rule, dict), "Port rule must be an object")
        if rule.get("mode") == "access":
            require(set(rule) == {"mode", "vlan"} and integer(rule["vlan"], 1, 4094),
                    "Access rule needs vlan 1..4094")
        else:
            require(rule.get("mode") == "trunk" and
                    set(rule) <= {"mode", "allowed", "native"} and "allowed" in rule,
                    "Expected access or trunk rule")
            allowed = rule["allowed"]
            require(isinstance(allowed, list) and 1 <= len(allowed) <= 128 and
                    all(integer(v, 1, 4094) for v in allowed), "Invalid allowed VLAN list")
            require(len(set(allowed)) == len(allowed), "Duplicate VLAN IDs")
            if "native" in rule:
                require(integer(rule["native"], 1, 4094) and rule["native"] in allowed,
                        "Native VLAN must be allowed")
        # Bind policy to both name and MAC: replacement cards start isolated.
        result[name] = {**rule, "mac": ports[name].lower()}
    return result


class Vlans:
    def __init__(self, linux, state):
        self.linux, self.state = linux, Path(state)
        self.desired = None
        self.applied = None
        self.last_error = None
        self.lock = asyncio.Lock()
        if self.state.exists():
            stored = json.loads(self.state.read_text())
            require(isinstance(stored, dict), "Invalid stored VLAN policy")
            macs = {n: r["mac"] for n, r in stored.items()}
            clean = {n: {k: v for k, v in r.items() if k != "mac"} for n, r in stored.items()}
            self.desired = validate_policy({"ports": clean}, macs)

    def save(self, rules):
        self.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=self.state.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(rules, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.state)
            directory = os.open(self.state.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    async def configure(self, value):
        if self.lock.locked():
            raise ApiError("BUSY", "VLAN policy is being applied; retry later")
        async with self.lock:
            ports = await self.linux.ports()
            rules = validate_policy(value, {p["ifname"]: p["address"] for p in ports})
            self.save(rules)
            self.desired = rules
            self.applied = None
            return {"applied": False, "state": "pending"}

    async def reconcile(self):
        async with self.lock:
            await self._reconcile(await self.linux.ports())

    async def _reconcile(self, ports):
        signature = sorted((p["ifname"], p["address"].lower(), p["ifindex"]) for p in ports)
        if self.applied == signature:
            return {"applied": True}
        try:
            # No automatic VLAN 1 membership for cards attached in the future.
            await self.linux.run("ip", "link", "set", "dev", "br0", "type", "bridge",
                                 "vlan_default_pvid", "0", "vlan_filtering", "1")
            entries = await self.linux.query("bridge", "-j", "vlan", "show")
            targets = {p["ifname"] for p in ports} | {"br0"}
            for entry in entries:
                if entry["ifname"] not in targets:
                    continue
                for vlan in entry.get("vlans", []):
                    vid = str(vlan["vlan"])
                    if "vlanEnd" in vlan:
                        vid += "-" + str(vlan["vlanEnd"])
                    args = ["bridge", "vlan", "del", "dev", entry["ifname"], "vid", vid]
                    if entry["ifname"] == "br0":
                        args += ["self"]
                    await self.linux.run(*args)
            for port in ports:
                rule = (self.desired.get(port["ifname"]) if self.desired is not None else
                        {"mode": "access", "vlan": 1, "mac": port["address"].lower()})
                if not rule or rule["mac"] != port["address"].lower():
                    continue
                vlans = [rule["vlan"]] if rule["mode"] == "access" else rule["allowed"]
                native = rule["vlan"] if rule["mode"] == "access" else rule.get("native")
                for vid in vlans:
                    args = ["bridge", "vlan", "add", "dev", port["ifname"], "vid", str(vid)]
                    if vid == native:
                        args += ["pvid", "untagged"]
                    await self.linux.run(*args)
            self.applied = signature
            self.last_error = None
            return {"applied": True}
        except Exception as exc:
            self.last_error = str(exc)[:512]
            self.applied = None
            raise ApiError("VLAN_APPLY_FAILED", self.last_error) from exc

    async def read(self):
        return {"desired": self.desired, "last_error": self.last_error,
                "applied": self.applied is not None,
                "actual": await self.linux.query("bridge", "-j", "vlan", "show")}
