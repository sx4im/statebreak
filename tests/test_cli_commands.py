"""Integration tests for all CLI subcommands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from statebreak.cli import main


def test_cli_validate_valid_dir(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["validate", "scenarios"])
    assert code == 0
    out, _err = capsys.readouterr()
    assert "valid scenario" in out


def test_cli_validate_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["validate", "scenarios", "--json"])
    assert code == 0
    out, _err = capsys.readouterr()
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) >= 6
    assert all(item["valid"] is True for item in parsed)


def test_cli_list(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["list", "scenarios"])
    assert code == 0
    out, _err = capsys.readouterr()
    assert "Available Scenarios:" in out
    assert "Available Reference Adapters:" in out


def test_cli_list_json(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["list", "scenarios", "--json"])
    assert code == 0
    out, _err = capsys.readouterr()
    parsed = json.loads(out)
    assert "scenarios" in parsed
    assert "adapters" in parsed


def test_cli_run_guarded_pass(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "scenarios/timeout-after-commit.yml", "--agent", "guarded"])
    assert code == 0
    out, _err = capsys.readouterr()
    assert "StateBreak Run Report: `timeout-after-commit`" in out
    assert "PASS" in out


def test_cli_run_naive_fail(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "scenarios/approval-expiry.yml", "--agent", "naive"])
    assert code == 1
    out, _err = capsys.readouterr()
    assert "StateBreak Run Report: `approval-expiry`" in out
    assert "FAIL" in out


def test_cli_run_output_file(tmp_path: Path) -> None:
    out_file = tmp_path / "report.json"
    code = main([
        "run",
        "scenarios/timeout-after-commit.yml",
        "--agent",
        "guarded",
        "--format",
        "json",
        "-o",
        str(out_file),
    ])
    assert code == 0
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["verdict"] == "pass"


def test_cli_report_conversion(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_file = tmp_path / "report.json"
    main([
        "run",
        "scenarios/duplicate-retry.yml",
        "--agent",
        "guarded",
        "--format",
        "json",
        "-o",
        str(out_file),
    ])

    code = main(["report", str(out_file), "--format", "markdown"])
    assert code == 0
    out, _err = capsys.readouterr()
    assert "StateBreak Run Report: `duplicate-retry`" in out


def test_cli_explain(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["explain", "scenarios/approval-expiry.yml"])
    assert code == 0
    out, _err = capsys.readouterr()
    assert "Scenario: approval-expiry" in out
    assert "Injected Fault Invariants:" in out
    assert "Authoritative Oracle Rules:" in out
