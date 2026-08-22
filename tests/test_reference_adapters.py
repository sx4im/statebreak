"""Integration tests comparing NaiveAdapter vs GuardedAdapter across failure scenarios."""

from __future__ import annotations

from statebreak.adapter import AdapterContext
from statebreak.adapters import GuardedAdapter, NaiveAdapter
from statebreak.clock import VirtualClock
from statebreak.faults import FaultScheduler
from statebreak.gateway import ToolGateway
from statebreak.models import FaultSpec
from statebreak.world import LocalWorld


def test_stale_read_naive_vs_guarded() -> None:
    # World is at v2
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world_naive = LocalWorld({"entities": [{"id": "order-001", "status": "pending"}]})
    world_naive.update_entity("order-001", {"status": "in_progress"})  # Now v2

    world_guarded = LocalWorld({"entities": [{"id": "order-001", "status": "pending"}]})
    world_guarded.update_entity("order-001", {"status": "in_progress"})  # Now v2

    fault = FaultSpec(
        id="f-stale",
        at="after_read",
        type="stale_read",
        target="order-001",
        params={"stale_version": "v1", "stale_status": "pending"},
    )

    sched_naive = FaultScheduler([fault], seed=42)
    gw_naive = ToolGateway(world_naive, sched_naive, clock)
    ctx_naive = AdapterContext("Process order", (), gateway=gw_naive, clock=clock)

    sched_guarded = FaultScheduler([fault], seed=42)
    gw_guarded = ToolGateway(world_guarded, sched_guarded, clock)
    ctx_guarded = AdapterContext("Process order", (), gateway=gw_guarded, clock=clock)

    # Naive adapter commits without version check -> overwrites authoritative state
    res_naive = NaiveAdapter().run(ctx_naive)
    assert res_naive.status == "completed"

    # Guarded adapter uses expected_version lock -> detects version conflict & needs_review
    res_guarded = GuardedAdapter().run(ctx_guarded)
    assert res_guarded.status == "needs_review"
    assert any(c.name == "stale_detected" for c in res_guarded.claims)


def test_timeout_after_commit_naive_vs_guarded() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world_naive = LocalWorld({"entities": [{"id": "order-001", "status": "pending"}]})
    world_guarded = LocalWorld({"entities": [{"id": "order-001", "status": "pending"}]})

    fault = FaultSpec(
        id="f-timeout",
        at="after_commit_before_response",
        type="timeout_after_commit",
        target="order-001",
    )

    sched_naive = FaultScheduler([fault], seed=42)
    gw_naive = ToolGateway(world_naive, sched_naive, clock)
    ctx_naive = AdapterContext("Process order", (), gateway=gw_naive, clock=clock)

    sched_guarded = FaultScheduler([fault], seed=42)
    gw_guarded = ToolGateway(world_guarded, sched_guarded, clock)
    ctx_guarded = AdapterContext("Process order", (), gateway=gw_guarded, clock=clock)

    # Naive blindly assumes success on unknown outcome
    res_naive = NaiveAdapter().run(ctx_naive)
    assert res_naive.status == "completed"
    assert any(c.name == "task_completed" and c.value is True for c in res_naive.claims)

    # Guarded reconciles authoritative state to verify commit before claiming completion
    res_guarded = GuardedAdapter().run(ctx_guarded)
    assert res_guarded.status == "completed"
    assert any(c.name == "reconciled" and c.value is True for c in res_guarded.claims)


def test_wrong_target_drift_naive_vs_guarded() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world_naive = LocalWorld({
        "entities": [
            {"id": "order-001", "status": "pending"},
            {"id": "order-001-drift", "status": "pending"},
        ]
    })
    world_guarded = LocalWorld({
        "entities": [
            {"id": "order-001", "status": "pending"},
            {"id": "order-001-drift", "status": "pending"},
        ]
    })

    fault = FaultSpec(
        id="f-drift",
        at="before_commit",
        type="wrong_target",
        target="order-001",
        params={"substitute_target": "order-001-drift"},
    )

    sched_naive = FaultScheduler([fault], seed=42)
    gw_naive = ToolGateway(world_naive, sched_naive, clock)
    ctx_naive = AdapterContext("Process order", (), gateway=gw_naive, clock=clock)

    sched_guarded = FaultScheduler([fault], seed=42)
    gw_guarded = ToolGateway(world_guarded, sched_guarded, clock)
    ctx_guarded = AdapterContext("Process order", (), gateway=gw_guarded, clock=clock)

    # Naive fails to notice target drift
    res_naive = NaiveAdapter().run(ctx_naive)
    assert res_naive.status == "completed"

    # Guarded detects target drift and returns needs_review
    res_guarded = GuardedAdapter().run(ctx_guarded)
    assert res_guarded.status == "needs_review"
    assert any(c.name == "target_verified" and c.value is False for c in res_guarded.claims)


def test_partial_write_naive_vs_guarded() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world_naive = LocalWorld({
        "entities": [{"id": "order-001", "status": "pending", "audit": ""}]
    })
    world_guarded = LocalWorld({
        "entities": [{"id": "order-001", "status": "pending", "audit": ""}]
    })

    fault = FaultSpec(
        id="f-partial",
        at="after_commit_before_response",
        type="partial_write",
        target="order-001",
        params={"omitted_fields": ["audit"]},
    )

    sched_naive = FaultScheduler([fault], seed=42)
    gw_naive = ToolGateway(world_naive, sched_naive, clock)
    ctx_naive = AdapterContext("Process order", (), gateway=gw_naive, clock=clock)

    sched_guarded = FaultScheduler([fault], seed=42)
    gw_guarded = ToolGateway(world_guarded, sched_guarded, clock)
    ctx_guarded = AdapterContext("Process order", (), gateway=gw_guarded, clock=clock)

    # Naive ignores partial write
    res_naive = NaiveAdapter().run(ctx_naive)
    assert res_naive.status == "completed"

    # Guarded detects partial write and flags needs_review
    res_guarded = GuardedAdapter().run(ctx_guarded)
    assert res_guarded.status == "needs_review"
    assert any(c.name == "partial_write_detected" for c in res_guarded.claims)
