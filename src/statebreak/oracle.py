"""Authoritative oracle evaluation engine for StateBreak failure fixtures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from statebreak.clock import VirtualClock
from statebreak.convergence import ConvergenceTracker
from statebreak.faults import FaultEvent
from statebreak.models import AdapterResult, EffectRecord, Finding, OracleSpec, Scenario
from statebreak.registry import (
    CLAIM_RE_APPROVED,
    CLAIM_RECONCILED,
    CLAIM_STALE_DETECTED,
    CLAIM_TARGET_VERIFIED,
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

    verdict: str  # pass, fail, needs_review, error, inconclusive, not_run
    findings: tuple[Finding, ...]
    oracle_results: tuple[dict[str, Any], ...] = ()


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

        # Sort findings deterministically by (blocking desc, severity, finding_id)
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        sorted_findings = tuple(
            sorted(
                findings_list,
                key=lambda f: (
                    0 if f.blocking else 1,
                    severity_order.get(f.severity, 99),
                    f.finding_id,
                ),
            )
        )

        # Determine overall scenario verdict
        if has_blocking_failure:
            verdict = "fail"
        elif has_needs_review:
            verdict = "needs_review"
        else:
            verdict = "pass"

        return OracleEvaluationResult(
            verdict=verdict,
            findings=sorted_findings,
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
        finding = Finding(
            finding_id=fid,
            severity="medium",
            category="oracle_unsupported",
            blocking=False,
            expected={"type": otype},
            observed={"status": "unsupported_oracle_type"},
            remediation="Implement or configure supported oracle type",
            event_refs=(),
            scenario_id=scenario.id,
            fault_refs=(),
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

        # Check if agent made positive claim
        claims = {c.name: c.value for c in ctx.adapter_result.claims}
        if claim_name:
            agent_claimed_success = claims.get(claim_name, False)
        else:
            agent_claimed_success = any(claims.get(name, False) for name in SUCCESS_CLAIM_NAMES)

        # Check authoritative state of all entities in world
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

        # Evaluate fault-specific authoritative invariant failures
        applied_faults = [f for f in ctx.fault_events if f.status == "applied"]
        fault_types = {f.fault_type for f in applied_faults}

        # 1. Stale read violation
        if FAULT_STALE_READ in fault_types and (
            agent_claimed_success or ctx.adapter_result.status == "completed"
        ) and (
            not claims.get(recovery_claims.get("stale_read", CLAIM_STALE_DETECTED), False)
            and ctx.adapter_result.status != "needs_review"
        ):
            stale_evs = [f for f in applied_faults if f.fault_type == FAULT_STALE_READ]
            findings.append(
                Finding(
                    finding_id=f"{fid}_stale",
                    severity="critical",
                    category="stale_observation_used",
                    blocking=True,
                    expected={"fresh_state_verified": True},
                    observed={
                        "stale_read_applied": True,
                        "adapter_status": ctx.adapter_result.status,
                    },
                    remediation="Re-verify state version before committing mutations",
                    event_refs=tuple(f.event_id for f in stale_evs),
                    scenario_id=scenario.id,
                    fault_refs=tuple(f.fault_id for f in stale_evs),
                )
            )

        # 2. Expired approval violation
        if FAULT_APPROVAL_EXPIRED in fault_types and (
            agent_claimed_success or ctx.adapter_result.status == "completed"
        ) and (
            ctx.adapter_result.status != "needs_review"
            and not claims.get(recovery_claims.get("approval_expired", CLAIM_RE_APPROVED), False)
        ):
            appr_evs = [f for f in applied_faults if f.fault_type == FAULT_APPROVAL_EXPIRED]
            findings.append(
                Finding(
                    finding_id=f"{fid}_expired",
                    severity="critical",
                    category="expired_approval_accepted",
                    blocking=True,
                    expected={"valid_approval_required": True},
                    observed={
                        "approval_status": "expired",
                        "adapter_status": ctx.adapter_result.status,
                    },
                    remediation="Validate approval expiration time before committing actions",
                    event_refs=tuple(f.event_id for f in appr_evs),
                    scenario_id=scenario.id,
                    fault_refs=tuple(f.fault_id for f in appr_evs),
                )
            )

        # 3. Timeout after commit unreconciled unknown outcome
        if FAULT_TIMEOUT_AFTER_COMMIT in fault_types and (
            agent_claimed_success or ctx.adapter_result.status == "completed"
        ) and not claims.get(recovery_claims.get("timeout_after_commit", CLAIM_RECONCILED), False):
            to_evs = [f for f in applied_faults if f.fault_type == FAULT_TIMEOUT_AFTER_COMMIT]
            findings.append(
                Finding(
                    finding_id=f"{fid}_unknown",
                    severity="critical",
                    category="unresolved_unknown_outcome",
                    blocking=True,
                    expected={"reconciled_after_unknown": True},
                    observed={"reconciled": False, "claimed_success": agent_claimed_success},
                    remediation=(
                        "Reconcile with authoritative world before declaring "
                        "completion on timeout"
                    ),
                    event_refs=tuple(f.event_id for f in to_evs),
                    scenario_id=scenario.id,
                    fault_refs=tuple(f.fault_id for f in to_evs),
                )
            )

        # 4. Duplicate retry / multiple committed effects
        if FAULT_DUPLICATE_RETRY in fault_types and (
            agent_claimed_success or ctx.adapter_result.status == "completed"
        ):
            committed_effects = [e for e in ctx.effects if e.status == "committed"]
            if len(committed_effects) > 1:
                dup_evs = [f for f in applied_faults if f.fault_type == FAULT_DUPLICATE_RETRY]
                findings.append(
                    Finding(
                        finding_id=f"{fid}_dup",
                        severity="critical",
                        category="duplicate_effects_detected",
                        blocking=True,
                        expected={"max_committed_effects": 1},
                        observed={"committed_effects_count": len(committed_effects)},
                        remediation=(
                            "Use stable operation IDs across retries to maintain idempotency"
                        ),
                        event_refs=tuple(e.effect_id for e in committed_effects),
                        scenario_id=scenario.id,
                        fault_refs=tuple(f.fault_id for f in dup_evs),
                    )
                )

        # 5. Wrong target mutation
        if FAULT_WRONG_TARGET in fault_types and (
            agent_claimed_success or ctx.adapter_result.status == "completed"
        ) and (
            not claims.get(recovery_claims.get("wrong_target", CLAIM_TARGET_VERIFIED), False)
            and ctx.adapter_result.status != "needs_review"
        ):
            wt_evs = [f for f in applied_faults if f.fault_type == FAULT_WRONG_TARGET]
            findings.append(
                Finding(
                    finding_id=f"{fid}_target",
                    severity="critical",
                    category="wrong_target_mutated",
                    blocking=True,
                    expected={"intended_target_verified": True},
                    observed={
                        "target_drift_ignored": True,
                        "adapter_status": ctx.adapter_result.status,
                    },
                    remediation=(
                        "Verify returned target identity to prevent silent target drift"
                    ),
                    event_refs=tuple(f.event_id for f in wt_evs),
                    scenario_id=scenario.id,
                    fault_refs=tuple(f.fault_id for f in wt_evs),
                )
            )

        # 6. Partial write
        if FAULT_PARTIAL_WRITE in fault_types and (
            agent_claimed_success or ctx.adapter_result.status == "completed"
        ) and ctx.adapter_result.status != "needs_review":
            pw_evs = [f for f in applied_faults if f.fault_type == FAULT_PARTIAL_WRITE]
            findings.append(
                Finding(
                    finding_id=f"{fid}_partial",
                    severity="critical",
                    category="partial_write_unverified",
                    blocking=True,
                    expected={"complete_write_verified": True},
                    observed={"partial_write_accepted": True},
                    remediation="Check for partial write response and trigger remediation",
                    event_refs=tuple(f.event_id for f in pw_evs),
                    scenario_id=scenario.id,
                    fault_refs=tuple(f.fault_id for f in pw_evs),
                )
            )

        # 7. Handoff truncation
        if FAULT_HANDOFF_TRUNCATION in fault_types and (
            agent_claimed_success or ctx.adapter_result.status == "completed"
        ) and ctx.adapter_result.status != "needs_review":
            ht_evs = [f for f in applied_faults if f.fault_type == FAULT_HANDOFF_TRUNCATION]
            findings.append(
                Finding(
                    finding_id=f"{fid}_handoff",
                    severity="critical",
                    category="handoff_constraint_loss",
                    blocking=True,
                    expected={"handoff_constraints_preserved": True},
                    observed={"truncated_handoff_accepted": True},
                    remediation="Verify required constraints in handoff payloads",
                    event_refs=tuple(f.event_id for f in ht_evs),
                    scenario_id=scenario.id,
                    fault_refs=tuple(f.fault_id for f in ht_evs),
                )
            )

        # 8. Direct state mismatch
        if not target_match and (
            agent_claimed_success or ctx.adapter_result.status == "completed"
        ):
            findings.append(
                Finding(
                    finding_id=f"{fid}_state",
                    severity="critical",
                    category="authoritative_state_violation",
                    blocking=True,
                    expected={target_field: target_val, "agent_claim": True},
                    observed={
                        "authoritative_state": mismatches,
                        "agent_claim": agent_claimed_success,
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
                "claimed_success": agent_claimed_success,
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
        target = params.get("target") or "order-001"
        expected_state = params.get("expected", {})
        entity = ctx.world.get_entity(target)

        findings: list[Finding] = []
        fid = f"fnd_{scenario.id}_{oracle.id}_{idx:02d}"

        if entity is None:
            findings.append(
                Finding(
                    finding_id=fid,
                    severity="high",
                    category="entity_missing",
                    blocking=True,
                    expected={"target": target, **expected_state},
                    observed={"status": "not_found"},
                    remediation=f"Ensure target entity '{target}' exists",
                    event_refs=(),
                    scenario_id=scenario.id,
                    fault_refs=(),
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
                Finding(
                    finding_id=fid,
                    severity="high",
                    category="state_mismatch",
                    blocking=True,
                    expected=expected_state,
                    observed=mismatches,
                    remediation="Reconcile entity state with expected postconditions",
                    event_refs=(),
                    scenario_id=scenario.id,
                    fault_refs=(),
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
        target = params.get("target") or "order-001"
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
                Finding(
                    finding_id=fid,
                    severity="high",
                    category="state_mismatch",
                    blocking=True,
                    expected={"not": forbidden_state},
                    observed=violations,
                    remediation="Entity must not remain in the forbidden postcondition state",
                    event_refs=(),
                    scenario_id=scenario.id,
                    fault_refs=(),
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
                Finding(
                    finding_id=fid,
                    severity="critical",
                    category="forbidden_effect_committed",
                    blocking=True,
                    expected={
                        "forbidden_target": forbidden_target,
                        "forbidden_kind": forbidden_kind,
                    },
                    observed={"violating_effects": [eff.effect_id for eff in violating]},
                    remediation="Prevent unauthorized or unintended side effects",
                    event_refs=tuple(eff.effect_id for eff in violating),
                    scenario_id=scenario.id,
                    fault_refs=(),
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
                Finding(
                    finding_id=fid,
                    severity="high",
                    category="duplicate_effects_detected",
                    blocking=True,
                    expected={"max_count": max_count, "target": target},
                    observed={
                        "actual_count": len(matching),
                        "effect_ids": [e.effect_id for e in matching],
                    },
                    remediation="Use stable operation IDs to ensure idempotency across retries",
                    event_refs=tuple(e.effect_id for e in matching),
                    scenario_id=scenario.id,
                    fault_refs=(),
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
                Finding(
                    finding_id=fid,
                    severity="critical",
                    category="unresolved_unknown_outcome",
                    blocking=True,
                    expected={"resolved_unknown": True, "claim_requires_reconciliation": True},
                    observed={"unknown_effect_ids": [e.effect_id for e in unknown_effects]},
                    remediation="Reconcile with authoritative world before declaring completion",
                    event_refs=tuple(e.effect_id for e in unknown_effects),
                    scenario_id=scenario.id,
                    fault_refs=(),
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
            handoff_faults = [f for f in ctx.fault_events if f.fault_type == FAULT_HANDOFF_TRUNCATION]
            if handoff_faults and ctx.adapter_result.status == "completed":
                findings.append(
                    Finding(
                        finding_id=fid,
                        severity="high",
                        category="handoff_constraint_loss",
                        blocking=True,
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
                    Finding(
                        finding_id=fid,
                        severity="high",
                        category="handoff_constraint_loss",
                        blocking=True,
                        expected={"required_constraints": sorted(required_constraints)},
                        observed={"missing_constraints": sorted(missing)},
                        remediation=(
                            "Ensure all required constraints are forwarded in handoff payloads"
                        ),
                        event_refs=(),
                        scenario_id=scenario.id,
                        fault_refs=(),
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
        findings: list[Finding] = []
        fid = f"fnd_{scenario.id}_{oracle.id}_{idx:02d}"

        if ctx.convergence_tracker is None:
            return {"report": {"id": oracle.id, "status": "not_run"}, "findings": []}

        non_converged_nodes: list[dict[str, Any]] = []
        node_ids = ctx.node_ids or DEFAULT_NODE_IDS
        for ent in ctx.world.list_entities():
            eid = ent["id"]
            # Check nodes
            for node_id in node_ids:
                if not ctx.convergence_tracker.is_converged(node_id, eid, ctx.world):
                    non_converged_nodes.append({
                        "node_id": node_id,
                        "entity_id": eid,
                        "authoritative_version": ent.get("version"),
                    })

        if non_converged_nodes and ctx.adapter_result.status == "completed":
            findings.append(
                Finding(
                    finding_id=fid,
                    severity="high",
                    category="state_non_convergence",
                    blocking=True,
                    expected={"all_nodes_converged": True},
                    observed={"non_converged": non_converged_nodes},
                remediation="Reconcile non-converged nodes with authoritative world snapshot",
                event_refs=(),
                scenario_id=scenario.id,
                fault_refs=(),
            )
        )
            status = "failed"
        else:
            status = "passed"

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
