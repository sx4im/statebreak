"""Unit tests for ScenarioRunner end-to-end execution."""

from __future__ import annotations

import pytest
from statebreak.errors import UsageError
from statebreak.runner import ScenarioRunner


def test_runner_guarded_pass() -> None:
    runner = ScenarioRunner()
    report = runner.run_scenario("scenarios/timeout-after-commit.yml", adapter="guarded", seed=42)

    assert report.verdict == "pass"
    assert report.scenario_id == "timeout-after-commit"
    assert report.seed == 42
    assert report.adapter["name"] == "guarded-adapter"
    assert len(report.findings) == 0


def test_runner_naive_failure() -> None:
    runner = ScenarioRunner()
    report = runner.run_scenario("scenarios/approval-expiry.yml", adapter="naive", seed=42)

    assert report.verdict == "fail"
    assert report.adapter["name"] == "naive-adapter"
    assert len(report.findings) >= 1
    assert any(f.blocking for f in report.findings)


def test_runner_deterministic_replay() -> None:
    runner = ScenarioRunner()
    r1 = runner.run_scenario("scenarios/timeout-after-commit.yml", adapter="guarded", seed=123)
    r2 = runner.run_scenario("scenarios/timeout-after-commit.yml", adapter="guarded", seed=123)

    assert r1.scenario_hash == r2.scenario_hash
    assert r1.verdict == r2.verdict
    assert len(r1.findings) == len(r2.findings)
    assert len(r1.events) == len(r2.events)
    assert len(r1.effects) == len(r2.effects)


def test_runner_invalid_adapter_raises() -> None:
    runner = ScenarioRunner()
    with pytest.raises(UsageError, match="unknown adapter"):
        runner.run_scenario("scenarios/approval-expiry.yml", adapter="nonexistent")


def test_runner_run_scenarios_batch() -> None:
    runner = ScenarioRunner()
    reports = runner.run_scenarios(
        ["scenarios/approval-expiry.yml", "scenarios/duplicate-retry.yml"],
        adapter="guarded",
        seed=42,
    )
    assert len(reports) == 2
    assert all(r.verdict in ("pass", "needs_review") for r in reports)
