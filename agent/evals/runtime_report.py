"""Runtime Golden Eval 报告。"""

from __future__ import annotations

import json
from pathlib import Path

from .runtime_contracts import RuntimeGoldenReport, RuntimeGoldenStatus


def render_runtime_golden_report(report: RuntimeGoldenReport) -> str:
    lines = [
        "Runtime Golden Eval",
        f"mode={report.mode}",
        f"passed={report.passed}/{report.total} pass_rate={report.pass_rate:.2%}",
        f"fixture={report.fixture_path}",
    ]

    failures = [
        item
        for item in report.results
        if item.status is not RuntimeGoldenStatus.PASS
    ]
    if failures:
        lines.append("")
        lines.append("Failures:")
        for result in failures:
            lines.append(f"- {result.case.case_id}: {result.status.value}")
            for check in result.checks:
                if not check.passed:
                    lines.append(
                        f"    {check.name}: expected={check.expected!r}; actual={check.actual!r}"
                    )
            for warning in result.warnings:
                lines.append(f"    {warning}")
    return "\n".join(lines)


def write_runtime_golden_json(
    report: RuntimeGoldenReport,
    path: Path | str,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output
