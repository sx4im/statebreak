"""Tests for package importability and metadata."""

import re

import statebreak
from statebreak.errors import ConfigurationError, InternalError, StateBreakError, UsageError


def test_import_statebreak_package() -> None:
    """Verify package imports cleanly and exports version."""
    assert hasattr(statebreak, "__version__")
    assert isinstance(statebreak.__version__, str)
    assert len(statebreak.__version__) > 0


def test_version_format() -> None:
    """Verify semantic version string format (e.g. 0.1.0)."""
    assert re.match(r"^\d+\.\d+\.\d+", statebreak.__version__) is not None


def test_error_hierarchy() -> None:
    """Verify exception hierarchy and exit code mappings."""
    base_err = StateBreakError("base", exit_code=1)
    assert base_err.exit_code == 1
    assert base_err.message == "base"
    assert issubclass(StateBreakError, Exception)

    usage_err = UsageError("usage")
    assert usage_err.exit_code == 2
    assert isinstance(usage_err, StateBreakError)

    cfg_err = ConfigurationError("cfg")
    assert cfg_err.exit_code == 2
    assert isinstance(cfg_err, StateBreakError)

    internal_err = InternalError("internal")
    assert internal_err.exit_code == 3
    assert isinstance(internal_err, StateBreakError)


def test_public_exports_in_all() -> None:
    """Verify all symbols declared in __all__ are accessible on module root."""
    assert hasattr(statebreak, "__all__")
    for name in statebreak.__all__:
        assert hasattr(statebreak, name), f"missing exported symbol {name}"
