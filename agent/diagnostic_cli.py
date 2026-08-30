from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent.diagnostic import DiagnosticEvidenceComposer, GovernedDiagnosticPlanner
from agent.diagnostic.current_orchestrator import build_current_diagnostic_orchestrator
from agent.response import render_deterministic, validate_answer_draft


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Governed Phase 6C diagnostic CLI")
    parser.add_argument("question")
    args = parser.parse_args()

    planner = GovernedDiagnosticPlanner(ROOT)
    plan = planner.plan(args.question)
    diagnostic = build_current_diagnostic_orchestrator(ROOT).execute(plan)
    envelope = DiagnosticEvidenceComposer(ROOT).compose(diagnostic)
    draft = render_deterministic(envelope)
    validate_answer_draft(envelope, draft)
    print(json.dumps({
        "diagnostic": diagnostic.to_dict(),
        "envelope": envelope.to_dict(),
        "answer": draft.answer,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
