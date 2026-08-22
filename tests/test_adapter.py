"""Unit tests for AgentAdapter protocol and adapter data models."""

from __future__ import annotations

from statebreak.adapter import (
    AdapterContext,
    AdapterError,
    AdapterResult,
    AgentAdapter,
    AgentClaim,
    CoordinationMessage,
    HandoffPayload,
    ToolObservation,
    ToolOutcome,
    ToolRequest,
)
from statebreak.clock import VirtualClock


class DummyAdapter:
    """Minimal fake adapter satisfying AgentAdapter protocol."""

    name = "dummy-adapter"
    version = "1.0.0"

    def run(self, context: AdapterContext) -> AdapterResult:
        context.add_claim("task_completed", True, text="Executed dummy task")
        return AdapterResult(
            claims=context.claims,
            status="completed",
            adapter_name=self.name,
            adapter_version=self.version,
        )


def test_agent_adapter_protocol_conformance() -> None:
    adapter = DummyAdapter()
    assert isinstance(adapter, AgentAdapter)
    assert adapter.name == "dummy-adapter"
    assert adapter.version == "1.0.0"


def test_adapter_context_claims_and_time() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    ctx = AdapterContext(
        task_instruction="Process test item",
        allowed_tools=("read_state", "commit_effect"),
        gateway=None,
        clock=clock,
        node_id="node-01",
        run_id="run-100",
        scenario_id="scenario-001",
        seed=42,
    )
    assert ctx.task_instruction == "Process test item"
    assert ctx.allowed_tools == ("read_state", "commit_effect")
    assert ctx.node_id == "node-01"
    assert ctx.run_id == "run-100"
    assert ctx.get_current_time() == "2026-01-01T09:00:00Z"
    assert len(ctx.claims) == 0

    ctx.add_claim("refund_approved", True, "Approval confirmed")
    assert len(ctx.claims) == 1
    assert isinstance(ctx.claims[0], AgentClaim)
    assert ctx.claims[0].name == "refund_approved"
    assert ctx.claims[0].value is True


def test_dummy_adapter_run() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    ctx = AdapterContext(
        task_instruction="Run task",
        allowed_tools=(),
        gateway=None,
        clock=clock,
    )
    adapter = DummyAdapter()
    res = adapter.run(ctx)
    assert res.status == "completed"
    assert res.adapter_name == "dummy-adapter"
    assert len(res.claims) == 1
    assert res.claims[0].name == "task_completed"


def test_tool_data_models_serialization() -> None:
    req = ToolRequest(name="read", target="rec-1", payload={"key": "val"}, operation_id="op_1")
    assert req.name == "read"
    assert req.target == "rec-1"

    obs = ToolObservation(
        name="read",
        target="rec-1",
        state_version="v2",
        observed_at="2026-01-01T09:00:00Z",
        data={"status": "active"},
    )
    obs_dict = obs.to_dict()
    assert obs_dict["state_version"] == "v2"
    assert obs_dict["data"] == {"status": "active"}

    outcome = ToolOutcome(
        status="committed",
        effect_id="eff_01",
        operation_id="op_1",
        target="rec-1",
        result={"version": "v3"},
        state_version="v3",
    )
    out_dict = outcome.to_dict()
    assert out_dict["status"] == "committed"
    assert out_dict["state_version"] == "v3"

    handoff = HandoffPayload(
        task_id="task-1",
        summary="Summary of work",
        context={"entity": "e1"},
        constraints=("must_expire",),
    )
    h_dict = handoff.to_dict()
    assert h_dict["task_id"] == "task-1"
    assert h_dict["constraints"] == ("must_expire",)

    msg = CoordinationMessage(
        message_id="msg_01",
        run_id="run_1",
        sender_id="node-01",
        recipient_id="node-02",
        message_type="state_update",
        operation_id="op_1",
        entity_id="e1",
        expected_version="v2",
        virtual_timestamp="2026-01-01T09:00:00Z",
    )
    msg_dict = msg.to_dict()
    assert msg_dict["message_id"] == "msg_01"
    assert msg_dict["sender_id"] == "node-01"


def test_adapter_error_exit_code() -> None:
    err = AdapterError("Fatal adapter error")
    assert err.exit_code == 3
    assert "Fatal adapter error" in str(err)
