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
# Default multi-node topology
# ---------------------------------------------------------------------------
DEFAULT_NODE_IDS = ("node-01", "node-02", "node-03")

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
