"""Integration tests and demonstrations for multi-node coordination and convergence."""

from __future__ import annotations

from statebreak.adapter import AdapterContext
from statebreak.adapters import MultiNodeAdapter
from statebreak.clock import VirtualClock
from statebreak.convergence import ConvergenceTracker
from statebreak.coordination import MessageQueue
from statebreak.faults import FaultScheduler
from statebreak.gateway import ToolGateway
from statebreak.world import LocalWorld


def test_two_nodes_commit_and_broadcast_convergence() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "shared-doc-01", "status": "draft"}]})
    sched = FaultScheduler([], seed=42)
    gateway = ToolGateway(world, sched, clock)
    queue = MessageQueue(nodes=["node-01", "node-02"], run_id="run-demo-1")
    tracker = ConvergenceTracker()

    ctx1 = AdapterContext(
        "Sync document",
        (),
        gateway=gateway,
        clock=clock,
        node_id="node-01",
        run_id="run-demo-1",
        coordination=queue,
    )
    ctx2 = AdapterContext(
        "Sync document",
        (),
        gateway=gateway,
        clock=clock,
        node_id="node-02",
        run_id="run-demo-1",
        coordination=queue,
    )

    adapter1 = MultiNodeAdapter()
    adapter2 = MultiNodeAdapter()

    # Node 1 executes: commits v2 and broadcasts to queue
    res1 = adapter1.run(ctx1)
    assert res1.status == "completed"
    assert world.get_entity_version("shared-doc-01") == "v2"
    assert queue.has_messages("node-02")

    # Node 2 receives broadcast and processes
    res2 = adapter2.run(ctx2)
    assert res2.status == "completed"
    assert any(c.name == "peer_update_received" for c in res2.claims)

    # Verify convergence tracking
    tracker.observe("node-01", "shared-doc-01", {"version": "v2"})
    tracker.observe("node-02", "shared-doc-01", {"version": "v2"})
    assert tracker.is_converged("node-01", "shared-doc-01", world) is True
    assert tracker.is_converged("node-02", "shared-doc-01", world) is True


def test_concurrent_proposals_conflict_and_reconciliation() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "shared-doc-01", "status": "draft"}]})
    sched = FaultScheduler([], seed=42)
    gateway = ToolGateway(world, sched, clock)
    tracker = ConvergenceTracker()

    # Both nodes observe v1
    obs1 = gateway.read("read", "shared-doc-01")
    obs2 = gateway.read("read", "shared-doc-01")
    tracker.observe("node-01", "shared-doc-01", obs1)
    tracker.observe("node-02", "shared-doc-01", obs2)

    # Node 1 commits first with expected_version="v1" -> commits v2
    out1 = gateway.act(
        "commit",
        "shared-doc-01",
        {"status": "node1_edit"},
        operation_id="op_node1",
        expected_version="v1",
        node_id="node-01",
    )
    assert out1.status == "committed"
    assert out1.state_version == "v2"
    tracker.record_outcome("node-01", "op_node1", out1)

    # Node 2 proposes with stale expected_version="v1" -> rejected
    out2 = gateway.act(
        "commit",
        "shared-doc-01",
        {"status": "node2_edit"},
        operation_id="op_node2",
        expected_version="v1",
        node_id="node-02",
    )
    assert out2.status == "rejected"
    assert "version conflict" in (out2.error or "")

    # Node 2 is not converged until reconciliation
    assert tracker.is_converged("node-02", "shared-doc-01", world) is False

    # Node 2 reconciles
    fresh_obs = tracker.reconcile("node-02", "shared-doc-01", world, clock)
    assert fresh_obs.state_version == "v2"
    assert tracker.is_converged("node-02", "shared-doc-01", world) is True


def test_three_node_duplicate_and_reorder_tolerance() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    queue = MessageQueue(nodes=["node-01", "node-02", "node-03"], run_id="run-demo-3")

    # Node 1 sends messages to Node 2
    m1 = queue.send("node-01", "node-02", "state_update", {"v": 1}, clock=clock)
    queue.send("node-01", "node-02", "state_update", {"v": 2}, clock=clock)

    # Inject duplicate of m1
    queue.duplicate_message(m1.message_id)
    assert queue.pending_count("node-02") == 3

    # Run Node 2 adapter with deduplication
    ctx2 = AdapterContext(
        "Sync",
        (),
        gateway=None,
        clock=clock,
        node_id="node-02",
        coordination=queue,
    )
    adapter2 = MultiNodeAdapter()
    res2 = adapter2.run(ctx2)

    assert res2.status == "completed"
    assert any(c.name == "duplicate_message_ignored" for c in res2.claims)


def test_three_node_deterministic_replay() -> None:
    def run_simulation(seed: int) -> tuple[str, str, int]:
        clk = VirtualClock("2026-01-01T09:00:00Z")
        w = LocalWorld({
            "entities": [
                {"id": "doc-A", "status": "init", "val": 10},
                {"id": "doc-B", "status": "init", "val": 20},
            ]
        })
        sched = FaultScheduler([], seed=seed)
        gw = ToolGateway(w, sched, clk)
        q = MessageQueue(nodes=["node-01", "node-02", "node-03"], run_id=f"run_{seed}")
        tr = ConvergenceTracker()

        # Step 1: Node 1 mutates doc-A
        gw.act(
            "update",
            "doc-A",
            {"val": 100},
            operation_id="op_n1_A",
            expected_version="v1",
            node_id="node-01",
        )
        q.send("node-01", "*", "state_update", {"id": "doc-A", "version": "v2"}, clock=clk)

        # Step 2: Node 2 mutates doc-B
        gw.act(
            "update",
            "doc-B",
            {"val": 200},
            operation_id="op_n2_B",
            expected_version="v1",
            node_id="node-02",
        )
        q.send("node-02", "*", "state_update", {"id": "doc-B", "version": "v2"}, clock=clk)

        # Step 3: Node 3 processes all broadcasts and reconciles
        q.receive_all("node-03")
        tr.reconcile("node-03", "doc-A", w, clk)
        tr.reconcile("node-03", "doc-B", w, clk)

        snap = w.snapshot(clk)
        return snap.entities_hash, snap.snapshot_id, len(q.get_history())

    # Deterministic replay check: two identical runs produce identical hashes and snapshot IDs
    res_run1 = run_simulation(42)
    res_run2 = run_simulation(42)
    assert res_run1 == res_run2
