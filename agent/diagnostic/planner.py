from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from agent.semantic_query import GovernedSemanticQueryPlanner, SemanticQueryStatus

from .contracts import DiagnosticRequestPlan


_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{1,2}-\d{1,2}(?!\d)")
_CN_DATE = re.compile(r"(?<!\d)\d{4}年\d{1,2}月\d{1,2}日")
_SQL_RE = re.compile(r"\b(select|delete|drop|truncate|update|insert|merge|alter|create)\b", re.I)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _alias_position(question: str, alias: str) -> int | None:
    alias = str(alias).strip()
    if not alias:
        return None
    if _CJK_RE.search(alias):
        pos = question.casefold().find(alias.casefold())
        return pos if pos >= 0 else None
    match = re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", question, re.I)
    return match.start() if match else None


class GovernedDiagnosticPlanner:
    """Natural-language diagnostic planner that reuses the Phase 5 semantic-query contract.

    It owns only diagnostic interpretation. Metric definitions and query semantics remain owned by
    the governed registry + MetricFlow planner. Relative dates are resolved to explicit UTC dates
    before the Phase 5 planner is called.
    """

    def __init__(self, project_root: Path | str, *, now_provider=None):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/diagnostic_orchestrator_policy.yml").read_text(encoding="utf-8")
        )
        self.routing = yaml.safe_load(
            (self.root / "agent/contracts/intent_routing.yml").read_text(encoding="utf-8")
        )
        self.semantic_planner = GovernedSemanticQueryPlanner(self.root)
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def plan(self, question: str) -> DiagnosticRequestPlan:
        q = question.strip()
        if not q:
            return self._stop(SemanticQueryStatus.CLARIFICATION_REQUIRED, q, "Diagnostic question is empty.")
        lowered = q.casefold()
        if _SQL_RE.search(q) or "--where" in lowered or "{{ dimension(" in lowered:
            return self._stop(
                SemanticQueryStatus.BLOCKED,
                q,
                "Arbitrary SQL / raw predicates are outside the governed diagnostic surface.",
            )

        metrics = self._resolve_metrics(q)
        if not metrics:
            return self._stop(
                SemanticQueryStatus.CLARIFICATION_REQUIRED,
                q,
                "Diagnostic analysis requires one explicit governed metric.",
            )
        if len(metrics) > 1:
            return self._stop(
                SemanticQueryStatus.CLARIFICATION_REQUIRED,
                q,
                "Diagnostic analysis currently accepts exactly one governed metric per diagnosis.",
            )

        resolved_question, time_resolution = self._resolve_relative_date(q)
        semantic_plan = self.semantic_planner.plan(
            metric=metrics[0],
            question=resolved_question,
            limit=1,
        )
        return DiagnosticRequestPlan(
            status=semantic_plan.status,
            question=q,
            resolved_question=resolved_question,
            metric=metrics[0],
            semantic_plan=semantic_plan,
            relative_time_resolution=time_resolution,
            warnings=list(semantic_plan.warnings),
        )

    def _resolve_metrics(self, question: str) -> list[str]:
        found = []
        for metric, aliases in self.routing.get("metric_aliases", {}).items():
            candidates = [metric, *aliases]
            positions = [
                (pos, -len(str(alias)), metric)
                for alias in candidates
                if (pos := _alias_position(question, str(alias))) is not None
            ]
            if positions:
                found.append(min(positions))
        found.sort()
        return list(dict.fromkeys(metric for _, _, metric in found))

    def _resolve_relative_date(self, question: str) -> tuple[str, str | None]:
        if _ISO_DATE.search(question) or _CN_DATE.search(question):
            return question, None
        now = self.now_provider()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        relative_markers = self.policy.get("relative_time", {})
        if any(marker.casefold() in question.casefold() for marker in relative_markers.get("today", [])):
            value = now.date().isoformat()
            return f"{question} {value}", f"today->{value} UTC"
        if any(marker.casefold() in question.casefold() for marker in relative_markers.get("yesterday", [])):
            value = (now.date() - timedelta(days=1)).isoformat()
            return f"{question} {value}", f"yesterday->{value} UTC"
        return question, None

    @staticmethod
    def _stop(status, question, warning):
        return DiagnosticRequestPlan(
            status=status,
            question=question,
            resolved_question=question,
            warnings=[warning],
        )
