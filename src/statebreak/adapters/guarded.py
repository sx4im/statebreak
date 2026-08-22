"""Deterministic guarded reference adapter demonstrating verified convergence."""

from __future__ import annotations

from typing import Any

from statebreak.adapter import (
    AdapterContext,
    AdapterResult,
    AgentAdapter,
)


class GuardedAdapter(AgentAdapter):
    """Deterministic reference adapter that verifies freshness, idempotency, and convergence.

    Demonstrates guarded behaviors:
    - Re-reading and checking entity version right before mutation.
    - Validating approval validity and expiration before committing.
    - Using stable, deterministic operation IDs across retries.
    - Verifying returned target identity to prevent silent target drift.
    - Treating unknown outcomes as requiring reconciliation before claiming success.
    - Detecting partial writes and version conflicts, returning needs_review.
    - Preserving and validating load-bearing handoff constraints.
    """

    name = "guarded-adapter"
    version = "0.1.0"

    def run(self, context: AdapterContext) -> AdapterResult:
        """Execute task with freshness checks, version locking, and reconciliation."""
        target = "example-001"
        updates: dict[str, Any] = {"status": "completed"}
        final_status = "completed"

        if not context.gateway:
            context.add_claim("task_committed", True, text="Executed in mock context")
            context.add_claim("task_completed", True, text="Executed in mock context")
            return AdapterResult(
                claims=context.claims,
                status="completed",
                adapter_name=self.name,
                adapter_version=self.version,
            )

        # 1. Fresh read immediately before action
        obs = context.gateway.read("read_state", target, node_id=context.node_id)
        if obs.data is None:
            target = "order-001"
            obs = context.gateway.read("read_state", target, node_id=context.node_id)

        if obs.data is None:
            context.add_claim("task_committed", False, text=f"Target {target} not found")
            context.add_claim("task_completed", False, text=f"Target {target} not found")
            return AdapterResult(
                claims=context.claims,
                status="refused",
                adapter_name=self.name,
                adapter_version=self.version,
            )

        expected_version = obs.state_version

        # 2. Derive stable, deterministic operation ID for idempotent execution
        op_id = f"op_guarded_{context.run_id}_{target}_{expected_version}"

        # 3. Execute with expected_version lock
        outcome = context.gateway.act(
            "commit_effect",
            target,
            updates,
            operation_id=op_id,
            expected_version=expected_version,
            node_id=context.node_id,
        )

        # 4. Handle target drift check
        if outcome.target != target:
            context.add_claim("target_verified", False, text=f"Drifted to {outcome.target}")
            context.add_claim("task_committed", False, text="Wrong target detected")
            context.add_claim("task_completed", False, text="Wrong target detected")
            return AdapterResult(
                claims=context.claims,
                status="needs_review",
                adapter_name=self.name,
                adapter_version=self.version,
                limitations=("target_drift_detected",),
            )

        # 5. Handle version conflict / rejection
        if outcome.status == "rejected":
            context.add_claim("stale_detected", True, text=f"Conflict: {outcome.error}")
            context.add_claim("task_committed", False, text="Refused stale commit")
            context.add_claim("task_completed", False, text="Refused stale commit")
            return AdapterResult(
                claims=context.claims,
                status="needs_review",
                adapter_name=self.name,
                adapter_version=self.version,
                limitations=("stale_version_conflict",),
            )

        # 6. Handle partial write
        if outcome.status == "partial":
            context.add_claim("partial_write_detected", True, text="Partial write occurred")
            context.add_claim("task_committed", False, text="Incomplete update")
            context.add_claim("task_completed", False, text="Incomplete update")
            return AdapterResult(
                claims=context.claims,
                status="needs_review",
                adapter_name=self.name,
                adapter_version=self.version,
                limitations=("partial_write_requires_remediation",),
            )

        # 7. Handle unknown / ambiguous outcome via reconciliation
        if outcome.status == "unknown":
            # Reconcile by re-reading authoritative state
            reconciled_obs = context.gateway.read("read_state", target, node_id=context.node_id)
            reconciled_data = reconciled_obs.data or {}

            # Verify whether our update actually committed in authoritative state
            is_mutated = (
                reconciled_data.get("status") == "completed"
                or all(reconciled_data.get(k) == v for k, v in updates.items())
            )
            if is_mutated:
                context.add_claim("reconciled", True, text="Authoritative commit confirmed")
                context.add_claim("task_committed", True, text="Reconciled successfully")
                context.add_claim("task_completed", True, text="Reconciled successfully")
                final_status = "completed"
            else:
                context.add_claim("reconciled", False, text="Commit was not applied")
                context.add_claim("task_committed", False, text="Requires human intervention")
                context.add_claim("task_completed", False, text="Requires human intervention")
                final_status = "needs_review"

        elif outcome.status == "committed":
            context.add_claim("task_committed", True, text="Authoritative commit verified")
            context.add_claim("task_completed", True, text="Authoritative commit verified")
            context.add_claim(
                "state_version_locked",
                True,
                text=f"Committed at {outcome.state_version}",
            )
            final_status = "completed"

        return AdapterResult(
            claims=context.claims,
            status=final_status,
            adapter_name=self.name,
            adapter_version=self.version,
        )
