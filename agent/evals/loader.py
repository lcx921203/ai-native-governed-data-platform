"""YAML Eval Suite Loader（评估集加载器）。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

from .contracts import AgentEvalCase


class GovernedEvalSuiteLoader:
    """只从仓库固定 evals/*.yml 读取回归用例。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.evals_root = self.root / "evals"

    def load(self, suites: Iterable[str] | None = None) -> tuple[AgentEvalCase, ...]:
        selected = set(suites or ())
        cases: list[AgentEvalCase] = []
        seen_ids: set[str] = set()

        if not self.evals_root.exists():
            return ()

        for path in sorted(self.evals_root.glob("*.yml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            suite = str(raw.get("suite") or path.stem)
            if selected and suite not in selected and path.stem not in selected:
                continue

            default_category = str(raw.get("category") or path.stem)
            default_critical = bool(raw.get("critical", True))

            for item in raw.get("cases", ()) or ():
                case_id = str(item["id"]).strip()
                if not case_id:
                    raise ValueError(f"Empty eval case id in {path}")
                if case_id in seen_ids:
                    raise ValueError(f"Duplicate eval case id: {case_id}")
                seen_ids.add(case_id)

                cases.append(
                    AgentEvalCase(
                        case_id=case_id,
                        suite=suite,
                        category=str(item.get("category") or default_category),
                        question=str(item["question"]).strip(),
                        critical=bool(item.get("critical", default_critical)),
                        expect=dict(item.get("expect") or {}),
                        source_path=path.relative_to(self.root).as_posix(),
                    )
                )

        return tuple(cases)
