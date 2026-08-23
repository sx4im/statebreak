"""Unit tests for multi-format report rendering (JSON, Markdown, SARIF)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from statebreak.report import render_json, render_markdown, render_sarif
from statebreak.runner import ScenarioRunner


def test_render_json_schema_compliance() -> None:
    runner = ScenarioRunner()
    report = runner.run_scenario("scenarios/timeout-after-commit.yml", adapter="guarded")

    json_str = render_json(report)
    parsed = json.loads(json_str)

    schema_path = Path("schemas/report.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    # Validate against report JSON schema
    jsonschema.validate(instance=parsed, schema=schema)
    assert parsed["verdict"] == "pass"
    assert parsed["scenario_id"] == "timeout-after-commit"


def test_render_markdown_contents() -> None:
    runner = ScenarioRunner()
    report = runner.run_scenario("scenarios/approval-expiry.yml", adapter="naive")

    md_str = render_markdown(report)
    assert "# StateBreak Run Report: `approval-expiry`" in md_str
    assert "## Summary" in md_str
    assert "## Metrics" in md_str
    assert "## Findings" in md_str
    assert "## Execution Timeline" in md_str
    assert "## Diagnostic: Why this failed" in md_str


def test_render_sarif_structure() -> None:
    runner = ScenarioRunner()
    report = runner.run_scenario("scenarios/duplicate-retry.yml", adapter="naive")

    sarif = render_sarif(report)
    assert sarif["version"] == "2.1.0"
    assert "$schema" in sarif
    assert len(sarif["runs"]) == 1
    driver = sarif["runs"][0]["tool"]["driver"]
    assert driver["name"] == "StateBreak"
    assert driver["version"] == "0.1.0"
    assert len(sarif["runs"][0]["results"]) >= 1
