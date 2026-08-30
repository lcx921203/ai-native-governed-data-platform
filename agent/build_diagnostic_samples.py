from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from agent.diagnostic import DiagnosticEvidenceComposer, GovernedDiagnosticPlanner
from agent.diagnostic.current_orchestrator import build_current_diagnostic_orchestrator
from agent.response import render_deterministic

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "agent/generated/diagnostic_samples.json"

os.environ["PHASE6C_ALLOW_DIAGNOSTIC"] = "false"
planner = GovernedDiagnosticPlanner(
    ROOT,
    now_provider=lambda: datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc),
)
plan = planner.plan("为什么今天 Gross Sales 跌了这么多？")
result = build_current_diagnostic_orchestrator(ROOT).execute(plan)
envelope = DiagnosticEvidenceComposer(ROOT).compose(result)
draft = render_deterministic(envelope)
OUT.write_text(
    json.dumps(
        {
            "mode": "STATIC_DEFERRED_SAMPLE",
            "runtime_evidence": "DEFERRED",
            "plan": plan.to_dict(),
            "diagnostic": result.to_dict(),
            "response_envelope": envelope.to_dict(),
            "deterministic_answer": draft.answer,
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n",
    encoding="utf-8",
)
print(OUT)
