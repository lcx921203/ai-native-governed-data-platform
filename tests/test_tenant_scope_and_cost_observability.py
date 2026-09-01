"""Multi-Tenant Request Context + Cost Observability 契约测试。"""

from pathlib import Path

from agent.observability import GovernedRunObserver
from agent.router import DeterministicToolRouter
from agent.tenancy import (
    DimensionScope,
    GovernedRequestAuthorizer,
    RequestContext,
)


ROOT = Path(__file__).resolve().parents[1]


def analyst_context(**overrides):
    data = dict(
        tenant_id="tenant-west",
        subject="user-1",
        scopes=frozenset(
            {
                "commerce:semantic:read",
                "commerce:metadata:read",
                "commerce:operations:read",
                "commerce:knowledge:read",
            }
        ),
        allowed_metrics=frozenset({"gross_sales", "order_count"}),
        allowed_datasets=frozenset({"orders"}),
        allowed_entities=frozenset({"*"}),
        # 下面两个 Region 测试的自然语言都包含“美国”，Planner 会产生：
        # store__country=US + store__region=<West/South>。
        # 既然这组 Fixture 的目标是验证 Region Tenant Scope 冲突/去重，
        # 就必须显式允许 Planner 合法生成的 country filter，
        # 否则新的对象级 Dimension Allowlist 会更早按设计 Fail Closed。
        allowed_dimensions=frozenset({"store__country", "store__region"}),
        allowed_knowledge_scopes=frozenset({"architecture"}),
        dimension_scopes=(
            DimensionScope("store__region", ("West",)),
        ),
    )
    data.update(overrides)
    return RequestContext(**data)


def test_metric_authorization_passes_for_allowed_metric():
    route = DeterministicToolRouter(ROOT).plan(
        "2026-08-05 gross_sales 是多少？"
    )
    decision = GovernedRequestAuthorizer(ROOT).authorize(
        route,
        analyst_context(),
    )

    assert decision.allowed is True
    assert decision.required_scopes == ("commerce:semantic:read",)


def test_metric_authorization_blocks_metric_outside_object_scope():
    route = DeterministicToolRouter(ROOT).plan(
        "2026-08-05 activity_net_sales 是多少？"
    )
    decision = GovernedRequestAuthorizer(ROOT).authorize(
        route,
        analyst_context(),
    )

    assert decision.allowed is False
    assert "outside allowed metric scope" in decision.warnings[0]


def test_runtime_diagnosis_requires_metadata_and_operations_scope():
    route = DeterministicToolRouter(ROOT).plan(
        "为什么 orders 昨天没更新？"
    )
    context = analyst_context(
        scopes=frozenset({"commerce:metadata:read"}),
    )
    decision = GovernedRequestAuthorizer(ROOT).authorize(route, context)

    assert decision.allowed is False
    assert "commerce:operations:read" in decision.warnings[0]


def test_multi_value_dimension_scope_is_not_silently_downgraded():
    from agent.semantic_query import GovernedSemanticQueryPlanner
    from agent.tenancy import GovernedRequestScopeEnforcer

    semantic = GovernedSemanticQueryPlanner(ROOT).plan(
        metric="gross_sales",
        question="2026-08-05 gross_sales 是多少？",
    )
    context = analyst_context(
        dimension_scopes=(
            DimensionScope("store__region", ("West", "South")),
        )
    )

    scoped, warning = GovernedRequestScopeEnforcer(ROOT).apply(
        semantic,
        context,
    )

    assert scoped is semantic
    assert "exactly one canonical value" in warning


def test_conflicting_user_filter_and_tenant_scope_is_blocked():
    from agent.semantic_query import GovernedSemanticQueryPlanner
    from agent.tenancy import GovernedRequestScopeEnforcer

    semantic = GovernedSemanticQueryPlanner(ROOT).plan(
        metric="gross_sales",
        question="2026-08-05 美国南部地区 gross_sales 是多少？",
    )
    context = analyst_context(
        dimension_scopes=(
            DimensionScope("store__region", ("West",)),
        )
    )

    _, warning = GovernedRequestScopeEnforcer(ROOT).apply(
        semantic,
        context,
    )
    assert "conflicts with mandatory tenant scope" in warning


def test_same_user_filter_and_tenant_scope_is_deduplicated():
    from agent.semantic_query import GovernedSemanticQueryPlanner
    from agent.tenancy import GovernedRequestScopeEnforcer

    semantic = GovernedSemanticQueryPlanner(ROOT).plan(
        metric="gross_sales",
        question="2026-08-05 美国西部地区 gross_sales 是多少？",
    )
    scoped, warning = GovernedRequestScopeEnforcer(ROOT).apply(
        semantic,
        analyst_context(),
    )

    assert warning is None
    assert scoped.spec is not None
    region_filters = [
        item for item in scoped.spec.filters
        if item.dimension == "store__region"
    ]
    assert len(region_filters) == 1
    assert region_filters[0].value == "West"


def test_cost_observer_does_not_invent_dollar_cost():
    from agent.runtime.contracts import AgentRunResult, AgentRuntimeStatus

    result = AgentRunResult(
        question="x",
        status=AgentRuntimeStatus.ANSWERED,
        answer_validated=True,
    )
    observed = GovernedRunObserver(ROOT).attach(
        result,
        analyst_context(),
        total_duration_ms=12.5,
    )

    cost = observed.observability.cost
    assert cost.total_duration_ms == 12.5
    assert cost.provider_cost_usd is None
    assert cost.cost_per_answer_usd is None
    assert cost.monetary_cost_known is False
    assert observed.observability.tenant_id == "tenant-west"
