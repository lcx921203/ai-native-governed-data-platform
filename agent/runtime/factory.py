"""Unified Runtime 的部署工厂。

默认 deterministic。
只有同时满足：
- AGENT_RENDERER_MODE=openai
- 既有 PHASE4G_ALLOW_OPENAI_CALL=true
- OPENAI_API_KEY 已配置
时才实例化 Live OpenAI Renderer。

Live Provider 失败时不自动降级 deterministic，避免隐藏生产故障。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from agent.llm.providers.openai_responses import OpenAIResponsesRenderer

from .runtime import GovernedAgentRuntime


def build_runtime_from_env(
    project_root: Path | str,
) -> GovernedAgentRuntime:
    root = Path(project_root).resolve()
    policy = yaml.safe_load(
        (root / "agent/contracts/llm_runtime_policy.yml").read_text(
            encoding="utf-8"
        )
    )

    env_name = str(policy["renderer_mode_env"])
    mode = (
        os.getenv(env_name, str(policy["default_mode"]))
        .strip()
        .lower()
    )
    allowed = {str(x) for x in policy["allowed_modes"]}
    if mode not in allowed:
        raise ValueError(
            f"Unsupported {env_name}={mode!r}; allowed={sorted(allowed)}"
        )

    if mode == "deterministic":
        return GovernedAgentRuntime(root)

    renderer = OpenAIResponsesRenderer(root=root)
    return GovernedAgentRuntime(
        root,
        renderer=renderer.render,
    )
