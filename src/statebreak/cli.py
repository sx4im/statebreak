"""CLI entry point and command-line parsing for StateBreak."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

import jsonschema

from statebreak import __version__
from statebreak.errors import ConfigurationError, StateBreakError, UsageError
from statebreak.models import Finding, RunReport
from statebreak.report import render_json, render_markdown, render_sarif
from statebreak.runner import ScenarioRunner
from statebreak.scenario import _find_schema_path, load_scenario


def _load_report_schema() -> dict[str, Any]:
    """Load the bundled report.schema.json for validating re-rendered reports."""
    schema_path = _find_schema_path().parent / "report.schema.json"
    if not schema_path.exists():
        # Fall back to repo-root layout when running from a source checkout.
        schema_path = _find_schema_path().parents[1] / "schemas" / "report.schema.json"
    with open(schema_path, "r", encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
        return data


class StateBreakArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser providing clean errors and intercepting exits."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(message)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            sys.stderr.write(message)
        raise SystemExit(status)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser for StateBreak."""
    common_parser = StateBreakArgumentParser(add_help=False)
    common_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode and show tracebacks on unexpected errors.",
    )

    parser = StateBreakArgumentParser(
        prog="statebreak",
        description="StateBreak: Deterministic failure laboratory for AI agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common_parser],
        epilog=(
            "Commands:\n"
            "  validate PATH   Validate one scenario file or directory\n"
            "  list [PATH]     List bundled scenarios and adapters\n"
            "  run PATH        Run scenarios with an adapter and write a report\n"
            "  report PATH     Re-render JSON report as Markdown or SARIF\n"
            "  explain PATH    Print findings and timeline for one scenario\n"
            "  version         Print package version\n"
        ),
    )
    parser.add_argument(
        "-v",
        "--version",
        action="store_true",
        help="Print package version and exit.",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # version command
    subparsers.add_parser("version", parents=[common_parser], help="Print package version.")

    # validate command
    val_p = subparsers.add_parser(
        "validate", parents=[common_parser], help="Validate scenario file or directory."
    )
    val_p.add_argument("path", help="Path to scenario YAML file or directory.")
    val_p.add_argument("--json", action="store_true", help="Output results as JSON.")

    # list command
    list_p = subparsers.add_parser(
        "list", parents=[common_parser], help="List bundled scenarios and adapters."
    )
    list_p.add_argument(
        "path",
        nargs="?",
        default="scenarios",
        help="Path to scenarios directory (default: scenarios).",
    )
    list_p.add_argument("--json", action="store_true", help="Output list as JSON.")

    # run command
    run_p = subparsers.add_parser(
        "run", parents=[common_parser], help="Run scenarios with an adapter."
    )
    run_p.add_argument("scenario", help="Path to scenario YAML file.")
    run_p.add_argument(
        "--agent",
        "-a",
        default="guarded",
        help="Adapter name ('guarded', 'naive', 'multi_node', default: guarded).",
    )
    run_p.add_argument(
        "--seed",
        "-s",
        type=int,
        default=None,
        help="Deterministic random seed override.",
    )
    run_p.add_argument(
        "--format",
        "-f",
        choices=["markdown", "json", "sarif"],
        default="markdown",
        help="Output format (markdown, json, sarif; default: markdown).",
    )
    run_p.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write report to output file instead of stdout.",
    )

    # report command
    rep_p = subparsers.add_parser(
        "report", parents=[common_parser], help="Re-render a report file."
    )
    rep_p.add_argument("path", help="Path to JSON report file.")
    rep_p.add_argument(
        "--format",
        "-f",
        choices=["markdown", "sarif", "json"],
        default="markdown",
        help="Output format (markdown, sarif, json; default: markdown).",
    )
    rep_p.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write rendered output to file instead of stdout.",
    )

    # explain command
    exp_p = subparsers.add_parser(
        "explain", parents=[common_parser], help="Explain failure modes in a scenario."
    )
    exp_p.add_argument("scenario", help="Path to scenario YAML file.")

    return parser


def handle_validate(path_str: str, as_json: bool = False) -> int:
    """Validate scenario file or directory against schema."""
    target_path = Path(path_str)
    if not target_path.exists():
        raise UsageError(f"path does not exist: {path_str}")

    files: list[Path] = []
    if target_path.is_file():
        files = [target_path]
    else:
        files = sorted(list(target_path.glob("*.yml")) + list(target_path.glob("*.yaml")))

    if not files:
        raise UsageError(f"no YAML scenario files found in {path_str}")

    results: list[dict[str, Any]] = []
    has_errors = False

    for f in files:
        try:
            sc = load_scenario(f)
            results.append({"path": str(f), "id": sc.id, "valid": True})
            if not as_json:
                sys.stdout.write(f"✓ {f}: valid scenario (id: {sc.id})\n")
        except ConfigurationError as e:
            has_errors = True
            results.append({"path": str(f), "valid": False, "error": str(e)})
            if not as_json:
                sys.stderr.write(f"✗ {f}: invalid ({e})\n")

    if as_json:
        sys.stdout.write(json.dumps(results, indent=2) + "\n")

    return 2 if has_errors else 0


def handle_list(path_str: str, as_json: bool = False) -> int:
    """List available scenarios and reference adapters."""
    target_path = Path(path_str)
    scenario_list: list[dict[str, Any]] = []

    if target_path.exists() and target_path.is_dir():
        files = sorted(list(target_path.glob("*.yml")) + list(target_path.glob("*.yaml")))
        for f in files:
            try:
                sc = load_scenario(f)
                fault_types = [flt.type for flt in sc.faults]
                scenario_list.append({
                    "id": sc.id,
                    "file": str(f),
                    "instruction": sc.agent_task.instruction,
                    "faults": fault_types,
                    "oracles": [o.type for o in sc.oracles],
                })
            except ConfigurationError as e:
                # `list` is a discovery view: skip unreadable scenario files
                # instead of failing the whole listing.
                sys.stderr.write(f"warning: skipping {f}: {e}\n")

    adapters = [
        {"name": "guarded", "description": "Guarded adapter with freshness & reconciliation"},
        {"name": "naive", "description": "Naive adapter with stale-state reuse"},
        {"name": "multi_node", "description": "Multi-node coordinating adapter"},
    ]

    if as_json:
        out = {"scenarios": scenario_list, "adapters": adapters}
        sys.stdout.write(json.dumps(out, indent=2) + "\n")
    else:
        sys.stdout.write("Available Scenarios:\n")
        if not scenario_list:
            sys.stdout.write("  (No scenario files found)\n")
        for sc_info in scenario_list:
            faults_str = ", ".join(sc_info["faults"]) if sc_info["faults"] else "none"
            sys.stdout.write(f"  - {sc_info['id']:<24} (faults: {faults_str})\n")

        sys.stdout.write("\nAvailable Reference Adapters:\n")
        for a in adapters:
            sys.stdout.write(f"  - {a['name']:<14} {a['description']}\n")

    return 0


def handle_run(
    scenario_path: str,
    agent: str,
    seed: int | None,
    fmt: str,
    output_file: str | None,
) -> int:
    """Execute scenario and emit formatted report."""
    runner = ScenarioRunner()
    report = runner.run_scenario(scenario_path, adapter=agent, seed=seed)

    if fmt == "json":
        rendered = render_json(report)
    elif fmt == "sarif":
        rendered = json.dumps(render_sarif(report), indent=2)
    else:
        rendered = render_markdown(report)

    if output_file:
        out_p = Path(output_file)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered + "\n")

    # Exit code mapping: 0 for pass / needs_review, 1 for fail
    if report.verdict == "fail":
        return 1
    return 0


def handle_report(report_path: str, fmt: str, output_file: str | None) -> int:
    """Re-render a JSON report file after validating it against the report schema."""
    p = Path(report_path)
    if not p.exists():
        raise UsageError(f"report file not found: {report_path}")

    raw_data = json.loads(p.read_text(encoding="utf-8"))

    if not isinstance(raw_data, dict):
        raise UsageError(f"report file must contain a JSON object: {report_path}")

    # Validate against the bundled report schema before reconstructing, so
    # malformed reports fail loudly instead of silently rendering defaults.
    schema = _load_report_schema()
    try:
        jsonschema.validate(instance=raw_data, schema=schema)
    except jsonschema.ValidationError as err:
        loc = f" at {err.json_path}" if getattr(err, "json_path", "$") != "$" else ""
        raise UsageError(
            f"invalid StateBreak report{loc}: {err.message}"
        ) from err
    except jsonschema.SchemaError as err:
        raise UsageError(f"invalid report JSON schema: {err.message}") from err

    findings_list = tuple(
        Finding(
            finding_id=f.get("finding_id", ""),
            severity=f.get("severity", "medium"),
            category=f.get("category", ""),
            blocking=f.get("blocking", False),
            expected=f.get("expected", {}),
            observed=f.get("observed", {}),
            remediation=f.get("remediation", ""),
            event_refs=tuple(f.get("event_refs", ())),
            scenario_id=f.get("scenario_id", ""),
            fault_refs=tuple(f.get("fault_refs", ())),
        )
        for f in raw_data.get("findings", [])
    )

    report = RunReport(
        schema=raw_data.get("schema", "statebreak.report/v1"),
        run_id=raw_data.get("run_id", ""),
        scenario_id=raw_data.get("scenario_id", ""),
        scenario_hash=raw_data.get("scenario_hash", ""),
        seed=int(raw_data.get("seed", 42)),
        adapter=raw_data.get("adapter", {}),
        verdict=raw_data.get("verdict", "error"),
        metrics=raw_data.get("metrics", {}),
        events=tuple(raw_data.get("events", ())),
        effects=tuple(raw_data.get("effects", ())),
        findings=findings_list,
        limitations=tuple(raw_data.get("limitations", ())),
    )

    if fmt == "json":
        rendered = render_json(report)
    elif fmt == "sarif":
        rendered = json.dumps(render_sarif(report), indent=2)
    else:
        rendered = render_markdown(report)

    if output_file:
        out_p = Path(output_file)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered + "\n")

    return 0


def handle_explain(scenario_path: str) -> int:
    """Print human-readable explanation of scenario failure modes."""
    sc = load_scenario(scenario_path)
    sys.stdout.write(f"Scenario: {sc.id} (v{sc.version})\n")
    sys.stdout.write(f"Task: {sc.agent_task.instruction}\n")
    sys.stdout.write(f"Tools Allowed: {', '.join(sc.agent_task.tools)}\n\n")

    sys.stdout.write("Injected Fault Invariants:\n")
    for f in sc.faults:
        sys.stdout.write(
            f"  - Fault [{f.id}]: type={f.type}, at={f.at}, target={f.target or '-'}\n"
        )

    sys.stdout.write("\nAuthoritative Oracle Rules:\n")
    for o in sc.oracles:
        sys.stdout.write(f"  - Oracle [{o.id}]: type={o.type}\n")

    sys.stdout.write("\nExpected Outcomes by Adapter:\n")
    sys.stdout.write(
        "  - naive adapter: Expected to fail due to unverified execution and stale reads.\n"
    )
    sys.stdout.write(
        "  - guarded adapter: Expected to pass or request review via freshness checks.\n"
    )

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entry point returning exit code."""
    if argv is None:
        argv = sys.argv[1:]

    # Handle no-args by printing help cleanly with exit code 0
    if len(argv) == 0:
        parser = build_parser()
        parser.print_help()
        return 0

    debug_mode = "--debug" in argv

    try:
        # Fast-path standalone version command / flags
        if argv in (["version"], ["--version"], ["-v"]):
            sys.stdout.write(f"statebreak {__version__}\n")
            return 0

        # Fast-path help flags
        if argv in (["--help"], ["-h"], ["help"]):
            parser = build_parser()
            parser.print_help()
            return 0

        parser = build_parser()
        args, unknown = parser.parse_known_args(argv)

        if unknown:
            raise UsageError(f"unrecognized arguments: {' '.join(unknown)}")

        if getattr(args, "version", False):
            sys.stdout.write(f"statebreak {__version__}\n")
            return 0

        cmd = args.command
        if cmd == "version":
            sys.stdout.write(f"statebreak {__version__}\n")
            return 0
        elif cmd == "validate":
            return handle_validate(args.path, as_json=getattr(args, "json", False))
        elif cmd == "list":
            return handle_list(args.path, as_json=getattr(args, "json", False))
        elif cmd == "run":
            return handle_run(
                args.scenario,
                agent=args.agent,
                seed=args.seed,
                fmt=args.format,
                output_file=args.output,
            )
        elif cmd == "report":
            return handle_report(args.path, fmt=args.format, output_file=args.output)
        elif cmd == "explain":
            return handle_explain(args.scenario)
        else:
            raise UsageError(f"unknown command '{cmd}'")

    except SystemExit as exit_err:
        return int(exit_err.code) if exit_err.code is not None else 0

    except StateBreakError as err:
        sys.stderr.write(f"error: {err.message}\n")
        if debug_mode:
            traceback.print_exc(file=sys.stderr)
        return err.exit_code

    except Exception as exc:  # noqa: BLE001 - top-level CLI boundary
        if debug_mode:
            traceback.print_exc(file=sys.stderr)
        else:
            sys.stderr.write(f"error: unexpected internal fault: {exc}\n")
        return 3


if __name__ == "__main__":
    sys.exit(main())
