"""Multi-format report renderers for StateBreak execution reports."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from statebreak.models import EffectRecord, Finding, RunReport


def _report_to_dict(report: RunReport) -> dict[str, Any]:
    """Convert RunReport dataclass to JSON-serializable dictionary."""
    findings_dicts: list[dict[str, Any]] = []
    for f in report.findings:
        f_dict = asdict(f) if isinstance(f, Finding) else dict(f)
        findings_dicts.append(f_dict)

    effects_dicts: list[dict[str, Any]] = []
    for eff in report.effects:
        eff_dict = asdict(eff) if isinstance(eff, EffectRecord) else dict(eff)
        effects_dicts.append(eff_dict)

    return {
        "schema": report.schema,
        "run_id": report.run_id,
        "scenario_id": report.scenario_id,
        "scenario_hash": report.scenario_hash,
        "seed": report.seed,
        "adapter": dict(report.adapter),
        "verdict": report.verdict,
        "metrics": dict(report.metrics),
        "events": list(report.events),
        "effects": effects_dicts,
        "findings": findings_dicts,
        "limitations": list(report.limitations),
    }


def render_json(report: RunReport, indent: int = 2) -> str:
    """Render RunReport as formatted JSON string."""
    data = _report_to_dict(report)
    return json.dumps(data, indent=indent, sort_keys=True)


def render_markdown(report: RunReport) -> str:
    """Render RunReport as formatted Markdown document."""
    lines: list[str] = [
        f"# StateBreak Run Report: `{report.scenario_id}`",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| **Run ID** | `{report.run_id}` |",
        f"| **Scenario ID** | `{report.scenario_id}` |",
        f"| **Verdict** | **`{report.verdict.upper()}`** |",
        (
            f"| **Adapter** | `{report.adapter.get('name', 'unknown')} "
            f"v{report.adapter.get('version', '')}` |"
        ),
        f"| **Seed** | `{report.seed}` |",
        f"| **Scenario Hash** | `{report.scenario_hash[:16]}...` |",
        "",
    ]

    # Metrics Section
    lines.extend([
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
    ])
    for k, v in sorted(report.metrics.items()):
        formatted_val = f"{v:.4f}" if isinstance(v, float) and not v.is_integer() else str(v)
        lines.append(f"| `{k}` | `{formatted_val}` |")
    lines.append("")

    # Findings Section
    lines.extend([
        "## Findings",
        "",
    ])
    if not report.findings:
        lines.append("*No findings recorded. All scenario invariants held.*")
        lines.append("")
    else:
        lines.extend([
            "| ID | Severity | Category | Blocking | Remediation |",
            "|---|---|---|---|---|",
        ])
        for f in report.findings:
            f_id = f.finding_id if isinstance(f, Finding) else f.get("finding_id", "")
            sev = f.severity if isinstance(f, Finding) else f.get("severity", "")
            cat = f.category if isinstance(f, Finding) else f.get("category", "")
            is_blk = f.blocking if isinstance(f, Finding) else f.get("blocking", False)
            blk = "Yes" if is_blk else "No"
            rem = f.remediation if isinstance(f, Finding) else f.get("remediation", "")
            lines.append(f"| `{f_id}` | `{sev}` | `{cat}` | {blk} | {rem} |")
        lines.append("")

    # Timeline Section
    lines.extend([
        "## Execution Timeline",
        "",
        "| Event / Effect ID | Type / Kind | Target | Status |",
        "|---|---|---|---|",
    ])
    for ev in report.events:
        lines.append(
            f"| `{ev.get('event_id', '')}` | Fault: `{ev.get('fault_type', '')}` | "
            f"`{ev.get('target_entity_id', '-')}` | `{ev.get('status', '')}` |"
        )
    for eff in report.effects:
        if isinstance(eff, EffectRecord):
            eff_id, kind, target, status = eff.effect_id, eff.kind, eff.target, eff.status
        else:
            eff_id = eff.get("effect_id", "")
            kind = eff.get("kind", "")
            target = eff.get("target", "")
            status = eff.get("status", "")
        lines.append(f"| `{eff_id}` | Effect: `{kind}` | `{target}` | `{status}` |")
    lines.append("")

    # Diagnostic section
    if report.verdict == "fail":
        lines.extend([
            "## Diagnostic: Why this failed",
            "",
            "Authoritative state evaluation detected that declared scenario invariants were not "
            "satisfied. The agent adapter claimed completion or executed unverified actions in "
            "the presence of injected faults. Review findings table for remediation.",
            "",
        ])

    return "\n".join(lines)


def render_sarif(report: RunReport) -> dict[str, Any]:
    """Render RunReport as SARIF 2.1.0 object."""
    rules_map: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []

    for f in report.findings:
        f_id = f.finding_id if isinstance(f, Finding) else f.get("finding_id", "")
        sev = f.severity if isinstance(f, Finding) else f.get("severity", "medium")
        cat = f.category if isinstance(f, Finding) else f.get("category", "rule_violation")
        rem = f.remediation if isinstance(f, Finding) else f.get("remediation", "")

        level = "error" if sev in ("critical", "high") else "warning"

        if cat not in rules_map:
            rules_map[cat] = {
                "id": cat,
                "name": cat.replace("_", " ").title(),
                "shortDescription": {"text": f"StateBreak invariant check: {cat}"},
                "help": {"text": rem},
            }

        results.append({
            "ruleId": cat,
            "level": level,
            "message": {"text": f"[{sev.upper()}] {cat}: {rem} (finding: {f_id})"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": f"scenarios/{report.scenario_id}.yml",
                        }
                    }
                }
            ],
            "properties": {
                "finding_id": f_id,
                "scenario_id": report.scenario_id,
                "run_id": report.run_id,
            },
        })

    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "StateBreak",
                        "version": "0.1.0",
                        "informationUri": "https://statebreak.dev",
                        "rules": list(rules_map.values()),
                    }
                },
                "results": results,
            }
        ],
    }
