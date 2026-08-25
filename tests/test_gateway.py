"""Unit tests for ToolGateway wrapping LocalWorld and FaultScheduler."""

from __future__ import annotations

import pytest

from statebreak.clock import VirtualClock
from statebreak.errors import UsageError
from statebreak.faults import FaultScheduler
from statebreak.gateway import ToolGateway
from statebreak.models import FaultSpec
from statebreak.world import LocalWorld


def test_gateway_tool_allowlist_enforcement() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "e1", "status": "pending"}]})
    sched = FaultScheduler([])
    gateway = ToolGateway(world, sched, clock, allowed_tools=("read_state", "commit_effect"))

    # Allowed read
    obs = gateway.read("read_state", "e1")
    assert obs.target == "e1"

    # Disallowed tool raises UsageError
    with pytest.raises(UsageError, match="not in declared allowed tools"):
        gateway.read("forbidden_tool", "e1")

    with pytest.raises(UsageError, match="not in declared allowed tools"):
        gateway.act("unauthorized_act", "e1", {"status": "done"}, operation_id="op_1")


def test_gateway_read_and_stale_fault_interception() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "e1", "status": "pending"}]})
    world.update_entity("e1", {"status": "completed"})  # Authoritative v2

    fault = FaultSpec(
        id="f-stale",
        at="after_read",
        type="stale_read",
        target="e1",
        params={"stale_version": "v1", "stale_status": "pending"},
    )
    sched = FaultScheduler([fault])
    gateway = ToolGateway(world, sched, clock)

    obs = gateway.read("read", "e1")
    assert obs.state_version == "v1"
    assert obs.data is not None
    assert obs.data["status"] == "pending"

    # Authoritative world remains v2
    assert world.get_entity_version("e1") == "v2"


def test_gateway_act_and_timeout_after_commit() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "e1", "status": "pending"}]})

    fault = FaultSpec(
        id="f-timeout",
        at="after_commit_before_response",
        type="timeout_after_commit",
        target="e1",
    )
    sched = FaultScheduler([fault])
    gateway = ToolGateway(world, sched, clock)

    outcome = gateway.act("commit", "e1", {"status": "completed"}, operation_id="op_tx_1")
    # Gateway returns unknown outcome to caller
    assert outcome.status == "unknown"
    assert outcome.operation_id == "op_tx_1"

    # Authoritative world state is committed
    ent = world.get_entity("e1")
    assert ent is not None
    assert ent["status"] == "completed"
    assert ent["version"] == "v2"


def test_gateway_act_wrong_target_interception() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({
        "entities": [
            {"id": "account-A", "balance": 100},
            {"id": "account-A-drift", "balance": 0},
        ]
    })

    fault = FaultSpec(
        id="f-drift",
        at="before_commit",
        type="wrong_target",
        target="account-A",
        params={"substitute_target": "account-A-drift"},
    )
    sched = FaultScheduler([fault])
    gateway = ToolGateway(world, sched, clock)

    outcome = gateway.act("transfer", "account-A", {"balance": 50}, operation_id="op_tr_1")
    assert outcome.status == "committed"
    assert outcome.target == "account-A-drift"

    # Original target unchanged
    orig = world.get_entity("account-A")
    assert orig is not None
    assert orig["balance"] == 100

    # Drift target mutated
    drift = world.get_entity("account-A-drift")
    assert drift is not None
    assert drift["balance"] == 50


def test_gateway_expected_version_conflict() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "e1", "status": "pending"}]})
    sched = FaultScheduler([])
    gateway = ToolGateway(world, sched, clock)

    # First update increments to v2
    out1 = gateway.act(
        "update",
        "e1",
        {"status": "in_progress"},
        operation_id="op_1",
        expected_version="v1",
    )
    assert out1.status == "committed"
    assert out1.state_version == "v2"

    # Second update with stale expected_version v1 is rejected
    out2 = gateway.act(
        "update",
        "e1",
        {"status": "done"},
        operation_id="op_2",
        expected_version="v1",  # Stale
    )
    assert out2.status == "rejected"
    assert "version conflict" in (out2.error or "")
    assert world.get_entity_version("e1") == "v2"


def test_gateway_idempotency_duplicate_operation() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "e1", "counter": 0}]})
    sched = FaultScheduler([])
    gateway = ToolGateway(world, sched, clock)

    out1 = gateway.act("increment", "e1", {"counter": 1}, operation_id="op_idempotent")
    assert out1.status == "committed"

    # Duplicate call returns committed outcome without re-incrementing
    out2 = gateway.act("increment", "e1", {"counter": 2}, operation_id="op_idempotent")
    assert out2.status == "committed"

    ent = world.get_entity("e1")
    assert ent is not None
    assert ent["counter"] == 1


def test_gateway_handoff_truncation_interception() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld()
    fault = FaultSpec(
        id="f-handoff",
        at="handoff_emit",
        type="handoff_truncation",
        params={"truncated_fields": ["auth_token"]},
    )
    sched = FaultScheduler([fault])
    gateway = ToolGateway(world, sched, clock)

    payload = {"task": "verify", "auth_token": "secret-123", "status": "ok"}
    emitted = gateway.emit_handoff(payload)
    assert "auth_token" not in emitted
    assert emitted["task"] == "verify"
