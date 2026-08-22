"""StateBreak: Deterministic stale-state and false-success failure fixtures for AI agents."""

from statebreak.canonical import canonical_json, compute_scenario_hash, compute_sha256
from statebreak.errors import (
    ConfigurationError,
    FindingRegressionError,
    InternalError,
    StateBreakError,
    UsageError,
)
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
from statebreak.scenario import (
    load_scenario,
    load_scenario_from_dict,
    load_scenarios_from_dir,
    validate_scenario_dict,
)
from statebreak.adapter import (
    AdapterContext,
    AdapterError,
    AgentAdapter,
    CoordinationMessage,
    HandoffPayload,
    ToolObservation,
    ToolOutcome,
    ToolRequest,
)
from statebreak.adapters import GuardedAdapter, MultiNodeAdapter, NaiveAdapter
from statebreak.clock import VirtualClock
from statebreak.convergence import ConvergenceTracker
from statebreak.coordination import MessageQueue
from statebreak.faults import FaultDispatchResult, FaultEvent, FaultScheduler
from statebreak.gateway import ToolGateway
from statebreak.metrics import calculate_scenario_metrics
from statebreak.oracle import OracleContext, OracleEngine, OracleEvaluationResult
from statebreak.report import render_json, render_markdown, render_sarif
from statebreak.runner import ScenarioRunner
from statebreak.world import ApprovalObservation, LocalWorld, MutationResult

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "StateBreakError",
    "UsageError",
    "ConfigurationError",
    "FindingRegressionError",
    "InternalError",
    "ClockSpec",
    "FaultSpec",
    "AgentTaskSpec",
    "OracleSpec",
    "ExpectationSpec",
    "Scenario",
    "StateSnapshot",
    "EffectRecord",
    "Finding",
    "AgentClaim",
    "AdapterResult",
    "RunReport",
    "canonical_json",
    "compute_sha256",
    "compute_scenario_hash",
    "validate_scenario_dict",
    "load_scenario",
    "load_scenario_from_dict",
    "load_scenarios_from_dir",
    "VirtualClock",
    "LocalWorld",
    "MutationResult",
    "ApprovalObservation",
    "FaultScheduler",
    "FaultEvent",
    "FaultDispatchResult",
    "AgentAdapter",
    "AdapterContext",
    "AdapterError",
    "ToolRequest",
    "ToolObservation",
    "ToolOutcome",
    "HandoffPayload",
    "CoordinationMessage",
    "ToolGateway",
    "MessageQueue",
    "ConvergenceTracker",
    "NaiveAdapter",
    "GuardedAdapter",
    "MultiNodeAdapter",
    "OracleEngine",
    "OracleContext",
    "OracleEvaluationResult",
    "calculate_scenario_metrics",
    "ScenarioRunner",
    "render_json",
    "render_markdown",
    "render_sarif",
]
