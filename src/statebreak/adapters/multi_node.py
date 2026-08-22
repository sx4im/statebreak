"""Deterministic multi-node coordinating reference adapter."""

from __future__ import annotations

from typing import Any

from statebreak.adapter import (
    AdapterContext,
    AdapterResult,
    AgentAdapter,
)


class MultiNodeAdapter(AgentAdapter):
    """Deterministic reference adapter participating in multi-node coordination simulation.

    Demonstrates multi-node coordination:
    - Exchanging versioned state synchronization messages with peer nodes.
    - Deduplicating incoming coordination messages to prevent redundant processing.
    - Detecting concurrent version conflicts and invoking reconciliation.
    - Broadcasting updates after successful authoritative commits.
    """

    name = "multi-node-adapter"
    version = "0.1.0"

    def __init__(self) -> None:
        self._processed_message_ids: set[str] = set()

    def run(self, context: AdapterContext) -> AdapterResult:
        """Execute coordinating task across message queue and gateway."""
        target = "shared-doc-01"
        updates: dict[str, Any] = {"status": "synced"}
        final_status = "completed"
        peer_updated = False

        # 1. Process pending coordination messages
        if context.coordination:
            pending = context.coordination.receive_all(context.node_id)
            for msg in pending:
                if msg.message_id in self._processed_message_ids:
                    # Deduplicated duplicate delivery
                    context.add_claim(
                        "duplicate_message_ignored",
                        True,
                        text=f"Ignored dup {msg.message_id}",
                    )
                    continue

                self._processed_message_ids.add(msg.message_id)

                if msg.message_type == "state_update" and msg.entity_id == target:
                    peer_updated = True
                    context.add_claim(
                        "peer_update_received",
                        True,
                        text=f"Received update from {msg.sender_id}",
                    )

        # 2. Interact with gateway if available
        if context.gateway:
            if peer_updated:
                obs = context.gateway.read("read_state", target, node_id=context.node_id)
                context.add_claim(
                    "node_synced",
                    True,
                    text=f"Observed version {obs.state_version}",
                )
                final_status = "completed"
            else:
                obs = context.gateway.read("read_state", target, node_id=context.node_id)
                expected_ver = obs.state_version if obs.data else "v1"
                op_id = f"op_node_{context.node_id}_{target}_{expected_ver}"

                outcome = context.gateway.act(
                    "commit_effect",
                    target,
                    updates,
                    operation_id=op_id,
                    expected_version=expected_ver,
                    node_id=context.node_id,
                )

                if outcome.status == "committed":
                    context.add_claim(
                        "node_committed",
                        True,
                        text=f"Committed at {outcome.state_version}",
                    )
                    # 3. Broadcast state update to peers
                    if context.coordination:
                        context.coordination.send(
                            sender_id=context.node_id,
                            recipient_id="*",
                            message_type="state_update",
                            payload={"entity_id": target, "version": outcome.state_version},
                            operation_id=op_id,
                            entity_id=target,
                            expected_version=outcome.state_version,
                            clock=context.clock,
                        )
                    final_status = "completed"

                elif outcome.status == "rejected":
                    context.add_claim("conflict_detected", True, text=f"Stale: {outcome.error}")
                    final_status = "needs_review"

                elif outcome.status == "unknown":
                    context.add_claim(
                        "unknown_requires_reconcile",
                        True,
                        text="Reconciling unknown",
                    )
                    context.gateway.read("read_state", target, node_id=context.node_id)
                    final_status = "needs_review"

        return AdapterResult(
            claims=context.claims,
            status=final_status,
            adapter_name=self.name,
            adapter_version=self.version,
        )
