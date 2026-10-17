import json

import httpx
import pytest

from lab_manager.agent_client import (CODES, AgentBusy, AgentClient, AgentCommandFailed, AgentNotStopped,
                                      AgentRejected, AgentSourceMissing, AgentUncertain, AgentWrongNode)


def client(handler):
    return AgentClient("https://{node}.mgmt:8443", transport=httpx.MockTransport(handler),
                       start_timeout=330, stop_timeout=100)


async def test_requests_match_agent_api():
    seen = []

    def handler(request):
        seen.append((request.url.host, request.method, request.url.path,
                     json.loads(request.content) if request.content else None,
                     request.extensions["timeout"]["read"]))
        if request.url.path.endswith("/status"):
            return httpx.Response(200, json={"vmid": 2001, "state": "stopped"})
        return httpx.Response(200, json={"vmid": 2001, "state": "running", "completed_actions": []})

    agent = client(handler)
    assert await agent.status("pve2", 2001) == "stopped"
    await agent.set_mounts("pve2", 2001, student_key="main-42", data_mode="ro")
    await agent.start("pve2", 2001, student_key="main-42", code_revision="r7",
                      actions=("init_slot", "start_slot_service"))
    await agent.stop("pve2", 2001, force=True)
    assert seen == [
        ("pve2.mgmt", "GET", "/slots/2001/status", None, 30),
        ("pve2.mgmt", "PUT", "/slots/2001/mounts",
         {"code_revision": None, "student_key": "main-42", "data_mode": "ro"}, 30),
        ("pve2.mgmt", "POST", "/slots/2001/start",
         {"code_revision": "r7", "student_key": "main-42", "data_mode": "rw",
          "actions": ["init_slot", "start_slot_service"]}, 330),
        ("pve2.mgmt", "POST", "/slots/2001/stop", {"force": True}, 100),
    ]
    await agent.close()


@pytest.mark.parametrize("status,detail,exc", [
    (403, "Container is not marked as a lab slot", AgentRejected),
    (403, "VMID outside allowed lab range", AgentRejected),
    (404, "LXC not found on this node", AgentWrongNode),
    (409, "Another operation on this slot is in progress", AgentBusy),
    (409, "Slot must be stopped to change mount points", AgentNotStopped),
    (409, "Proxmox command failed: CT 2001 already running", AgentCommandFailed),
    (422, "Required CephFS directory does not exist", AgentSourceMissing),
    (422, "Mount ancestor is not administrator-controlled", AgentRejected),
    (503, "CephFS is not mounted at its expected root", AgentUncertain),
    (504, "Proxmox command timed out; check actual container state", AgentUncertain),
])
async def test_error_classification(status, detail, exc):
    agent = client(lambda r: httpx.Response(status, json={"detail": detail}))
    with pytest.raises(exc) as err:
        await agent.status("pve1", 2001)
    assert err.value.message == detail
    await agent.close()


@pytest.mark.parametrize("code", sorted(CODES))
async def test_error_codes_take_precedence_over_text(code):
    # Codes requested in docs/AGENT_REQUESTS.md; the HTTP status does not matter then.
    body = {"detail": {"code": code, "message": "in progress / must be stopped / does not exist"}}
    agent = client(lambda r: httpx.Response(409, json=body))
    with pytest.raises(CODES[code]) as err:
        await agent.status("pve1", 2001)
    assert type(err.value) is CODES[code] and err.value.code == code
    await agent.close()


async def test_validation_error_list_is_rejection():
    body = {"detail": [{"type": "extra_forbidden", "loc": ["body", "x"], "msg": "Extra inputs"}]}
    agent = client(lambda r: httpx.Response(422, json=body))
    with pytest.raises(AgentRejected) as err:
        await agent.stop("pve1", 2001)
    assert not isinstance(err.value, AgentSourceMissing)
    await agent.close()


async def test_transport_errors_are_uncertain():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    agent = client(handler)
    with pytest.raises(AgentUncertain, match="outcome unknown"):
        await agent.start("pve1", 2001, student_key="main-1")
    await agent.close()


def test_mtls_material_is_required(tmp_path):
    with pytest.raises((OSError, TypeError, ValueError)):
        AgentClient("https://{node}:8443", ca_file=tmp_path / "missing.pem",
                    cert_file=tmp_path / "c", key_file=tmp_path / "k")
