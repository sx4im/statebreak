"""Unit tests for reference adapter definitions and protocol compliance."""

from __future__ import annotations

from statebreak.adapter import AdapterContext, AgentAdapter
from statebreak.adapters import GuardedAdapter, MultiNodeAdapter, NaiveAdapter
from statebreak.clock import VirtualClock


def test_reference_adapters_protocol_compliance() -> None:
    naive = NaiveAdapter()
    guarded = GuardedAdapter()
    multi = MultiNodeAdapter()

    assert isinstance(naive, AgentAdapter)
    assert isinstance(guarded, AgentAdapter)
    assert isinstance(multi, AgentAdapter)

    assert naive.name == "naive-adapter"
    assert guarded.name == "guarded-adapter"
    assert multi.name == "multi-node-adapter"


def test_reference_adapters_run_in_mock_context() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    ctx_naive = AdapterContext("Task 1", (), gateway=None, clock=clock)
    ctx_guarded = AdapterContext("Task 2", (), gateway=None, clock=clock)
    ctx_multi = AdapterContext("Task 3", (), gateway=None, clock=clock)

    res_naive = NaiveAdapter().run(ctx_naive)
    res_guarded = GuardedAdapter().run(ctx_guarded)
    res_multi = MultiNodeAdapter().run(ctx_multi)

    assert res_naive.status == "completed"
    assert res_guarded.status == "completed"
    assert res_multi.status == "completed"
