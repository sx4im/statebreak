"""Deterministic virtual clock for StateBreak scenarios."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from statebreak.errors import ConfigurationError, UsageError
from statebreak.models import ClockSpec

# Maximum allowed single advance step: 365 days in seconds
MAX_ADVANCE_SECONDS = 365 * 24 * 3600


def parse_iso_utc(timestamp: str) -> datetime:
    """Parse an ISO 8601 string into a timezone-aware UTC datetime."""
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise ConfigurationError(f"invalid timestamp format: '{timestamp}'")

    ts = timestamp.strip()
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            # Assume UTC if naive
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            # Convert to UTC
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception as exc:
        raise ConfigurationError(
            f"invalid ISO 8601 timestamp string: '{timestamp}': {exc}"
        ) from exc


def format_iso_utc(dt: datetime) -> str:
    """Format a timezone-aware UTC datetime as canonical ISO 8601 UTC string ending in 'Z'."""
    utc_dt = dt.astimezone(timezone.utc)
    s = utc_dt.isoformat()
    if s.endswith("+00:00"):
        return s[:-6] + "Z"
    return s


class VirtualClock:
    """Deterministic virtual clock isolated from host wall-clock time."""

    def __init__(self, start: str | datetime | ClockSpec, step_seconds: int = 30) -> None:
        if isinstance(start, ClockSpec):
            self._start_dt = parse_iso_utc(start.start)
            self._step_seconds = int(start.step_seconds)
        elif isinstance(start, datetime):
            if start.tzinfo is None:
                self._start_dt = start.replace(tzinfo=timezone.utc)
            else:
                self._start_dt = start.astimezone(timezone.utc)
            self._step_seconds = int(step_seconds)
        elif isinstance(start, str):
            self._start_dt = parse_iso_utc(start)
            self._step_seconds = int(step_seconds)
        else:
            raise ConfigurationError(f"unsupported clock start type: {type(start)}")

        if self._step_seconds < 0:
            raise ConfigurationError(
                f"clock step_seconds must be non-negative, got {self._step_seconds}"
            )

        self._current_dt: datetime = self._start_dt

    @property
    def start_time(self) -> datetime:
        """Return the initial start datetime in UTC."""
        return self._start_dt

    @property
    def start_iso(self) -> str:
        """Return initial start ISO timestamp string."""
        return format_iso_utc(self._start_dt)

    @property
    def step_seconds(self) -> int:
        """Return default step advancement in seconds."""
        return self._step_seconds

    def now(self) -> datetime:
        """Return the current virtual UTC datetime."""
        return self._current_dt

    def now_iso(self) -> str:
        """Return the current virtual time formatted as canonical ISO 8601 UTC string."""
        return format_iso_utc(self._current_dt)

    def advance(self, duration: int | float | timedelta) -> str:
        """Advance virtual time by non-negative duration in seconds or timedelta."""
        if isinstance(duration, timedelta):
            delta = duration
            total_seconds = delta.total_seconds()
        elif isinstance(duration, (int, float)):
            total_seconds = float(duration)
            delta = timedelta(seconds=total_seconds)
        else:
            raise UsageError(f"unsupported duration type for clock advance: {type(duration)}")

        if total_seconds < 0:
            raise UsageError(
                f"clock advancement cannot be negative, got {total_seconds} seconds"
            )

        if total_seconds > MAX_ADVANCE_SECONDS:
            raise UsageError(
                f"clock advancement exceeds maximum allowed {MAX_ADVANCE_SECONDS}s: "
                f"{total_seconds}s"
            )

        self._current_dt = self._current_dt + delta
        return self.now_iso()

    def step(self, multiplier: int = 1) -> str:
        """Advance virtual time by `multiplier * step_seconds`."""
        if multiplier < 0:
            raise UsageError(f"step multiplier cannot be negative, got {multiplier}")
        return self.advance(self._step_seconds * multiplier)

    def is_expired(self, expiry: str | datetime) -> bool:
        """Check if an expiry timestamp has been reached or passed (inclusive: now >= expiry)."""
        if isinstance(expiry, str):
            expiry_dt = parse_iso_utc(expiry)
        elif isinstance(expiry, datetime):
            if expiry.tzinfo is None:
                expiry_dt = expiry.replace(tzinfo=timezone.utc)
            else:
                expiry_dt = expiry.astimezone(timezone.utc)
        else:
            raise UsageError(f"unsupported expiry type: {type(expiry)}")

        return self._current_dt >= expiry_dt

    def compare(self, target: str | datetime) -> int:
        """Compare current virtual time to target (-1 if before, 0 if equal, 1 if after)."""
        if isinstance(target, str):
            target_dt = parse_iso_utc(target)
        elif isinstance(target, datetime):
            if target.tzinfo is None:
                target_dt = target.replace(tzinfo=timezone.utc)
            else:
                target_dt = target.astimezone(timezone.utc)
        else:
            raise UsageError(f"unsupported target type: {type(target)}")

        if self._current_dt < target_dt:
            return -1
        if self._current_dt > target_dt:
            return 1
        return 0

    def reset(self) -> None:
        """Reset virtual clock back to its initial start timestamp."""
        self._current_dt = self._start_dt

    def clone(self) -> VirtualClock:
        """Create an independent clone of the virtual clock at its current timestamp."""
        cloned = VirtualClock(start=self._start_dt, step_seconds=self._step_seconds)
        cloned._current_dt = self._current_dt
        return cloned

    def to_dict(self) -> dict[str, Any]:
        """Serialize clock state to dictionary."""
        return {
            "start": self.start_iso,
            "current": self.now_iso(),
            "step_seconds": self._step_seconds,
        }

    def __repr__(self) -> str:
        return f"VirtualClock(current={self.now_iso()!r}, step={self._step_seconds})"

    def __str__(self) -> str:
        return self.now_iso()
