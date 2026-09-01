"""Provider-neutral LLM Usage（大模型用量）采集。

目标：
- Renderer 只负责记录 Provider 实际返回的 usage；
- Runtime 用 ContextVar 隔离并发请求；
- Observability 再根据受治理 Pricing Catalog 计算成本；
- 不把 API Key / Prompt 原文写入 Usage Event。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass(frozen=True)
class LLMUsageEvent:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    response_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "response_id": self.response_id,
        }


_CURRENT_USAGE_EVENTS: ContextVar[list[LLMUsageEvent] | None] = ContextVar(
    "governed_agent_llm_usage_events",
    default=None,
)


def record_llm_usage(event: LLMUsageEvent) -> None:
    """只在当前 Runtime 已打开 capture scope 时记录。"""

    events = _CURRENT_USAGE_EVENTS.get()
    if events is not None:
        events.append(event)


@contextmanager
def capture_llm_usage() -> Iterator[list[LLMUsageEvent]]:
    """为一次 Runtime Run 创建独立 Usage Collector。"""

    events: list[LLMUsageEvent] = []
    token = _CURRENT_USAGE_EVENTS.set(events)
    try:
        yield events
    finally:
        _CURRENT_USAGE_EVENTS.reset(token)
