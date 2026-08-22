"""Tests for CLI foundation, version, help, and error handling."""

from __future__ import annotations

import socket
from unittest import mock

import pytest
from statebreak import __version__
from statebreak.cli import main


def test_cli_version_command(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify 'version' subcommand prints package name and version and exits with 0."""
    exit_code = main(["version"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip() == f"statebreak {__version__}"
    assert captured.err == ""


def test_cli_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify '--version' and '-v' print version and exit with 0."""
    exit_code = main(["--version"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.strip() == f"statebreak {__version__}"

    exit_code_short = main(["-v"])
    captured_short = capsys.readouterr()
    assert exit_code_short == 0
    assert captured_short.out.strip() == f"statebreak {__version__}"


def test_cli_help_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify '--help' prints help screen and exits with 0."""
    exit_code = main(["--help"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "StateBreak:" in captured.out
    assert "validate" in captured.out
    assert "list" in captured.out
    assert "run" in captured.out
    assert "report" in captured.out
    assert "explain" in captured.out
    assert "version" in captured.out


def test_cli_empty_args_shows_help(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify running with no arguments displays help cleanly with exit code 0."""
    exit_code = main([])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "usage: statebreak" in captured.out


def test_cli_unknown_command(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify unknown command exits with code 2 and a concise error message."""
    exit_code = main(["nonexistent-command"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "error:" in captured.err
    assert "nonexistent-command" in captured.err
    assert "Traceback" not in captured.err


def test_cli_unknown_option(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify unknown option exits with code 2 and concise error message."""
    exit_code = main(["--invalid-flag"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "error:" in captured.err
    assert "unrecognized arguments: --invalid-flag" in captured.err
    assert "Traceback" not in captured.err


def test_cli_missing_required_argument(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify commands missing required arguments exit with code 2."""
    exit_code = main(["validate"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "error:" in captured.err
    assert "Traceback" not in captured.err


def test_cli_no_traceback_on_normal_error(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify normal errors do not print tracebacks by default."""
    exit_code = main(["run"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Traceback (most recent call last)" not in captured.err


def test_cli_debug_mode_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify --debug outputs tracebacks on unexpected errors."""
    with mock.patch("statebreak.cli.handle_validate", side_effect=RuntimeError("internal crash")):
        exit_code = main(["validate", "scenarios", "--debug"])
        captured = capsys.readouterr()
        assert exit_code == 3
        assert "Traceback (most recent call last)" in captured.err
        assert "internal crash" in captured.err


def test_cli_network_isolation(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify CLI never attempts network socket creation."""
    def guarded_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network socket attempted during offline CLI run!")

    with mock.patch.object(socket, "socket", side_effect=guarded_socket):
        exit_code = main(["version"])
        assert exit_code == 0
        help_code = main(["--help"])
        assert help_code == 0


def test_cli_no_secret_leaks(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify CLI does not leak environment secrets into output or stderr."""
    secret_token = "SB_TEST_SUPER_SECRET_TOKEN_987654321"
    monkeypatch.setenv("STATEBREAK_API_KEY", secret_token)
    monkeypatch.setenv("SECRET_TOKEN", secret_token)

    main(["version"])
    main(["--help"])
    main(["invalid-cmd"])

    captured = capsys.readouterr()
    assert secret_token not in captured.out
    assert secret_token not in captured.err
