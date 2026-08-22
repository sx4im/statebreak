# Scenario DSL Reference

StateBreak scenarios are declarative YAML documents conforming to `schemas/scenario.schema.json`. Each scenario defines an initial world, a set of injected faults, an agent task, and authoritative oracles.

## Anatomy of a Scenario

```yaml
schema: "statebreak.scenario/v1"
id: "approval-expiry"
version: 1
seed: 42

clock:
  start: "2026-01-01T09:00:00Z"
  step_seconds: 1

world:
  entities:
    - id: "example-001"
      type: "order"
      status: "pending"
      version: "v1"
      approval:
        id: "appr-001"
        status: "approved"
        expires_at: "2026-01-01T09:00:05Z"

faults:
  - id: "injected-conflict"
    at: "before_commit"
    type: "approval_expired"
    target: "example-001"

agent_task:
  instruction: "Complete order example-001 if the approval is valid."
  tools:
    - "read_state"
    - "check_approval"
    - "commit_effect"
  required_claim: "task_committed"

oracles:
  - id: "authoritative-postcondition"
    type: "claim_requires_state"
    params:
      claim: "task_committed"
      expression: "status == completed"

expectations:
  naive:
    verdict: "fail"
  guarded:
    verdict: "pass"
```

## Bundled Failure Scenarios

| Scenario File | Fault Type | Lifecycle Hook | Simulated Bug |
|---|---|---|---|
| `approval-expiry.yml` | `approval_expired` | `before_commit` | Approval expires between read and commit. |
| `timeout-after-commit.yml` | `timeout_after_commit` | `after_commit_before_response` | Tool returns timeout error after side effect commits. |
| `duplicate-retry.yml` | `duplicate_retry` | `before_retry` | Non-idempotent retry creates duplicate side effects. |
| `wrong-target.yml` | `wrong_target` | `before_commit` | Target ID drifts between agent planning and execution. |
| `partial-write.yml` | `partial_write` | `after_commit_before_response` | Partial database update reported as complete. |
| `handoff-loss.yml` | `handoff_truncation` | `handoff_emit` | Task handoff drops critical safety constraints. |

## Supported Fault Types

- `stale_read`: Replaces fresh observation with a stale version (`params: {stale_version: "v1"}`).
- `approval_expired`: Advances the clock or marks an approval entity as expired before mutation.
- `timeout_after_commit`: Obscures tool return status to `"unknown"` while keeping state committed.
- `duplicate_retry`: Intercepts retries and flags multiple non-idempotent mutations.
- `wrong_target`: Redirects mutation payload to an unintended target entity (`params: {substitute_target: "id-drift"}`).
- `partial_write`: Applies only a subset of updated fields (`params: {applied_fields: [...], omitted_fields: [...]}`).
- `handoff_truncation`: Drops required keys from task handoff payloads.
