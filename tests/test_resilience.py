"""Resilience matrix tests comparing naive vs guarded adapters across all bundled scenarios."""

from __future__ import annotations

import pytest
from statebreak.runner import ScenarioRunner

SCENARIOS = [
    ("scenarios/approval-expiry.yml", "approval-expiry"),
    ("scenarios/duplicate-retry.yml", "duplicate-retry"),
    ("scenarios/handoff-loss.yml", "handoff-loss"),
    ("scenarios/partial-write.yml", "partial-write"),
    ("scenarios/timeout-after-commit.yml", "timeout-after-commit"),
    ("scenarios/wrong-target.yml", "wrong-target"),
]


@pytest.mark.parametrize("scenario_path,scenario_id", SCENARIOS)
def test_resilience_matrix_naive_fails(scenario_path: str, scenario_id: str) -> None:
    """Naive adapter must fail on every failure-injection scenario."""
    runner = ScenarioRunner()
    report = runner.run_scenario(scenario_path, adapter="naive", seed=100)

    assert report.scenario_id == scenario_id
    assert report.verdict == "fail", f"Naive adapter unexpectedly passed {scenario_id}"
    assert len(report.findings) >= 1
    assert any(f.blocking for f in report.findings)


@pytest.mark.parametrize("scenario_path,scenario_id", SCENARIOS)
def test_resilience_matrix_guarded_passes_or_recovers(scenario_path: str, scenario_id: str) -> None:
    """Guarded adapter must pass or safely recover across all scenarios."""
    runner = ScenarioRunner()
    report = runner.run_scenario(scenario_path, adapter="guarded", seed=100)

    assert report.scenario_id == scenario_id
    assert report.verdict in ("pass", "needs_review")
    assert len(report.findings) == 0


def test_cross_process_deterministic_replay() -> None:
    """Independent runs with identical seed must produce identical hashes and verdicts."""
    runner = ScenarioRunner()
    r1 = runner.run_scenario("scenarios/timeout-after-commit.yml", adapter="guarded", seed=999)
    r2 = runner.run_scenario("scenarios/timeout-after-commit.yml", adapter="guarded", seed=999)

    assert r1.scenario_hash == r2.scenario_hash
    assert r1.verdict == r2.verdict
    assert len(r1.effects) == len(r2.effects)
    assert len(r1.events) == len(r2.events)
