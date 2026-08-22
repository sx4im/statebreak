# Writing Custom Adapters

StateBreak evaluates any agent framework through a minimal Python adapter implementing the `AgentAdapter` interface.

## The `AgentAdapter` Protocol

To test your agent, implement `run(context: AdapterContext) -> AdapterResult`:

```python
from statebreak.adapter import AgentAdapter, AdapterContext, AdapterResult

class MyAgentAdapter(AgentAdapter):
    name = "my-agent"
    version = "1.0.0"

    def run(self, context: AdapterContext) -> AdapterResult:
        gateway = context.gateway
        if not gateway:
            return AdapterResult(claims=(), status="error")

        # 1. Query current state via the gateway
        obs = gateway.read("read_state", "example-001")
        current_version = obs.version

        # 2. Check approval status if required
        approval = gateway.check_approval("appr-001")
        if not approval.is_valid:
            return AdapterResult(
                claims=context.claims,
                status="needs_review",
                adapter_name=self.name,
                adapter_version=self.version,
            )

        # 3. Commit mutation with optimistic version locking
        outcome = gateway.act(
            name="commit_effect",
            target="example-001",
            payload={"status": "completed"},
            operation_id="stable_op_id_123",
            expected_version=current_version,
        )

        # 4. Handle ambiguous timeout responses
        if outcome.status == "unknown":
            recheck = gateway.read("read_state", "example-001")
            if recheck.data and recheck.data.get("status") == "completed":
                context.add_claim("reconciled", True)
                context.add_claim("task_committed", True)
                return AdapterResult(claims=context.claims, status="completed")
            return AdapterResult(claims=context.claims, status="needs_review")

        # 5. Record verified claims
        if outcome.status == "committed":
            context.add_claim("task_committed", True)
            return AdapterResult(claims=context.claims, status="completed")

        return AdapterResult(claims=context.claims, status="needs_review")
```

## Running Your Custom Adapter

You can run your adapter directly in Python test suites or through the CLI:

```python
from statebreak.runner import ScenarioRunner

runner = ScenarioRunner()
report = runner.run_scenario(
    "scenarios/approval-expiry.yml",
    adapter_instance=MyAgentAdapter(),
    seed=42,
)

assert report.verdict == "pass"
assert len(report.findings) == 0
```
