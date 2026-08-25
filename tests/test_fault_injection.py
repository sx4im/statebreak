"""Integration tests for deterministic failure injection across all supported fault types."""

from __future__ import annotations

from statebreak.clock import VirtualClock
from statebreak.faults import FaultScheduler
from statebreak.models import FaultSpec
from statebreak.world import LocalWorld


def test_fault_stale_read_injection() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "example-001", "status": "pending"}]})

    # Mutate world to v2
    world.update_entity("example-001", {"status": "completed"})
    assert world.get_entity_version("example-001") == "v2"

    fault = FaultSpec(
        id="stale-01",
        at="after_read",
        type="stale_read",
        target="example-001",
        params={"stale_version": "v1", "stale_status": "pending"},
    )
    scheduler = FaultScheduler([fault])

    # Agent reads authoritative state (which is currently completed at v2)
    obs = world.get_entity("example-001")
    assert obs is not None
    assert obs["version"] == "v2"
    assert obs["status"] == "completed"

    # Scheduler intercepts after_read and injects stale observation
    res = scheduler.after_read("example-001", obs, world, clock)
    assert res.applied is True
    assert res.modified_observation is not None
    assert res.modified_observation["version"] == "v1"
    assert res.modified_observation["status"] == "pending"

    # Authoritative world state was NOT mutated
    fresh = world.get_entity("example-001")
    assert fresh is not None
    assert fresh["version"] == "v2"
    assert fresh["status"] == "completed"

    # Event was recorded
    evts = scheduler.get_events()
    assert len(evts) == 1
    assert evts[0].fault_type == "stale_read"
    assert evts[0].before_hash != evts[0].after_hash


def test_fault_approval_expired_injection() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z", step_seconds=60)
    world = LocalWorld({
        "approvals": [
            {
                "id": "appr-001",
                "subject": "refund-1001",
                "amount": 25.0,
                "expires_at": "2026-01-01T09:00:30Z",
                "status": "approved",
            }
        ],
        "refunds": [{"id": "refund-1001", "status": "pending"}],
    })

    fault = FaultSpec(
        id="expire-appr",
        at="before_commit",
        type="approval_expired",
        target="appr-001",
    )
    scheduler = FaultScheduler([fault])

    # Initially valid approval
    assert world.check_approval("appr-001", clock).is_valid is True

    # Scheduler intercepts before_commit and advances clock or expires approval
    res = scheduler.before_commit("refund-1001", "op_1", {"status": "refunded"}, world, clock)
    assert res.applied is True

    # Subsequent approval check now evaluates to expired!
    obs_after = world.check_approval("appr-001", clock)
    assert obs_after.is_valid is False
    assert obs_after.status == "expired"


def test_fault_timeout_after_commit_injection() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "example-001", "status": "pending"}]})

    fault = FaultSpec(
        id="timeout-commit",
        at="after_commit_before_response",
        type="timeout_after_commit",
        target="example-001",
    )
    scheduler = FaultScheduler([fault])

    # Commit mutation in world
    mutation_res = world.update_entity(
        "example-001",
        updates={"status": "completed"},
        operation_id="op_tx_1",
    )
    assert mutation_res.success is True
    assert mutation_res.status == "committed"

    # Scheduler intercepts after_commit_before_response and turns response to unknown
    dispatch_res = scheduler.after_commit_before_response(
        "example-001",
        "op_tx_1",
        mutation_res,
        world,
        clock,
    )
    assert dispatch_res.applied is True
    assert dispatch_res.modified_result is not None
    assert dispatch_res.modified_result.status == "unknown"

    # But authoritative world state remains committed
    ent = world.get_entity("example-001")
    assert ent is not None
    assert ent["status"] == "completed"
    assert ent["version"] == "v2"

    # Authoritative effect in ledger remains committed
    eff = world.get_effect_by_operation("op_tx_1")
    assert eff is not None
    assert eff.status == "committed"


def test_fault_duplicate_retry_injection() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({
        "entities": [{"id": "example-001", "status": "pending", "charge_count": 0}]
    })

    fault = FaultSpec(
        id="dup-retry",
        at="before_retry",
        type="duplicate_retry",
        target="example-001",
    )
    scheduler = FaultScheduler([fault])

    # First attempt with operation_id="op_charge_1"
    res1 = world.update_entity(
        "example-001",
        updates={"status": "charged", "charge_count": 1},
        operation_id="op_charge_1",
    )
    assert res1.success is True
    assert world.get_entity_version("example-001") == "v2"

    # Scheduler intercepts before_retry
    dispatch_res = scheduler.before_retry("example-001", "op_charge_1", world, clock)
    assert dispatch_res.applied is True

    # Second attempt (retry) with same operation_id="op_charge_1" is idempotent
    res2 = world.update_entity(
        "example-001",
        updates={"status": "charged", "charge_count": 2},
        operation_id="op_charge_1",
    )
    assert res2.success is True
    # Version remains v2 and charge_count remains 1
    assert world.get_entity_version("example-001") == "v2"
    ent = world.get_entity("example-001")
    assert ent is not None
    assert ent["charge_count"] == 1


def test_fault_wrong_target_injection() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({
        "entities": [
            {"id": "account-100", "balance": 1000},
            {"id": "account-100-drift", "balance": 0},
        ]
    })

    fault = FaultSpec(
        id="wrong-tgt",
        at="before_commit",
        type="wrong_target",
        target="account-100",
        params={"substitute_target": "account-100-drift"},
    )
    scheduler = FaultScheduler([fault])

    # Scheduler intercepts before_commit and substitutes target
    dispatch_res = scheduler.before_commit(
        "account-100",
        "op_transfer",
        {"balance": 500},
        world,
        clock,
    )
    assert dispatch_res.applied is True
    assert dispatch_res.modified_target == "account-100-drift"

    # Mutation redirected to substitute target leaves original intact
    world.update_entity(
        dispatch_res.modified_target,
        updates={"balance": 500},
        operation_id="op_transfer",
    )

    orig_ent = world.get_entity("account-100")
    assert orig_ent is not None
    assert orig_ent["balance"] == 1000  # Original not modified
    assert orig_ent["version"] == "v1"

    drift_ent = world.get_entity("account-100-drift")
    assert drift_ent is not None
    assert drift_ent["balance"] == 500
    assert drift_ent["version"] == "v2"


def test_fault_partial_write_injection() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({
        "entities": [
            {"id": "record-01", "name": "Initial", "address": "Old", "synced": False}
        ]
    })

    fault = FaultSpec(
        id="part-write",
        at="after_commit_before_response",
        type="partial_write",
        target="record-01",
        params={"applied_fields": ["name"], "omitted_fields": ["address", "synced"]},
    )
    scheduler = FaultScheduler([fault])

    # World applies partial write
    mutation_res = world.partial_update_entity(
        entity_id="record-01",
        updates={"name": "Updated", "address": "New", "synced": True},
        applied_fields=["name"],
        omitted_fields=["address", "synced"],
        operation_id="op_part_1",
    )
    assert mutation_res.status == "partial"

    dispatch_res = scheduler.after_commit_before_response(
        "record-01",
        "op_part_1",
        mutation_res,
        world,
        clock,
    )
    assert dispatch_res.applied is True
    assert dispatch_res.modified_result is not None
    assert dispatch_res.modified_result.status == "partial"
    assert dispatch_res.modified_result.applied_fields == ("name",)
    assert dispatch_res.modified_result.omitted_fields == ("address", "synced")


def test_fault_handoff_truncation_injection() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")

    fault = FaultSpec(
        id="handoff-loss",
        at="handoff_emit",
        type="handoff_truncation",
        params={"truncated_fields": ["safety_constraints", "approval_token"]},
    )
    scheduler = FaultScheduler([fault])

    payload = {
        "task_id": "task-999",
        "summary": "Transfer requested",
        "safety_constraints": "Must verify approval-expiry",
        "approval_token": "token-xyz",
    }

    dispatch_res = scheduler.handoff_emit(payload, clock)
    assert dispatch_res.applied is True
    assert dispatch_res.modified_handoff is not None
    assert "safety_constraints" not in dispatch_res.modified_handoff
    assert "approval_token" not in dispatch_res.modified_handoff
    assert dispatch_res.modified_handoff["task_id"] == "task-999"
    assert dispatch_res.modified_handoff["summary"] == "Transfer requested"


def test_deterministic_replay_across_runs() -> None:
    """Verify that running identical sequences with the same seed yields identical outputs."""
    def execute_simulation(seed: int) -> tuple[str, list[dict[str, object]]]:
        clock = VirtualClock("2026-01-01T09:00:00Z", step_seconds=30)
        world = LocalWorld({"entities": [{"id": "e1", "status": "pending", "val": 0}]})
        faults = (
            FaultSpec(id="f1", at="after_read", type="stale_read", target="e1"),
            FaultSpec(
                id="f2",
                at="after_commit_before_response",
                type="timeout_after_commit",
                target="e1",
            ),
        )
        sched = FaultScheduler(faults)

        # 1. Read
        obs = world.get_entity("e1")
        assert obs is not None
        _ = sched.after_read("e1", obs, world, clock)

        # 2. Advance time and update
        clock.step()
        mut = world.update_entity("e1", {"status": "done", "val": 10}, operation_id="op_1")
        _ = sched.after_commit_before_response("e1", "op_1", mut, world, clock)

        snap = world.snapshot(clock)
        events = [e.to_dict() for e in sched.get_events()]
        return snap.entities_hash, events

    run1 = execute_simulation(seed=42)
    run2 = execute_simulation(seed=42)

    assert run1 == run2
    assert len(run1[1]) == 2
