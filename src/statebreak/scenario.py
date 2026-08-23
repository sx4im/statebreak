"""Scenario loading, schema validation, and security checking."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import jsonschema
import yaml

from statebreak.canonical import compute_scenario_hash
from statebreak.errors import ConfigurationError
from statebreak.models import (
    AgentTaskSpec,
    ClockSpec,
    ExpectationSpec,
    FaultSpec,
    OracleSpec,
    Scenario,
)
from statebreak.registry import (
    VALID_FAULT_TYPES,
    VALID_LIFECYCLE_POINTS,
    VALID_ORACLE_TYPES,
)

# Backwards-compatible aliases: lifecycle points were previously named
# VALID_FAULT_LIFECYCLE_POINTS in this module.
VALID_FAULT_LIFECYCLE_POINTS = VALID_LIFECYCLE_POINTS

# Suspicious secret detection patterns
SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS Access Key
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}"),  # GitHub Token
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),  # OpenAI/API Secret Key
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),  # Private keys
]

# URL detection matches http(s) URLs ANYWHERE inside a string value, not only
# when the whole string starts with one — an instruction like
# "see https://evil.example.net/x" must be rejected just like a bare URL.
URL_PATTERN = re.compile(r"https?://[^\s\"'><)]+", re.IGNORECASE)

ALLOWED_HOST_SUFFIXES = (
    ".example.test",
    ".test",
    "localhost",
    "127.0.0.1",
    "::1",
)

MAX_SCENARIO_FILE_SIZE = 1_048_576  # 1 MB


def _find_schema_path() -> Path:
    """Find path to scenario.schema.json."""
    # Try relative to package directory (installed package or source layout)
    pkg_schema = Path(__file__).resolve().parent / "schemas" / "scenario.schema.json"
    if pkg_schema.exists():
        return pkg_schema

    # Try relative to repository root
    repo_schema = Path(__file__).resolve().parents[2] / "schemas" / "scenario.schema.json"
    if repo_schema.exists():
        return repo_schema

    # Fallback to current working directory
    cwd_schema = Path.cwd() / "schemas" / "scenario.schema.json"
    if cwd_schema.exists():
        return cwd_schema

    raise ConfigurationError(
        f"scenario.schema.json could not be located in {pkg_schema} or {repo_schema}"
    )


def _load_schema() -> dict[str, Any]:
    """Load JSON schema for scenario validation."""
    schema_path = _find_schema_path()
    try:
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_data: dict[str, Any] = json.load(f)
            return schema_data
    except Exception as exc:
        raise ConfigurationError(
            f"failed to load scenario schema from {schema_path}: {exc}"
        ) from exc


def _check_security_invariants(data: Any, path: str = "scenario") -> None:
    """Recursively check for credentials, unsafe URLs, or dangerous values."""
    if isinstance(data, str):
        # 1. Check for secret patterns
        for pattern in SECRET_PATTERNS:
            if pattern.search(data):
                raise ConfigurationError(
                    f"security check failed at {path}: value matches suspicious credential pattern"
                )

        # 2. Check for non-test URLs (anywhere in the string, not only as prefix)
        for match in URL_PATTERN.finditer(data):
            try:
                parsed = urlparse(match.group(0))
                hostname = (parsed.hostname or "").lower()
            except ValueError as exc:
                raise ConfigurationError(
                    f"security check failed at {path}: invalid URL '{match.group(0)}'"
                ) from exc
            if not any(
                hostname == allowed.lstrip(".") or hostname.endswith(allowed)
                for allowed in ALLOWED_HOST_SUFFIXES
            ):
                raise ConfigurationError(
                    f"security check failed at {path}: external non-test URL "
                    f"'{match.group(0)}' is not permitted "
                    f"(allowed domains: *.example.test, *.test, localhost)"
                )

    elif isinstance(data, dict):
        for k, v in data.items():
            _check_security_invariants(v, f"{path}.{k}")
    elif isinstance(data, list):
        for i, item in enumerate(data):
            _check_security_invariants(item, f"{path}[{i}]")


def _check_semantic_invariants(data: dict[str, Any]) -> None:
    """Validate semantic rules not fully expressible in JSON schema."""
    # Check schema string
    if data.get("schema") != "statebreak.scenario/v1":
        raise ConfigurationError(
            f"unsupported scenario schema version: '{data.get('schema')}' "
            "(expected 'statebreak.scenario/v1')"
        )

    # Check clock timestamp format
    clock = data.get("clock")
    if isinstance(clock, dict) and "start" in clock:
        start_str = clock["start"]
        try:
            # Validate ISO 8601 parseable ("Z" suffix is supported natively on 3.11+)
            datetime.fromisoformat(start_str)
        except Exception as exc:
            raise ConfigurationError(
                f"invalid clock.start timestamp '{start_str}': must be valid ISO 8601 string"
            ) from exc

    # Check faults
    faults = data.get("faults", [])
    if isinstance(faults, list):
        for i, fault in enumerate(faults):
            if isinstance(fault, dict):
                fault_at = fault.get("at")
                if fault_at not in VALID_FAULT_LIFECYCLE_POINTS:
                    raise ConfigurationError(
                        f"faults[{i}] has unknown lifecycle point '{fault_at}' "
                        f"(valid: {sorted(VALID_FAULT_LIFECYCLE_POINTS)})"
                    )
                fault_type = fault.get("type")
                if fault_type not in VALID_FAULT_TYPES:
                    raise ConfigurationError(
                        f"faults[{i}] has unknown fault type '{fault_type}' "
                        f"(valid: {sorted(VALID_FAULT_TYPES)})"
                    )

    # Check oracles
    oracles = data.get("oracles", [])
    if isinstance(oracles, list):
        for i, oracle in enumerate(oracles):
            if isinstance(oracle, dict):
                oracle_type = oracle.get("type")
                if oracle_type not in VALID_ORACLE_TYPES:
                    raise ConfigurationError(
                        f"oracles[{i}] has unknown oracle type '{oracle_type}' "
                        f"(valid: {sorted(VALID_ORACLE_TYPES)})"
                    )


def _normalize_json_primitives(data: Any) -> Any:
    """Normalize YAML types (e.g. datetime, date) into JSON-compatible primitives."""
    if isinstance(data, datetime):
        s = data.isoformat()
        if s.endswith("+00:00"):
            return s[:-6] + "Z"
        if data.tzinfo is None:
            return s + "Z"
        return s
    if isinstance(data, date):
        return data.isoformat()
    if isinstance(data, dict):
        return {str(k): _normalize_json_primitives(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_normalize_json_primitives(item) for item in data]
    return data


def validate_scenario_dict(data: dict[str, Any]) -> None:
    """Validate a raw scenario dictionary against JSON schema, security, and semantic rules."""
    if not isinstance(data, dict):
        raise ConfigurationError("scenario root must be a JSON object / YAML mapping")

    # Ensure primitives are JSON-compatible (handling yaml-parsed datetimes)
    data = _normalize_json_primitives(data)

    schema = _load_schema()
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as err:
        loc = f" at {err.json_path}" if hasattr(err, "json_path") and err.json_path != "$" else ""
        raise ConfigurationError(f"scenario schema validation error{loc}: {err.message}") from err
    except jsonschema.SchemaError as err:
        raise ConfigurationError(f"invalid scenario JSON schema: {err.message}") from err

    _check_security_invariants(data)
    _check_semantic_invariants(data)


def load_scenario_from_dict(data: dict[str, Any]) -> Scenario:
    """Validate and construct an immutable Scenario dataclass from dictionary."""
    data = _normalize_json_primitives(data)
    validate_scenario_dict(data)

    # Clock
    clock_data = data["clock"]
    clock = ClockSpec(
        start=str(clock_data["start"]),
        step_seconds=int(clock_data.get("step_seconds", 30)),
    )

    # Faults
    fault_list: list[FaultSpec] = []
    for f in data["faults"]:
        params = {k: v for k, v in f.items() if k not in {"id", "at", "type", "target", "repeat"}}
        fault_list.append(
            FaultSpec(
                id=str(f["id"]),
                at=str(f["at"]),
                type=str(f["type"]),
                target=f.get("target"),
                repeat=f.get("repeat"),
                params=params,
            )
        )

    # Agent Task
    task_data = data["agent_task"]
    task_params = {
        k: v for k, v in task_data.items() if k not in {"instruction", "tools", "required_claim"}
    }
    agent_task = AgentTaskSpec(
        instruction=str(task_data["instruction"]),
        tools=tuple(str(t) for t in task_data["tools"]),
        required_claim=task_data.get("required_claim"),
        params=task_params,
    )

    # Oracles
    oracle_list: list[OracleSpec] = []
    for o in data["oracles"]:
        params = {
            k: v
            for k, v in o.items()
            if k not in {"id", "type", "expression", "effect", "when", "claim"}
        }
        oracle_list.append(
            OracleSpec(
                id=str(o["id"]),
                type=str(o["type"]),
                expression=o.get("expression"),
                effect=o.get("effect"),
                when=o.get("when"),
                claim=o.get("claim"),
                params=params,
            )
        )

    # Expectations
    expectations: dict[str, ExpectationSpec] = {}
    if "expectations" in data and isinstance(data["expectations"], dict):
        for name, exp in data["expectations"].items():
            if isinstance(exp, dict):
                expectations[str(name)] = ExpectationSpec(
                    verdict=str(exp["verdict"]),
                    finding_ids=tuple(str(fid) for fid in exp.get("finding_ids", ())),
                )

    scenario_hash = compute_scenario_hash(data)

    return Scenario(
        schema=str(data["schema"]),
        id=str(data["id"]),
        version=int(data["version"]),
        seed=int(data["seed"]),
        clock=clock,
        world=dict(data["world"]),
        faults=tuple(fault_list),
        agent_task=agent_task,
        oracles=tuple(oracle_list),
        expectations=expectations,
        scenario_hash=scenario_hash,
    )


def load_scenario(path: str | Path) -> Scenario:
    """Load, validate, and parse a scenario file (YAML or JSON)."""
    file_path = Path(path).resolve()
    if not file_path.exists():
        raise ConfigurationError(f"scenario file not found: {file_path}")
    if not file_path.is_file():
        raise ConfigurationError(f"scenario path is not a regular file: {file_path}")

    file_size = file_path.stat().st_size
    if file_size > MAX_SCENARIO_FILE_SIZE:
        raise ConfigurationError(
            f"scenario file exceeds maximum allowed size of "
            f"{MAX_SCENARIO_FILE_SIZE} bytes: {file_path}"
        )

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as exc:
        raise ConfigurationError(f"failed to read scenario file {file_path}: {exc}") from exc

    try:
        data = yaml.safe_load(content)
    except Exception as exc:
        raise ConfigurationError(
            f"failed to parse scenario YAML/JSON from {file_path}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ConfigurationError(
            f"scenario content in {file_path} must be a YAML mapping or JSON object"
        )

    return load_scenario_from_dict(data)


def load_scenarios_from_dir(dir_path: str | Path) -> list[Scenario]:
    """Discover and load all scenario files in a directory, sorted by scenario id."""
    target_dir = Path(dir_path).resolve()
    if not target_dir.exists():
        raise ConfigurationError(f"scenarios directory not found: {target_dir}")
    if not target_dir.is_dir():
        raise ConfigurationError(f"scenarios path is not a directory: {target_dir}")

    scenario_extensions = {".yml", ".yaml", ".json"}
    scenario_files = [
        p for p in target_dir.iterdir() if p.is_file() and p.suffix.lower() in scenario_extensions
    ]

    if not scenario_files:
        raise ConfigurationError(f"no scenario files found in directory: {target_dir}")

    scenarios = [load_scenario(p) for p in sorted(scenario_files)]
    return sorted(scenarios, key=lambda s: s.id)
