import json

import httpx
import pytest

from lab_manager.agent_client import (CODES, AgentBusy, AgentClient, AgentCommandFailed, AgentNotStopped,
                                      AgentQuarantined, AgentRejected, AgentSourceMissing, AgentUncertain,
                                      AgentWrongNode, start_budget_warnings)


def client(handler):
    return AgentClient("https://{node}.mgmt:8443", transport=httpx.MockTransport(handler))


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
         {"code_revision": None, "student_key": "main-42", "data_mode": "ro"}, 120),
        ("pve2.mgmt", "POST", "/slots/2001/start",
         {"code_revision": "r7", "student_key": "main-42", "data_mode": "rw",
          "actions": ["init_slot", "start_slot_service"]}, 720),
        ("pve2.mgmt", "POST", "/slots/2001/stop", {"force": True}, 130),
    ]
    await agent.close()


@pytest.mark.parametrize("status,detail,exc", [
    (403, "Container is not marked as a lab slot", AgentRejected),
    (403, "VMID outside allowed lab range", AgentRejected),
    (404, "LXC not found on this node", AgentWrongNode),
    (409, "Another operation on this slot is in progress", AgentBusy),
    (409, "Slot must be stopped to change mount points", AgentNotStopped),
    (409, "Proxmox command failed: CT 2001 already running", AgentCommandFailed),
    (409, "Existing student directory has a different owner", AgentRejected),
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


async def test_ensure_student():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("/main-7"):
            return httpx.Response(409, json={"detail": "Existing student directory has a different owner"})
        return httpx.Response(200, json={"student_key": "main-42", "ready": True})

    agent = client(handler)
    assert await agent.ensure_student("pve1", "main-42") == {"student_key": "main-42", "ready": True}
    assert seen == [("PUT", "/students/main-42", b"")]
    with pytest.raises(AgentRejected):
        await agent.ensure_student("pve1", "main-7")
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


async def test_reset_request_and_error_codes():
    seen = []

    def handler(request):
        seen.append((request.url.host, request.method, request.url.path, json.loads(request.content),
                     request.extensions["timeout"]["read"]))
        return httpx.Response(200, json={"vmid": 2001, "state": "stopped", "reset": True, "snapshot": "lab-clean"})

    agent = client(handler)
    assert (await agent.reset("pve2", 2001))["snapshot"] == "lab-clean"
    assert seen == [("pve2.mgmt", "POST", "/slots/2001/reset", {}, 270)]
    await agent.close()
    assert CODES["SNAPSHOT_MISSING"] is AgentRejected and CODES["SNAPSHOT_UNSAFE"] is AgentRejected


async def test_reset_on_an_agent_without_the_route_is_rejected_not_a_migration():
    agent = client(lambda request: httpx.Response(404, json={"detail": "Not Found"}))
    with pytest.raises(AgentRejected) as err:
        await agent.reset("pve2", 2001)
    assert err.value.code == "RESET_UNSUPPORTED"
    # A real "CT is not on this node" still means a migration.
    agent = client(lambda request: httpx.Response(404, json={"detail": {"code": "CT_NOT_ON_NODE", "message": "x"}}))
    with pytest.raises(AgentWrongNode):
        await agent.reset("pve2", 2001)


async def test_reset_answer_must_confirm_the_rollback():
    agent = client(lambda request: httpx.Response(200, json={"vmid": 2001, "state": "stopped"}))
    with pytest.raises(AgentUncertain):
        await agent.reset("pve2", 2001)


def uncertain_reply(code, status=409, **extra):
    return lambda request: httpx.Response(status, json={"detail": {"code": code, "message": "text", **extra}})


@pytest.mark.parametrize("code,status", [("COMMAND_UNCERTAIN", 409), ("PCT_TIMEOUT", 504)])
async def test_a_mark_of_the_agent_is_a_quarantine_not_a_wait(code, status):
    agent = client(uncertain_reply(code, status, command="exec"))
    with pytest.raises(AgentQuarantined) as err:
        await agent.start("pve1", 2001, student_key="main-1")
    assert isinstance(err.value, AgentUncertain)  # Code that only knows AgentUncertain still stops
    assert err.value.code == code
    assert err.value.command == "exec"


async def test_pct_timeout_of_a_mutating_call_from_an_agent_without_command_is_a_quarantine():
    agent = client(uncertain_reply("PCT_TIMEOUT", 504))  # Agent 1.2.0 names no command
    with pytest.raises(AgentQuarantined) as err:
        await agent.stop("pve1", 2001)
    assert err.value.command is None


@pytest.mark.parametrize("call", ["status", "status_command"])
async def test_a_timed_out_status_read_leaves_no_mark_and_is_only_uncertain(call):
    extra = {"command": "status"} if call == "status_command" else {}
    agent = client(uncertain_reply("PCT_TIMEOUT", 504, **extra))
    with pytest.raises(AgentUncertain) as err:
        await agent.status("pve1", 2001)
    assert not isinstance(err.value, AgentQuarantined)


@pytest.mark.parametrize("code,status", [("PCT_UNAVAILABLE", 503), ("STORAGE_UNAVAILABLE", 503),
                                         ("FENCE_STORAGE_ERROR", 503)])
async def test_failures_without_a_mark_keep_waiting(code, status):
    agent = client(uncertain_reply(code, status))
    with pytest.raises(AgentUncertain) as err:
        await agent.start("pve1", 2001, student_key="main-1")
    assert not isinstance(err.value, AgentQuarantined)


async def test_connection_loss_is_not_a_quarantine():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    agent = client(handler)
    with pytest.raises(AgentUncertain) as err:
        await agent.start("pve1", 2001, student_key="main-1")
    assert not isinstance(err.value, AgentQuarantined)


async def test_start_budget_of_the_agent():
    def handler(request):
        assert request.url.path == "/capabilities"
        return httpx.Response(200, json={"version": "1.2.1", "start_budget_seconds": 480})

    assert await client(handler).start_budget("pve1") == 480
    # An agent older than 1.2.1 has no such route.
    assert await client(lambda r: httpx.Response(404, json={"detail": "Not Found"})).start_budget("pve1") is None


async def test_start_budget_warning_names_the_node():
    agent = client(lambda r: httpx.Response(200, json={"start_budget_seconds": 1920}))
    lines = await start_budget_warnings(agent, ["pve1", "pve2"], 720)
    assert len(lines) == 2 and "pve1" in lines[0] and "1980" in lines[0]
    assert await start_budget_warnings(agent, ["pve1"], 3600) == []
    old = client(lambda r: httpx.Response(404, json={"detail": "Not Found"}))
    assert await start_budget_warnings(old, ["pve1"], 720) == []
