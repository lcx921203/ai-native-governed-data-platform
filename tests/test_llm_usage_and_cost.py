"""Live Renderer Usage + Cost Observability 契约测试。

不访问网络；Fake OpenAI Response 模拟 Provider 返回的 usage 结构。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.llm import LLMUsageEvent, capture_llm_usage
from agent.llm.providers.openai_responses import (
    OpenAIProviderConfig,
    OpenAIProviderUnavailable,
    OpenAIResponsesRenderer,
)
from agent.observability import GovernedLLMPricing, GovernedRunObserver
from agent.response import AnswerStatus, Claim, ClaimKind, ResponseEnvelope
from agent.runtime.contracts import AgentRunResult, AgentRuntimeStatus
from agent.runtime.factory import build_runtime_from_env
from agent.tenancy import RequestContext


ROOT = Path(__file__).resolve().parents[1]


class FakeResponses:
    def __init__(self, response):
        self.response = response

    def create(self, **kwargs):
        return self.response


class FakeClient:
    def __init__(self, response):
        self.responses = FakeResponses(response)


def envelope():
    return ResponseEnvelope(
        question="gross_sales 的定义是什么？",
        intent="METRIC_DEFINITION",
        status=AnswerStatus.ANSWERED,
        claims=[
            Claim(
                id="C01",
                kind=ClaimKind.DEFINITION,
                text="Gross Sales is governed by MetricFlow.",
                evidence="STATIC_CONTRACT",
            )
        ],
    )


def fake_response():
    return SimpleNamespace(
        id="resp-test",
        model="gpt-5.6-terra",
        status="completed",
        output=[],
        output_text=(
            '{"answer":"Gross Sales is governed by MetricFlow.",'
            '"used_claim_ids":["C01"],'
            '"acknowledged_limitations":[]}'
        ),
        usage=SimpleNamespace(
            input_tokens=1000,
            output_tokens=100,
            total_tokens=1100,
            input_tokens_details=SimpleNamespace(
                cached_tokens=200,
                cache_write_tokens=100,
            ),
            output_tokens_details=SimpleNamespace(
                reasoning_tokens=40,
            ),
        ),
    )


def test_openai_renderer_records_actual_provider_usage():
    renderer = OpenAIResponsesRenderer(
        root=ROOT,
        config=OpenAIProviderConfig(
            model="gpt-5.6-terra",
            max_output_tokens=1200,
        ),
        client=FakeClient(fake_response()),
    )

    with capture_llm_usage() as events:
        draft = renderer.render(envelope())

    assert draft.used_claim_ids == ("C01",)
    assert len(events) == 1
    event = events[0]
    assert event.provider == "openai"
    assert event.model == "gpt-5.6-terra"
    assert event.input_tokens == 1000
    assert event.cached_input_tokens == 200
    assert event.cache_write_tokens == 100
    assert event.output_tokens == 100
    assert event.reasoning_tokens == 40
    assert event.total_tokens == 1100


def test_terra_pricing_uses_cached_and_cache_write_rates():
    event = LLMUsageEvent(
        provider="openai",
        model="gpt-5.6-terra",
        input_tokens=1000,
        cached_input_tokens=200,
        cache_write_tokens=100,
        output_tokens=100,
        reasoning_tokens=40,
        total_tokens=1100,
    )

    result = GovernedLLMPricing(ROOT).price([event])

    assert result.known is True
    # uncached 700 * $2/M + cached 200 * $0.20/M
    # + cache-write 100 * ($2 * 1.25)/M + output 100 * $12/M
    assert result.total_cost_usd == pytest.approx(0.00289, abs=1e-12)


def test_long_context_pricing_stays_unknown_in_v1():
    event = LLMUsageEvent(
        provider="openai",
        model="gpt-5.6-terra",
        input_tokens=272001,
        output_tokens=100,
        total_tokens=272101,
    )

    result = GovernedLLMPricing(ROOT).price([event])

    assert result.known is False
    assert result.total_cost_usd is None
    assert "long-context cost is intentionally unknown" in result.warnings[0]


def test_observer_sets_cost_per_answer_only_for_validated_answer():
    event = LLMUsageEvent(
        provider="openai",
        model="gpt-5.6-terra",
        input_tokens=1000,
        output_tokens=100,
        total_tokens=1100,
    )
    context = RequestContext.local_compat()

    answered = AgentRunResult(
        question="x",
        status=AgentRuntimeStatus.ANSWERED,
        answer_validated=True,
    )
    observed = GovernedRunObserver(ROOT).attach(
        answered,
        context,
        total_duration_ms=10,
        llm_usage_events=[event],
    )
    assert observed.observability.cost.monetary_cost_known is True
    assert observed.observability.cost.provider_cost_usd is not None
    assert (
        observed.observability.cost.cost_per_answer_usd
        == observed.observability.cost.provider_cost_usd
    )

    failed = AgentRunResult(
        question="x",
        status=AgentRuntimeStatus.ERROR,
        answer_validated=False,
    )
    failed = GovernedRunObserver(ROOT).attach(
        failed,
        context,
        total_duration_ms=10,
        llm_usage_events=[event],
    )
    assert failed.observability.cost.provider_cost_usd is not None
    assert failed.observability.cost.cost_per_answer_usd is None


def test_runtime_factory_defaults_to_deterministic(monkeypatch):
    monkeypatch.delenv("AGENT_RENDERER_MODE", raising=False)
    runtime = build_runtime_from_env(ROOT)

    assert runtime is not None
    assert callable(runtime.renderer)


def test_runtime_factory_openai_preserves_existing_live_gate(monkeypatch):
    monkeypatch.setenv("AGENT_RENDERER_MODE", "openai")
    monkeypatch.setenv("PHASE4G_ALLOW_OPENAI_CALL", "false")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(OpenAIProviderUnavailable):
        build_runtime_from_env(ROOT)
