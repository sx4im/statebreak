"""Deterministic naive reference adapter demonstrating unverified agent execution.

DEMO-ONLY: this adapter targets scenario task param ``target_entity`` (or the
bundled demo entities ``example-001``/``order-001``). It intentionally models
unsafe behaviors; it is a teaching example, not a production adapter.
"""

from __future__ import annotations

from typing import Any

from statebreak.adapter import (
    AdapterContext,
    AdapterResult,
    AgentAdapter,
)


class NaiveAdapter(AgentAdapter):
    """Deterministic reference adapter that trusts local context without verifying convergence.

    Demonstrates failure modes:
    - Reusing stale state without re-verifying before commit.
    - Retrying with non-stable operation IDs leading to duplicate effects.
    - Treating unknown/timeout outcomes as immediate successes.
    - Ignoring target mismatches.
    - Emitting handoffs without verifying required constraints.
    """

    name = "naive-adapter"
    version = "0.1.0"

    def __init__(self, step_counter: int = 0) -> None:
        self._step_counter = step_counter

    def run(self, context: AdapterContext) -> AdapterResult:
        """Execute task naively, trusting local responses without freshness checks."""
        # 1. Target selection: scenario task params first (``target_entity``),
        # then demo fallbacks example-001 -> order-001.
        target = str(
            context.task_params.get("target_entity")
            or context.task_params.get("target")
            or "example-001"
        )
        updates: dict[str, Any] = {"status": "completed"}
        op_id = f"op_naive_{self._step_counter}"
        self._step_counter += 1

        # 2. Read once without verifying freshness
        if context.gateway:
            obs = context.gateway.read("read_state", target, node_id=context.node_id)
            if obs.data is None:
                # Demo fallback for bundled scenarios.
                target = "order-001"
                obs = context.gateway.read("read_state", target, node_id=context.node_id)

            # 3. Act without passing expected_version or checking target drift
            outcome = context.gateway.act(
                "commit_effect",
                target,
                updates,
                operation_id=op_id,
                expected_version=None,  # Naive ignores version locking
                node_id=context.node_id,
            )

            # In duplicate-retry scenarios, naive adapter retries with a new op_id on uncertainty
            has_retry_task = "retry" in context.task_instruction.lower()
            is_dup_scenario = "duplicate" in context.scenario_id or "retry" in context.scenario_id
            if outcome.status in ("unknown", "rejected") or has_retry_task or is_dup_scenario:
                retry_op_id = f"op_naive_retry_{self._step_counter}"
                self._step_counter += 1
                context.gateway.act(
                    "commit_effect",
                    target,
                    updates,
                    operation_id=retry_op_id,
                    expected_version=None,
                    node_id=context.node_id,
                )

            # In handoff scenarios, naive adapter emits handoff without verifying constraints
            if "handoff" in context.scenario_id:
                context.gateway.emit_handoff(
                    {"task_id": f"handoff_{context.scenario_id}", "status": "completed"}
                )

            # 4. Naive behavior: blindly claim success regardless of outcome status
            context.add_claim("task_committed", True, text="Action assumed committed")
            context.add_claim("task_completed", True, text="Action assumed successful")
            context.add_claim("state_mutated", True, text=f"Target {target} updated")

        else:
            context.add_claim("task_committed", True, text="Executed in mock context")
            context.add_claim("task_completed", True, text="Executed in mock context")

        return AdapterResult(
            claims=context.claims,
            status="completed",
            adapter_name=self.name,
            adapter_version=self.version,
            limitations=("trusts_local_context", "no_version_check", "no_idempotency_keys"),
        )
