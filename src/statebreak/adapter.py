"""Framework-neutral agent adapter protocol and data models for StateBreak."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from statebreak.errors import StateBreakError
from statebreak.models import AdapterResult, AgentClaim

__all__ = [
    "AdapterError",
    "ToolRequest",
    "ToolObservation",
    "ToolOutcome",
    "HandoffPayload",
    "CoordinationMessage",
    "AdapterContext",
    "AgentAdapter",
    "AgentClaim",
    "AdapterResult",
]


class AdapterError(StateBreakError):
    """Error raised when an agent adapter encounters a fatal execution condition."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=3)


@dataclass(frozen=True)
class ToolRequest:
    """Declared intent to execute a tool operation through the gateway."""

    name: str
    target: str
    payload: dict[str, Any] = field(default_factory=dict)
    operation_id: str | None = None
    expected_version: str | None = None


@dataclass(frozen=True)
class ToolObservation:
    """Read observation of world state returned through the gateway."""

    name: str
    target: str
    state_version: str
    observed_at: str
    data: dict[str, Any] | None
    source: str = "world"

    def to_dict(self) -> dict[str, Any]:
        """Convert observation to dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class ToolOutcome:
    """Authoritative outcome of an action or mutation returned through the gateway."""

    status: str  # committed, rejected, partial, unknown
    effect_id: str | None
    operation_id: str | None
    target: str
    result: dict[str, Any] | None = None
    error: str | None = None
    state_version: str | None = None
    applied_fields: tuple[str, ...] = ()
    omitted_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Convert outcome to dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class HandoffPayload:
    """Structured payload passed between subtasks or coordinating nodes."""

    task_id: str
    summary: str
    context: dict[str, Any] = field(default_factory=dict)
    constraints: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert handoff payload to dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class CoordinationMessage:
    """Structured envelope for in-process node-to-node coordination."""

    message_id: str
    run_id: str
    sender_id: str
    recipient_id: str
    message_type: str  # state_update, reconcile_req, reconcile_resp, task_handoff, ack
    operation_id: str | None = None
    entity_id: str | None = None
    expected_version: str | None = None
    payload_hash: str | None = None
    virtual_timestamp: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert coordination message to dictionary."""
        return asdict(self)


class AdapterContext:
    """Execution context supplied to an AgentAdapter.

    Exposes task instructions, typed ToolGateway, local node identity,
    and coordination interfaces while strictly isolating world internals.
    """

    def __init__(
        self,
        task_instruction: str,
        allowed_tools: tuple[str, ...],
        gateway: Any,
        clock: Any,
        node_id: str = "node-01",
        run_id: str = "run_default",
        scenario_id: str = "scenario_default",
        seed: int = 42,
        coordination: Any | None = None,
    ) -> None:
        self.task_instruction = task_instruction
        self.allowed_tools = allowed_tools
        self.gateway = gateway
        self.clock = clock
        self.node_id = node_id
        self.run_id = run_id
        self.scenario_id = scenario_id
        self.seed = seed
        self.coordination = coordination
        self._claims: list[AgentClaim] = []

    @property
    def claims(self) -> tuple[AgentClaim, ...]:
        """Return captured agent outcome claims."""
        return tuple(self._claims)

    def add_claim(self, name: str, value: bool, text: str | None = None) -> None:
        """Register an agent outcome claim."""
        self._claims.append(AgentClaim(name=name, value=value, text=text))

    def get_current_time(self) -> str:
        """Return current virtual time ISO string without wall-clock leaks."""
        return self.clock.now_iso() if hasattr(self.clock, "now_iso") else str(self.clock)


@runtime_checkable
class AgentAdapter(Protocol):
    """Protocol that all StateBreak agent adapters must satisfy."""

    name: str
    version: str

    def run(self, context: AdapterContext) -> AdapterResult:
        """Execute the agent against the supplied adapter context."""
        ...
