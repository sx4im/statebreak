"""Core contracts and data models for StateBreak."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from statebreak.registry import DEFAULT_NODE_IDS


@dataclass(frozen=True)
class ClockSpec:
    """Virtual clock specification for deterministic scenario execution."""

    start: str
    step_seconds: int = 30


@dataclass(frozen=True)
class FaultSpec:
    """Fault injection specification at declared lifecycle point."""

    id: str
    at: str
    type: str
    target: str | None = None
    repeat: int | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentTaskSpec:
    """Declared task instructions, allowed tools, and required claims."""

    instruction: str
    tools: tuple[str, ...]
    required_claim: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OracleSpec:
    """Invariant or postcondition specification to evaluate after execution."""

    id: str
    type: str
    expression: str | None = None
    effect: str | None = None
    when: str | None = None
    claim: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExpectationSpec:
    """Expected outcome for reference adapter verification."""

    verdict: str
    finding_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    """Complete declarative scenario definition."""

    schema: str
    id: str
    version: int
    seed: int
    clock: ClockSpec
    world: dict[str, Any]
    faults: tuple[FaultSpec, ...]
    agent_task: AgentTaskSpec
    oracles: tuple[OracleSpec, ...]
    expectations: dict[str, ExpectationSpec] = field(default_factory=dict)
    scenario_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert scenario to dictionary matching scenario schema."""
        raw_dict: dict[str, Any] = asdict(self)
        if self.scenario_hash is None:
            raw_dict.pop("scenario_hash", None)
        return raw_dict

    @property
    def node_ids(self) -> tuple[str, ...]:
        """Declared multi-node topology, or the registry default.

        Scenarios declare ``world.nodes``; anything else falls back to
        :data:`statebreak.registry.DEFAULT_NODE_IDS`.
        """
        raw_nodes = self.world.get("nodes")
        if isinstance(raw_nodes, list) and raw_nodes:
            return tuple(str(n) for n in raw_nodes)
        return DEFAULT_NODE_IDS


@dataclass(frozen=True)
class StateSnapshot:
    """Authoritative snapshot of synthetic world state at a point in time."""

    snapshot_id: str
    state_version: str
    captured_at: str
    entities_hash: str


@dataclass(frozen=True)
class EffectRecord:
    """Record of proposed, attempted, committed, or rejected side-effect."""

    effect_id: str
    operation_id: str
    kind: str
    target: str
    status: str  # proposed, attempted, committed, rejected, unknown, verified
    payload_hash: str
    provider_id: str | None = None
    event_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Finding:
    """Evaluated oracle invariant failure or regression."""

    finding_id: str
    severity: str  # critical, high, medium, low, info
    category: str
    blocking: bool
    expected: dict[str, Any]
    observed: dict[str, Any]
    remediation: str
    event_refs: tuple[str, ...] = ()
    scenario_id: str | None = None
    fault_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentClaim:
    """Outcome claim declared by agent adapter."""

    name: str
    value: bool
    text: str | None = None


@dataclass(frozen=True)
class AdapterResult:
    """Result returned by an AgentAdapter execution."""

    claims: tuple[AgentClaim, ...]
    status: str  # completed, stopped, needs_review, error
    adapter_name: str
    adapter_version: str
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunReport:
    """Standardized report for a scenario execution."""

    schema: str
    run_id: str
    scenario_id: str
    scenario_hash: str
    seed: int
    adapter: dict[str, str]
    verdict: str  # pass, fail, needs_review, error
    metrics: dict[str, float]
    events: tuple[dict[str, Any], ...] = ()
    effects: tuple[EffectRecord, ...] = ()
    findings: tuple[Finding, ...] = ()
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Convert run report to dictionary matching report schema."""
        return asdict(self)
