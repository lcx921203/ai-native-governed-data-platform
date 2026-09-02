"""Semantic Context Repository Process Snapshot Cache 的契约测试。"""

from __future__ import annotations

from pathlib import Path

import yaml

from agent.context import (
    GovernedContextLoader,
    GovernedContextRepository,
)
from agent.context.cached_repository import (
    clear_semantic_snapshot_process_cache,
    semantic_snapshot_cache_info,
)
from agent.context.repository import (
    GovernedContextRepository as UncachedGovernedContextRepository,
)
from agent.tools.governed_metadata import GovernedMetadataTools


ROOT = Path(__file__).resolve().parents[1]
METRIC = "activity_net_sales"


def test_public_context_repository_uses_cached_production_implementation():
    """Package 级 Repository 必须指向 Cached 实现，避免 Tool 继续走旧热路径。"""

    assert (
        GovernedContextRepository.__module__
        == "agent.context.cached_repository"
    )


def test_cached_metric_context_matches_existing_repository_semantics():
    """Cache 只改变读取方式，不改变 Metric Context 业务语义。"""

    clear_semantic_snapshot_process_cache()

    uncached = UncachedGovernedContextRepository(
        ROOT
    ).metric_context(METRIC)
    cached = GovernedContextRepository(
        ROOT
    ).metric_context(METRIC)

    assert cached == uncached


def test_semantic_snapshot_is_shared_across_repository_instances():
    """Context Loader 与 Metadata Tool 的两个 Repository 实例只 Build 一次。"""

    clear_semantic_snapshot_process_cache()

    first = GovernedContextRepository(ROOT)
    first_summary = (
        first.warm_semantic_snapshot()
    )
    first_info = (
        semantic_snapshot_cache_info()
    )

    second = GovernedContextRepository(ROOT)
    second_summary = (
        second.warm_semantic_snapshot()
    )
    second_info = (
        semantic_snapshot_cache_info()
    )

    assert first_summary["metric_count"] > 0
    assert (
        second_summary["metric_count"]
        == first_summary["metric_count"]
    )

    assert first_info["misses"] == 1
    assert first_info["currsize"] == 1

    # 第二个实例共享同一个 Repo Root Snapshot，不能再 Build。
    assert second_info["misses"] == 1
    assert second_info["hits"] >= 1
    assert second_info["currsize"] == 1


def test_metric_context_payload_is_copy_isolated_from_process_snapshot():
    """调用方修改返回 Payload 不能污染后续请求的共享 Snapshot。"""

    clear_semantic_snapshot_process_cache()
    repository = GovernedContextRepository(
        ROOT
    )

    first = repository.metric_context(
        METRIC
    )
    assert first is not None
    first["definition"][
        "__test_mutation__"
    ] = True
    first["related_models"].append(
        "__test_model__"
    )

    second = repository.metric_context(
        METRIC
    )
    assert second is not None

    assert (
        "__test_mutation__"
        not in second["definition"]
    )
    assert (
        "__test_model__"
        not in second["related_models"]
    )


def test_context_loader_warms_snapshot_before_request_stage_execution():
    """Context Loader 初始化即预热，API Readiness 构造 Runtime 时完成 Cold Build。"""

    clear_semantic_snapshot_process_cache()

    loader = GovernedContextLoader(
        ROOT
    )
    after_loader = (
        semantic_snapshot_cache_info()
    )

    assert after_loader["misses"] == 1
    assert after_loader["currsize"] == 1

    # GovernedMetadataTools 使用 package 级 Cached Repository。
    # 第一次 Tool Lookup 只能命中已预热 Snapshot，不能出现第二次 Build。
    tools = GovernedMetadataTools(
        ROOT
    )
    result = tools.get_metric_context(
        metric=METRIC
    )
    after_tool = (
        semantic_snapshot_cache_info()
    )

    assert result["status"] == "ANSWERED"
    assert after_tool["misses"] == 1
    assert after_tool["hits"] >= 1

    # 保持引用，避免静态检查器把初始化误认为无效。
    assert loader.repo.metric_context(
        METRIC
    ) is not None


def test_cache_policy_keeps_static_semantics_out_of_request_path():
    """Cache Policy 必须明确部署期快照边界与隐私边界。"""

    policy = yaml.safe_load(
        (
            ROOT
            / "agent/contracts/context_repository_cache_policy.yml"
        ).read_text(
            encoding="utf-8"
        )
    )

    assert policy["version"] == 1
    assert (
        policy["mode"]
        == "process_scoped_immutable_snapshot"
    )

    principles = policy["principles"]
    assert (
        principles[
            "source_of_truth_remains_dbt_metricflow_and_git"
        ]
        is True
    )
    assert (
        principles[
            "semantic_snapshot_is_warmed_before_api_readiness"
        ]
        is True
    )
    assert (
        principles[
            "request_path_does_not_poll_source_files"
        ]
        is True
    )
    assert (
        principles[
            "contract_change_requires_new_deploy_or_process_restart"
        ]
        is True
    )

    for privacy_key in (
        "prompt_is_never_cached",
        "answer_is_never_cached",
        "tenant_or_subject_is_never_cached",
        "jwt_or_bearer_token_is_never_cached",
        "runtime_query_result_is_never_cached",
    ):
        assert (
            principles[privacy_key]
            is True
        )
