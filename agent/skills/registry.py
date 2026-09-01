"""Governed Analytics Skill Registry（受治理分析技能注册表）。

职责：
- 只从仓库固定 ``skills/**/*.yml`` 读取 Skill；
- 只允许 ACTIVE Skill；
- 只在 Router 已经给出 ANALYSIS Intent 后解析具体 Skill；
- 依据受治理 metric + 明确方向 marker 做确定性匹配；
- 不调用 LLM，不接受用户传入任意 Skill 文件路径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .contracts import AnalyticsSkill, SkillResolution, SkillResolutionStatus


class GovernedSkillRegistry:
    """加载并解析仓库内受治理 Analytics Skills。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.skills_root = self.root / "skills"
        self._skills = self._load_skills()

    @staticmethod
    def _enum_value(value: Any) -> str:
        raw = getattr(value, "value", value)
        return str(raw)

    def _load_skills(self) -> tuple[AnalyticsSkill, ...]:
        """从固定仓库目录加载 Skill；不接受运行时任意路径。"""
        loaded: list[AnalyticsSkill] = []
        if not self.skills_root.exists():
            return ()

        for path in sorted(self.skills_root.glob("**/*.yml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(raw, dict):
                continue

            match = raw.get("match") or {}
            requirements = raw.get("requirements") or {}
            skill = AnalyticsSkill(
                skill_id=str(raw.get("id", "")).strip(),
                version=int(raw.get("version", 1)),
                domain=str(raw.get("domain", "")).strip(),
                status=str(raw.get("status", "INACTIVE")).strip(),
                description=str(raw.get("description", "")).strip(),
                intents=tuple(str(x) for x in match.get("intents", ())),
                metrics=tuple(str(x) for x in match.get("metrics", ())),
                direction=str(match.get("direction", "")).strip(),
                direction_markers=tuple(str(x) for x in match.get("direction_markers", ())),
                required_metrics=tuple(str(x) for x in requirements.get("required_metrics", ())),
                optional_metrics=tuple(str(x) for x in requirements.get("optional_metrics", ())),
                dimensions=tuple(str(x) for x in requirements.get("dimensions", ())),
                analysis_steps=tuple(dict(x) for x in raw.get("analysis_steps", ())),
                guardrails=dict(raw.get("guardrails") or {}),
                authority={str(k): str(v) for k, v in (raw.get("authority") or {}).items()},
                source_path=path.relative_to(self.root).as_posix(),
            )
            if skill.skill_id:
                loaded.append(skill)
        return tuple(loaded)

    def list_active(self) -> tuple[AnalyticsSkill, ...]:
        """列出当前 ACTIVE Skill，便于 Context Loader / Eval 使用。"""
        return tuple(skill for skill in self._skills if skill.active)

    def resolve(self, route: Any) -> SkillResolution:
        """根据 Router 的 ANALYSIS 结果确定唯一 Skill。

        Router 已负责“这是不是分析问题”；Registry 只负责“用哪个分析方法”。
        如果没有唯一匹配，Fail Closed，不让 LLM 自由选择或拼装多个 Skill。
        """
        intent = self._enum_value(getattr(route, "intent", "UNKNOWN"))
        if intent != "ANALYSIS":
            return SkillResolution(
                SkillResolutionStatus.BLOCKED,
                warnings=("Skill Registry only accepts ANALYSIS routes.",),
            )

        target_id = str(getattr(route, "target_id", "") or "")
        target_metrics = {item for item in target_id.split(",") if item}
        question = str(getattr(route, "question", "") or "")
        q = question.casefold()

        candidates: list[AnalyticsSkill] = []
        for skill in self.list_active():
            if intent not in skill.intents:
                continue
            if target_metrics and not target_metrics.intersection(skill.metrics):
                continue
            if skill.direction_markers and not any(marker.casefold() in q for marker in skill.direction_markers):
                continue
            candidates.append(skill)

        if not candidates:
            return SkillResolution(
                SkillResolutionStatus.NOT_FOUND,
                warnings=(
                    f"No ACTIVE governed skill matched intent={intent}, target={target_id}.",
                ),
            )

        if len(candidates) > 1:
            ids = tuple(skill.skill_id for skill in candidates)
            return SkillResolution(
                SkillResolutionStatus.AMBIGUOUS,
                candidate_ids=ids,
                warnings=("More than one governed skill matched; explicit disambiguation is required.",),
            )

        return SkillResolution(
            SkillResolutionStatus.RESOLVED,
            skill=candidates[0],
            candidate_ids=(candidates[0].skill_id,),
        )
