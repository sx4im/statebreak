"""Authoritative in-memory local synthetic world for StateBreak scenarios."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from statebreak.canonical import canonical_json, compute_sha256
from statebreak.clock import VirtualClock
from statebreak.errors import ConfigurationError, UsageError
from statebreak.models import EffectRecord, StateSnapshot

MAX_PAYLOAD_SIZE = 65_536  # 64 KB limit for mutation payloads
MAX_ENTITIES_COUNT = 10_000

# Secret patterns to sanitize/reject
SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
]


@dataclass(frozen=True)
class MutationResult:
    """Result of an entity state mutation attempt."""

    success: bool
    status: str  # committed, rejected, partial, unknown
    entity_id: str
    before_version: str
    after_version: str
    effect: EffectRecord | None = None
    error: str | None = None
    applied_fields: tuple[str, ...] = ()
    omitted_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ApprovalObservation:
    """Read observation of an approval entity."""

    approval_id: str
    status: str  # approved, expired, revoked, pending, rejected, not_found
    subject: str | None = None
    scope: str | None = None
    amount: float | None = None
    expires_at: str | None = None
    is_valid: bool = False


class LocalWorld:
    """Authoritative in-memory synthetic world for deterministic scenario execution."""

    def __init__(self, initial_world: dict[str, Any] | None = None) -> None:
        self._initial_config: dict[str, Any] = copy.deepcopy(initial_world) if initial_world else {}
        self._entities: dict[str, dict[str, Any]] = {}
        self._entity_versions: dict[str, int] = {}
        self._effects: list[EffectRecord] = []
        self._operations: dict[str, EffectRecord] = {}
        self._effect_counter: int = 0

        self._load_initial_state(self._initial_config)

    def _load_initial_state(self, config: dict[str, Any]) -> None:
        """Populate world entities from scenario world configuration."""
        self._entities.clear()
        self._entity_versions.clear()
        self._effects.clear()
        self._operations.clear()
        self._effect_counter = 0

        if not config:
            return

        # 1. Check for entities list under "entities"
        if "entities" in config and isinstance(config["entities"], list):
            for item in config["entities"]:
                if isinstance(item, dict) and "id" in item:
                    self.register_entity(item)

        # 2. Check for named entity collections (approvals, refunds, orders, messages, documents)
        for key, val in config.items():
            if key == "entities":
                continue
            if isinstance(val, list):
                entity_type = key[:-1] if key.endswith("s") else key
                for item in val:
                    if isinstance(item, dict) and "id" in item:
                        item_copy = dict(item)
                        if "type" not in item_copy:
                            item_copy["type"] = entity_type
                        self.register_entity(item_copy)

    def register_entity(self, entity_data: dict[str, Any]) -> None:
        """Register an initial entity in the world."""
        if not isinstance(entity_data, dict):
            raise ConfigurationError("entity data must be a dictionary")
        if "id" not in entity_data or not str(entity_data["id"]).strip():
            raise ConfigurationError("entity must have a non-empty string 'id'")

        entity_id = str(entity_data["id"]).strip()
        if len(self._entities) >= MAX_ENTITIES_COUNT and entity_id not in self._entities:
            raise UsageError(f"world exceeds maximum entity limit of {MAX_ENTITIES_COUNT}")

        cleaned = copy.deepcopy(entity_data)
        cleaned["id"] = entity_id
        if "type" not in cleaned:
            cleaned["type"] = "synthetic_record"

        # Sanitize secret check
        for k, v in cleaned.items():
            if isinstance(v, str):
                for pat in SECRET_PATTERNS:
                    if pat.search(v):
                        raise ConfigurationError(
                            f"security violation: entity {entity_id}.{k} "
                            "contains credential pattern"
                        )

        self._entities[entity_id] = cleaned
        if entity_id not in self._entity_versions:
            self._entity_versions[entity_id] = 1

    def has_entity(self, entity_id: str) -> bool:
        """Check if an entity exists in the world."""
        return entity_id in self._entities

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """Get an isolated deep copy of an entity with its current state version."""
        if entity_id not in self._entities:
            return None
        ent = copy.deepcopy(self._entities[entity_id])
        ent["version"] = f"v{self._entity_versions[entity_id]}"
        return ent

    def get_entity_version(self, entity_id: str) -> str:
        """Get current state version string of an entity."""
        if entity_id not in self._entities:
            raise UsageError(f"entity '{entity_id}' not found in world")
        return f"v{self._entity_versions[entity_id]}"

    def list_entities(self, entity_type: str | None = None) -> list[dict[str, Any]]:
        """List all entities, optionally filtered by type, sorted by entity ID."""
        result: list[dict[str, Any]] = []
        for eid in sorted(self._entities.keys()):
            ent = self._entities[eid]
            if entity_type is None or ent.get("type") == entity_type:
                copy_ent = copy.deepcopy(ent)
                copy_ent["version"] = f"v{self._entity_versions[eid]}"
                result.append(copy_ent)
        return result

    def check_approval(
        self, approval_id: str, clock: VirtualClock | None = None
    ) -> ApprovalObservation:
        """Check status of an approval entity against current virtual time."""
        ent = self.get_entity(approval_id)
        if ent is None:
            return ApprovalObservation(
                approval_id=approval_id,
                status="not_found",
                is_valid=False,
            )

        declared_status = str(ent.get("status", "pending")).lower()
        expires_at = ent.get("expires_at")
        subject = ent.get("subject")
        scope = ent.get("scope")
        amount = float(ent["amount"]) if "amount" in ent and ent["amount"] is not None else None

        if declared_status == "revoked":
            return ApprovalObservation(
                approval_id=approval_id,
                status="revoked",
                subject=subject,
                scope=scope,
                amount=amount,
                expires_at=expires_at,
                is_valid=False,
            )

        if declared_status == "rejected":
            return ApprovalObservation(
                approval_id=approval_id,
                status="rejected",
                subject=subject,
                scope=scope,
                amount=amount,
                expires_at=expires_at,
                is_valid=False,
            )

        # Check virtual clock expiry if provided and expires_at is declared
        if clock is not None and expires_at:
            if clock.is_expired(str(expires_at)):
                return ApprovalObservation(
                    approval_id=approval_id,
                    status="expired",
                    subject=subject,
                    scope=scope,
                    amount=amount,
                    expires_at=str(expires_at),
                    is_valid=False,
                )

        is_valid = declared_status == "approved"
        return ApprovalObservation(
            approval_id=approval_id,
            status=declared_status,
            subject=subject,
            scope=scope,
            amount=amount,
            expires_at=str(expires_at) if expires_at else None,
            is_valid=is_valid,
        )

    def _next_effect_id(self) -> str:
        self._effect_counter += 1
        return f"eff_{self._effect_counter:04d}"

    def update_entity(
        self,
        entity_id: str,
        updates: dict[str, Any],
        expected_version: str | None = None,
        operation_id: str | None = None,
        kind: str = "update",
        provider_id: str | None = None,
        event_refs: tuple[str, ...] = (),
    ) -> MutationResult:
        """Update an entity with expected-version checking and effect recording."""
        if entity_id not in self._entities:
            eff_id = self._next_effect_id()
            op_id = operation_id or f"op_{eff_id}"
            eff = EffectRecord(
                effect_id=eff_id,
                operation_id=op_id,
                kind=kind,
                target=entity_id,
                status="rejected",
                payload_hash=compute_sha256(canonical_json(updates)),
                provider_id=provider_id,
                event_refs=event_refs,
            )
            self._record_effect(eff)
            return MutationResult(
                success=False,
                status="rejected",
                entity_id=entity_id,
                before_version="v0",
                after_version="v0",
                effect=eff,
                error=f"target entity '{entity_id}' does not exist in world",
            )

        # Check payload size limit
        payload_json = canonical_json(updates)
        if len(payload_json) > MAX_PAYLOAD_SIZE:
            raise UsageError(
                f"mutation payload exceeds maximum size of {MAX_PAYLOAD_SIZE} bytes"
            )

        cur_ver_num = self._entity_versions[entity_id]
        cur_ver_str = f"v{cur_ver_num}"

        # Idempotency check: if operation_id was already committed
        if operation_id and operation_id in self._operations:
            existing_effect = self._operations[operation_id]
            return MutationResult(
                success=True,
                status="committed",
                entity_id=entity_id,
                before_version=cur_ver_str,
                after_version=cur_ver_str,
                effect=existing_effect,
                applied_fields=(),
            )

        # Expected version conflict check
        if expected_version is not None and expected_version != cur_ver_str:
            eff_id = self._next_effect_id()
            op_id = operation_id or f"op_{eff_id}"
            eff = EffectRecord(
                effect_id=eff_id,
                operation_id=op_id,
                kind=kind,
                target=entity_id,
                status="rejected",
                payload_hash=compute_sha256(payload_json),
                provider_id=provider_id,
                event_refs=event_refs,
            )
            self._record_effect(eff)
            return MutationResult(
                success=False,
                status="rejected",
                entity_id=entity_id,
                before_version=cur_ver_str,
                after_version=cur_ver_str,
                effect=eff,
                error=(
                    f"version conflict: expected '{expected_version}', "
                    f"authoritative is '{cur_ver_str}'"
                ),
            )

        # Apply mutation
        target_ent = self._entities[entity_id]
        applied: list[str] = []
        for k, v in updates.items():
            if k not in {"id", "type"}:
                target_ent[k] = copy.deepcopy(v)
                applied.append(k)

        # Increment version on mutation
        new_ver_num = cur_ver_num + 1
        self._entity_versions[entity_id] = new_ver_num
        new_ver_str = f"v{new_ver_num}"

        eff_id = self._next_effect_id()
        op_id = operation_id or f"op_{eff_id}"
        eff = EffectRecord(
            effect_id=eff_id,
            operation_id=op_id,
            kind=kind,
            target=entity_id,
            status="committed",
            payload_hash=compute_sha256(payload_json),
            provider_id=provider_id,
            event_refs=event_refs,
        )
        self._record_effect(eff)

        return MutationResult(
            success=True,
            status="committed",
            entity_id=entity_id,
            before_version=cur_ver_str,
            after_version=new_ver_str,
            effect=eff,
            applied_fields=tuple(applied),
        )

    def partial_update_entity(
        self,
        entity_id: str,
        updates: dict[str, Any],
        applied_fields: list[str],
        omitted_fields: list[str],
        expected_version: str | None = None,
        operation_id: str | None = None,
        kind: str = "partial_write",
        provider_id: str | None = None,
        event_refs: tuple[str, ...] = (),
    ) -> MutationResult:
        """Apply a partial write, incrementing entity version and recording partial effect."""
        if entity_id not in self._entities:
            eff_id = self._next_effect_id()
            op_id = operation_id or f"op_{eff_id}"
            eff = EffectRecord(
                effect_id=eff_id,
                operation_id=op_id,
                kind=kind,
                target=entity_id,
                status="rejected",
                payload_hash=compute_sha256(canonical_json(updates)),
                provider_id=provider_id,
                event_refs=event_refs,
            )
            self._record_effect(eff)
            return MutationResult(
                success=False,
                status="rejected",
                entity_id=entity_id,
                before_version="v0",
                after_version="v0",
                effect=eff,
                error=f"target entity '{entity_id}' does not exist in world",
            )

        cur_ver_num = self._entity_versions[entity_id]
        cur_ver_str = f"v{cur_ver_num}"

        if expected_version is not None and expected_version != cur_ver_str:
            eff_id = self._next_effect_id()
            op_id = operation_id or f"op_{eff_id}"
            eff = EffectRecord(
                effect_id=eff_id,
                operation_id=op_id,
                kind=kind,
                target=entity_id,
                status="rejected",
                payload_hash=compute_sha256(canonical_json(updates)),
                provider_id=provider_id,
                event_refs=event_refs,
            )
            self._record_effect(eff)
            return MutationResult(
                success=False,
                status="rejected",
                entity_id=entity_id,
                before_version=cur_ver_str,
                after_version=cur_ver_str,
                effect=eff,
                error=(
                    f"version conflict: expected '{expected_version}', "
                    f"authoritative is '{cur_ver_str}'"
                ),
            )

        # Apply only declared applied_fields
        target_ent = self._entities[entity_id]
        actually_applied: list[str] = []
        for field_name in applied_fields:
            if field_name in updates and field_name not in {"id", "type"}:
                target_ent[field_name] = copy.deepcopy(updates[field_name])
                actually_applied.append(field_name)

        new_ver_num = cur_ver_num + 1
        self._entity_versions[entity_id] = new_ver_num
        new_ver_str = f"v{new_ver_num}"

        eff_id = self._next_effect_id()
        op_id = operation_id or f"op_{eff_id}"
        eff = EffectRecord(
            effect_id=eff_id,
            operation_id=op_id,
            kind=kind,
            target=entity_id,
            status="partial",
            payload_hash=compute_sha256(canonical_json(updates)),
            provider_id=provider_id,
            event_refs=event_refs,
        )
        self._record_effect(eff)

        return MutationResult(
            success=True,
            status="partial",
            entity_id=entity_id,
            before_version=cur_ver_str,
            after_version=new_ver_str,
            effect=eff,
            applied_fields=tuple(actually_applied),
            omitted_fields=tuple(omitted_fields),
        )

    def commit_effect_with_ambiguous_response(
        self,
        entity_id: str,
        updates: dict[str, Any],
        expected_version: str | None = None,
        operation_id: str | None = None,
        kind: str = "commit_effect",
        provider_id: str | None = None,
        event_refs: tuple[str, ...] = (),
    ) -> MutationResult:
        """Commit state in world, but return an unknown/ambiguous outcome status."""
        result = self.update_entity(
            entity_id=entity_id,
            updates=updates,
            expected_version=expected_version,
            operation_id=operation_id,
            kind=kind,
            provider_id=provider_id,
            event_refs=event_refs,
        )
        if result.success:
            return MutationResult(
                success=True,
                status="unknown",  # Outward response is unknown
                entity_id=result.entity_id,
                before_version=result.before_version,
                after_version=result.after_version,
                effect=result.effect,
                applied_fields=result.applied_fields,
            )
        return result

    def _record_effect(self, effect: EffectRecord) -> None:
        """Append an effect to the ledger and register its operation ID."""
        self._effects.append(effect)
        if effect.operation_id:
            self._operations[effect.operation_id] = effect

    def get_effects(
        self,
        operation_id: str | None = None,
        target: str | None = None,
        status: str | None = None,
    ) -> tuple[EffectRecord, ...]:
        """Query effect records from the ledger."""
        filtered = self._effects
        if operation_id is not None:
            filtered = [e for e in filtered if e.operation_id == operation_id]
        if target is not None:
            filtered = [e for e in filtered if e.target == target]
        if status is not None:
            filtered = [e for e in filtered if e.status == status]
        return tuple(filtered)

    def get_effect_by_operation(self, operation_id: str) -> EffectRecord | None:
        """Lookup an effect by its operation ID."""
        return self._operations.get(operation_id)

    def snapshot(self, captured_at: str | VirtualClock) -> StateSnapshot:
        """Generate a sanitized, deterministic StateSnapshot of the authoritative world."""
        if isinstance(captured_at, VirtualClock):
            clock_str = captured_at.now_iso()
        else:
            clock_str = str(captured_at)

        # Build sanitized entity dictionary
        clean_entities: dict[str, dict[str, Any]] = {}
        total_version_sum = 0
        for eid in sorted(self._entities.keys()):
            ent = self._entities[eid]
            ver_num = self._entity_versions[eid]
            total_version_sum += ver_num
            ent_clean = copy.deepcopy(ent)
            ent_clean["version"] = f"v{ver_num}"
            clean_entities[eid] = ent_clean

        canonical_entities = canonical_json(clean_entities)
        entities_hash = compute_sha256(canonical_entities)
        snapshot_id = f"snap_{entities_hash[:16]}"
        aggregate_version = f"v{total_version_sum}"

        return StateSnapshot(
            snapshot_id=snapshot_id,
            state_version=aggregate_version,
            captured_at=clock_str,
            entities_hash=entities_hash,
        )

    def reset(self) -> None:
        """Reset world back to initial scenario state."""
        self._load_initial_state(self._initial_config)

    def clone(self) -> LocalWorld:
        """Create an independent deep copy of the local world."""
        cloned = LocalWorld()
        cloned._initial_config = copy.deepcopy(self._initial_config)
        cloned._entities = copy.deepcopy(self._entities)
        cloned._entity_versions = copy.deepcopy(self._entity_versions)
        cloned._effects = list(self._effects)
        cloned._operations = dict(self._operations)
        cloned._effect_counter = self._effect_counter
        return cloned
