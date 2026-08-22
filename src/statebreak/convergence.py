"""State convergence tracking and reconciliation engine for multi-node simulation."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from statebreak.adapter import ToolObservation, ToolOutcome
from statebreak.clock import VirtualClock
from statebreak.models import StateSnapshot
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
        observation: ToolObservation | dict[str, Any],
    ) -> None:
        """Record or update a node's local observation of an entity."""
        self._ensure_node(node_id)
        if isinstance(observation, ToolObservation):
            ver = observation.state_version
            ts = observation.observed_at
            data = copy.deepcopy(observation.data)
        else:
            ver = str(observation.get("version", "v1"))
            ts = str(observation.get("observed_at", ""))
            data = copy.deepcopy(observation)

        self._node_views[node_id][entity_id] = NodeEntityView(
            entity_id=entity_id,
            version=ver,
            observed_at=ts,
            data=data,
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
                    "expected_version": outcome.state_version or "v1",
                    "status": "unknown",
                }

        else:
            # Rejected or failed: remove from in-flight
            self._in_flight_ops[node_id].pop(operation_id, None)

    def reconcile(
        self,
        node_id: str,
        entity_id: str,
        world: LocalWorld,
        clock: VirtualClock,
    ) -> ToolObservation:
        """Refresh node view directly from authoritative state and clear in-flight ambiguities."""
        self._ensure_node(node_id)
        ent = world.get_entity(entity_id)
        obs_at = clock.now_iso()

        if ent is None:
            obs = ToolObservation(
                name="read",
                target=entity_id,
                state_version="v0",
                observed_at=obs_at,
                data=None,
            )
        else:
            ver = str(ent.get("version", "v1"))
            obs = ToolObservation(
                name="read",
                target=entity_id,
                state_version=ver,
                observed_at=obs_at,
                data=copy.deepcopy(ent),
            )

        self.observe(node_id, entity_id, obs)

        # Clear any in-flight operations for this entity
        to_remove = [
            op_id
            for op_id, op_data in self._in_flight_ops[node_id].items()
            if op_data.get("entity_id") == entity_id
        ]
        for op_id in to_remove:
            self._in_flight_ops[node_id].pop(op_id, None)

        return obs

    def apply_authoritative_snapshot(
        self,
        node_id: str,
        snapshot: StateSnapshot,
        world: LocalWorld,
    ) -> None:
        """Apply an authoritative snapshot to refresh all entity observations for node_id."""
        self._ensure_node(node_id)
        for ent in world.list_entities():
            eid = ent["id"]
            ver = ent["version"]
            self._node_views[node_id][eid] = NodeEntityView(
                entity_id=eid,
                version=ver,
                observed_at=snapshot.captured_at,
                data=copy.deepcopy(ent),
            )
        # Clear all in-flight operations for this node
        self._in_flight_ops[node_id].clear()

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

    def convergence_status(self, node_id: str, world: LocalWorld) -> dict[str, Any]:
        """Return detailed convergence status for a node across all observed entities."""
        self._ensure_node(node_id)
        entities_report: dict[str, dict[str, Any]] = {}
        all_converged = True

        for entity_id, view in self._node_views[node_id].items():
            if world.has_entity(entity_id):
                auth_ver = world.get_entity_version(entity_id)
                converged = self.is_converged(node_id, entity_id, world)
            else:
                auth_ver = "non-existent"
                converged = False

            if not converged:
                all_converged = False

            entities_report[entity_id] = {
                "local_version": view.version,
                "authoritative_version": auth_ver,
                "converged": converged,
            }

        unreconciled = [
            {"operation_id": op_id, **op_data}
            for op_id, op_data in self._in_flight_ops[node_id].items()
            if op_data.get("status") == "unknown"
        ]

        if unreconciled:
            all_converged = False

        return {
            "node_id": node_id,
            "is_converged": all_converged and len(entities_report) > 0,
            "entities": entities_report,
            "unreconciled_operations": unreconciled,
        }

    def reset(self) -> None:
        """Reset all node views and in-flight operations."""
        self._node_views.clear()
        self._in_flight_ops.clear()
