"""A new owner drains accepted work, persists a fence and rejects delayed old requests."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.lifecycle import Lifecycle
from test_slot import HEADERS, payload


def test_superseded_owner_cannot_deploy_or_stop(mock_server):
    client, projects, _ = mock_server
    r = client.post("/v1/fence", headers=HEADERS, json={"epoch": 2, "generation": 4})
    assert r.status_code == 200
    r = client.post("/v1/labs/deploy", headers=HEADERS, json=payload())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "STALE_OWNER"
    assert projects == {}
    r = client.post("/v1/fence", headers=HEADERS, json={"epoch": 1, "generation": 100})
    assert r.status_code == 409


def test_restart_rejects_previous_boot_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOT_STATE_DIR", str(tmp_path / "local-state"))
    async def scenario():
        service = Lifecycle(SimpleNamespace())
        await service.claim(5, 4)
        await service.close()
        service = Lifecycle(SimpleNamespace())
        try:
            assert not service.owns({"X-Lab-Epoch": "5", "X-Lab-Generation": "4"})
            with pytest.raises(HTTPException):
                await service.claim(5, 4)
            await service.claim(5, 5)
            assert service.owns({"X-Lab-Epoch": "5", "X-Lab-Generation": "5"})
            with pytest.raises(HTTPException):
                await service.claim(4, 999)
        finally:
            await service.close()
    asyncio.run(scenario())


def test_claim_drains_inflight_operation_before_accepting_new_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOT_STATE_DIR", str(tmp_path / "local-state"))
    async def scenario():
        service = Lifecycle(SimpleNamespace())
        await service.claim(1, 2)
        entered, resume = asyncio.Event(), asyncio.Event()
        async def operation():
            entered.set()
            await resume.wait()
        task = asyncio.create_task(operation())
        service.tasks.add(task)
        task.add_done_callback(service.tasks.discard)
        claim = asyncio.create_task(service.claim(2, 4))
        try:
            await entered.wait()
            await asyncio.sleep(0)
            assert not claim.done()
            assert not service.owns({"X-Lab-Epoch": "1", "X-Lab-Generation": "2"})
            assert not service.owns({"X-Lab-Epoch": "2", "X-Lab-Generation": "4"})
            resume.set()
            await asyncio.wait_for(claim, 2)
            assert service.owns({"X-Lab-Epoch": "2", "X-Lab-Generation": "4"})
        finally:
            resume.set()
            await asyncio.gather(task, claim, return_exceptions=True)
            await service.close()
    asyncio.run(scenario())
