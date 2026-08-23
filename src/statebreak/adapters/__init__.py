"""Deterministic reference adapters for StateBreak failure fixtures."""

from statebreak.adapters.guarded import GuardedAdapter
from statebreak.adapters.multi_node import MultiNodeAdapter
from statebreak.adapters.naive import NaiveAdapter

__all__ = [
    "GuardedAdapter",
    "MultiNodeAdapter",
    "NaiveAdapter",
]
