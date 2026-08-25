"""State convergence tracking for multi-node simulation."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from statebreak.adapter import ToolObservation, ToolOutcome
from statebreak.world import LocalWorld


@dataclass(frozen=True)
class NodeEntityView:
    """Node-local cached observation of an entity."""

    entity_id: str
    version: str
    observed_at: str
    data: dict[str, Any] | None = None


class ConvergenceTracker:
    """Tracks node-local observations and evaluates convergence against authoritative state."""

    def __init__(self) -> None:
        self._node_views: dict[str, dict[str, NodeEntityView]] = {}
        self._in_flight_ops: dict[str, dict[str, dict[str, Any]]] = {}

    def _ensure_node(self, node_id: str) -> None:
        if node_id not in self._node_views:
            self._node_views[node_id] = {}
        if node_id not in self._in_flight_ops:
            self._in_flight_ops[node_id] = {}

    def observe(
        self,
        node_id: str,
        entity_id: str,
        observation: ToolObservation,
    ) -> None:
        """Record or update a node's local observation of an entity."""
        self._ensure_node(node_id)
        self._node_views[node_id][entity_id] = NodeEntityView(
            entity_id=entity_id,
            version=observation.state_version,
            observed_at=observation.observed_at,
            data=copy.deepcopy(observation.data),
        )

    def record_proposal(
        self,
        node_id: str,
        operation_id: str,
        entity_id: str,
        expected_version: str,
        updates: dict[str, Any],
    ) -> None:
        """Track an in-flight proposed operation before gateway outcome is known."""
        self._ensure_node(node_id)
        self._in_flight_ops[node_id][operation_id] = {
            "entity_id": entity_id,
            "expected_version": expected_version,
            "updates": copy.deepcopy(updates),
            "status": "proposed",
        }

    def record_outcome(
        self,
        node_id: str,
        operation_id: str,
        outcome: ToolOutcome,
    ) -> None:
        """Update node convergence tracking upon receiving a gateway outcome."""
        self._ensure_node(node_id)
        if outcome.status == "committed" and outcome.state_version:
            # Update node's local version view to committed version
            current_view = self._node_views[node_id].get(outcome.target)
            self._node_views[node_id][outcome.target] = NodeEntityView(
                entity_id=outcome.target,
                version=outcome.state_version,
                observed_at=current_view.observed_at if current_view else "",
                data=current_view.data if current_view else None,
            )
            self._in_flight_ops[node_id].pop(operation_id, None)

        elif outcome.status == "unknown":
            # Keep in-flight with unknown status requiring reconciliation
            if operation_id in self._in_flight_ops[node_id]:
                self._in_flight_ops[node_id][operation_id]["status"] = "unknown"
            else:
                self._in_flight_ops[node_id][operation_id] = {
                    "entity_id": outcome.target,
                    "expected_version": outcome.state_version,
                    "status": "unknown",
                }

        else:
            # Rejected or failed: remove from in-flight
            self._in_flight_ops[node_id].pop(operation_id, None)

    def is_converged(self, node_id: str, entity_id: str, world: LocalWorld) -> bool:
        """Check if node_id is converged with authoritative world state on entity_id."""
        if node_id not in self._node_views or entity_id not in self._node_views[node_id]:
            return False

        if not world.has_entity(entity_id):
            return False

        local_ver = self._node_views[node_id][entity_id].version
        auth_ver = world.get_entity_version(entity_id)

        if local_ver != auth_ver:
            return False

        # Must have zero in-flight unknown operations for this entity
        in_flight = self._in_flight_ops.get(node_id, {})
        for op_data in in_flight.values():
            if op_data.get("entity_id") == entity_id and op_data.get("status") == "unknown":
                return False

        return True

    def reset(self) -> None:
        """Reset all node views and in-flight operations."""
        self._node_views.clear()
        self._in_flight_ops.clear()
