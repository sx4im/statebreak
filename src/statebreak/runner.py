"""End-to-end scenario runner executing scenarios against world, faults, adapters, and oracles."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from statebreak.adapter import AdapterContext, AgentAdapter
from statebreak.adapters.guarded import GuardedAdapter
from statebreak.adapters.multi_node import MultiNodeAdapter
from statebreak.adapters.naive import NaiveAdapter
from statebreak.canonical import compute_scenario_hash
from statebreak.clock import VirtualClock
from statebreak.convergence import ConvergenceTracker
from statebreak.coordination import MessageQueue
from statebreak.errors import ConfigurationError, UsageError
from statebreak.faults import FaultScheduler
from statebreak.gateway import ToolGateway
from statebreak.metrics import calculate_scenario_metrics
from statebreak.models import Finding, RunReport, Scenario
from statebreak.oracle import OracleContext, OracleEngine, OracleEvaluationResult
from statebreak.registry import DEFAULT_NODE_IDS
from statebreak.scenario import load_scenario
from statebreak.world import LocalWorld


class ScenarioRunner:
    """Orchestrates end-to-end execution of StateBreak scenarios."""

    def __init__(self) -> None:
        self._oracle_engine = OracleEngine()

    def resolve_adapter(self, adapter_spec: str | AgentAdapter) -> AgentAdapter:
        """Resolve adapter name string or return existing AgentAdapter instance."""
        if isinstance(adapter_spec, AgentAdapter):
            return adapter_spec

        if not isinstance(adapter_spec, str):
            raise UsageError(f"invalid adapter specification: {adapter_spec}")

        name = adapter_spec.lower().strip()
        if name in ("guarded", "guarded-adapter"):
            return GuardedAdapter()
        elif name in ("naive", "naive-adapter"):
            return NaiveAdapter()
        elif name in ("multi_node", "multi-node", "multi-node-adapter"):
            return MultiNodeAdapter()
        else:
            raise UsageError(
                f"unknown adapter '{adapter_spec}'. Supported: 'guarded', 'naive', 'multi_node'"
            )

    def run_scenario(
        self,
        scenario_input: str | Path | Scenario,
        adapter: str | AgentAdapter = "guarded",
        seed: int | None = None,
        run_id: str | None = None,
        node_id: str = "node-01",
    ) -> RunReport:
        """Execute a single scenario end-to-end and return standardized RunReport."""
        # 1. Load scenario if path provided
        if isinstance(scenario_input, Scenario):
            scenario = scenario_input
        elif isinstance(scenario_input, (str, Path)):
            scenario = load_scenario(scenario_input)
        else:
            raise ConfigurationError(f"invalid scenario input type: {type(scenario_input)}")

        effective_seed = seed if seed is not None else scenario.seed
        effective_run_id = (
            run_id if run_id is not None else f"run_{scenario.id}_{effective_seed}"
        )
        scenario_hash = compute_scenario_hash(scenario.to_dict())

        # 2. Initialize deterministic environment
        clock = VirtualClock(
            start=scenario.clock.start,
            step_seconds=scenario.clock.step_seconds,
        )
        world = LocalWorld(scenario.world)
        fault_scheduler = FaultScheduler(scenario.faults, seed=effective_seed)

        # Node topology: scenarios may declare world.nodes; fall back to default.
        raw_nodes = scenario.world.get("nodes")
        node_ids: tuple[str, ...] = (
            tuple(str(n) for n in raw_nodes) if isinstance(raw_nodes, list) and raw_nodes else DEFAULT_NODE_IDS
        )
        queue = MessageQueue(
            nodes=list(node_ids),
            run_id=effective_run_id,
        )
        convergence_tracker = ConvergenceTracker()
        gateway = ToolGateway(
            world=world,
            fault_scheduler=fault_scheduler,
            clock=clock,
            allowed_tools=scenario.agent_task.tools,
            convergence_tracker=convergence_tracker,
        )

        # 3. Resolve and execute adapter
        adapter_instance = self.resolve_adapter(adapter)
        context = AdapterContext(
            task_instruction=scenario.agent_task.instruction,
            allowed_tools=scenario.agent_task.tools,
            gateway=gateway,
            clock=clock,
            node_id=node_id,
            run_id=effective_run_id,
            scenario_id=scenario.id,
            seed=effective_seed,
            coordination=queue,
            task_params=scenario.agent_task.params,
        )

        adapter_result = adapter_instance.run(context)

        # 4. Collect execution evidence
        effects = world.get_effects()
        fault_events = fault_scheduler.get_events()

        # 5. Authoritative oracle evaluation
        oracle_ctx = OracleContext(
            scenario_id=scenario.id,
            run_id=effective_run_id,
            world=world,
            clock=clock,
            effects=effects,
            fault_events=fault_events,
            adapter_result=adapter_result,
            convergence_tracker=convergence_tracker,
            handoff_payload=gateway.last_handoff_payload(),
            node_ids=node_ids,
        )
        oracle_res = self._oracle_engine.evaluate(scenario, oracle_ctx)
        oracle_res = self._check_expectations(scenario, adapter_instance, oracle_res)

        # 6. Calculate deterministic metrics
        metrics = calculate_scenario_metrics([(scenario, oracle_res, oracle_ctx)])

        # 7. Assemble standardized RunReport
        events_dicts: tuple[dict[str, Any], ...] = tuple(
            e.to_dict() for e in fault_events
        )

        return RunReport(
            schema="statebreak.report/v1",
            run_id=effective_run_id,
            scenario_id=scenario.id,
            scenario_hash=scenario_hash,
            seed=effective_seed,
            adapter={
                "name": adapter_instance.name,
                "version": adapter_instance.version,
            },
            verdict=oracle_res.verdict,
            metrics=metrics,
            events=events_dicts,
            effects=effects,
            findings=oracle_res.findings,
            limitations=adapter_result.limitations,
        )

    def _check_expectations(
        self,
        scenario: Scenario,
        adapter: AgentAdapter,
        result: OracleEvaluationResult,
    ) -> OracleEvaluationResult:
        """Enforce declared per-adapter expectations against the actual verdict.

        Without this check, a scenario whose ``expectations`` block predicts
        ``fail`` for an adapter would silently report an unexpected ``pass``
        as a success — expectations were decorative. A mismatch is a
        regression of the oracle/adapter contract and blocks.
        """
        expectation = scenario.expectations.get(adapter.name)
        if expectation is None:
            expectation = scenario.expectations.get(adapter.name.removesuffix("-adapter"))
        if expectation is None or expectation.verdict == result.verdict:
            return result

        finding = Finding(
            finding_id=f"fnd_{scenario.id}_expectation_{adapter.name}",
            severity="critical",
            category="expectation_mismatch",
            blocking=True,
            expected={"verdict": expectation.verdict, "adapter": adapter.name},
            observed={"verdict": result.verdict},
            remediation=(
                "Adapter behavior or oracle semantics changed relative to the "
                "scenario's declared expectation; update one or the other"
            ),
            event_refs=(),
            scenario_id=scenario.id,
            fault_refs=(),
        )
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        findings = tuple(
            sorted(
                (*result.findings, finding),
                key=lambda f: (
                    0 if f.blocking else 1,
                    severity_order.get(f.severity, 99),
                    f.finding_id,
                ),
            )
        )
        return OracleEvaluationResult(
            verdict="fail",
            findings=findings,
            oracle_results=result.oracle_results,
        )

    def run_scenarios(
        self,
        scenario_paths: list[str | Path],
        adapter: str | AgentAdapter = "guarded",
        seed: int | None = None,
    ) -> list[RunReport]:
        """Execute multiple scenarios sequentially and return list of reports."""
        reports: list[RunReport] = []
        for p in scenario_paths:
            rep = self.run_scenario(p, adapter=adapter, seed=seed)
            reports.append(rep)
        return reports
