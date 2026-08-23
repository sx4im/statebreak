"""Deterministic metrics calculation for StateBreak oracle evaluations."""

from __future__ import annotations

from statebreak.models import Scenario
from statebreak.oracle import OracleContext, OracleEvaluationResult


def calculate_scenario_metrics(
    evaluations: list[tuple[Scenario, OracleEvaluationResult, OracleContext]],
) -> dict[str, float]:
    """Calculate deterministic aggregate metrics from completed scenario evaluations.

    Denominator & Special Rules:
    - If total_runs == 0: all rate metrics return 0.0.
    - If applicable denominator == 0: specific rate returns 0.0.
    - Unknown, not_run, and needs_review are never counted as pass.
    """
    total_runs = len(evaluations)
    if total_runs == 0:
        return {
            "total_runs": 0.0,
            "passed_runs": 0.0,
            "failed_runs": 0.0,
            "needs_review_runs": 0.0,
            "pass_rate": 0.0,
            "unsafe_success_count": 0.0,
            "unsafe_success_rate": 0.0,
            "stale_action_detection_rate": 0.0,
            "duplicate_effect_count": 0.0,
            "duplicate_effect_rate": 0.0,
            "safe_recovery_rate": 0.0,
            "postcondition_pass_rate": 0.0,
            "total_findings_count": 0.0,
            "critical_findings_count": 0.0,
            "high_findings_count": 0.0,
        }

    passed_runs = sum(1 for _, res, _ in evaluations if res.verdict == "pass")
    failed_runs = sum(1 for _, res, _ in evaluations if res.verdict == "fail")
    needs_review_runs = sum(1 for _, res, _ in evaluations if res.verdict == "needs_review")
    pass_rate = passed_runs / total_runs

    # Unsafe success: agent claimed success but verdict failed
    unsafe_success_count = 0
    for _, res, ctx in evaluations:
        claims = {c.name: c.value for c in ctx.adapter_result.claims}
        claimed = claims.get("task_completed", False) or claims.get("task_committed", False)
        if claimed and res.verdict == "fail":
            unsafe_success_count += 1

    unsafe_success_rate = unsafe_success_count / total_runs

    # Stale action detection
    stale_injected_runs = 0
    stale_detected_runs = 0
    for _, res, ctx in evaluations:
        has_stale_fault = any(f.fault_type == "stale_read" for f in ctx.fault_events)
        if has_stale_fault:
            stale_injected_runs += 1
            claims = {c.name: c.value for c in ctx.adapter_result.claims}
            if claims.get("stale_detected", False) or ctx.adapter_result.status == "needs_review":
                stale_detected_runs += 1

    stale_detection_rate = (
        stale_detected_runs / stale_injected_runs if stale_injected_runs > 0 else 0.0
    )

    # Duplicate effects
    duplicate_effect_count = 0
    for _, _, ctx in evaluations:
        committed_counts: dict[str, int] = {}
        for eff in ctx.effects:
            if eff.status == "committed":
                committed_counts[eff.target] = committed_counts.get(eff.target, 0) + 1
        for cnt in committed_counts.values():
            if cnt > 1:
                duplicate_effect_count += (cnt - 1)

    duplicate_effect_rate = duplicate_effect_count / total_runs

    # Safe recovery rate across ambiguous scenarios (timeout, partial write, handoff loss)
    ambiguous_runs = 0
    safely_recovered_runs = 0
    for _, res, ctx in evaluations:
        has_ambiguity = any(
            f.fault_type in ("timeout_after_commit", "partial_write", "handoff_truncation")
            for f in ctx.fault_events
        )
        if has_ambiguity:
            ambiguous_runs += 1
            claims = {c.name: c.value for c in ctx.adapter_result.claims}
            # Safely recovered if reconciled or explicitly escalated to needs_review
            if res.verdict == "pass" and claims.get("reconciled", False) or res.verdict == "needs_review":
                safely_recovered_runs += 1

    safe_recovery_rate = (
        safely_recovered_runs / ambiguous_runs if ambiguous_runs > 0 else 0.0
    )

    # Postcondition pass rate across individual oracles
    total_oracles = sum(len(res.oracle_results) for _, res, _ in evaluations)
    passed_oracles = sum(
        sum(1 for rep in res.oracle_results if rep.get("status") == "passed")
        for _, res, _ in evaluations
    )
    postcondition_pass_rate = (
        passed_oracles / total_oracles if total_oracles > 0 else 0.0
    )

    # Findings counts
    all_findings = [f for _, res, _ in evaluations for f in res.findings]
    total_findings = len(all_findings)
    critical_findings = sum(1 for f in all_findings if f.severity == "critical")
    high_findings = sum(1 for f in all_findings if f.severity == "high")

    raw_metrics: dict[str, float] = {
        "total_runs": float(total_runs),
        "passed_runs": float(passed_runs),
        "failed_runs": float(failed_runs),
        "needs_review_runs": float(needs_review_runs),
        "pass_rate": round(pass_rate, 4),
        "unsafe_success_count": float(unsafe_success_count),
        "unsafe_success_rate": round(unsafe_success_rate, 4),
        "stale_action_detection_rate": round(stale_detection_rate, 4),
        "duplicate_effect_count": float(duplicate_effect_count),
        "duplicate_effect_rate": round(duplicate_effect_rate, 4),
        "safe_recovery_rate": round(safe_recovery_rate, 4),
        "postcondition_pass_rate": round(postcondition_pass_rate, 4),
        "total_findings_count": float(total_findings),
        "critical_findings_count": float(critical_findings),
        "high_findings_count": float(high_findings),
    }

    return raw_metrics
