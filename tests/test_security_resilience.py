"""Security and resilience tests covering isolation, offline execution, and bounds."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any
import pytest
from statebreak.errors import ConfigurationError
from statebreak.runner import ScenarioRunner
from statebreak.scenario import load_scenario


def test_offline_execution_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify runner completes without initiating socket connections."""
    socket_calls: list[Any] = []

    def mock_socket(*args: Any, **kwargs: Any) -> Any:
        socket_calls.append(args)
        raise RuntimeError("Network socket creation blocked in offline test")

    monkeypatch.setattr(socket, "socket", mock_socket)
    runner = ScenarioRunner()
    report = runner.run_scenario("scenarios/timeout-after-commit.yml", adapter="guarded")
    assert report.verdict == "pass"
    assert len(socket_calls) == 0


def test_rejection_of_secret_in_scenario(tmp_path: Path) -> None:
    """Scenarios with embedded secret patterns must be rejected at load time."""
    bad_scenario = tmp_path / "bad.yml"
    bad_scenario.write_text(
        """schema: "statebreak.scenario/v1"
id: "bad-scenario"
version: 1
seed: 42
clock:
  start: "2026-01-01T00:00:00Z"
world:
  api_key: "AKIAIOSFODNN7EXAMPLE"
faults: []
agent_task:
  instruction: "test"
  tools: ["read"]
oracles:
  - id: o1
    type: "claim_requires_state"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="suspicious credential pattern"):
        load_scenario(bad_scenario)
