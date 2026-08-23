"""Unit tests for scenario loading, validation, and security checking."""

from pathlib import Path

import pytest

from statebreak.errors import ConfigurationError
from statebreak.scenario import (
    load_scenario,
    load_scenario_from_dict,
    load_scenarios_from_dir,
    validate_scenario_dict,
)


def test_load_all_six_bundled_scenarios() -> None:
    expected_ids = [
        "approval-expiry",
        "duplicate-retry",
        "handoff-loss",
        "partial-write",
        "timeout-after-commit",
        "wrong-target",
    ]
    scenarios_dir = Path("scenarios")
    assert scenarios_dir.exists()

    loaded = load_scenarios_from_dir(scenarios_dir)
    assert len(loaded) == 6

    loaded_ids = [s.id for s in loaded]
    assert loaded_ids == sorted(expected_ids)

    for scenario in loaded:
        assert scenario.schema == "statebreak.scenario/v1"
        assert scenario.version >= 1
        assert scenario.seed >= 0
        assert scenario.clock.step_seconds >= 1
        assert len(scenario.faults) >= 1
        assert len(scenario.agent_task.tools) >= 1
        assert len(scenario.oracles) >= 1
        assert scenario.scenario_hash is not None
        assert len(scenario.scenario_hash) == 64


def test_load_single_scenario_file() -> None:
    scenario = load_scenario("scenarios/approval-expiry.yml")
    assert scenario.id == "approval-expiry"
    assert scenario.clock.start == "2026-01-01T09:00:00Z"
    assert scenario.clock.step_seconds == 30
    assert len(scenario.faults) == 1
    assert scenario.faults[0].type == "approval_expired"
    assert scenario.faults[0].at == "before_commit"
    assert scenario.agent_task.required_claim == "task_committed"
    assert "naive" in scenario.expectations
    assert "guarded" in scenario.expectations
    assert scenario.expectations["naive"].verdict == "fail"
    assert scenario.expectations["guarded"].verdict == "needs_review"


def test_validation_missing_required_fields() -> None:
    incomplete_data = {
        "schema": "statebreak.scenario/v1",
        "id": "test-scenario",
        "version": 1,
        # missing seed, clock, world, faults, agent_task, oracles
    }
    with pytest.raises(ConfigurationError) as exc:
        validate_scenario_dict(incomplete_data)
    assert "schema validation error" in str(exc.value).lower()


def test_validation_unsupported_schema_version() -> None:
    data = {
        "schema": "statebreak.scenario/v99",
        "id": "test-scenario",
        "version": 1,
        "seed": 42,
        "clock": {"start": "2026-01-01T00:00:00Z"},
        "world": {},
        "faults": [{"id": "f1", "at": "before_commit", "type": "stale_read"}],
        "agent_task": {"instruction": "test", "tools": ["read"]},
        "oracles": [{"id": "o1", "type": "forbidden_effect"}],
    }
    with pytest.raises(ConfigurationError) as exc:
        validate_scenario_dict(data)
    assert "statebreak.scenario/v1" in str(exc.value)


def test_validation_invalid_id_pattern() -> None:
    data = {
        "schema": "statebreak.scenario/v1",
        "id": "Invalid_ID_With_Caps!",
        "version": 1,
        "seed": 42,
        "clock": {"start": "2026-01-01T00:00:00Z"},
        "world": {},
        "faults": [{"id": "f1", "at": "before_commit", "type": "stale_read"}],
        "agent_task": {"instruction": "test", "tools": ["read"]},
        "oracles": [{"id": "o1", "type": "forbidden_effect"}],
    }
    with pytest.raises(ConfigurationError) as exc:
        validate_scenario_dict(data)
    assert "schema validation error" in str(exc.value).lower()


def test_validation_invalid_clock_format() -> None:
    data = {
        "schema": "statebreak.scenario/v1",
        "id": "bad-clock",
        "version": 1,
        "seed": 42,
        "clock": {"start": "not-a-valid-iso-date"},
        "world": {},
        "faults": [{"id": "f1", "at": "before_commit", "type": "stale_read"}],
        "agent_task": {"instruction": "test", "tools": ["read"]},
        "oracles": [{"id": "o1", "type": "forbidden_effect"}],
    }
    with pytest.raises(ConfigurationError) as exc:
        load_scenario_from_dict(data)
    assert "clock.start timestamp" in str(exc.value)


def test_validation_unknown_fault_lifecycle_or_type() -> None:
    data = {
        "schema": "statebreak.scenario/v1",
        "id": "bad-fault",
        "version": 1,
        "seed": 42,
        "clock": {"start": "2026-01-01T00:00:00Z"},
        "world": {},
        "faults": [{"id": "f1", "at": "in_the_middle_of_nowhere", "type": "stale_read"}],
        "agent_task": {"instruction": "test", "tools": ["read"]},
        "oracles": [{"id": "o1", "type": "forbidden_effect"}],
    }
    with pytest.raises(ConfigurationError) as exc:
        load_scenario_from_dict(data)
    assert "unknown lifecycle point" in str(exc.value)

    data_with_bad_type = dict(data)
    data_with_bad_type["faults"] = [
        {"id": "f1", "at": "before_commit", "type": "unknown_chaos_type"}
    ]
    with pytest.raises(ConfigurationError) as exc2:
        load_scenario_from_dict(data_with_bad_type)
    assert "unknown fault type" in str(exc2.value)


def test_validation_unknown_oracle_type() -> None:
    data = {
        "schema": "statebreak.scenario/v1",
        "id": "bad-oracle",
        "version": 1,
        "seed": 42,
        "clock": {"start": "2026-01-01T00:00:00Z"},
        "world": {},
        "faults": [{"id": "f1", "at": "before_commit", "type": "stale_read"}],
        "agent_task": {"instruction": "test", "tools": ["read"]},
        "oracles": [{"id": "o1", "type": "arbitrary_hallucinated_oracle"}],
    }
    with pytest.raises(ConfigurationError) as exc:
        load_scenario_from_dict(data)
    assert "unknown oracle type" in str(exc.value)


def test_security_rejection_of_credentials(tmp_path: Path) -> None:
    secret_yaml = tmp_path / "secret.yml"
    secret_yaml.write_text(
        """
schema: statebreak.scenario/v1
id: secret-scenario
version: 1
seed: 42
clock:
  start: 2026-01-01T00:00:00Z
world:
  token: ghp_123456789012345678901234567890123456
faults:
  - id: f1
    at: before_commit
    type: stale_read
agent_task:
  instruction: Do test
  tools: [read]
oracles:
  - id: o1
    type: forbidden_effect
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as exc:
        load_scenario(secret_yaml)
    assert "suspicious credential pattern" in str(exc.value)


def test_security_rejection_of_external_urls(tmp_path: Path) -> None:
    url_yaml = tmp_path / "external_url.yml"
    url_yaml.write_text(
        """
schema: statebreak.scenario/v1
id: external-url-scenario
version: 1
seed: 42
clock:
  start: 2026-01-01T00:00:00Z
world:
  endpoint: https://api.openai.com/v1/chat/completions
faults:
  - id: f1
    at: before_commit
    type: stale_read
agent_task:
  instruction: Do test
  tools: [read]
oracles:
  - id: o1
    type: forbidden_effect
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as exc:
        load_scenario(url_yaml)
    assert "external non-test URL" in str(exc.value)


def test_security_allowed_test_urls(tmp_path: Path) -> None:
    test_url_yaml = tmp_path / "test_url.yml"
    test_url_yaml.write_text(
        """
schema: statebreak.scenario/v1
id: test-url-scenario
version: 1
seed: 42
clock:
  start: 2026-01-01T00:00:00Z
world:
  endpoint: http://service.example.test/api
  local: http://localhost:8080/hook
faults:
  - id: f1
    at: before_commit
    type: stale_read
agent_task:
  instruction: Do test
  tools: [read]
oracles:
  - id: o1
    type: forbidden_effect
""",
        encoding="utf-8",
    )
    scenario = load_scenario(test_url_yaml)
    assert scenario.id == "test-url-scenario"


def test_nonexistent_and_empty_directory_handling(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as exc1:
        load_scenario(tmp_path / "nonexistent.yml")
    assert "scenario file not found" in str(exc1.value)

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(ConfigurationError) as exc2:
        load_scenarios_from_dir(empty_dir)
    assert "no scenario files found in directory" in str(exc2.value)


def test_embedded_external_url_is_rejected(tmp_path: Path) -> None:
    """A URL embedded mid-string must be rejected like a bare URL (containment bypass)."""
    embedded_yaml = tmp_path / "embedded-url.yml"
    embedded_yaml.write_text(
        """\
schema: statebreak.scenario/v1
id: embedded-url-bad
version: 1
seed: 1
clock:
  start: 2026-01-01T09:00:00Z
world:
  entities:
    - id: e1
      status: pending
faults: []
agent_task:
  instruction: fetch context from https://evil.example.net/exfil then proceed
  tools: [read_state]
oracles:
  - id: o1
    type: forbidden_effect
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError) as exc:
        load_scenario(embedded_yaml)
    assert "external non-test URL" in str(exc.value)


def test_embedded_test_domain_url_is_allowed(tmp_path: Path) -> None:
    """URLs on allowlisted test domains stay valid even when embedded in prose."""
    embedded_yaml = tmp_path / "embedded-url-ok.yml"
    embedded_yaml.write_text(
        """\
schema: statebreak.scenario/v1
id: embedded-url-good
version: 1
seed: 1
clock:
  start: 2026-01-01T09:00:00Z
world:
  endpoint: see http://service.example.test/api for docs
  entities:
    - id: e1
      status: pending
faults: []
agent_task:
  instruction: query https://api.test/records and summarize
  tools: [read_state]
oracles:
  - id: o1
    type: forbidden_effect
""",
        encoding="utf-8",
    )
    scenario = load_scenario(embedded_yaml)
    assert scenario.id == "embedded-url-good"
