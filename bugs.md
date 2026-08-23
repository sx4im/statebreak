# StateBreak — Known Issues & Bugs

Generated from codebase review on 2026-08-21. Ordered by priority.

---

## P0 — Critical

### 1. No version control
- **Where:** repository root (no `.git/`)
- **What:** The project is not a git repo, yet `IMPLEMENTATION_STATUS.md` / `STATEBREAK_CHAT_HANDOFF.md` declare it "READY FOR PUBLIC BETA". No history, no rollback, no remote backup.
- **Fix:** `git init && git add -A && git commit`, then push to a remote. Check `.gitignore` covers `__pycache__/`, `.mypy_cache/`, `.pytest_cache/`, `dist/` first.
- **Also:** README badges point to `github.com/statebreak/statebreak` which doesn't exist yet — create the repo or remove badges until published.

### 2. Oracle type registry inconsistency
- **Where:** `src/statebreak/scenario.py:46-54` vs `src/statebreak/oracle.py:108`
- **What:** Two problems in opposite directions:
  - `VALID_ORACLE_TYPES` allows `state_not_equals`, but `OracleEngine` never implements it → silently degrades to an "unsupported oracle" medium finding instead of evaluating.
  - `convergence_verified` **is** implemented (`oracle.py:108`) but missing from `VALID_ORACLE_TYPES` → any scenario using it is rejected at load time. Unreachable feature.
- **Fix:** Single shared source of truth for oracle types (move the set to one module, import in both). Implement `state_not_equals` or drop it from the schema/registry. Add `convergence_verified` to the registry + a scenario that uses it.

---

## P1 — High

### 3. "Generic" oracle is coupled to reference adapters' claim vocabulary
- **Where:** `src/statebreak/oracle.py:130-390` (`_eval_claim_requires_state`)
- **What:** Hardcodes 7 fault-specific checks keyed on magic claim names emitted only by the bundled adapters: `stale_detected` (:181), `re_approved` (:209), `reconciled` (:234), `target_verified` (:284). A user's custom adapter that doesn't emit this exact vocabulary gets different (possibly wrong) verdicts even when behavior is correct.
- **Fix:** Extract claim names into named constants shared by adapters + oracle; better, make fault-specific expectations declarative (scenario params) rather than hardcoded branches in the engine.

### 4. Duplicated fault type/lifecycle registries (two sources of truth)
- **Where:** `src/statebreak/scenario.py:27-44` and `src/statebreak/faults.py:15-32`
- **What:** `VALID_FAULT_TYPES` and lifecycle points are defined independently in both modules. They currently agree; nothing guarantees they keep agreeing. Adding a new fault requires editing both plus `oracle.py`.
- **Fix:** Define once (e.g. in `faults.py`), import into `scenario.py`.

### 5. Hardcoded node IDs
- **Where:** `src/statebreak/runner.py:82` and `src/statebreak/oracle.py:657`
- **What:** `"node-01"`, `"node-02"`, `"node-03"` baked into the `MessageQueue` init and the convergence oracle check. Scenarios can't declare their own node topology; oracle silently assumes exactly 3 nodes.
- **Fix:** Derive node list from scenario config (e.g. `world.nodes` or a top-level field) and pass it through `OracleContext`.

### 6. Reference adapters hardcode entity IDs and reconciliation logic
- **Where:** `src/statebreak/adapters/guarded.py:33,49` (targets `example-001` / fallback `order-001`), `guarded.py:124` (reconcile = `status == "completed"`)
- **What:** Guarded adapter only works against scenarios using those exact IDs/status values. Fine as demo, misleading as reference implementation ("10-line adapter" claim in README oversells generality).
- **Fix:** Read target from `context` (scenario instruction/task params) or document clearly that bundled adapters are demo-only.

---

## P2 — Medium

### 7. Phantom docs referenced by project files
- **Where:** `IMPLEMENTATION_STATUS.md`, `TODO.md`
- **What:** Referenced docs don't exist:
  - `docs/resilience-matrix.md`
  - `docs/production-readiness.md`
  - `docs/decision-log.md`
  - `docs/test-evidence.md`
  - `docs/phase-4-world-contract.md`
  - `docs/phase-5-fault-contract.md`
  - `docs/phase-6-adapter-coordination-contract.md`
  - `docs/phase-7-reference-adapters.md`
  - `docs/phase-8-oracle-contract.md`
  - `docs/phase-9-runner-contract.md`
- **Also:** `IMPLEMENTATION_STATUS.md` claims flake8 passes; repo is configured for ruff (`pyproject.toml:50`). Status file describes itself as mid-"Phase 9" while claiming completion — stale either way.
- **Fix:** Delete the references or write the docs. Reconcile lint-tool claims.

### 8. Code duplication in world mutation paths
- **Where:** `src/statebreak/world.py:225-346` (`update_entity`) vs `world.py:348-450` (`partial_update_entity`)
- **What:** Near-identical not-found rejection block (:237-258 vs :361-383) and version-conflict rejection block (:284-309 vs :388-413). ~60 duplicated lines; fixes must be applied twice.
- **Fix:** Extract `_reject(effect_id, ...)` helper or unify into one internal mutate function with a `partial` flag.

### 9. Seed parameter is decorative
- **Where:** `FaultScheduler.__init__` seed param, `runner.py:68` (`effective_seed`), CLI `--seed`
- **What:** Seed is stored, hashed into events, and reported — but no code consumes randomness. Deterministic-by-construction is fine, but the API implies stochastic behavior that doesn't exist.
- **Fix:** Either use it (e.g. randomized-but-replayable fault ordering) or document that seed is currently reserved/provenance-only.

### 10. `before_read` hook fires faults that do nothing
- **Where:** `src/statebreak/faults.py:192-210`
- **What:** Any fault declared `at: before_read` is recorded as `status="applied"` with reason "applied ... before read" but has no mutator — comment admits "no standard before_read mutator in MVP". Reports will show applied faults that changed nothing, confusing users reading fault timelines.
- **Fix:** Either implement a real before_read effect or record these as `skipped`/`rejected` with reason "not implemented".

---

## P3 — Low

### 11. Coverage tooling declared but unused
- **Where:** `pyproject.toml:42` (`pytest-cov>=5` in dev deps)
- **What:** No coverage config, no gate, no Makefile target reporting it despite 153 tests.
- **Fix:** Add `--cov=statebreak --cov-report=term-missing` to a Makefile `coverage` target; optionally add a fail-under threshold.

### 12. Build artifacts / caches present in tree
- **Where:** `src/statebreak/__pycache__/`, `src/statebreak/adapters/__pycache__/`, `tests/__pycache__/`, `examples/__pycache__/custom-adapter.cpython-312.pyc`, `.mypy_cache/`, `.pytest_cache/`
- **What:** Will pollute the first commit once git is initialized if `.gitignore` misses them (verify before committing).
- **Fix:** `find . -name __pycache__ -type d -exec rm -rf {} +` and confirm `.gitignore` entries.

### 13. `handle_report` reconstructs report without validation
- **Where:** `src/statebreak/cli.py:260-296`
- **What:** Rebuilds `RunReport` from arbitrary JSON with permissive defaults (`verdict` defaults to `"error"`, empty strings accepted). Malformed reports render instead of failing loudly. Minor, but contradicts the strict-validation posture everywhere else.
- **Fix:** Validate against `schemas/report.schema.json` (it exists but is only used elsewhere) before re-rendering.

### 14. Approval-expiry fault advances clock by one step regardless of gap
- **Where:** `src/statebreak/faults.py:277-279`
- **What:** If approval has `expires_at`, expiry is simulated by advancing the clock one `step_seconds` (default 30s). If `expires_at` is more than one step in the future, approval may *still be valid* after the fault fires — the injected failure can silently no-op depending on scenario timing.
- **Fix:** Advance clock directly past `expires_at + 1s` (or force status to expired unconditionally).

### 15. README example drift risk
- **Where:** `README.md:84-117` (adapter example)
- **What:** Example imports `from statebreak.adapter import AgentAdapter, AdapterContext, AdapterResult` — correct today, but the "assert len(report.findings) == 0" line depends on the claim-vocabulary coupling from issue #3. If #3 changes semantics, README example breaks silently.
- **Fix:** Add an integration test that executes the exact README example.

---

## Verification commands

```bash
python3 -m pytest          # should stay green: 153 passed
python3 -m mypy src        # should stay clean under --strict
python3 -m statebreak validate scenarios/
```

Reproduce issue #2 (unreachable convergence oracle):
```bash
cat > /tmp/cv.yml <<EOF
schema: statebreak.scenario/v1
id: cv-test
version: 1
seed: 1
clock: {start: 2026-01-01T09:00:00Z}
world: {entities: [{id: e1, status: pending}]}
faults: []
agent_task: {instruction: x, tools: [read_state]}
oracles: [{id: o1, type: convergence_verified}]
EOF
python3 -m statebreak validate /tmp/cv.yml   # rejected, though oracle.py implements it
```

---

## Squash log — 2026-08-23 (second bug-smash pass)

Status of the original list:
- #1: git initialized + baseline commit (83b864e); README badge commented out pending publication. **Fixed.**
- #2, #3, #4, #5, #6, #8, #9, #10, #11, #13, #14, #15: **Fixed** in the first bug-smash pass (`registry.py`, `tests/test_bug_smash.py`).
- #7: phantom doc references removed from `STATEBREAK_CHAT_HANDOFF.md`; flake8 claim corrected to ruff in `IMPLEMENTATION_STATUS.md`. **Fixed.**
- #12: caches excluded from baseline commit. **Fixed.**

New bugs found and squashed in this pass:

### 16. Oracle dispatch was stringly-typed despite registry.py
- **Where:** `oracle.py` `_evaluate_single_oracle` (if/elif on mixed literals/constants), `_eval_claim_requires_state` (hardcoded `"stale_read"` etc.)
- **Fix:** `_ORACLE_DISPATCH` class-level dispatch map keyed on `ORACLE_*` constants; fault branches now use `FAULT_*` constants. New test asserts every `VALID_ORACLE_TYPES` entry has a handler.

### 17. Scenario `expectations` were decorative — and wrong
- **Where:** `runner.py` never compared actual verdicts to `scenario.expectations`.
- **Fix:** `ScenarioRunner._check_expectations` adds a blocking `expectation_mismatch` finding on mismatch. Enforcement immediately exposed that `approval-expiry`, `partial-write`, and `wrong-target` declared `guarded: pass` while the guarded adapter safely recovers with `needs_review` — fixtures, `test_scenario_loader`, and the README table corrected to match reality.

### 18. Demo scenarios never exercised `params.target_entity`
- **Where:** all 6 `scenarios/*.yml` relied on the adapters' hardcoded `example-001` fallback.
- **Fix:** every scenario now declares `agent_task: {params: {target_entity: example-001}}`.

### 19. Dead field `FaultDispatchResult.modified_payload`
- **Fix:** removed (never set anywhere).

---

## Squash log — 2026-08-23 (third pass: audit remediation)

Independent audit found the second pass left the tree red in three places; all fixed and verified:

### 20. Lint gate red: 65 ruff errors (docs claimed green)
- **Where:** repo-wide (`I001`, `UP017`, `SIM102`, `RUF059`, `BLE001`, `F401`, …)
- **What:** `make check` did not include lint, so the gate drifted silently while bugs.md claimed "should stay clean".
- **Fix:** all 65 findings resolved (43 auto-fixed, 22 by hand: blind excepts narrowed to `ConfigurationError` with a loud stderr warning in `handle_list`, SIM102 collapses, `datetime.UTC` aliases, unused unpacks). `Makefile` now runs `check: lint typecheck test`.

### 21. URL containment bypass in scenario security scan
- **Where:** `src/statebreak/scenario.py` `_check_security_invariants`
- **What:** URL policy only fired when a string *started with* `http(s)://`; an instruction like `"see https://evil.example.net/x"` passed validation while a bare URL value was rejected. Secrets used `.search()` (mid-string) but URLs used `.startswith()`.
- **Fix:** `URL_PATTERN` regex now finds http(s) URLs anywhere inside string values; each match is host-checked against the allowlist. Regression tests added: embedded external URL rejected, embedded allowlisted (`*.test`, `*.example.test`) URL accepted.

### 22. Doc example drift in docs/custom-adapters.md
- **What:** example used nonexistent `obs.version` (actual: `state_version`) and a `adapter_instance=` kwarg the runner does not accept (actual: `adapter=`); final assert was stricter than the documented guarantee.
- **Fix:** examples corrected; new "Idempotency Semantics" section documents that a replayed `operation_id` with a different payload returns the original effect without applying the new payload.

**Verification after pass 3:** `make check` exit 0 (ruff clean, mypy strict clean, 173 passed), wheel + sdist build, wheel installs and runs in a fresh venv, CLI smoke exits 0/0/1, adversarial probes 7/7.
