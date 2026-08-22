"""Unit tests for authoritative OracleEngine and finding generation."""

from __future__ import annotations

from statebreak.clock import VirtualClock
from statebreak.convergence import ConvergenceTracker
from statebreak.faults import FaultEvent
from statebreak.models import (
    AdapterResult,
    AgentClaim,
    AgentTaskSpec,
    ClockSpec,
    EffectRecord,
    OracleSpec,
    Scenario,
)
from statebreak.oracle import OracleContext, OracleEngine
from statebreak.world import LocalWorld


def make_test_scenario(oracles: list[OracleSpec]) -> Scenario:
    return Scenario(
        schema="statebreak.scenario/v1",
        id="test-scenario",
        version=1,
        seed=42,
        clock=ClockSpec(start="2026-01-01T09:00:00Z"),
        world={"entities": [{"id": "order-001", "status": "pending"}]},
        faults=(),
        agent_task=AgentTaskSpec(instruction="Process order", tools=("read", "act")),
        oracles=tuple(oracles),
    )


def test_oracle_claim_requires_state_pass() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "order-001", "status": "completed"}]})
    engine = OracleEngine()

    oracle = OracleSpec(
        id="post-check",
        type="claim_requires_state",
        params={"claim": "task_completed", "expression": "status == completed"},
    )
    scenario = make_test_scenario([oracle])

    adapter_res = AdapterResult(
        claims=(AgentClaim("task_completed", True),),
        status="completed",
        adapter_name="test-adapter",
        adapter_version="1.0",
    )
    ctx = OracleContext(
        scenario_id="test-scenario",
        run_id="run-1",
        world=world,
        clock=clock,
        effects=(),
        fault_events=(),
        adapter_result=adapter_res,
    )

    result = engine.evaluate(scenario, ctx)
    assert result.verdict == "pass"
    assert len(result.findings) == 0


def test_oracle_claim_requires_state_false_success_failure() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    # World is still pending, but agent claimed success
    world = LocalWorld({"entities": [{"id": "order-001", "status": "pending"}]})
    engine = OracleEngine()

    oracle = OracleSpec(
        id="post-check",
        type="claim_requires_state",
        params={"claim": "task_completed", "expression": "status == completed"},
    )
    scenario = make_test_scenario([oracle])

    fault_ev = FaultEvent(
        event_id="evt_1",
        fault_id="f-stale",
        fault_type="stale_read",
        lifecycle_point="after_read",
        target_entity_id="order-001",
        virtual_timestamp="2026-01-01T09:00:00Z",
        before_hash="abc",
        after_hash="def",
        status="applied",
    )

    adapter_res = AdapterResult(
        claims=(AgentClaim("task_completed", True),),
        status="completed",
        adapter_name="naive-adapter",
        adapter_version="1.0",
    )
    ctx = OracleContext(
        scenario_id="test-scenario",
        run_id="run-1",
        world=world,
        clock=clock,
        effects=(),
        fault_events=(fault_ev,),
        adapter_result=adapter_res,
    )

    result = engine.evaluate(scenario, ctx)
    assert len(result.findings) >= 1
    assert any(f.blocking for f in result.findings)
    categories = {"authoritative_state_violation", "stale_observation_used"}
    assert any(f.category in categories for f in result.findings)


def test_oracle_forbidden_effect() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld()
    engine = OracleEngine()

    oracle = OracleSpec(
        id="forbidden-check",
        type="forbidden_effect",
        params={"target": "unauthorized-target"},
    )
    scenario = make_test_scenario([oracle])

    eff = EffectRecord(
        effect_id="eff_bad",
        operation_id="op_bad",
        kind="commit",
        target="unauthorized-target",
        status="committed",
        payload_hash="123",
        provider_id=None,
        event_refs=(),
    )
    adapter_res = AdapterResult((), "completed", "test", "1.0")
    ctx = OracleContext("test-scenario", "run-1", world, clock, (eff,), (), adapter_res)

    result = engine.evaluate(scenario, ctx)
    assert result.verdict == "fail"
    assert any(f.category == "forbidden_effect_committed" for f in result.findings)


def test_oracle_duplicate_effect_count() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld()
    engine = OracleEngine()

    oracle = OracleSpec(
        id="count-check",
        type="effect_count",
        params={"target": "order-001", "max_count": 1},
    )
    scenario = make_test_scenario([oracle])

    e1 = EffectRecord("eff_1", "op_1", "act", "order-001", "committed", "h1", None, ())
    e2 = EffectRecord("eff_2", "op_2", "act", "order-001", "committed", "h2", None, ())
    adapter_res = AdapterResult((), "completed", "test", "1.0")
    ctx = OracleContext("test-scenario", "run-1", world, clock, (e1, e2), (), adapter_res)

    result = engine.evaluate(scenario, ctx)
    assert result.verdict == "fail"
    assert any(f.category == "duplicate_effects_detected" for f in result.findings)


def test_oracle_unresolved_unknown_effect() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld()
    engine = OracleEngine()

    oracle = OracleSpec(id="unknown-check", type="no_unresolved_unknown_effect")
    scenario = make_test_scenario([oracle])

    eff_unknown = EffectRecord("eff_u", "op_u", "act", "order-001", "unknown", "h", None, ())
    adapter_res = AdapterResult((), "completed", "test", "1.0")
    ctx = OracleContext("test-scenario", "run-1", world, clock, (eff_unknown,), (), adapter_res)

    result = engine.evaluate(scenario, ctx)
    assert result.verdict == "fail"
    assert any(f.category == "unresolved_unknown_outcome" for f in result.findings)


def test_oracle_convergence_verified() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "shared-1", "val": 10}]})
    world.update_entity("shared-1", {"val": 20})  # Now v2
    tracker = ConvergenceTracker()
    tracker.observe("node-01", "shared-1", {"version": "v1"})  # Stale v1

    engine = OracleEngine()
    oracle = OracleSpec(id="conv-check", type="convergence_verified")
    scenario = make_test_scenario([oracle])

    adapter_res = AdapterResult((), "completed", "test", "1.0")
    ctx = OracleContext(
        "test-scenario",
        "run-1",
        world,
        clock,
        (),
        (),
        adapter_res,
        convergence_tracker=tracker,
    )

    result = engine.evaluate(scenario, ctx)
    assert result.verdict == "fail"
    assert any(f.category == "state_non_convergence" for f in result.findings)
