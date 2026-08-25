"""StateBreak: Deterministic stale-state and false-success failure fixtures for AI agents."""

from statebreak.adapter import (
    AdapterContext,
    AdapterError,
    AgentAdapter,
    CoordinationMessage,
    HandoffPayload,
    ToolObservation,
    ToolOutcome,
)
from statebreak.adapters import GuardedAdapter, MultiNodeAdapter, NaiveAdapter
from statebreak.canonical import canonical_json, compute_scenario_hash, compute_sha256
from statebreak.clock import VirtualClock
from statebreak.convergence import ConvergenceTracker
from statebreak.coordination import MessageQueue
from statebreak.errors import (
    ConfigurationError,
    FindingRegressionError,
    InternalError,
    StateBreakError,
    UsageError,
)
from statebreak.faults import FaultDispatchResult, FaultEvent, FaultScheduler
from statebreak.gateway import ToolGateway
from statebreak.metrics import calculate_scenario_metrics
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
from statebreak.oracle import OracleContext, OracleEngine, OracleEvaluationResult
from statebreak.report import render_json, render_markdown, render_sarif
from statebreak.runner import ScenarioRunner
from statebreak.scenario import (
    load_scenario,
    load_scenario_from_dict,
    load_scenarios_from_dir,
    validate_scenario_dict,
)
from statebreak.world import ApprovalObservation, LocalWorld, MutationResult

__version__ = "0.1.0"

__all__ = [
    "AdapterContext",
    "AdapterError",
    "AdapterResult",
    "AgentAdapter",
    "AgentClaim",
    "AgentTaskSpec",
    "ApprovalObservation",
    "ClockSpec",
    "ConfigurationError",
    "ConvergenceTracker",
    "CoordinationMessage",
    "EffectRecord",
    "ExpectationSpec",
    "FaultDispatchResult",
    "FaultEvent",
    "FaultScheduler",
    "FaultSpec",
    "Finding",
    "FindingRegressionError",
    "GuardedAdapter",
    "HandoffPayload",
    "InternalError",
    "LocalWorld",
    "MessageQueue",
    "MultiNodeAdapter",
    "MutationResult",
    "NaiveAdapter",
    "OracleContext",
    "OracleEngine",
    "OracleEvaluationResult",
    "OracleSpec",
    "RunReport",
    "Scenario",
    "ScenarioRunner",
    "StateBreakError",
    "StateSnapshot",
    "ToolGateway",
    "ToolObservation",
    "ToolOutcome",
    "UsageError",
    "VirtualClock",
    "__version__",
    "calculate_scenario_metrics",
    "canonical_json",
    "compute_scenario_hash",
    "compute_sha256",
    "load_scenario",
    "load_scenario_from_dict",
    "load_scenarios_from_dir",
    "render_json",
    "render_markdown",
    "render_sarif",
    "validate_scenario_dict",
]
