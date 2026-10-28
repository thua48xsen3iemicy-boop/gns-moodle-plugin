"""Receiver-side ownership survives process restart and serializes with host work."""
import subprocess
import threading

import pytest
from fastapi import HTTPException, Request

import agent
from test_agent import env  # noqa: F401 - shared host/CT fixture

REAL_RUN = agent.run_cmd


def test_old_and_missing_owners_cannot_touch_reassigned_slot(env):
    client, calls, state, *_ = env
    response = client.post("/slots/2201/claim", json={"epoch": 2, "generation": 4})
    assert response.status_code == 200
    for path, body in (("start", {"student_key": "student42"}), ("stop", {}),
                       ("mounts", {"student_key": "student42"}), ("size", {"cores": 2, "memory_mb": 1024}),
                       ("reset", {})):
        method = client.put if path in ("mounts", "size") else client.post
        response = method(f"/slots/2201/{path}", json=body)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "STALE_OWNER"
    assert calls == [] and state["value"] == "stopped"
    # A fresh service process reads the same file, rather than forgetting its fence.
    assert agent.fence_state(2201)["epoch"] == 2
    assert client.post("/slots/2201/claim", json={"epoch": 1, "generation": 999}).status_code == 409


def test_size_is_fenced_and_only_allowed_while_stopped(env):
    client, calls, state, *_ = env
    response = client.put("/slots/2201/size", json={"cores": 8, "memory_mb": 16384})
    assert response.status_code == 200
    assert calls[-1] == [agent.PCT, "set", "2201", "--cores", "8", "--memory", "16384"]
    state["value"] = "running"
    assert client.put("/slots/2201/size", json={"cores": 8, "memory_mb": 16384}).status_code == 409


def test_timed_out_host_command_blocks_transfer_until_operator_reconciles(env, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)
    monkeypatch.setattr(agent.subprocess, "run", timeout)
    with agent.slot_lock(2201), pytest.raises(HTTPException):
        REAL_RUN([agent.PCT, "start", "2201"])
    assert agent.fence_state(2201)["uncertain"] is True
    client = env[0]
    response = client.post("/slots/2201/claim", json={"epoch": 100, "generation": 100})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "COMMAND_UNCERTAIN"


def test_timed_out_rollback_quarantines_the_slot(env, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)
    monkeypatch.setattr(agent.subprocess, "run", timeout)
    with agent.slot_lock(2201), pytest.raises(HTTPException):
        REAL_RUN([agent.PCT, "rollback", "2201", agent.CLEAN_SNAPSHOT])
    assert agent.fence_state(2201)["uncertain"] is True


def test_transfer_cannot_overtake_inflight_host_command(env, monkeypatch):
    _, calls, state, *_ = env
    entered, resume = threading.Event(), threading.Event()
    original = agent.run_cmd
    def delayed(args, timeout=45):
        if args[:2] == [agent.PCT, "start"]:
            entered.set()
            assert resume.wait(2)
        return original(args, timeout)
    monkeypatch.setattr(agent, "run_cmd", delayed)
    request = Request({"type": "http", "headers": [(b"x-lab-epoch", b"1"), (b"x-lab-generation", b"1")]})
    results = []
    def start():
        results.append(agent.start(2201, agent.Start(student_key="student42"), request))
    thread = threading.Thread(target=start)
    thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(HTTPException) as error:
            agent.claim(2201, agent.Fence(epoch=2, generation=4))
        assert error.value.detail["code"] == "SLOT_BUSY"
    finally:
        resume.set()
        thread.join(2)
    assert not thread.is_alive() and results[0]["state"] == "running"
    agent.claim(2201, agent.Fence(epoch=2, generation=4))
    with pytest.raises(HTTPException) as error:
        agent.stop(2201, agent.Stop(), request)
    assert error.value.detail["code"] == "STALE_OWNER"
    assert state["value"] == "running"


def test_agent_crash_keeps_write_ahead_marker_for_next_process(env, monkeypatch):
    def killed(*args, **kwargs):
        assert agent.fence_state(2201)["inflight"] is True
        raise KeyboardInterrupt("simulated service death during pct")
    monkeypatch.setattr(agent.subprocess, "run", killed)
    with agent.slot_lock(2201), pytest.raises(KeyboardInterrupt):
        REAL_RUN([agent.PCT, "start", "2201"])
    assert agent.fence_state(2201)["inflight"] is True
    with pytest.raises(HTTPException) as error:
        agent.claim(2201, agent.Fence(epoch=100, generation=100))
    assert error.value.detail["code"] == "COMMAND_UNCERTAIN"


def test_timed_out_guest_exec_quarantines_the_slot_and_names_the_command(env, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)
    monkeypatch.setattr(agent.subprocess, "run", timeout)
    with agent.slot_lock(2201), pytest.raises(HTTPException) as err:
        REAL_RUN([agent.PCT, "exec", "2201", "--", *agent.GUEST_ACTIONS["init_slot"]])
    assert err.value.status_code == 504
    assert err.value.detail["code"] == "PCT_TIMEOUT"
    assert err.value.detail["command"] == "exec"
    assert agent.fence_state(2201)["command"] == "exec"
    assert agent.fence_state(2201)["uncertain"] is True
    response = env[0].post("/slots/2201/claim", json={"epoch": 100, "generation": 100})
    assert response.json()["detail"]["code"] == "COMMAND_UNCERTAIN"
