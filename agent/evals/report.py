"""Agent Eval 报告格式化。"""

from __future__ import annotations

import json
from pathlib import Path

from .contracts import AgentEvalReport, EvalCaseStatus


def render_text_report(report: AgentEvalReport) -> str:
    """生成适合 CI / 本地终端阅读的精简报告。"""

    lines = [
        "Agent Eval Report",
        f"mode={report.mode}",
        (
            f"passed={report.passed}/{report.total - report.skipped} "
            f"failed={report.failed} skipped={report.skipped} "
            f"pass_rate={report.pass_rate:.2%}"
        ),
        "",
    ]

    for category, summary in report.category_summary().items():
        lines.append(
            f"[{category}] {summary['passed']}/{summary['measured']} "
            f"({summary['pass_rate']:.2%})"
        )

    failures = [
        item
        for item in report.results
        if item.status in {EvalCaseStatus.FAIL, EvalCaseStatus.ERROR}
    ]
    if failures:
        lines.extend(["", "Failures:"])
        for result in failures:
            lines.append(f"- {result.case.case_id}: {result.status.value}")
            for check in result.checks:
                if not check.passed:
                    lines.append(
                        f"    {check.name}: expected={check.expected!r}, actual={check.actual!r}"
                    )
            for warning in result.warnings:
                lines.append(f"    {warning}")

    return "\n".join(lines)


def write_json_report(report: AgentEvalReport, path: Path | str) -> Path:
    """输出机器可读报告，便于后续 CI artifact / trend analysis。"""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output
