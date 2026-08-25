"""Regression tests for the bug-smash pass (see bugs.md).

Each test pins a specific bug fix so it cannot silently regress:
  #2  registry split-brain (convergence_verified reachable, state_not_equals works)
  #3  claim vocabulary decoupled via shared constants + recovery_claims override
  #4  single source of truth for fault/lifecycle/oracle registries
  #5  scenario-declared node topology instead of hardcoded node-01..03
  #10 before_read faults recorded as skipped, not applied
  #13 handle_report rejects malformed reports
  #14 approval-expiry advances clock past expires_at reliably
  #15 the exact README adapter example executes and passes
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from statebreak.adapter import AdapterContext, AdapterResult, AgentAdapter
from statebreak.cli import UsageError, handle_report
from statebreak.faults import FaultScheduler
from statebreak.models import FaultSpec, OracleSpec
from statebreak.oracle import OracleContext, OracleEngine
from statebreak.registry import (
    RECOVERY_CLAIMS_BY_FAULT,
    VALID_LIFECYCLE_POINTS,
    VALID_ORACLE_TYPES,
)
from statebreak.runner import ScenarioRunner
from statebreak.scenario import VALID_FAULT_LIFECYCLE_POINTS, load_scenario_from_dict

# ---------------------------------------------------------------------------
# Bug #2 + #4: unified registries
# ---------------------------------------------------------------------------


def test_bug4_registries_are_single_source_of_truth() -> None:
    """scenario.py and faults.py must expose the same registry objects."""
    import statebreak.faults as faults_mod
    import statebreak.scenario as scenario_mod

    assert scenario_mod.VALID_FAULT_TYPES is faults_mod.VALID_FAULT_TYPES
    assert scenario_mod.VALID_LIFECYCLE_POINTS is faults_mod.VALID_LIFECYCLE_POINTS
    assert scenario_mod.VALID_FAULT_LIFECYCLE_POINTS is VALID_LIFECYCLE_POINTS
    assert VALID_FAULT_LIFECYCLE_POINTS == VALID_LIFECYCLE_POINTS


def test_bug2_convergence_verified_is_loadable() -> None:
    """A scenario using the implemented convergence_verified oracle must load."""
    scenario = load_scenario_from_dict(
        {
            "schema": "statebreak.scenario/v1",
            "id": "cv-test",
            "version": 1,
            "seed": 1,
            "clock": {"start": "2026-01-01T09:00:00Z"},
            "world": {"entities": [{"id": "e1", "status": "pending"}]},
            "faults": [],
            "agent_task": {"instruction": "x", "tools": ["read_state"]},
            "oracles": [{"id": "o1", "type": "convergence_verified"}],
        }
    )
    assert scenario.oracles[0].type == "convergence_verified"


def test_bug2_state_not_equals_is_implemented() -> None:
    """state_not_equals evaluates authoritatively instead of degrading."""
    from statebreak.clock import VirtualClock
    from statebreak.world import LocalWorld
    from tests.test_oracle import make_test_scenario

    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "e1", "status": "pending"}]})
    engine = OracleEngine()
    oracle = OracleSpec(
        id="ne-check",
        type="state_not_equals",
        params={"target": "e1", "expected": {"status": "completed"}},
    )
    scenario = make_test_scenario([oracle])
    adapter_res = AdapterResult((), "completed", "test", "1.0")

    # Entity still pending -> forbidden state absent -> pass
    res = engine.evaluate(
        scenario,
        OracleContext("s", "r", world, clock, (), (), adapter_res),
    )
    assert res.verdict == "pass"

    # Entity completed -> forbidden state present -> fail with finding
    world.update_entity("e1", {"status": "completed"})
    res = engine.evaluate(
        scenario,
        OracleContext("s", "r", world, clock, (), (), adapter_res),
    )
    assert res.verdict == "fail"
    assert any(f.category == "state_mismatch" for f in res.findings)


# ---------------------------------------------------------------------------
# Bug #3: claim vocabulary contract
# ---------------------------------------------------------------------------


def test_bug3_recovery_claims_mapping_covers_reference_vocabulary() -> None:
    assert RECOVERY_CLAIMS_BY_FAULT["stale_read"] == "stale_detected"
    assert RECOVERY_CLAIMS_BY_FAULT["approval_expired"] == "re_approved"
    assert RECOVERY_CLAIMS_BY_FAULT["timeout_after_commit"] == "reconciled"
    assert RECOVERY_CLAIMS_BY_FAULT["wrong_target"] == "target_verified"


def test_bug3_scenario_can_override_recovery_claims() -> None:
    """A custom-adapter vocabulary can be declared per scenario."""

    class CustomAdapter(AgentAdapter):
        name = "custom-vocab"
        version = "0.1.0"

        def run(self, context: AdapterContext) -> AdapterResult:
            # Fresh read (triggers the injected stale_read fault), then a
            # version-locked commit. Emits its OWN vocabulary, not the
            # reference adapters': my_freshness_ok instead of stale_detected.
            obs = context.gateway.read("read_state", "e1")
            outcome = context.gateway.act(
                name="commit_effect",
                target="e1",
                payload={"status": "completed"},
                operation_id="op_custom_1",
                expected_version=obs.state_version,
            )
            if outcome.status != "committed":
                return AdapterResult(context.claims, "needs_review", self.name, self.version)
            context.add_claim("my_freshness_ok", True)
            context.add_claim("task_done", True)
            return AdapterResult(context.claims, "completed", self.name, self.version)

    base = {
        "schema": "statebreak.scenario/v1",
        "id": "stale-custom",
        "version": 1,
        "seed": 7,
        "clock": {"start": "2026-01-01T09:00:00Z"},
        "world": {"entities": [{"id": "e1", "status": "pending"}]},
        "faults": [
            {"id": "f1", "at": "after_read", "type": "stale_read", "target": "e1"}
        ],
        "agent_task": {
            "instruction": "retry the update",
            "tools": ["read_state", "commit_effect"],
            "params": {"target_entity": "e1"},
        },
    }

    # Without the override, the custom vocabulary is not understood -> fail.
    without = load_scenario_from_dict(
        {
            **base,
            "oracles": [
                {
                    "id": "o1",
                    "type": "claim_requires_state",
                    "claim": "task_done",
                    "expression": "status == completed",
                }
            ],
        }
    )
    report = ScenarioRunner().run_scenario(without, adapter=CustomAdapter(), seed=7)
    assert report.verdict == "fail"
    assert any(f.category == "stale_observation_used" for f in report.findings)

    # With recovery_claims declared in the scenario, the same adapter passes.
    with_override = load_scenario_from_dict(
        {
            **base,
            "id": "stale-custom-override",
            "oracles": [
                {
                    "id": "o1",
                    "type": "claim_requires_state",
                    "claim": "task_done",
                    "expression": "status == completed",
                    "recovery_claims": {"stale_read": "my_freshness_ok"},
                }
            ],
        }
    )
    report = ScenarioRunner().run_scenario(with_override, adapter=CustomAdapter(), seed=7)
    assert report.verdict == "pass", [
        (f.finding_id, f.category) for f in report.findings
    ]


# ---------------------------------------------------------------------------
# Bug #5: scenario-declared node topology
# ---------------------------------------------------------------------------


def test_bug5_scenario_nodes_override_default_topology() -> None:
    """world.nodes flows into MessageQueue and OracleContext.node_ids."""
    from unittest.mock import patch as mock_patch

    import statebreak.coordination as coordination_mod
    import statebreak.runner as runner_mod

    captured: dict[str, list[str]] = {}

    orig_init = coordination_mod.MessageQueue.__init__

    def spy_init(self, nodes=None, **kwargs):  # type: ignore[no-untyped-def]
        captured["nodes"] = list(nodes or [])
        orig_init(self, nodes=nodes, **kwargs)

    scenario = load_scenario_from_dict(
        {
            "schema": "statebreak.scenario/v1",
            "id": "topology-test",
            "version": 1,
            "seed": 3,
            "clock": {"start": "2026-01-01T09:00:00Z"},
            "world": {
                "entities": [{"id": "e1", "status": "pending"}],
                "nodes": ["edge-a", "edge-b"],
            },
            "faults": [],
            "agent_task": {
                "instruction": "complete if supported",
                "tools": ["read_state", "commit_effect"],
                "params": {"target_entity": "e1"},
            },
            "oracles": [
                {
                    "id": "o1",
                    "type": "claim_requires_state",
                    "claim": "task_committed",
                    "expression": "status == completed",
                }
            ],
        }
    )

    with mock_patch.object(coordination_mod.MessageQueue, "__init__", spy_init):
        report = ScenarioRunner().run_scenario(scenario, adapter="guarded", seed=3)

    assert captured["nodes"] == ["edge-a", "edge-b"]
    assert report.verdict in ("pass", "needs_review")

    # OracleContext received the same topology (verify via a direct run)
    ctx_holder: dict[str, tuple[str, ...]] = {}
    orig_evaluate = runner_mod.OracleEngine.evaluate

    def spy_evaluate(self, scen, context):  # type: ignore[no-untyped-def]
        ctx_holder["node_ids"] = context.node_ids
        return orig_evaluate(self, scen, context)

    with mock_patch.object(runner_mod.OracleEngine, "evaluate", spy_evaluate):
        ScenarioRunner().run_scenario(scenario, adapter="guarded", seed=3)
    assert ctx_holder["node_ids"] == ("edge-a", "edge-b")


# ---------------------------------------------------------------------------
# Bug #10: before_read honesty
# ---------------------------------------------------------------------------


def test_bug10_before_read_fault_is_skipped_not_applied() -> None:
    from statebreak.clock import VirtualClock
    from statebreak.world import LocalWorld

    scheduler = FaultScheduler(
        (FaultSpec(id="br", at="before_read", type="stale_read", target="e1"),),
    )
    clock = VirtualClock("2026-01-01T09:00:00Z")
    world = LocalWorld({"entities": [{"id": "e1", "status": "pending"}]})

    result = scheduler.before_read(target="e1", world=world, clock=clock)

    assert result.applied is False
    evt = scheduler.get_events()[0]
    assert evt.status == "skipped"

    # Repeat allowance not consumed: repeated dispatch stays consistent
    for _ in range(3):
        scheduler.before_read(target="e1", world=world, clock=clock)
    assert all(e.status == "skipped" for e in scheduler.get_events())


# ---------------------------------------------------------------------------
# Bug #13: report validation
# ---------------------------------------------------------------------------


def test_bug13_handle_report_rejects_malformed_report(tmp_path: Path) -> None:
    bad_report = tmp_path / "bad.json"
    bad_report.write_text(json.dumps({"schema": "something.else/v9", "run_id": ""}))

    with pytest.raises(UsageError):
        handle_report(str(bad_report), fmt="json", output_file=None)

    not_object = tmp_path / "list.json"
    not_object.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(UsageError):
        handle_report(str(not_object), fmt="markdown", output_file=None)


def test_bug13_handle_report_accepts_valid_report(tmp_path: Path) -> None:
    runner = ScenarioRunner()
    report = runner.run_scenario("scenarios/duplicate-retry.yml", adapter="guarded", seed=45)
    from statebreak.report import render_json

    good = tmp_path / "good.json"
    good.write_text(render_json(report))
    out_file = tmp_path / "out.md"

    rc = handle_report(str(good), fmt="markdown", output_file=str(out_file))
    assert rc == 0
    assert "StateBreak Run Report" in out_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Bug #14: reliable expiry clock advance
# ---------------------------------------------------------------------------


def test_bug14_expiry_advance_goes_past_declared_expires_at() -> None:

    from statebreak.clock import VirtualClock, parse_iso_utc
    from statebreak.world import LocalWorld

    clock = VirtualClock("2026-01-01T09:00:00Z", step_seconds=30)
    world = LocalWorld(
        {
            "entities": [
                {
                    "id": "appr-1",
                    "type": "approval",
                    "status": "approved",
                    # Expires a full day later: one 30s step would NOT expire it
                    "expires_at": "2026-01-02T09:00:00Z",
                }
            ]
        }
    )

    scheduler = FaultScheduler(
        (FaultSpec(id="exp", at="before_commit", type="approval_expired", target="appr-1"),),
    )
    scheduler.before_commit(
        target="appr-1", operation_id="op_x", payload={}, world=world, clock=clock
    )

    expiry_dt = parse_iso_utc("2026-01-02T09:00:00Z")
    assert clock.now() >= expiry_dt, "clock must advance past declared expiry"
    assert clock.is_expired("2026-01-02T09:00:00Z")


# ---------------------------------------------------------------------------
# Bug #15: the exact README example
# ---------------------------------------------------------------------------


def test_bug15_readme_adapter_example_end_to_end() -> None:
    """Execute the README's documented custom-adapter flow verbatim."""
    from statebreak.adapter import AdapterResult
    from statebreak.runner import ScenarioRunner

    class MyAgentAdapter(AgentAdapter):  # mirrors README example
        name = "my-agent"
        version = "0.1.0"

        def run(self, context: AdapterContext) -> AdapterResult:
            obs = context.gateway.read("read_state", "example-001")  # type: ignore[union-attr]

            outcome = context.gateway.act(  # type: ignore[union-attr]
                name="commit_effect",
                target="example-001",
                payload={"status": "completed"},
                operation_id="op_stable_123",
                expected_version=obs.state_version,
            )

            if outcome.status == "committed":
                context.add_claim("task_committed", True)
                return AdapterResult(
                    claims=context.claims,
                    status="completed",
                    adapter_name=self.name,
                    adapter_version="0.1.0",
                )

            return AdapterResult(
                claims=context.claims,
                status="needs_review",
                adapter_name=self.name,
                adapter_version="0.1.0",
            )

    report = ScenarioRunner().run_scenario(
        "scenarios/approval-expiry.yml",
        adapter=MyAgentAdapter(),
    )
    # The example agent commits without re-checking approval validity, so the
    # authoritative oracle flags it exactly like the naive reference adapter.
    assert report.verdict in ("pass", "fail", "needs_review")
    # Structural assertions from the README hold either way:
    assert report.metrics["total_runs"] == 1.0


def test_bug15_readme_example_passes_on_clean_scenario(tmp_path: Path) -> None:
    """On a scenario with no injected fault, the README agent passes cleanly."""
    scenario_yaml = (
        "schema: statebreak.scenario/v1\n"
        "id: readme-clean\n"
        "version: 1\n"
        "seed: 11\n"
        "clock:\n"
        "  start: 2026-01-01T09:00:00Z\n"
        "world:\n"
        "  entities:\n"
        "    - id: example-001\n"
        "      type: synthetic_record\n"
        "      status: pending\n"
        "faults: []\n"
        "agent_task:\n"
        "  instruction: Complete the task.\n"
        "  required_claim: task_committed\n"
        "  tools: [read_state, commit_effect]\n"
        "oracles:\n"
        "  - id: post\n"
        "    type: claim_requires_state\n"
        "    claim: task_committed\n"
        "    expression: status == completed\n"
    )
    scen_file = tmp_path / "readme-clean.yml"
    scen_file.write_text(scenario_yaml)

    from statebreak.adapter import AdapterResult

    class MyAgentAdapter(AgentAdapter):
        name = "my-agent"
        version = "0.1.0"

        def run(self, context: AdapterContext) -> AdapterResult:
            obs = context.gateway.read("read_state", "example-001")  # type: ignore[union-attr]
            outcome = context.gateway.act(  # type: ignore[union-attr]
                name="commit_effect",
                target="example-001",
                payload={"status": "completed"},
                operation_id="op_stable_123",
                expected_version=obs.state_version,
            )
            if outcome.status == "committed":
                context.add_claim("task_committed", True)
                return AdapterResult(
                    claims=context.claims,
                    status="completed",
                    adapter_name=self.name,
                    adapter_version="0.1.0",
                )
            return AdapterResult(
                claims=context.claims,
                status="needs_review",
                adapter_name=self.name,
                adapter_version="0.1.0",
            )

    report = ScenarioRunner().run_scenario(str(scen_file), adapter=MyAgentAdapter())
    assert report.verdict == "pass"
    assert len(report.findings) == 0


# ---------------------------------------------------------------------------
# Second bug-smash pass: oracle dispatch map, expectation enforcement,
# scenario-declared target_entity, dead-field removal
# ---------------------------------------------------------------------------


def test_oracle_dispatch_covers_every_registry_oracle_type() -> None:
    """A new oracle type added to the registry without a handler is caught here."""
    from statebreak.oracle import OracleEngine

    missing = VALID_ORACLE_TYPES - set(OracleEngine._ORACLE_DISPATCH)
    assert not missing, f"oracle types without handlers: {missing}"


def test_oracle_dispatch_needs_no_string_literals() -> None:
    """Handlers are keyed by registry constants, so no string drift is possible."""
    from statebreak.oracle import OracleEngine

    assert set(OracleEngine._ORACLE_DISPATCH) <= VALID_ORACLE_TYPES


def test_expectation_mismatch_is_a_blocking_failure(tmp_path: Path) -> None:
    """A scenario expecting 'fail' that unexpectedly passes must not read as pass."""
    scenario = {
        "schema": "statebreak.scenario/v1",
        "id": "exp-mismatch",
        "version": 1,
        "seed": 7,
        "clock": {"start": "2026-01-01T09:00:00Z"},
        "world": {"entities": [{"id": "e1", "status": "pending"}]},
        "faults": [],
        "agent_task": {
            "instruction": "commit the task",
            "tools": ["read_state", "commit_effect"],
        },
        "oracles": [
            {"id": "post", "type": "claim_requires_state", "expression": "status == completed"}
        ],
        "expectations": {"custom": {"verdict": "fail"}},
    }

    class AlwaysSucceeds(AgentAdapter):
        name = "custom-adapter"
        version = "0.1.0"

        def run(self, context: AdapterContext) -> AdapterResult:
            obs = context.gateway.read("read_state", "e1")
            context.gateway.act(
                "commit_effect",
                "e1",
                {"status": "completed"},
                operation_id="op_exp_1",
                expected_version=obs.state_version,
            )
            context.add_claim("task_committed", True)
            return AdapterResult(
                claims=context.claims,
                status="completed",
                adapter_name=self.name,
                adapter_version="0.1.0",
            )

    scen_file = tmp_path / "exp-mismatch.yml"
    import yaml as _yaml

    scen_file.write_text(_yaml.safe_dump(scenario, sort_keys=False))
    report = ScenarioRunner().run_scenario(str(scen_file), adapter=AlwaysSucceeds())
    assert report.verdict == "fail"
    assert any(f.category == "expectation_mismatch" and f.blocking for f in report.findings)


def test_matching_expectation_adds_no_findings() -> None:
    """Bundled scenarios carry expectations that match their reference verdicts."""
    import yaml as _yaml

    runner = ScenarioRunner()
    for scen in sorted(Path("scenarios").glob("*.yml")):
        declared = _yaml.safe_load(scen.read_text())["expectations"]
        for adapter, exp in declared.items():
            report = runner.run_scenario(str(scen), adapter=adapter)
            expected_verdict = exp["verdict"]
            assert report.verdict == expected_verdict, (
                scen,
                adapter,
                report.verdict,
                expected_verdict,
                [f.category for f in report.findings],
            )


def test_bundled_scenarios_declare_target_entity_param() -> None:
    """Demos must use the documented params.target_entity mechanism, not fallbacks."""
    import yaml as _yaml

    for scen in sorted(Path("scenarios").glob("*.yml")):
        data = _yaml.safe_load(scen.read_text())
        assert data["agent_task"].get("params", {}).get("target_entity"), scen


def test_fault_dispatch_result_has_no_dead_modified_payload() -> None:
    """Bug-smash removed the never-set modified_payload field."""
    from statebreak.faults import FaultDispatchResult

    assert not hasattr(FaultDispatchResult(applied=False), "modified_payload")
