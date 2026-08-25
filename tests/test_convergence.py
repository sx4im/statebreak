"""Unit tests for ConvergenceTracker and multi-node state convergence."""

from __future__ import annotations

from statebreak.adapter import ToolObservation, ToolOutcome
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


def test_convergence_unknown_operation_blocks_convergence() -> None:
    world = LocalWorld({"entities": [{"id": "tx-01", "status": "pending"}]})
    tracker = ConvergenceTracker()

    obs = ToolObservation(
        "read", "tx-01", "v1", "2026-01-01T09:00:00Z", {"status": "pending"}
    )
    tracker.observe("node-01", "tx-01", obs)

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


def test_convergence_multi_node_discrepancy() -> None:
    world = LocalWorld({"entities": [{"id": "item-1", "val": 10}]})
    tracker = ConvergenceTracker()

    def obs_v1() -> ToolObservation:
        return ToolObservation(
            "read", "item-1", "v1", "2026-01-01T09:00:00Z", {"val": 10}
        )

    # Both nodes observe v1
    tracker.observe("node-01", "item-1", obs_v1())
    tracker.observe("node-02", "item-1", obs_v1())

    assert tracker.is_converged("node-01", "item-1", world) is True
    assert tracker.is_converged("node-02", "item-1", world) is True

    # Node-01 performs update and receives committed v2
    world.update_entity("item-1", {"val": 20}, operation_id="op_node1")
    out_node1 = ToolOutcome("committed", "eff_1", "op_node1", "item-1", state_version="v2")
    tracker.record_outcome("node-01", "op_node1", out_node1)

    # Node-01 is converged at v2; Node-02 is stale at v1
    assert tracker.is_converged("node-01", "item-1", world) is True
    assert tracker.is_converged("node-02", "item-1", world) is False
