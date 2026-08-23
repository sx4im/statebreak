"""Deterministic fault scheduler and failure injection engine for StateBreak."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any

from statebreak.canonical import canonical_json, compute_sha256
from statebreak.clock import VirtualClock, parse_iso_utc
from statebreak.errors import ConfigurationError
from statebreak.models import FaultSpec
from statebreak.registry import VALID_FAULT_TYPES, VALID_LIFECYCLE_POINTS
from statebreak.world import LocalWorld, MutationResult


@dataclass(frozen=True)
class FaultEvent:
    """Immutable structured record of an injected or skipped fault."""

    event_id: str
    fault_id: str
    fault_type: str
    lifecycle_point: str
    virtual_timestamp: str
    target_entity_id: str | None = None
    operation_id: str | None = None
    before_hash: str | None = None
    after_hash: str | None = None
    trigger_count: int = 1
    seed: int = 42
    status: str = "applied"  # applied, skipped, rejected
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert fault event to dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class FaultDispatchResult:
    """Outcome returned when a lifecycle point is dispatched through the fault scheduler."""

    applied: bool
    fault_id: str | None = None
    event: FaultEvent | None = None
    modified_observation: dict[str, Any] | None = None
    modified_target: str | None = None
    modified_result: MutationResult | None = None
    modified_handoff: dict[str, Any] | None = None


class FaultScheduler:
    """Deterministic, declarative fault scheduler for scenario lifecycle hooks."""

    def __init__(
        self,
        faults: tuple[FaultSpec, ...] | list[FaultSpec] = (),
        seed: int = 42,
    ) -> None:
        # ``seed`` is provenance-only today: no scheduler behavior consumes
        # randomness. Injection is deterministic-by-construction; the seed is
        # hashed into fault events and reported so runs remain attributable
        # and replayable. Reserved for future randomized-but-replayable
        # fault ordering.
        self._seed = int(seed)
        self._faults: list[FaultSpec] = []
        self._fault_map: dict[str, FaultSpec] = {}
        self._trigger_counts: dict[str, int] = {}
        self._events: list[FaultEvent] = []

        seen_ids: set[str] = set()
        for f in faults:
            if not isinstance(f, FaultSpec):
                raise ConfigurationError(f"expected FaultSpec instance, got {type(f)}")
            if f.id in seen_ids:
                raise ConfigurationError(f"duplicate fault ID '{f.id}' declared in scenario")
            seen_ids.add(f.id)

            if f.at not in VALID_LIFECYCLE_POINTS:
                raise ConfigurationError(
                    f"unknown fault lifecycle point '{f.at}' in fault '{f.id}' "
                    f"(valid: {sorted(VALID_LIFECYCLE_POINTS)})"
                )
            if f.type not in VALID_FAULT_TYPES:
                raise ConfigurationError(
                    f"unknown fault type '{f.type}' in fault '{f.id}' "
                    f"(valid: {sorted(VALID_FAULT_TYPES)})"
                )
            if f.repeat is not None and f.repeat < 1:
                raise ConfigurationError(
                    f"fault repeat count must be at least 1, got {f.repeat} in fault '{f.id}'"
                )

            self._faults.append(f)
            self._fault_map[f.id] = f
            self._trigger_counts[f.id] = 0

    @property
    def seed(self) -> int:
        """Return scenario seed."""
        return self._seed

    def get_fault(self, fault_id: str) -> FaultSpec | None:
        """Lookup a declared fault specification by ID."""
        return self._fault_map.get(fault_id)

    def get_events(self) -> tuple[FaultEvent, ...]:
        """Return all logged fault events."""
        return tuple(self._events)

    def reset(self) -> None:
        """Reset trigger counts and clear the event log for deterministic replay."""
        for fid in self._trigger_counts:
            self._trigger_counts[fid] = 0
        self._events.clear()

    def _should_trigger(
        self,
        fault: FaultSpec,
        target: str | None = None,
        world: LocalWorld | None = None,
    ) -> bool:
        """Check if fault matches target and has remaining repeat allowance."""
        # If target specified on fault, it must match or be a known approval entity in world
        if fault.target is not None and target is not None and fault.target != target:
            if (
                fault.type == "approval_expired"
                and world is not None
                and world.has_entity(fault.target)
            ):
                pass
            else:
                return False

        max_repeats = fault.repeat if fault.repeat is not None else 1
        current_count = self._trigger_counts.get(fault.id, 0)
        return current_count < max_repeats

    def _record_event(
        self,
        fault: FaultSpec,
        clock: VirtualClock,
        status: str,
        reason: str,
        target: str | None = None,
        operation_id: str | None = None,
        before_hash: str | None = None,
        after_hash: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> FaultEvent:
        """Generate a deterministic fault event and append to the event log."""
        count = self._trigger_counts.get(fault.id, 0) + (1 if status == "applied" else 0)
        if status == "applied":
            self._trigger_counts[fault.id] = count

        event_id = f"evt_fault_{fault.id}_{max(count, 1)}"
        evt = FaultEvent(
            event_id=event_id,
            fault_id=fault.id,
            fault_type=fault.type,
            lifecycle_point=fault.at,
            virtual_timestamp=clock.now_iso(),
            target_entity_id=target or fault.target,
            operation_id=operation_id,
            before_hash=before_hash,
            after_hash=after_hash,
            trigger_count=max(count, 1),
            seed=self._seed,
            status=status,
            reason=reason,
            details=copy.deepcopy(details) if details else {},
        )
        self._events.append(evt)
        return evt

    def before_read(
        self,
        target: str,
        world: LocalWorld,
        clock: VirtualClock,
    ) -> FaultDispatchResult:
        """Dispatch before_read lifecycle point."""
        for f in self._faults:
            if f.at == "before_read" and self._should_trigger(f, target):
                # No standard before_read mutator exists yet: record the event
                # honestly as skipped so fault timelines don't claim mutations
                # that never happened. Repeat allowance is NOT consumed.
                evt = self._record_event(
                    fault=f,
                    clock=clock,
                    status="skipped",
                    reason=(f"{f.type} declared at before_read has no implemented "
                            f"mutator; no effect applied"),
                    target=target,
                )
                return FaultDispatchResult(applied=False, fault_id=f.id, event=evt)
        return FaultDispatchResult(applied=False)

    def after_read(
        self,
        target: str,
        observation: dict[str, Any],
        world: LocalWorld,
        clock: VirtualClock,
    ) -> FaultDispatchResult:
        """Dispatch after_read lifecycle point (e.g. stale_read injection)."""
        for f in self._faults:
            if (
                f.at == "after_read"
                and f.type == "stale_read"
                and self._should_trigger(f, target)
            ):
                before_json = canonical_json(observation)
                before_hash = compute_sha256(before_json)

                stale_obs = copy.deepcopy(observation)
                # Override with stale attributes from params or degrade version to v1
                stale_ver = f.params.get("stale_version", "v1")
                stale_obs["version"] = stale_ver
                if "stale_status" in f.params:
                    stale_obs["status"] = f.params["stale_status"]
                elif "status" in stale_obs and stale_obs["status"] == "completed":
                    stale_obs["status"] = "pending"

                after_json = canonical_json(stale_obs)
                after_hash = compute_sha256(after_json)

                evt = self._record_event(
                    fault=f,
                    clock=clock,
                    status="applied",
                    reason=f"injected stale read observation with version '{stale_ver}'",
                    target=target,
                    before_hash=before_hash,
                    after_hash=after_hash,
                    details={
                        "stale_version": stale_ver,
                        "original_version": observation.get("version"),
                    },
                )
                return FaultDispatchResult(
                    applied=True,
                    fault_id=f.id,
                    event=evt,
                    modified_observation=stale_obs,
                )
        return FaultDispatchResult(applied=False)

    def before_commit(
        self,
        target: str,
        operation_id: str,
        payload: dict[str, Any],
        world: LocalWorld,
        clock: VirtualClock,
    ) -> FaultDispatchResult:
        """Dispatch before_commit lifecycle point (e.g. approval_expired, wrong_target)."""
        for f in self._faults:
            if f.at == "before_commit" and self._should_trigger(f, target, world=world):
                if f.type == "approval_expired":
                    # Expire the approval entity in world or advance clock
                    appr_target = f.target or target
                    appr_ent = world.get_entity(appr_target)
                    before_hash = compute_sha256(canonical_json(appr_ent)) if appr_ent else None

                    if appr_ent and "expires_at" in appr_ent:
                        # Advance the clock deterministically PAST the declared
                        # expiry (+1 second) so the approval is genuinely expired
                        # regardless of scenario timing or step size.
                        try:
                            expiry_dt = parse_iso_utc(str(appr_ent["expires_at"]))
                            delta_seconds = (expiry_dt - clock.now()).total_seconds() + 1.0
                            if delta_seconds > 0:
                                clock.advance(delta_seconds)
                        except ConfigurationError:
                            # Unparseable expiry timestamp: fall back to
                            # forcing the status to expired.
                            world.update_entity(appr_target, {"status": "expired"})
                    elif appr_ent:
                        # No declared expiry: simulate expiry by status update
                        world.update_entity(appr_target, {"status": "expired"})

                    after_ent = world.get_entity(appr_target)
                    after_hash = compute_sha256(canonical_json(after_ent)) if after_ent else None

                    evt = self._record_event(
                        fault=f,
                        clock=clock,
                        status="applied",
                        reason=f"expired approval target '{appr_target}' before commit",
                        target=appr_target,
                        operation_id=operation_id,
                        before_hash=before_hash,
                        after_hash=after_hash,
                    )
                    return FaultDispatchResult(applied=True, fault_id=f.id, event=evt)

                elif f.type == "wrong_target":
                    substitute = f.params.get("substitute_target", f"{target}-drift")
                    evt = self._record_event(
                        fault=f,
                        clock=clock,
                        status="applied",
                        reason=f"substituted target '{target}' with wrong target '{substitute}'",
                        target=target,
                        operation_id=operation_id,
                        details={"intended_target": target, "substitute_target": substitute},
                    )
                    return FaultDispatchResult(
                        applied=True,
                        fault_id=f.id,
                        event=evt,
                        modified_target=substitute,
                    )

        return FaultDispatchResult(applied=False)

    def after_commit_before_response(
        self,
        target: str,
        operation_id: str,
        result: MutationResult,
        world: LocalWorld,
        clock: VirtualClock,
    ) -> FaultDispatchResult:
        """Dispatch after_commit_before_response (e.g. timeout_after_commit, partial_write)."""
        for f in self._faults:
            if f.at == "after_commit_before_response" and self._should_trigger(f, target):
                if f.type == "timeout_after_commit":
                    # Obscure return status to unknown while authoritative state remains committed
                    modified_res = MutationResult(
                        success=True,
                        status="unknown",
                        entity_id=result.entity_id,
                        before_version=result.before_version,
                        after_version=result.after_version,
                        effect=result.effect,
                        applied_fields=result.applied_fields,
                    )
                    evt = self._record_event(
                        fault=f,
                        clock=clock,
                        status="applied",
                        reason="obscured commit response into unknown timeout status",
                        target=target,
                        operation_id=operation_id,
                        details={
                            "authoritative_status": result.status,
                            "outward_status": "unknown",
                        },
                    )
                    return FaultDispatchResult(
                        applied=True,
                        fault_id=f.id,
                        event=evt,
                        modified_result=modified_res,
                    )

                elif f.type == "partial_write":
                    applied_fields = tuple(f.params.get("applied_fields", ["status"]))
                    omitted_fields = tuple(f.params.get("omitted_fields", ["extra_field"]))
                    modified_res = MutationResult(
                        success=True,
                        status="partial",
                        entity_id=result.entity_id,
                        before_version=result.before_version,
                        after_version=result.after_version,
                        effect=result.effect,
                        applied_fields=applied_fields,
                        omitted_fields=omitted_fields,
                    )
                    evt = self._record_event(
                        fault=f,
                        clock=clock,
                        status="applied",
                        reason="injected partial write outcome",
                        target=target,
                        operation_id=operation_id,
                        details={
                            "applied_fields": list(applied_fields),
                            "omitted_fields": list(omitted_fields),
                        },
                    )
                    return FaultDispatchResult(
                        applied=True,
                        fault_id=f.id,
                        event=evt,
                        modified_result=modified_res,
                    )

        return FaultDispatchResult(applied=False)

    def before_retry(
        self,
        target: str,
        operation_id: str,
        world: LocalWorld,
        clock: VirtualClock,
    ) -> FaultDispatchResult:
        """Dispatch before_retry lifecycle point (e.g. duplicate_retry tracking)."""
        for f in self._faults:
            if (
                f.at == "before_retry"
                and f.type == "duplicate_retry"
                and self._should_trigger(f, target)
            ):
                evt = self._record_event(
                    fault=f,
                    clock=clock,
                    status="applied",
                    reason=f"injected duplicate retry check for operation '{operation_id}'",
                    target=target,
                    operation_id=operation_id,
                )
                return FaultDispatchResult(applied=True, fault_id=f.id, event=evt)
        return FaultDispatchResult(applied=False)

    def handoff_emit(
        self,
        payload: dict[str, Any],
        clock: VirtualClock,
    ) -> FaultDispatchResult:
        """Dispatch handoff_emit lifecycle point (e.g. handoff_truncation)."""
        for f in self._faults:
            if (
                f.at == "handoff_emit"
                and f.type == "handoff_truncation"
                and self._should_trigger(f)
            ):
                before_json = canonical_json(payload)
                before_hash = compute_sha256(before_json)

                truncated_payload = copy.deepcopy(payload)
                truncated_keys = f.params.get(
                    "truncated_fields",
                    ["constraints", "context", "history"],
                )
                omitted: list[str] = []
                for k in truncated_keys:
                    if k in truncated_payload:
                        del truncated_payload[k]
                        omitted.append(k)

                # If none of the default keys matched, drop the last key in payload if any
                if not omitted and truncated_payload:
                    last_key = list(truncated_payload.keys())[-1]
                    del truncated_payload[last_key]
                    omitted.append(last_key)

                after_json = canonical_json(truncated_payload)
                after_hash = compute_sha256(after_json)

                evt = self._record_event(
                    fault=f,
                    clock=clock,
                    status="applied",
                    reason=f"truncated handoff fields: {omitted}",
                    before_hash=before_hash,
                    after_hash=after_hash,
                    details={"omitted_fields": omitted},
                )
                return FaultDispatchResult(
                    applied=True,
                    fault_id=f.id,
                    event=evt,
                    modified_handoff=truncated_payload,
                )

        return FaultDispatchResult(applied=False)
