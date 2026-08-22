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
from statebreak.coordination import MessageQueue
from statebreak.errors import ConfigurationError, UsageError
from statebreak.faults import FaultScheduler
from statebreak.gateway import ToolGateway
from statebreak.metrics import calculate_scenario_metrics
from statebreak.models import RunReport, Scenario
from statebreak.oracle import OracleContext, OracleEngine
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
        queue = MessageQueue(
            nodes=["node-01", "node-02", "node-03"],
            run_id=effective_run_id,
        )
        gateway = ToolGateway(
            world=world,
            fault_scheduler=fault_scheduler,
            clock=clock,
            allowed_tools=scenario.agent_task.tools,
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
        )
        oracle_res = self._oracle_engine.evaluate(scenario, oracle_ctx)

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
