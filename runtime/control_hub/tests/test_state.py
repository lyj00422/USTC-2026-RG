import json

import pytest

from control_hub.services.event_log import EventLog
from control_hub.state import ControlLease, HubState, LeaseConflict


def test_hub_state_exposes_line_module_for_uart_status():
    modules = {item.key: item for item in HubState().modules()}
    assert modules["arm"].available
    assert modules["camera"].available
    assert modules["chassis"].available
    assert modules["chassis"].href == "/chassis"
    assert modules["line"].available
    assert modules["line"].href == "/operate"
    assert not modules["encoders"].available


def test_control_lease_conflicts_and_expires():
    lease = ControlLease(ttl_ms=2000, token_factory=lambda: "token-1")
    token = lease.acquire("browser-a", now_ms=100)
    assert token == "token-1"
    assert lease.is_valid(token, now_ms=2099)
    with pytest.raises(LeaseConflict):
        lease.acquire("browser-b", now_ms=1000)
    assert not lease.is_valid(token, now_ms=2101)


def test_control_lease_heartbeat_extends_expiry():
    lease = ControlLease(ttl_ms=100, token_factory=lambda: "token")
    token = lease.acquire("browser", now_ms=0)
    lease.heartbeat(token, now_ms=90)
    assert lease.is_valid(token, now_ms=189)


def test_event_log_is_bounded_and_persists_jsonl(tmp_path):
    path = tmp_path / "hub.jsonl"
    log = EventLog(path, capacity=2)
    log.append("connected", "arm", {"state": "LOCKED"})
    log.append("status", "arm", {"state": "READY"})
    log.append("snapshot", "camera", {"file": "a.jpg"})
    assert [event["type"] for event in log.recent()] == ["status", "snapshot"]
    persisted = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(persisted) == 3
    assert [item["sequence"] for item in persisted] == [1, 2, 3]
