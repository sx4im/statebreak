"""Terminal color helpers for StateBreak's human-readable output.

Coloring is cosmetic and conservative: output degrades to plain text when
stdout is not a TTY or ``NO_COLOR`` is set (https://no-color.org/). Set
``FORCE_COLOR=1`` to force codes on, e.g. for pager or CI previews. JSON and
SARIF renderers never emit escape codes.
"""

from __future__ import annotations

import os
import sys

_RESET = "\x1b[0m"


def _colors_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _paint(code: str, text: str) -> str:
    if not _colors_enabled():
        return text
    return f"\x1b[{code}m{text}{_RESET}"


def bold(text: str) -> str:
    return _paint("1", text)


def dim(text: str) -> str:
    return _paint("2", text)


def green(text: str) -> str:
    return _paint("32", text)


def yellow(text: str) -> str:
    return _paint("33", text)


def red(text: str) -> str:
    return _paint("31", text)


def cyan(text: str) -> str:
    return _paint("36", text)


_VERDICT_COLORS = {
    "pass": green,
    "fail": red,
    "error": red,
    "needs_review": yellow,
}

_SEVERITY_COLORS = {
    "critical": red,
    "high": red,
    "medium": yellow,
    "low": cyan,
    "info": dim,
}

#: Outcome statuses share one palette across timeline rows: committed effects
#: read as healthy, applied faults and unknown outcomes read as dangerous.
_STATUS_COLORS = {
    "committed": green,
    "applied": red,
    "unknown": yellow,
    "partial": yellow,
}


def verdict_text(verdict: str) -> str:
    """Return a verdict string colored for terminal display."""
    painter = _VERDICT_COLORS.get(verdict.lower())
    return painter(verdict.upper()) if painter else verdict.upper()


def severity_text(severity: str) -> str:
    """Return a finding severity string colored for terminal display."""
    painter = _SEVERITY_COLORS.get(severity.lower())
    return painter(severity) if painter else severity


def status_text(status: str) -> str:
    """Return an event/effect status string colored for terminal display."""
    painter = _STATUS_COLORS.get(status.lower())
    return painter(status) if painter else status
