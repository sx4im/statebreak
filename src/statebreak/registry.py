"""Single source of truth for StateBreak registries and shared vocabulary.

This module is the ONLY place where fault types, lifecycle points, oracle
types, default node topology, and the agent claim vocabulary are defined.
Validation layers (scenario loading, fault scheduling) and execution layers
(fault scheduler, oracle engine, reference adapters) all import from here,
which guarantees they can never drift apart.

If you add a fault type, lifecycle point, or oracle type, add it here and
implement it in the corresponding engine — then both sides stay in sync.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from statebreak.models import Finding

# ---------------------------------------------------------------------------
# Fault types
# ---------------------------------------------------------------------------
FAULT_STALE_READ = "stale_read"
FAULT_APPROVAL_EXPIRED = "approval_expired"
FAULT_TIMEOUT_AFTER_COMMIT = "timeout_after_commit"
FAULT_DUPLICATE_RETRY = "duplicate_retry"
FAULT_WRONG_TARGET = "wrong_target"
FAULT_PARTIAL_WRITE = "partial_write"
FAULT_HANDOFF_TRUNCATION = "handoff_truncation"

#: Target suffix substituted by the ``wrong_target`` fault when a scenario
#: does not declare ``params.substitute_target`` explicitly.
WRONG_TARGET_SUFFIX = "-drift"

#: Handoff fields dropped by the ``handoff_truncation`` fault when a scenario
#: does not declare ``params.truncated_fields`` explicitly. Mirrors the
#: structural fields of :class:`statebreak.adapter.HandoffPayload`.
DEFAULT_HANDOFF_TRUNCATED_FIELDS = ("constraints", "context", "history")

VALID_FAULT_TYPES = frozenset(
    {
        FAULT_STALE_READ,
        FAULT_APPROVAL_EXPIRED,
        FAULT_TIMEOUT_AFTER_COMMIT,
        FAULT_DUPLICATE_RETRY,
        FAULT_WRONG_TARGET,
        FAULT_PARTIAL_WRITE,
        FAULT_HANDOFF_TRUNCATION,
    }
)

# ---------------------------------------------------------------------------
# Fault lifecycle points
# ---------------------------------------------------------------------------
LIFECYCLE_BEFORE_READ = "before_read"
LIFECYCLE_AFTER_READ = "after_read"
LIFECYCLE_BEFORE_COMMIT = "before_commit"
LIFECYCLE_AFTER_COMMIT_BEFORE_RESPONSE = "after_commit_before_response"
LIFECYCLE_BEFORE_RETRY = "before_retry"
LIFECYCLE_HANDOFF_EMIT = "handoff_emit"

VALID_LIFECYCLE_POINTS = frozenset(
    {
        LIFECYCLE_BEFORE_READ,
        LIFECYCLE_AFTER_READ,
        LIFECYCLE_BEFORE_COMMIT,
        LIFECYCLE_AFTER_COMMIT_BEFORE_RESPONSE,
        LIFECYCLE_BEFORE_RETRY,
        LIFECYCLE_HANDOFF_EMIT,
    }
)

# ---------------------------------------------------------------------------
# Oracle types (must exactly match the branches in OracleEngine)
# ---------------------------------------------------------------------------
ORACLE_STATE_EQUALS = "state_equals"
ORACLE_STATE_NOT_EQUALS = "state_not_equals"
ORACLE_FORBIDDEN_EFFECT = "forbidden_effect"
ORACLE_EFFECT_COUNT = "effect_count"
ORACLE_CLAIM_REQUIRES_STATE = "claim_requires_state"
ORACLE_NO_UNRESOLVED_UNKNOWN_EFFECT = "no_unresolved_unknown_effect"
ORACLE_HANDOFF_CONTAINS = "handoff_contains"
ORACLE_CONVERGENCE_VERIFIED = "convergence_verified"

VALID_ORACLE_TYPES = frozenset(
    {
        ORACLE_STATE_EQUALS,
        ORACLE_STATE_NOT_EQUALS,
        ORACLE_FORBIDDEN_EFFECT,
        ORACLE_EFFECT_COUNT,
        ORACLE_CLAIM_REQUIRES_STATE,
        ORACLE_NO_UNRESOLVED_UNKNOWN_EFFECT,
        ORACLE_HANDOFF_CONTAINS,
        ORACLE_CONVERGENCE_VERIFIED,
    }
)

# ---------------------------------------------------------------------------
# Finding ordering (shared vocabulary for deterministic reports)
# ---------------------------------------------------------------------------
SEVERITY_ORDER: dict[str, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


def sort_findings(findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
    """Order findings deterministically by (blocking desc, severity, finding ID)."""
    return tuple(
        sorted(
            findings,
            key=lambda f: (
                0 if f.blocking else 1,
                SEVERITY_ORDER.get(f.severity, 99),
                f.finding_id,
            ),
        )
    )

# ---------------------------------------------------------------------------
# Default multi-node topology
# ---------------------------------------------------------------------------
DEFAULT_NODE_IDS = ("node-01", "node-02", "node-03")

#: Default single-node identity when a scenario does not declare nodes.
DEFAULT_NODE_ID = "node-01"

#: Default run identifier when none is supplied.
DEFAULT_RUN_ID = "run_default"

#: Recipient wildcard in :meth:`statebreak.coordination.MessageQueue.send`
#: meaning "every registered node except the sender".
BROADCAST_RECIPIENT = "*"

# ---------------------------------------------------------------------------
# Reference adapter names
#
# Canonical keys for ScenarioRunner.resolve_adapter. Aliases are normalized
# by lowercasing, replacing "-" with "_", and stripping a trailing
# "_adapter", so "guarded-adapter" resolves to "guarded".
# ---------------------------------------------------------------------------
ADAPTER_GUARDED = "guarded"
ADAPTER_NAIVE = "naive"
ADAPTER_MULTI_NODE = "multi_node"

VALID_ADAPTER_NAMES = frozenset(
    {ADAPTER_GUARDED, ADAPTER_NAIVE, ADAPTER_MULTI_NODE}
)

# ---------------------------------------------------------------------------
# Agent claim vocabulary (shared contract between adapters and the oracle)
#
# The generic ``claim_requires_state`` oracle inspects these claim names when
# deciding whether an adapter detected or safely recovered from an injected
# fault. Reference adapters emit them; custom adapters should emit the same
# names (or declare explicit recovery claims via scenario parameters).
# ---------------------------------------------------------------------------
CLAIM_TASK_COMMITTED = "task_committed"
CLAIM_TASK_COMPLETED = "task_completed"
CLAIM_STALE_DETECTED = "stale_detected"
CLAIM_RE_APPROVED = "re_approved"
CLAIM_RECONCILED = "reconciled"
CLAIM_TARGET_VERIFIED = "target_verified"

#: Claims treated as evidence of overall task success when a scenario does
#: not name a specific ``required_claim``.
SUCCESS_CLAIM_NAMES = (CLAIM_TASK_COMPLETED, CLAIM_TASK_COMMITTED)

#: Mapping of injected fault type -> claim name that certifies the adapter
#: detected/recovered from that fault class under ``claim_requires_state``.
RECOVERY_CLAIMS_BY_FAULT: dict[str, str] = {
    FAULT_STALE_READ: CLAIM_STALE_DETECTED,
    FAULT_APPROVAL_EXPIRED: CLAIM_RE_APPROVED,
    FAULT_TIMEOUT_AFTER_COMMIT: CLAIM_RECONCILED,
    FAULT_WRONG_TARGET: CLAIM_TARGET_VERIFIED,
}
