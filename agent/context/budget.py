"""Context Budget（上下文预算）工具。

这里的 token 数是跨模型的粗略估算，用于“进入 LLM 之前”的成本门控，
不是某个具体 tokenizer 的账单级精确值。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


class GovernedContextBudget:
    """统一计算和限制 Context 大小。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/context_loader_policy.yml").read_text(encoding="utf-8")
        )

    def estimate(self, payload: Any) -> int:
        if hasattr(payload, "to_dict"):
            payload = payload.to_dict()
        text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        chars_per_token = max(
            1,
            int(self.policy["limits"]["token_estimate_chars_per_token"]),
        )
        return (len(text) + chars_per_token - 1) // chars_per_token

    @property
    def initial_limit(self) -> int:
        return int(self.policy["limits"]["max_initial_estimated_tokens"])

    @property
    def expanded_limit(self) -> int:
        return int(self.policy["limits"]["max_expanded_estimated_tokens"])

    @property
    def max_expansion_steps(self) -> int:
        return int(self.policy["limits"]["max_expansion_steps"])
