"""Unit tests for StateBreak core models."""

from dataclasses import FrozenInstanceError

import pytest

from statebreak.models import (
    AdapterResult,
    AgentClaim,
    AgentTaskSpec,
    ClockSpec,
    EffectRecord,
    ExpectationSpec,
    FaultSpec,
    Finding,
    OracleSpec,
    RunReport,
    Scenario,
    StateSnapshot,
)


def test_clock_spec_defaults_and_immutability() -> None:
    clock = ClockSpec(start="2026-01-01T09:00:00Z")
    assert clock.start == "2026-01-01T09:00:00Z"
    assert clock.step_seconds == 30

    with pytest.raises(FrozenInstanceError):
        clock.step_seconds = 60  # type: ignore[misc]


def test_fault_spec_construction() -> None:
    fault = FaultSpec(
        id="f-1",
        at="before_commit",
        type="approval_expired",
        target="appr-001",
        repeat=1,
        params={"reason": "test"},
    )
    assert fault.id == "f-1"
    assert fault.at == "before_commit"
    assert fault.type == "approval_expired"
    assert fault.target == "appr-001"
    assert fault.repeat == 1
    assert fault.params == {"reason": "test"}

    with pytest.raises(FrozenInstanceError):
        fault.target = "appr-002"  # type: ignore[misc]


def test_scenario_model_and_to_dict() -> None:
    scenario = Scenario(
        schema="statebreak.scenario/v1",
        id="test-scenario",
        version=1,
        seed=42,
        clock=ClockSpec(start="2026-01-01T00:00:00Z", step_seconds=10),
        world={"entities": [{"id": "e-1", "type": "synthetic_record"}]},
        faults=(
            FaultSpec(id="f-1", at="before_commit", type="stale_read", target="e-1"),
        ),
        agent_task=AgentTaskSpec(
            instruction="Do something",
            tools=("read_state", "commit_effect"),
            required_claim="done",
        ),
        oracles=(
            OracleSpec(
                id="o-1",
                type="claim_requires_state",
                claim="done",
                expression="status == completed",
            ),
        ),
        expectations={"naive": ExpectationSpec(verdict="fail", finding_ids=("false-success",))},
        scenario_hash="abc123sha",
    )

    data = scenario.to_dict()
    assert data["schema"] == "statebreak.scenario/v1"
    assert data["id"] == "test-scenario"
    assert data["clock"]["step_seconds"] == 10
    assert len(data["faults"]) == 1
    assert data["faults"][0]["id"] == "f-1"
    assert data["agent_task"]["tools"] == ("read_state", "commit_effect")
    assert data["expectations"]["naive"]["verdict"] == "fail"
    assert data["scenario_hash"] == "abc123sha"

    with pytest.raises(FrozenInstanceError):
        scenario.id = "new-id"  # type: ignore[misc]


def test_finding_and_effect_record() -> None:
    finding = Finding(
        finding_id="false-success",
        severity="critical",
        category="state_conflict",
        blocking=True,
        expected={"status": "completed"},
        observed={"status": "pending"},
        remediation="Check approval before committing",
        event_refs=("ev-1", "ev-2"),
    )
    assert finding.finding_id == "false-success"
    assert finding.blocking is True
    assert finding.event_refs == ("ev-1", "ev-2")

    effect = EffectRecord(
        effect_id="eff-1",
        operation_id="op-1",
        kind="refund",
        target="ref-1",
        status="committed",
        payload_hash="sha256payload",
        provider_id="prov-1",
        event_refs=("ev-3",),
    )
    assert effect.effect_id == "eff-1"
    assert effect.status == "committed"


def test_agent_claim_and_adapter_result() -> None:
    claim = AgentClaim(name="task_committed", value=True, text="Refund processed")
    assert claim.name == "task_committed"
    assert claim.value is True

    result = AdapterResult(
        claims=(claim,),
        status="completed",
        adapter_name="naive",
        adapter_version="0.1.0",
    )
    assert len(result.claims) == 1
    assert result.status == "completed"


def test_state_snapshot_and_run_report() -> None:
    snapshot = StateSnapshot(
        snapshot_id="snap-1",
        state_version="v1",
        captured_at="2026-01-01T09:00:00Z",
        entities_hash="entities_sha",
    )
    assert snapshot.snapshot_id == "snap-1"

    report = RunReport(
        schema="statebreak.report/v1",
        run_id="run-1",
        scenario_id="approval-expiry",
        scenario_hash="sc_hash",
        seed=41,
        adapter={"name": "naive", "version": "0.1.0"},
        verdict="fail",
        metrics={"unsafe_success_rate": 1.0},
    )
    report_dict = report.to_dict()
    assert report_dict["schema"] == "statebreak.report/v1"
    assert report_dict["verdict"] == "fail"
    assert report_dict["metrics"]["unsafe_success_rate"] == 1.0
