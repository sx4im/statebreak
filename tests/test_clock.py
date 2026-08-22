"""Unit tests for deterministic VirtualClock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from statebreak.clock import VirtualClock, format_iso_utc, parse_iso_utc
from statebreak.errors import ConfigurationError, UsageError
from statebreak.models import ClockSpec


def test_clock_initialization_iso_string() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z", step_seconds=30)
    assert clock.now_iso() == "2026-01-01T09:00:00Z"
    assert clock.start_iso == "2026-01-01T09:00:00Z"
    assert clock.step_seconds == 30


def test_clock_initialization_from_clock_spec() -> None:
    spec = ClockSpec(start="2026-06-15T12:00:00Z", step_seconds=45)
    clock = VirtualClock(spec)
    assert clock.now_iso() == "2026-06-15T12:00:00Z"
    assert clock.step_seconds == 45


def test_clock_initialization_from_datetime() -> None:
    dt = datetime(2026, 3, 10, 8, 30, 0, tzinfo=timezone.utc)
    clock = VirtualClock(dt, step_seconds=15)
    assert clock.now_iso() == "2026-03-10T08:30:00Z"


def test_clock_timezone_normalization() -> None:
    # Offset +05:00 corresponds to 04:00:00Z
    clock = VirtualClock("2026-01-01T09:00:00+05:00")
    assert clock.now_iso() == "2026-01-01T04:00:00Z"


def test_clock_advancement_seconds_and_timedelta() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z", step_seconds=30)

    # Advance 0 seconds
    res0 = clock.advance(0)
    assert res0 == "2026-01-01T09:00:00Z"
    assert clock.now_iso() == "2026-01-01T09:00:00Z"

    # Advance 60 seconds
    res1 = clock.advance(60)
    assert res1 == "2026-01-01T09:01:00Z"
    assert clock.now_iso() == "2026-01-01T09:01:00Z"

    # Advance timedelta
    res2 = clock.advance(timedelta(minutes=5))
    assert res2 == "2026-01-01T09:06:00Z"
    assert clock.now_iso() == "2026-01-01T09:06:00Z"


def test_clock_step() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z", step_seconds=30)
    res = clock.step()
    assert res == "2026-01-01T09:00:30Z"

    res_mult = clock.step(multiplier=2)
    assert res_mult == "2026-01-01T09:01:30Z"


def test_clock_expiry_boundary() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    expiry = "2026-01-01T09:01:00Z"

    # Before boundary
    assert not clock.is_expired(expiry)

    # Advance to exactly the boundary
    clock.advance(60)
    assert clock.is_expired(expiry)  # Inclusive boundary: now >= expiry

    # Advance past boundary
    clock.advance(1)
    assert clock.is_expired(expiry)


def test_clock_compare() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")
    target = "2026-01-01T09:00:30Z"

    assert clock.compare(target) == -1
    clock.advance(30)
    assert clock.compare(target) == 0
    clock.advance(1)
    assert clock.compare(target) == 1


def test_clock_rejection_negative_and_invalid() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z")

    with pytest.raises(UsageError, match="cannot be negative"):
        clock.advance(-5)

    with pytest.raises(UsageError, match="cannot be negative"):
        clock.step(-1)

    with pytest.raises(UsageError, match="exceeds maximum allowed"):
        clock.advance(400 * 24 * 3600)

    with pytest.raises(ConfigurationError, match="invalid.*timestamp"):
        VirtualClock("not-a-valid-date")

    with pytest.raises(ConfigurationError, match="non-negative"):
        VirtualClock("2026-01-01T09:00:00Z", step_seconds=-10)


def test_clock_clone_and_reset() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z", step_seconds=30)
    clock.advance(120)
    assert clock.now_iso() == "2026-01-01T09:02:00Z"

    cloned = clock.clone()
    assert cloned.now_iso() == "2026-01-01T09:02:00Z"

    # Mutating cloned does not affect original
    cloned.advance(30)
    assert cloned.now_iso() == "2026-01-01T09:02:30Z"
    assert clock.now_iso() == "2026-01-01T09:02:00Z"

    # Reset original
    clock.reset()
    assert clock.now_iso() == "2026-01-01T09:00:00Z"


def test_clock_serialization() -> None:
    clock = VirtualClock("2026-01-01T09:00:00Z", step_seconds=30)
    clock.advance(30)
    d = clock.to_dict()
    assert d == {
        "start": "2026-01-01T09:00:00Z",
        "current": "2026-01-01T09:00:30Z",
        "step_seconds": 30,
    }


def test_parse_and_format_iso_utc() -> None:
    dt = parse_iso_utc("2026-12-31T23:59:59Z")
    assert dt.year == 2026
    assert dt.month == 12
    assert dt.day == 31
    assert dt.tzinfo == timezone.utc
    assert format_iso_utc(dt) == "2026-12-31T23:59:59Z"
