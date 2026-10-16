"""Bounded Telnet CLI exchange for trusted, prompt-aware IOL provisioning."""

import asyncio
import re
import time

from api.console import TelnetStripper


async def run_commands(port, commands, prompt):
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection("127.0.0.1", port), 3
    )
    parser = TelnetStripper()
    pattern = re.compile(prompt.encode())

    async def expect(seconds):
        data = bytearray()
        deadline = time.monotonic() + seconds
        while True:
            chunk = await asyncio.wait_for(
                reader.read(4096), max(0.01, deadline - time.monotonic())
            )
            if not chunk:
                raise ConnectionError("CLI closed before prompt")
            text, reply = parser.feed(chunk)
            data.extend(text)
            if reply:
                writer.write(reply)
                await writer.drain()
            if len(data) > 262144:
                raise RuntimeError("CLI response limit exceeded")
            if pattern.search(data):
                if re.search(
                    rb"(?im)^\s*%\s*(Invalid|Error|Incomplete|Ambiguous|Unknown)", data
                ):
                    raise RuntimeError("Cisco rejected provisioning command")
                return

    try:
        writer.write(b"\r\n")
        await writer.drain()
        await expect(30)
        for cmd in commands:
            writer.write(
                cmd["command"].encode().replace(b"\xff", b"\xff\xff") + b"\r\n"
            )
            await writer.drain()
            await expect(cmd["timeout_seconds"])
    finally:
        writer.close()
        await writer.wait_closed()
