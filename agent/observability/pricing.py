"""受治理 LLM Pricing Calculator（价格计算器）。

只计算满足以下条件的 Event：
1. Provider / Model 在 Pricing Catalog 中；
2. Provider 返回实际 usage；
3. input token 没超过当前 V1 可安全计价阈值；
4. cached / cache-write token 明细没有超过 input_tokens。

否则成本保持 unknown，不把未知误写成 $0。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable

import yaml

from agent.llm import LLMUsageEvent


@dataclass(frozen=True)
class PricingResult:
    known: bool
    total_cost_usd: float | None
    breakdown: tuple[dict, ...] = ()
    warnings: tuple[str, ...] = ()


class GovernedLLMPricing:
    """从仓库 Pricing Catalog 计算 Provider Usage 成本。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.catalog = yaml.safe_load(
            (self.root / "agent/contracts/llm_pricing.yml").read_text(
                encoding="utf-8"
            )
        )

    def price(self, events: Iterable[LLMUsageEvent]) -> PricingResult:
        events = tuple(events)
        if not events:
            return PricingResult(
                known=False,
                total_cost_usd=None,
                warnings=("No provider usage event was recorded.",),
            )

        rows: list[dict] = []
        total = Decimal("0")

        for event in events:
            row, warning = self._price_event(event)
            if warning:
                return PricingResult(
                    known=False,
                    total_cost_usd=None,
                    breakdown=tuple(rows),
                    warnings=(warning,),
                )
            assert row is not None
            rows.append(row)
            total += Decimal(str(row["cost_usd"]))

        return PricingResult(
            known=True,
            total_cost_usd=float(total),
            breakdown=tuple(rows),
        )

    def _price_event(
        self,
        event: LLMUsageEvent,
    ) -> tuple[dict | None, str | None]:
        provider = (self.catalog.get("providers") or {}).get(event.provider)
        if provider is None:
            return None, f"No pricing catalog exists for provider={event.provider!r}."

        model_id, model = self._resolve_model(provider, event.model)
        if model is None:
            return None, (
                f"No governed pricing entry exists for provider={event.provider!r}, "
                f"model={event.model!r}."
            )

        threshold = int(provider["max_standard_pricing_input_tokens"])
        if event.input_tokens > threshold:
            return None, (
                f"input_tokens={event.input_tokens} exceeds V1 standard-pricing "
                f"threshold={threshold}; long-context cost is intentionally unknown."
            )

        cached = max(0, int(event.cached_input_tokens))
        cache_write = max(0, int(event.cache_write_tokens))
        input_tokens = max(0, int(event.input_tokens))
        output_tokens = max(0, int(event.output_tokens))

        if cached + cache_write > input_tokens:
            return None, (
                "Provider usage details are internally inconsistent: "
                "cached_input_tokens + cache_write_tokens > input_tokens."
            )

        uncached = input_tokens - cached - cache_write
        million = Decimal("1000000")

        input_rate = Decimal(str(model["input"]))
        cached_rate = Decimal(str(model["cached_input"]))
        output_rate = Decimal(str(model["output"]))
        write_multiplier = Decimal(str(provider["cache_write_multiplier"]))

        input_cost = Decimal(uncached) / million * input_rate
        cached_cost = Decimal(cached) / million * cached_rate
        cache_write_cost = (
            Decimal(cache_write) / million * input_rate * write_multiplier
        )
        output_cost = Decimal(output_tokens) / million * output_rate
        event_cost = input_cost + cached_cost + cache_write_cost + output_cost

        return (
            {
                "provider": event.provider,
                "model": model_id,
                "reported_model": event.model,
                "input_tokens": input_tokens,
                "uncached_input_tokens": uncached,
                "cached_input_tokens": cached,
                "cache_write_tokens": cache_write,
                "output_tokens": output_tokens,
                "reasoning_tokens": int(event.reasoning_tokens),
                "input_cost_usd": float(input_cost),
                "cached_input_cost_usd": float(cached_cost),
                "cache_write_cost_usd": float(cache_write_cost),
                "output_cost_usd": float(output_cost),
                "cost_usd": float(event_cost),
            },
            None,
        )

    @staticmethod
    def _resolve_model(provider: dict, requested: str):
        for model_id, config in (provider.get("models") or {}).items():
            aliases = set(str(x) for x in config.get("aliases", ()))
            if requested == model_id or requested in aliases:
                return model_id, config
        return requested, None
