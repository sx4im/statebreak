"""Authoritative ToolGateway wrapping LocalWorld and FaultScheduler for adapters."""

from __future__ import annotations

import copy
from typing import Any

from statebreak.adapter import ToolObservation, ToolOutcome
from statebreak.clock import VirtualClock
from statebreak.convergence import ConvergenceTracker
from statebreak.errors import UsageError
from statebreak.faults import FaultScheduler
from statebreak.world import ApprovalObservation, LocalWorld, MutationResult


class ToolGateway:
    """Gateway enforcing typed validation, fault interception, and outcome isolation."""

    def __init__(
        self,
        world: LocalWorld,
        fault_scheduler: FaultScheduler,
        clock: VirtualClock,
        allowed_tools: tuple[str, ...] | list[str] | None = None,
        convergence_tracker: ConvergenceTracker | None = None,
    ) -> None:
        self._world = world
        self._fault_scheduler = fault_scheduler
        self._clock = clock
        self._allowed_tools = set(allowed_tools) if allowed_tools is not None else None
        self._convergence_tracker = convergence_tracker
        self._last_handoff_payload: dict[str, Any] | None = None

    def _check_tool_allowed(self, tool_name: str) -> None:
        if self._allowed_tools is not None and tool_name not in self._allowed_tools:
            raise UsageError(
                f"tool '{tool_name}' is not in declared allowed tools: "
                f"{sorted(self._allowed_tools)}"
            )

    def read(
        self,
        name: str,
        target: str,
        node_id: str | None = None,
    ) -> ToolObservation:
        """Execute a read tool operation against the authoritative world with fault hooks."""
        self._check_tool_allowed(name)
        if not target or not isinstance(target, str):
            raise UsageError("read target must be a non-empty string")

        # 1. Dispatch before_read fault hook
        self._fault_scheduler.before_read(target=target, world=self._world, clock=self._clock)

        # 2. Query world state
        entity_data = self._world.get_entity(target)
        observed_at = self._clock.now_iso()

        if entity_data is None:
            return ToolObservation(
                name=name,
                target=target,
                state_version="v0",
                observed_at=observed_at,
                data=None,
                source="world",
            )

        # 3. Dispatch after_read fault hook (e.g. stale_read injection)
        after_dispatch = self._fault_scheduler.after_read(
            target=target,
            observation=entity_data,
            world=self._world,
            clock=self._clock,
        )

        final_data = (
            after_dispatch.modified_observation
            if after_dispatch.applied and after_dispatch.modified_observation is not None
            else entity_data
        )

        state_version = str(final_data.get("version", "v1"))

        final_obs = ToolObservation(
            name=name,
            target=target,
            state_version=state_version,
            observed_at=observed_at,
            data=copy.deepcopy(final_data),
            source="world",
        )

        # Track node-local observation for convergence evaluation
        if self._convergence_tracker is not None and node_id:
            self._convergence_tracker.observe(node_id, target, final_obs)

        return final_obs

    def act(
        self,
        name: str,
        target: str,
        payload: dict[str, Any],
        operation_id: str,
        expected_version: str | None = None,
        node_id: str | None = None,
    ) -> ToolOutcome:
        """Execute a mutating or side-effect action with fault hooks and idempotency checks."""
        self._check_tool_allowed(name)
        if not target or not isinstance(target, str):
            raise UsageError("action target must be a non-empty string")
        if not operation_id or not isinstance(operation_id, str):
            raise UsageError("action operation_id must be a non-empty string")
        if not isinstance(payload, dict):
            raise UsageError("action payload must be a dictionary")

        # 1. Dispatch before_commit and before_retry fault hooks
        self._fault_scheduler.before_retry(
            target=target,
            operation_id=operation_id,
            world=self._world,
            clock=self._clock,
        )

        before_dispatch = self._fault_scheduler.before_commit(
            target=target,
            operation_id=operation_id,
            payload=payload,
            world=self._world,
            clock=self._clock,
        )

        actual_target = target
        if before_dispatch.applied and before_dispatch.modified_target:
            actual_target = before_dispatch.modified_target

        # 2. Execute mutation in authoritative world
        mutation_result: MutationResult = self._world.update_entity(
            entity_id=actual_target,
            updates=payload,
            expected_version=expected_version,
            operation_id=operation_id,
            kind=name,
            provider_id=node_id,
        )

        # 3. Dispatch after_commit_before_response fault hook (e.g. timeout_after_commit)
        after_dispatch = self._fault_scheduler.after_commit_before_response(
            target=actual_target,
            operation_id=operation_id,
            result=mutation_result,
            world=self._world,
            clock=self._clock,
        )

        final_result = (
            after_dispatch.modified_result
            if after_dispatch.applied and after_dispatch.modified_result is not None
            else mutation_result
        )

        effect_id = final_result.effect.effect_id if final_result.effect else None

        outcome = ToolOutcome(
            status=final_result.status,
            effect_id=effect_id,
            operation_id=operation_id,
            target=actual_target,
            result={"version": final_result.after_version} if final_result.success else None,
            error=final_result.error,
            state_version=final_result.after_version if final_result.success else None,
            applied_fields=final_result.applied_fields,
            omitted_fields=final_result.omitted_fields,
        )

        # Track operation outcome for convergence evaluation
        if self._convergence_tracker is not None and node_id:
            self._convergence_tracker.record_outcome(node_id, operation_id, outcome)

        return outcome

    def check_approval(
        self,
        approval_id: str,
        node_id: str | None = None,
    ) -> ApprovalObservation:
        """Check status of an approval entity against current virtual clock."""
        return self._world.check_approval(approval_id, self._clock)

    def emit_handoff(
        self,
        payload: dict[str, Any],
        node_id: str | None = None,
    ) -> dict[str, Any]:
        """Emit a handoff payload, subject to fault interception (e.g. handoff_truncation)."""
        if not isinstance(payload, dict):
            raise UsageError("handoff payload must be a dictionary")

        dispatch = self._fault_scheduler.handoff_emit(payload, self._clock)
        if dispatch.applied and dispatch.modified_handoff is not None:
            emitted = copy.deepcopy(dispatch.modified_handoff)
        else:
            emitted = copy.deepcopy(payload)

        # Capture the actual (post-fault) handoff for oracle evaluation
        self._last_handoff_payload = copy.deepcopy(emitted)
        return emitted

    def last_handoff_payload(self) -> dict[str, Any] | None:
        """Return the most recent emitted handoff payload (post-fault), or None."""
        return copy.deepcopy(self._last_handoff_payload) if self._last_handoff_payload is not None else None
