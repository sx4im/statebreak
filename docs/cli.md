# CLI Reference

StateBreak provides a command-line interface with standard subcommands for validating scenarios, listing available fixtures, running tests, and generating reports.

## Synopsis

```bash
statebreak <command> [options]
```

## Subcommands

### `validate`
Validate one scenario file or a directory of scenarios against the JSON schema and security rules:

```bash
statebreak validate scenarios/
statebreak validate scenarios/approval-expiry.yml --json
```

### `list`
List bundled scenarios and reference adapters:

```bash
statebreak list
statebreak list --json
```

### `run`
Run one or more scenarios against an agent adapter and generate execution reports:

```bash
# Run single scenario with guarded adapter
statebreak run scenarios/approval-expiry.yml --agent guarded

# Run all scenarios with naive adapter and output markdown
statebreak run scenarios/ --agent naive --format markdown -o report.md

# Generate SARIF 2.1.0 output for CI
statebreak run scenarios/ --agent guarded --format sarif -o results.sarif
```

### `report`
Re-render an existing JSON run report as Markdown or SARIF:

```bash
statebreak report report.json --format markdown
statebreak report report.json --format sarif -o results.sarif
```

### `explain`
Explain the injected faults, invariants, and oracle rules for a scenario:

```bash
statebreak explain scenarios/timeout-after-commit.yml
statebreak explain scenarios/timeout-after-commit.yml --json
```

### `version`
Print the StateBreak package version:

```bash
statebreak version
```

## Exit Codes

| Exit Code | Meaning |
|---|---|
| `0` | All scenarios passed or safely requested review (`NEEDS_REVIEW`). |
| `1` | One or more scenarios failed due to blocking finding violations. |
| `2` | Usage error, invalid CLI flags, or schema validation error. |
| `3` | Unexpected internal runtime error. |
