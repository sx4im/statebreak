"""Canonical serialization and deterministic SHA-256 hashing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any


def _normalize_obj(obj: Any) -> Any:
    """Recursively normalize objects for canonical JSON serialization."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return _normalize_obj(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _normalize_obj(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_normalize_obj(item) for item in obj]
    if isinstance(obj, (set, frozenset)):
        # Sets are sorted for deterministic representation
        normalized_set = [_normalize_obj(item) for item in obj]
        return sorted(normalized_set, key=lambda x: canonical_json(x))
    return obj


def canonical_json(obj: Any) -> str:
    """Serialize any object into a deterministic canonical JSON string.

    Features:
    - Lexicographically sorted dictionary keys.
    - Compact separators (no extra whitespace).
    - UTF-8 safe (ensure_ascii=False).
    - Normalized nested containers and dataclasses.
    """
    normalized = _normalize_obj(obj)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_sha256(data: str | bytes | dict[str, Any] | Any) -> str:
    """Compute deterministic SHA-256 hex digest for given data."""
    if isinstance(data, bytes):
        raw_bytes = data
    elif isinstance(data, str):
        raw_bytes = data.encode("utf-8")
    else:
        raw_bytes = canonical_json(data).encode("utf-8")

    return hashlib.sha256(raw_bytes).hexdigest()


def compute_scenario_hash(scenario_data: Any) -> str:
    """Compute canonical SHA-256 hash for a scenario object or dict.

    Excludes any existing scenario_hash field to avoid self-referential hashing.
    """
    if hasattr(scenario_data, "to_dict"):
        raw_dict = scenario_data.to_dict()
    elif is_dataclass(scenario_data) and not isinstance(scenario_data, type):
        raw_dict = asdict(scenario_data)
    elif isinstance(scenario_data, dict):
        raw_dict = dict(scenario_data)
    else:
        raise TypeError(f"Unsupported scenario type for hashing: {type(scenario_data)}")

    # Remove scenario_hash if present
    raw_dict.pop("scenario_hash", None)

    return compute_sha256(raw_dict)
