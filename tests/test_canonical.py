"""Unit tests for canonical serialization and SHA-256 hashing."""

import pytest

from statebreak.canonical import canonical_json, compute_scenario_hash, compute_sha256
from statebreak.models import (
    AgentTaskSpec,
    ClockSpec,
    FaultSpec,
    OracleSpec,
    Scenario,
)


def test_canonical_json_sorting() -> None:
    dict_a = {"z": 1, "a": 2, "m": {"b": 3, "a": 4}}
    dict_b = {"a": 2, "m": {"a": 4, "b": 3}, "z": 1}

    serialized_a = canonical_json(dict_a)
    serialized_b = canonical_json(dict_b)

    assert serialized_a == serialized_b
    assert serialized_a == '{"a":2,"m":{"a":4,"b":3},"z":1}'


def test_canonical_json_nested_containers() -> None:
    data = {
        "list": [3, 2, 1],
        "tuple": (1, 2, 3),
        "set": {"c", "b", "a"},
    }
    serialized = canonical_json(data)
    # Lists preserve order, sets are sorted
    assert '"list":[3,2,1]' in serialized
    assert '"tuple":[1,2,3]' in serialized
    assert '"set":["a","b","c"]' in serialized


def test_compute_sha256_types() -> None:
    hash_str = compute_sha256("hello world")
    assert len(hash_str) == 64
    assert isinstance(hash_str, str)

    hash_bytes = compute_sha256(b"hello world")
    assert hash_str == hash_bytes

    hash_dict_1 = compute_sha256({"k1": "v1", "k2": "v2"})
    hash_dict_2 = compute_sha256({"k2": "v2", "k1": "v1"})
    assert hash_dict_1 == hash_dict_2


def test_compute_scenario_hash_determinism() -> None:
    scenario = Scenario(
        schema="statebreak.scenario/v1",
        id="approval-expiry",
        version=1,
        seed=41,
        clock=ClockSpec(start="2026-01-01T09:00:00Z", step_seconds=30),
        world={"approvals": [{"id": "appr-001", "status": "approved"}]},
        faults=(
            FaultSpec(id="f-1", at="before_commit", type="approval_expired", target="appr-001"),
        ),
        agent_task=AgentTaskSpec(
            instruction="Refund refund-1001",
            tools=("read_approval", "commit_refund"),
            required_claim="refund_committed",
        ),
        oracles=(
            OracleSpec(
                id="no-refund",
                type="forbidden_effect",
                effect="refund_committed",
                when="approval.status != approved",
            ),
        ),
    )

    hash_1 = compute_scenario_hash(scenario)
    assert len(hash_1) == 64

    # Adding a scenario_hash to the model shouldn't change the computed hash
    scenario_with_hash = Scenario(
        schema=scenario.schema,
        id=scenario.id,
        version=scenario.version,
        seed=scenario.seed,
        clock=scenario.clock,
        world=scenario.world,
        faults=scenario.faults,
        agent_task=scenario.agent_task,
        oracles=scenario.oracles,
        expectations=scenario.expectations,
        scenario_hash="some-previous-hash",
    )
    hash_2 = compute_scenario_hash(scenario_with_hash)
    assert hash_1 == hash_2


def test_compute_scenario_hash_invalid_type() -> None:
    with pytest.raises(TypeError):
        compute_scenario_hash(12345)
