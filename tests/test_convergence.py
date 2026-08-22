"""Unit tests for ConvergenceTracker and multi-node state convergence."""

from __future__ import annotations

from statebreak.adapter import ToolObservation, ToolOutcome
from statebreak.clock import VirtualClock
from statebreak.convergence import ConvergenceTracker
from statebreak.world import LocalWorld


def test_convergence_single_node_fresh_vs_stale() -> None:
    world = LocalWorld({"entities": [{"id": "doc-01", "status": "draft"}]})
    tracker = ConvergenceTracker()

    # Node-01 observes initial state v1
    obs_v1 = ToolObservation("read", "doc-01", "v1", "2026-01-01T09:00:00Z", {"status": "draft"})
    tracker.observe("node-01", "doc-01", obs_v1)

    assert tracker.is_converged("node-01", "doc-01", world) is True

    # Authoritative world updates to v2
    world.update_entity("doc-01", {"status": "published"})
    assert world.get_entity_version("doc-01") == "v2"

    # Node-01 is now stale / non-converged
    assert tracker.is_converged("node-01", "doc-01", world) is False
    status_report = tracker.convergence_status("node-01", world)
    assert status_report["is_converged"] is False
    assert status_report["entities"]["doc-01"]["local_version"] == "v1"
    assert status_report["entities"]["doc-01"]["authoritative_version"] == "v2"


def test_convergence_reconcile_restores_convergence() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "doc-01", "status": "draft"}]})
    tracker = ConvergenceTracker()

    tracker.observe("node-01", "doc-01", {"version": "v1", "status": "draft"})
    world.update_entity("doc-01", {"status": "published"})

    assert tracker.is_converged("node-01", "doc-01", world) is False

    # Node reconciles against authoritative world
    fresh_obs = tracker.reconcile("node-01", "doc-01", world, clock)
    assert fresh_obs.state_version == "v2"
    assert tracker.is_converged("node-01", "doc-01", world) is True


def test_convergence_unknown_operation_blocks_convergence() -> None:
    world = LocalWorld({"entities": [{"id": "tx-01", "status": "pending"}]})
    tracker = ConvergenceTracker()

    tracker.observe("node-01", "tx-01", {"version": "v1", "status": "pending"})

    # Node records an in-flight proposal
    tracker.record_proposal(
        "node-01",
        "op_tx_1",
        "tx-01",
        "v1",
        {"status": "committed"},
    )

    # World commits the update to v2
    world.update_entity("tx-01", {"status": "committed"}, operation_id="op_tx_1")

    # Gateway returns unknown outcome to node
    unknown_outcome = ToolOutcome(
        status="unknown",
        effect_id="eff_1",
        operation_id="op_tx_1",
        target="tx-01",
        state_version="v2",
    )
    tracker.record_outcome("node-01", "op_tx_1", unknown_outcome)

    # Even though local version is v2, in-flight unknown operation blocks convergence
    assert tracker.is_converged("node-01", "tx-01", world) is False
    status = tracker.convergence_status("node-01", world)
    assert status["is_converged"] is False
    assert len(status["unreconciled_operations"]) == 1

    # Reconciling clears the unknown operation and restores convergence
    clock = VirtualClock("2026-01-01T09:00:00Z")
    tracker.reconcile("node-01", "tx-01", world, clock)
    assert tracker.is_converged("node-01", "tx-01", world) is True


def test_convergence_multi_node_discrepancy() -> None:
    world = LocalWorld({"entities": [{"id": "item-1", "val": 10}]})
    tracker = ConvergenceTracker()

    # Both nodes observe v1
    tracker.observe("node-01", "item-1", {"version": "v1", "val": 10})
    tracker.observe("node-02", "item-1", {"version": "v1", "val": 10})

    assert tracker.is_converged("node-01", "item-1", world) is True
    assert tracker.is_converged("node-02", "item-1", world) is True

    # Node-01 performs update and receives committed v2
    world.update_entity("item-1", {"val": 20}, operation_id="op_node1")
    out_node1 = ToolOutcome("committed", "eff_1", "op_node1", "item-1", state_version="v2")
    tracker.record_outcome("node-01", "op_node1", out_node1)

    # Node-01 is converged at v2; Node-02 is stale at v1
    assert tracker.is_converged("node-01", "item-1", world) is True
    assert tracker.is_converged("node-02", "item-1", world) is False


def test_convergence_apply_authoritative_snapshot() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "e1", "val": 1}, {"id": "e2", "val": 2}]})
    tracker = ConvergenceTracker()

    # World mutates
    world.update_entity("e1", {"val": 10})
    world.update_entity("e2", {"val": 20})

    # Node-02 is not converged
    assert tracker.is_converged("node-02", "e1", world) is False

    # Apply snapshot
    snap = world.snapshot(clock)
    tracker.apply_authoritative_snapshot("node-02", snap, world)

    assert tracker.is_converged("node-02", "e1", world) is True
    assert tracker.is_converged("node-02", "e2", world) is True
