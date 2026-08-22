"""Unit tests for deterministic metrics calculation."""

from __future__ import annotations

from statebreak.clock import VirtualClock
from statebreak.faults import FaultEvent
from statebreak.metrics import calculate_scenario_metrics
from statebreak.models import (
    AdapterResult,
    AgentClaim,
    AgentTaskSpec,
    ClockSpec,
    Finding,
    OracleSpec,
    Scenario,
)
from statebreak.oracle import OracleContext, OracleEvaluationResult
from statebreak.world import LocalWorld


def test_metrics_empty_evaluations_zero_denominators() -> None:
    metrics = calculate_scenario_metrics([])
    assert metrics["total_runs"] == 0.0
    assert metrics["pass_rate"] == 0.0
    assert metrics["unsafe_success_rate"] == 0.0
    assert metrics["stale_action_detection_rate"] == 0.0
    assert metrics["duplicate_effect_rate"] == 0.0
    assert metrics["safe_recovery_rate"] == 0.0


def test_metrics_mixed_outcomes_calculation() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld()
    scenario = Scenario(
        schema="statebreak.scenario/v1",
        id="sc-1",
        version=1,
        seed=42,
        clock=ClockSpec(start="2026-01-01T09:00:00Z"),
        world={},
        faults=(),
        agent_task=AgentTaskSpec(instruction="test", tools=()),
        oracles=(OracleSpec(id="o1", type="claim_requires_state"),),
    )

    # Run 1: Pass
    eval_pass = OracleEvaluationResult(
        verdict="pass",
        findings=(),
        oracle_results=({"status": "passed"},),
    )
    ctx_pass = OracleContext(
        scenario_id="sc-1",
        run_id="r1",
        world=world,
        clock=clock,
        effects=(),
        fault_events=(),
        adapter_result=AdapterResult((AgentClaim("task_completed", True),), "completed", "a", "1"),
    )

    # Run 2: Fail (Unsafe Success) with stale fault
    fnd = Finding("f1", "critical", "stale_read", True, {}, {}, "", (), "sc-1", ("f-stale",))
    eval_fail = OracleEvaluationResult(
        verdict="fail",
        findings=(fnd,),
        oracle_results=({"status": "failed"},),
    )
    stale_ev = FaultEvent(
        "ev1",
        "f-stale",
        "stale_read",
        "after_read",
        "o1",
        "",
        "a",
        "b",
        "applied",
    )
    ctx_fail = OracleContext(
        scenario_id="sc-1",
        run_id="r2",
        world=world,
        clock=clock,
        effects=(),
        fault_events=(stale_ev,),
        adapter_result=AdapterResult((AgentClaim("task_completed", True),), "completed", "a", "1"),
    )

    # Run 3: Needs Review (Guarded recovery from timeout)
    eval_review = OracleEvaluationResult(
        verdict="needs_review",
        findings=(),
        oracle_results=({"status": "passed"},),
    )
    timeout_ev = FaultEvent(
        "ev2",
        "f-to",
        "timeout_after_commit",
        "after_commit",
        "o1",
        "",
        "a",
        "b",
        "applied",
    )
    ctx_review = OracleContext(
        scenario_id="sc-1",
        run_id="r3",
        world=world,
        clock=clock,
        effects=(),
        fault_events=(timeout_ev,),
        adapter_result=AdapterResult((), "needs_review", "a", "1"),
    )

    evaluations = [
        (scenario, eval_pass, ctx_pass),
        (scenario, eval_fail, ctx_fail),
        (scenario, eval_review, ctx_review),
    ]

    metrics = calculate_scenario_metrics(evaluations)
    assert metrics["total_runs"] == 3.0
    assert metrics["passed_runs"] == 1.0
    assert metrics["failed_runs"] == 1.0
    assert metrics["needs_review_runs"] == 1.0
    assert metrics["pass_rate"] == 0.3333
    assert metrics["unsafe_success_count"] == 1.0
    assert metrics["unsafe_success_rate"] == 0.3333
    assert metrics["safe_recovery_rate"] == 1.0
    assert metrics["critical_findings_count"] == 1.0
