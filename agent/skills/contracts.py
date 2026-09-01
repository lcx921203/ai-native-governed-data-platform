"""Analytics Skills（分析技能）的结构化契约。

Skill 不拥有指标公式、Join 口径或业务事实；它只描述：
“遇到某一类受治理分析问题，应该按照什么顺序分析”。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SkillResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    NOT_FOUND = "NOT_FOUND"
    AMBIGUOUS = "AMBIGUOUS"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class AnalyticsSkill:
    skill_id: str
    version: int
    domain: str
    status: str
    description: str
    intents: tuple[str, ...]
    metrics: tuple[str, ...]
    direction: str
    direction_markers: tuple[str, ...]
    required_metrics: tuple[str, ...]
    optional_metrics: tuple[str, ...]
    dimensions: tuple[str, ...]
    analysis_steps: tuple[dict[str, Any], ...]
    guardrails: dict[str, Any]
    authority: dict[str, str]
    source_path: str

    @property
    def active(self) -> bool:
        return self.status.upper() == "ACTIVE"


@dataclass(frozen=True)
class SkillResolution:
    status: SkillResolutionStatus
    skill: AnalyticsSkill | None = None
    candidate_ids: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "skill_id": self.skill.skill_id if self.skill else None,
            "candidate_ids": list(self.candidate_ids),
            "warnings": list(self.warnings),
        }
