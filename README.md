# StateBreak

> Deterministic failure laboratory for AI agents: inject stale state, expired approvals, timeouts, duplicate retries, wrong targets, and broken handoffs—then verify authoritative outcomes locally.

<!-- CI badge: add once a CI workflow exists -->
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Repository:** [github.com/sx4im/statebreak](https://github.com/sx4im/statebreak)

StateBreak tests how autonomous AI agents behave when stateful systems fail around them. It injects deterministic state drift, expired approvals, ambiguous commit timeouts, duplicate retries, and corrupted task handoffs without live cloud costs, real side effects, or model API calls.

---

## 30-Second Quickstart

Install StateBreak with zero external dependencies:

```bash
# 1. Install
pip install statebreak

# 2. Validate bundled failure fixtures
statebreak validate scenarios/

# 3. Run failure suite against a naive agent (fails on injected races)
statebreak run scenarios/approval-expiry.yml --agent naive

# 4. Run against a guarded agent (reconciles and recovers safely)
statebreak run scenarios/approval-expiry.yml --agent guarded
```

---

## The Problem: False Success in Agent Workflows

In agentic workflows, an agent tool call often returns `200 OK` while the underlying state is broken:
- **Expired Approvals:** An approval was valid when the agent planned its action, but expired before the commit executed.
- **Commit Timeouts:** A database mutation committed, but the network timed out before the response returned. The agent retries and creates duplicate side effects.
- **Target Drift:** An entity was updated or deleted by another process between the agent's read and write steps.
- **Interrupted Handoffs:** A multi-agent task handoff dropped a safety constraint, causing the downstream agent to act blindly.

StateBreak isolates these failure modes into versioned, reproducible test fixtures evaluated against an **authoritative local world**, not LLM self-reports.

---

## How It Works

```text
  ┌─────────────────┐       ┌─────────────────┐       ┌──────────────────┐
  │ Scenario Fixture│──────►│ Fault Scheduler ├──────►│  Tool Gateway    │
  │ (Initial State) │       │ (Injects Races) │       │ (Preserves Unk.) │
  └─────────────────┘       └─────────────────┘       └────────┬─────────┘
                                                               │
                                                               ▼
  ┌─────────────────┐       ┌─────────────────┐       ┌──────────────────┐
  │   Run Report    │◄──────┤ Authoritative   │◄──────┤  Agent Adapter   │
  │ (SARIF / JSON)  │       │  Oracle Engine  │       │ (Under Test)     │
  └─────────────────┘       └─────────────────┘       └──────────────────┘
```

1. **Local World & Virtual Clock:** Initializes an in-memory transactional state store and deterministic clock.
2. **Fault Scheduler:** Intercepts tool calls at allowlisted lifecycle hooks (`before_read`, `before_commit`, `after_commit`, `before_retry`, `handoff_emit`).
3. **Agent Adapter:** Your agent runs against the gateway using its standard tool interface.
4. **Authoritative Oracle:** Evaluates the ground-truth database state and side-effect ledger against scenario invariants, catching unverified completion claims.

---

## Bundled Failure Scenarios

| Scenario | Injected Failure | Naive Agent Behavior | Guarded Agent Behavior |
|---|---|---|---|
| [`approval-expiry`](scenarios/approval-expiry.yml) | Approval expires before commit | Executes write anyway (`FAIL`) | Detects expiry, requests re-approval (`NEEDS_REVIEW` = safe recovery) |
| [`timeout-after-commit`](scenarios/timeout-after-commit.yml) | Timeout after write commits | Assumes failure or blind success (`FAIL`) | Re-reads state to reconcile (`PASS`) |
| [`duplicate-retry`](scenarios/duplicate-retry.yml) | Ambiguous network error | Retries with new ID, double charges (`FAIL`) | Uses stable idempotency keys (`PASS`) |
| [`wrong-target`](scenarios/wrong-target.yml) | Target ID drifted | Modifies wrong entity (`FAIL`) | Revalidates version lock and target (`NEEDS_REVIEW` = safe recovery) |
| [`partial-write`](scenarios/partial-write.yml) | First write succeeds, second fails | Reports complete success (`FAIL`) | Flags partial state for review (`NEEDS_REVIEW` = safe recovery) |
| [`handoff-loss`](scenarios/handoff-loss.yml) | Handoff drops constraints | Executes without safety rules (`FAIL`) | Pauses and requests missing context (`PASS`) |

---

## Testing Your Own Agent

Wrap your agent in a 10-line Python adapter:

```python
from statebreak.adapter import AgentAdapter, AdapterContext, AdapterResult
from statebreak.runner import ScenarioRunner

class MyAgentAdapter(AgentAdapter):
    name = "my-agent"
    version = "0.1.0"

    def run(self, context: AdapterContext) -> AdapterResult:
        # Query state through the gateway
        obs = context.gateway.read("read_state", "example-001")
        
        # Execute mutation with optimistic version locking
        outcome = context.gateway.act(
            name="commit_effect",
            target="example-001",
            payload={"status": "completed"},
            operation_id="op_stable_123",
            expected_version=obs.state_version,
        )

        if outcome.status == "committed":
            context.add_claim("task_committed", True)
            return AdapterResult(
                claims=context.claims,
                status="completed",
                adapter_name=self.name,
                adapter_version="0.1.0",
            )

        return AdapterResult(
            claims=context.claims,
            status="needs_review",
            adapter_name=self.name,
            adapter_version="0.1.0",
        )

# Run directly in pytest
report = ScenarioRunner().run_scenario(
    "scenarios/approval-expiry.yml",
    adapter=MyAgentAdapter(),
)
assert report.verdict in ("pass", "needs_review")
assert len(report.findings) == 0
```

---

## Scope Boundaries

| StateBreak Does | StateBreak Does Not Do |
|---|---|
| Inject reproducible state conflicts locally | Intercept live production network traffic |
| Run agents against synthetic tool gateways | Host, schedule, or orchestrate agent runtimes |
| Compare authoritative state against claims | Replace application authorization policies |
| Generate SARIF 2.1.0 and JSON reports for CI | Depend on external model APIs or cloud infrastructure |

---

## Documentation

- [Architecture & Execution Model](docs/architecture.md)
- [Scenario DSL & Fault Types](docs/scenario-dsl.md)
- [Writing Custom Adapters](docs/custom-adapters.md)
- [Oracles and Metrics Reference](docs/oracles-and-metrics.md)
- [CLI Commands & Options](docs/cli.md)
- [GitHub Actions & SARIF CI Integration](docs/ci-github-actions.md)

---

## Contributing

Contributions of new failure scenarios, framework adapters (LangChain, AutoGen, CrewAI), and oracle rules are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License. See [LICENSE](LICENSE) for details.
