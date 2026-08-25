"""Authoritative oracle evaluation engine for StateBreak failure fixtures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from statebreak.clock import VirtualClock
from statebreak.convergence import ConvergenceTracker
from statebreak.errors import ConfigurationError
from statebreak.faults import FaultEvent
from statebreak.models import AdapterResult, EffectRecord, Finding, OracleSpec, Scenario
from statebreak.registry import (
    DEFAULT_NODE_IDS,
    FAULT_APPROVAL_EXPIRED,
    FAULT_DUPLICATE_RETRY,
    FAULT_HANDOFF_TRUNCATION,
    FAULT_PARTIAL_WRITE,
    FAULT_STALE_READ,
    FAULT_TIMEOUT_AFTER_COMMIT,
    FAULT_WRONG_TARGET,
    ORACLE_CLAIM_REQUIRES_STATE,
    ORACLE_CONVERGENCE_VERIFIED,
    ORACLE_EFFECT_COUNT,
    ORACLE_FORBIDDEN_EFFECT,
    ORACLE_HANDOFF_CONTAINS,
    ORACLE_NO_UNRESOLVED_UNKNOWN_EFFECT,
    ORACLE_STATE_EQUALS,
    ORACLE_STATE_NOT_EQUALS,
    RECOVERY_CLAIMS_BY_FAULT,
    SUCCESS_CLAIM_NAMES,
    sort_findings,
)
from statebreak.world import LocalWorld


@dataclass(frozen=True)
class OracleContext:
    """Execution evidence provided to the oracle engine for authoritative evaluation."""

    scenario_id: str
    run_id: str
    world: LocalWorld
    clock: VirtualClock
    effects: tuple[EffectRecord, ...]
    fault_events: tuple[FaultEvent, ...]
    adapter_result: AdapterResult
    convergence_tracker: ConvergenceTracker | None = None
    handoff_payload: dict[str, Any] | None = None
    node_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class OracleEvaluationResult:
    """Consolidated outcome of oracle evaluation."""

    verdict: str  # pass, fail, needs_review
    findings: tuple[Finding, ...]
    oracle_results: tuple[dict[str, Any], ...] = ()


def _finding(
    finding_id: str,
    *,
    severity: str,
    category: str,
    expected: dict[str, Any],
    observed: dict[str, Any],
    remediation: str,
    event_refs: tuple[str, ...] = (),
    scenario_id: str | None = None,
    fault_refs: tuple[str, ...] = (),
    blocking: bool = True,
) -> Finding:
    """Build a Finding with the shared field ordering used across all oracles."""
    return Finding(
        finding_id=finding_id,
        severity=severity,
        category=category,
        blocking=blocking,
        expected=expected,
        observed=observed,
        remediation=remediation,
        event_refs=event_refs,
        scenario_id=scenario_id,
        fault_refs=fault_refs,
    )


class OracleEngine:
    """Pure authoritative oracle engine evaluating evidence against declared scenario oracles."""

    def evaluate(self, scenario: Scenario, context: OracleContext) -> OracleEvaluationResult:
        """Evaluate all declared oracles against completed execution evidence."""
        findings_list: list[Finding] = []
        oracle_reports: list[dict[str, Any]] = []
        has_blocking_failure = False
        has_needs_review = context.adapter_result.status == "needs_review"

        for idx, oracle in enumerate(scenario.oracles, start=1):
            eval_res = self._evaluate_single_oracle(oracle, scenario, context, idx)
            oracle_reports.append(eval_res["report"])

            for finding in eval_res["findings"]:
                findings_list.append(finding)
                if finding.blocking:
                    has_blocking_failure = True

        if has_blocking_failure:
            verdict = "fail"
        elif has_needs_review:
            verdict = "needs_review"
        else:
            verdict = "pass"

        return OracleEvaluationResult(
            verdict=verdict,
            findings=sort_findings(tuple(findings_list)),
            oracle_results=tuple(oracle_reports),
        )

    def _evaluate_single_oracle(
        self,
        oracle: OracleSpec,
        scenario: Scenario,
        ctx: OracleContext,
        idx: int,
    ) -> dict[str, Any]:
        """Evaluate one declared oracle specification."""
        otype = oracle.type
        raw = oracle.params or {}

        handler = self._ORACLE_DISPATCH.get(otype)
        if handler is not None:
            return handler(self, oracle, scenario, ctx, idx, raw)

        # Unknown oracle type
        fid = f"fnd_{scenario.id}_{oracle.id}_{idx:02d}"
        finding = _finding(
            fid,
            severity="medium",
            category="oracle_unsupported",
            expected={"type": otype},
            observed={"status": "unsupported_oracle_type"},
            remediation="Implement or configure supported oracle type",
            scenario_id=scenario.id,
            blocking=False,
        )
        return {
            "report": {"id": oracle.id, "type": otype, "status": "not_run"},
            "findings": [finding],
        }

    def _eval_claim_requires_state(
        self,
        oracle: OracleSpec,
        scenario: Scenario,
        ctx: OracleContext,
        idx: int,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        claim_name = params.get("claim") or getattr(scenario.agent_task, "required_claim", None)
        expression = str(params.get("expression", "status == completed"))

        # Per-scenario recovery-claim vocabulary: scenarios may override which
        # claim names certify detection/recovery for each fault class, so custom
        # adapters are not forced to emit the reference-adapter vocabulary.
        recovery_claims = RECOVERY_CLAIMS_BY_FAULT
        override = params.get("recovery_claims")
        if isinstance(override, dict) and override:
            recovery_claims = {
                **RECOVERY_CLAIMS_BY_FAULT,
                **{str(k): str(v) for k, v in override.items()},
            }

        # Parse key == val from expression
        target_field = "status"
        target_val = "completed"
        if "==" in expression:
            parts = [p.strip() for p in expression.split("==")]
            target_field = parts[0]
            target_val = parts[1]

        claims = {c.name: c.value for c in ctx.adapter_result.claims}

        def claimed_success() -> bool:
            if claim_name:
                return bool(claims.get(claim_name, False))
            return any(claims.get(name, False) for name in SUCCESS_CLAIM_NAMES)

        def recovered_from(fault_type: str) -> bool:
            default_claim = recovery_claims.get(fault_type, "")
            return bool(default_claim and claims.get(default_claim, False))

        agent_succeeded = claimed_success() or ctx.adapter_result.status == "completed"
        escalated = ctx.adapter_result.status == "needs_review"

        # Authoritative state of all entities in the world vs the expression
        world_entities = ctx.world.list_entities()
        target_match = False
        mismatches: dict[str, Any] = {}
        for ent in world_entities:
            val = ent.get(target_field)
            if str(val) == target_val:
                target_match = True
            else:
                mismatches[ent["id"]] = {target_field: val, "version": ent.get("version")}

        findings: list[Finding] = []
        fid = f"fnd_{scenario.id}_{oracle.id}_{idx:02d}"
        applied_faults = [f for f in ctx.fault_events if f.status == "applied"]
        fault_types = {f.fault_type for f in applied_faults}

        def fault_invariant_finding(
            fault_type: str,
            suffix: str,
            category: str,
            expected: dict[str, Any],
            observed: dict[str, Any],
            remediation: str,
            event_refs: tuple[str, ...],
        ) -> Finding:
            """Build the standard critical finding for an unrecovered fault invariant."""
            return _finding(
                f"{fid}_{suffix}",
                severity="critical",
                category=category,
                expected=expected,
                observed={**observed, "adapter_status": ctx.adapter_result.status},
                remediation=remediation,
                event_refs=event_refs,
                scenario_id=scenario.id,
                fault_refs=tuple(f.fault_id for f in applied_faults
                                 if f.fault_type == fault_type),
            )

        # 1-7. Fault-specific authoritative invariant failures. Each row fires
        # when the fault was applied, the agent proceeded as successful, and no
        # recovery/detection claim certifies safe handling (where applicable).
        invariant_rows: tuple[tuple[str, str, str, dict[str, Any], dict[str, Any], str], ...]
        invariant_rows = (
            (
                FAULT_STALE_READ,
                "stale",
                "stale_observation_used",
                {"fresh_state_verified": True},
                {"stale_read_applied": True},
                "Re-verify state version before committing mutations",
            ),
            (
                FAULT_APPROVAL_EXPIRED,
                "expired",
                "expired_approval_accepted",
                {"valid_approval_required": True},
                {"approval_status": "expired"},
                "Validate approval expiration time before committing actions",
            ),
            (
                FAULT_TIMEOUT_AFTER_COMMIT,
                "unknown",
                "unresolved_unknown_outcome",
                {"reconciled_after_unknown": True},
                {"reconciled": False, "claimed_success": agent_succeeded},
                "Reconcile with authoritative world before declaring completion on timeout",
            ),
            (
                FAULT_WRONG_TARGET,
                "target",
                "wrong_target_mutated",
                {"intended_target_verified": True},
                {"target_drift_ignored": True},
                "Verify returned target identity to prevent silent target drift",
            ),
            (
                FAULT_PARTIAL_WRITE,
                "partial",
                "partial_write_unverified",
                {"complete_write_verified": True},
                {"partial_write_accepted": True},
                "Check for partial write response and trigger remediation",
            ),
            (
                FAULT_HANDOFF_TRUNCATION,
                "handoff",
                "handoff_constraint_loss",
                {"handoff_constraints_preserved": True},
                {"truncated_handoff_accepted": True},
                "Verify required constraints in handoff payloads",
            ),
        )

        for fault_type, suffix, category, expected, observed, remediation in invariant_rows:
            if fault_type not in fault_types or not agent_succeeded or escalated:
                continue
            if fault_type in RECOVERY_CLAIMS_BY_FAULT and recovered_from(fault_type):
                continue

            refs = tuple(
                f.event_id for f in applied_faults if f.fault_type == fault_type
            )
            findings.append(
                fault_invariant_finding(
                    fault_type, suffix, category, expected, observed, remediation, refs
                )
            )

        # Duplicate retry: more than one committed effect under claimed success
        committed_effects = [e for e in ctx.effects if e.status == "committed"]
        if (
            FAULT_DUPLICATE_RETRY in fault_types
            and agent_succeeded
            and len(committed_effects) > 1
        ):
            findings.append(
                fault_invariant_finding(
                    FAULT_DUPLICATE_RETRY,
                    "dup",
                    "duplicate_effects_detected",
                    {"max_committed_effects": 1},
                    {"committed_effects_count": len(committed_effects)},
                    "Use stable operation IDs across retries to maintain idempotency",
                    tuple(e.effect_id for e in committed_effects),
                )
            )

        # Direct state mismatch against the declared expression
        if not target_match and agent_succeeded:
            findings.append(
                _finding(
                    f"{fid}_state",
                    severity="critical",
                    category="authoritative_state_violation",
                    expected={target_field: target_val, "agent_claim": True},
                    observed={
                        "authoritative_state": mismatches,
                        "agent_claim": claimed_success(),
                        "adapter_status": ctx.adapter_result.status,
                    },
                    remediation=(
                        "Verify entity state freshness and commit status before claiming success"
                    ),
                    event_refs=tuple(f.event_id for f in applied_faults),
                    scenario_id=scenario.id,
                    fault_refs=tuple(f.fault_id for f in applied_faults),
                )
            )

        if findings:
            status = "failed"
        elif ctx.adapter_result.status in ("needs_review", "refused"):
            status = "passed"
        else:
            status = "passed" if target_match else "failed"

        return {
            "report": {
                "id": oracle.id,
                "type": oracle.type,
                "status": status,
                "claimed_success": claimed_success(),
                "state_match": target_match,
            },
            "findings": findings,
        }

    def _eval_state_equals(
        self,
        oracle: OracleSpec,
        scenario: Scenario,
        ctx: OracleContext,
        idx: int,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        target = params.get("target")
        if not target:
            raise ConfigurationError(
                f"oracle '{oracle.id}' (state_equals) requires params.target"
            )
        expected_state = params.get("expected", {})
        entity = ctx.world.get_entity(target)

        findings: list[Finding] = []
        fid = f"fnd_{scenario.id}_{oracle.id}_{idx:02d}"

        if entity is None:
            findings.append(
                _finding(
                    fid,
                    severity="high",
                    category="entity_missing",
                    expected={"target": target, **expected_state},
                    observed={"status": "not_found"},
                    remediation=f"Ensure target entity '{target}' exists",
                    scenario_id=scenario.id,
                )
            )
            return {"report": {"id": oracle.id, "status": "failed"}, "findings": findings}

        mismatches = {
            k: {"expected": v, "observed": entity.get(k)}
            for k, v in expected_state.items()
            if entity.get(k) != v
        }

        if mismatches:
            findings.append(
                _finding(
                    fid,
                    severity="high",
                    category="state_mismatch",
                    expected=expected_state,
                    observed=mismatches,
                    remediation="Reconcile entity state with expected postconditions",
                    scenario_id=scenario.id,
                )
            )
            status = "failed"
        else:
            status = "passed"

        return {"report": {"id": oracle.id, "status": status}, "findings": findings}

    def _eval_state_not_equals(
        self,
        oracle: OracleSpec,
        scenario: Scenario,
        ctx: OracleContext,
        idx: int,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        target = params.get("target")
        if not target:
            raise ConfigurationError(
                f"oracle '{oracle.id}' (state_not_equals) requires params.target"
            )
        forbidden_state = params.get("expected", {})
        entity = ctx.world.get_entity(target)

        findings: list[Finding] = []
        fid = f"fnd_{scenario.id}_{oracle.id}_{idx:02d}"

        if entity is None:
            # A missing entity trivially satisfies "state is not X".
            return {"report": {"id": oracle.id, "status": "passed"}, "findings": findings}

        violations = {
            k: {"forbidden": v, "observed": entity.get(k)}
            for k, v in forbidden_state.items()
            if entity.get(k) == v
        }

        if violations:
            findings.append(
                _finding(
                    fid,
                    severity="high",
                    category="state_mismatch",
                    expected={"not": forbidden_state},
                    observed=violations,
                    remediation="Entity must not remain in the forbidden postcondition state",
                    scenario_id=scenario.id,
                )
            )
            status = "failed"
        else:
            status = "passed"

        return {"report": {"id": oracle.id, "status": status}, "findings": findings}

    def _eval_forbidden_effect(
        self,
        oracle: OracleSpec,
        scenario: Scenario,
        ctx: OracleContext,
        idx: int,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        forbidden_target = params.get("target")
        forbidden_kind = params.get("kind")
        findings: list[Finding] = []
        fid = f"fnd_{scenario.id}_{oracle.id}_{idx:02d}"

        violating = [
            eff
            for eff in ctx.effects
            if eff.status == "committed"
            and (forbidden_target is None or eff.target == forbidden_target)
            and (forbidden_kind is None or eff.kind == forbidden_kind)
        ]

        if violating:
            findings.append(
                _finding(
                    fid,
                    severity="critical",
                    category="forbidden_effect_committed",
                    expected={
                        "forbidden_target": forbidden_target,
                        "forbidden_kind": forbidden_kind,
                    },
                    observed={"violating_effects": [eff.effect_id for eff in violating]},
                    remediation="Prevent unauthorized or unintended side effects",
                    event_refs=tuple(eff.effect_id for eff in violating),
                    scenario_id=scenario.id,
                )
            )
            status = "failed"
        else:
            status = "passed"

        return {"report": {"id": oracle.id, "status": status}, "findings": findings}

    def _eval_effect_count(
        self,
        oracle: OracleSpec,
        scenario: Scenario,
        ctx: OracleContext,
        idx: int,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        max_count = int(params.get("max_count", 1))
        target = params.get("target")
        findings: list[Finding] = []
        fid = f"fnd_{scenario.id}_{oracle.id}_{idx:02d}"

        matching = [
            eff
            for eff in ctx.effects
            if eff.status == "committed" and (target is None or eff.target == target)
        ]

        if len(matching) > max_count:
            findings.append(
                _finding(
                    fid,
                    severity="high",
                    category="duplicate_effects_detected",
                    expected={"max_count": max_count, "target": target},
                    observed={
                        "actual_count": len(matching),
                        "effect_ids": [e.effect_id for e in matching],
                    },
                    remediation="Use stable operation IDs to ensure idempotency across retries",
                    event_refs=tuple(e.effect_id for e in matching),
                    scenario_id=scenario.id,
                )
            )
            status = "failed"
        else:
            status = "passed"

        return {
            "report": {"id": oracle.id, "status": status, "count": len(matching)},
            "findings": findings,
        }

    def _eval_no_unresolved_unknown(
        self,
        oracle: OracleSpec,
        scenario: Scenario,
        ctx: OracleContext,
        idx: int,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        findings: list[Finding] = []
        fid = f"fnd_{scenario.id}_{oracle.id}_{idx:02d}"

        unknown_effects = [eff for eff in ctx.effects if eff.status == "unknown"]

        if unknown_effects and ctx.adapter_result.status == "completed":
            findings.append(
                _finding(
                    fid,
                    severity="critical",
                    category="unresolved_unknown_outcome",
                    expected={"resolved_unknown": True, "claim_requires_reconciliation": True},
                    observed={"unknown_effect_ids": [e.effect_id for e in unknown_effects]},
                    remediation="Reconcile with authoritative world before declaring completion",
                    event_refs=tuple(e.effect_id for e in unknown_effects),
                    scenario_id=scenario.id,
                )
            )
            status = "failed"
        else:
            status = "passed"

        return {"report": {"id": oracle.id, "status": status}, "findings": findings}

    def _eval_handoff_contains(
        self,
        oracle: OracleSpec,
        scenario: Scenario,
        ctx: OracleContext,
        idx: int,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        required_constraints = set(params.get("constraints", []))
        findings: list[Finding] = []
        fid = f"fnd_{scenario.id}_{oracle.id}_{idx:02d}"

        if ctx.handoff_payload is None:
            # Check if handoff truncation fault was applied
            handoff_faults = [
                f for f in ctx.fault_events if f.fault_type == FAULT_HANDOFF_TRUNCATION
            ]
            if handoff_faults and ctx.adapter_result.status == "completed":
                findings.append(
                    _finding(
                        fid,
                        severity="high",
                        category="handoff_constraint_loss",
                        expected={"constraints": sorted(required_constraints)},
                        observed={"status": "missing_or_truncated"},
                        remediation=(
                            "Preserve load-bearing handoff constraints during task transitions"
                        ),
                        event_refs=tuple(f.event_id for f in handoff_faults),
                        scenario_id=scenario.id,
                        fault_refs=tuple(f.fault_id for f in handoff_faults),
                    )
                )
                status = "failed"
            else:
                status = "passed"
        else:
            actual_constraints = set(ctx.handoff_payload.get("constraints", []))
            missing = required_constraints - actual_constraints
            if missing and ctx.adapter_result.status == "completed":
                findings.append(
                    _finding(
                        fid,
                        severity="high",
                        category="handoff_constraint_loss",
                        expected={"required_constraints": sorted(required_constraints)},
                        observed={"missing_constraints": sorted(missing)},
                        remediation=(
                            "Ensure all required constraints are forwarded in handoff payloads"
                        ),
                        scenario_id=scenario.id,
                    )
                )
                status = "failed"
            else:
                status = "passed"

        return {"report": {"id": oracle.id, "status": status}, "findings": findings}

    def _eval_convergence_verified(
        self,
        oracle: OracleSpec,
        scenario: Scenario,
        ctx: OracleContext,
        idx: int,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if ctx.convergence_tracker is None:
            return {"report": {"id": oracle.id, "status": "not_run"}, "findings": []}

        non_converged_nodes: list[dict[str, Any]] = []
        node_ids = ctx.node_ids or DEFAULT_NODE_IDS
        for ent in ctx.world.list_entities():
            eid = ent["id"]
            for node_id in node_ids:
                if not ctx.convergence_tracker.is_converged(node_id, eid, ctx.world):
                    non_converged_nodes.append({
                        "node_id": node_id,
                        "entity_id": eid,
                        "authoritative_version": ent.get("version"),
                    })

        findings: list[Finding] = []
        status = "passed"
        if non_converged_nodes and ctx.adapter_result.status == "completed":
            fid = f"fnd_{scenario.id}_{oracle.id}_{idx:02d}"
            findings.append(
                _finding(
                    fid,
                    severity="high",
                    category="state_non_convergence",
                    expected={"all_nodes_converged": True},
                    observed={"non_converged": non_converged_nodes},
                    remediation="Reconcile non-converged nodes with authoritative world snapshot",
                    scenario_id=scenario.id,
                )
            )
            status = "failed"

        return {"report": {"id": oracle.id, "status": status}, "findings": findings}

    # Dispatch table keyed on registry constants: an oracle type missing here
    # is caught by the registry-coverage test; no string literals can drift
    # from the registry.
    _ORACLE_DISPATCH: ClassVar[dict[str, Callable[..., dict[str, Any]]]] = {
        ORACLE_CLAIM_REQUIRES_STATE: _eval_claim_requires_state,
        ORACLE_STATE_EQUALS: _eval_state_equals,
        ORACLE_STATE_NOT_EQUALS: _eval_state_not_equals,
        ORACLE_FORBIDDEN_EFFECT: _eval_forbidden_effect,
        ORACLE_EFFECT_COUNT: _eval_effect_count,
        ORACLE_NO_UNRESOLVED_UNKNOWN_EFFECT: _eval_no_unresolved_unknown,
        ORACLE_HANDOFF_CONTAINS: _eval_handoff_contains,
        ORACLE_CONVERGENCE_VERIFIED: _eval_convergence_verified,
    }
