"""执行 dbt，同时保留 Dagster Event 与 dbt 自己能够证明的失败语义。

Dagster 继续接收 dbt 事件流；非零退出时再读取 ``run_results.json`` 做结构化分类。
本适配器不从 stderr / message 文本猜失败原因，也不扩大 Step Retry 权限。
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

import dagster as dg
from dagster_dbt import DbtCliResource

from .failure_classification import (
    DbtFailureObservation,
    FailureClassSource,
    allow_step_retry,
    classify_dbt_failure,
    failure_class_tags,
)


def _safe_run_results(invocation: Any) -> dict[str, Any] | None:
    """安全读取一次 dbt invocation 的 ``run_results.json``。
    
    拿到字典就返回；读取失败或格式不符返回 None，让分类器 Fail Closed，而不是让“读证据失败”掩盖原始 dbt 失败。
    """
    try:
        artifact = invocation.get_artifact("run_results.json")
    except Exception:
        return None
    return artifact if isinstance(artifact, dict) else None


def _record_dbt_failure_tags(
    context: dg.AssetExecutionContext,
    *,
    failure_class,
    source: FailureClassSource,
    component: str,
    reason_code: str,
) -> None:
    """把 dbt 失败分类结果写入当前 Dagster Run Tags。
    
    这些 Tag 是跨 Run Recovery 的结构化输入；持久化异常只记 warning，不替代原始 dbt failure。
    """
    tags = failure_class_tags(
        failure_class,
        source=source,
        component=component,
        reason_code=reason_code,
        stage=component,
    )
    try:
        context.instance.add_run_tags(context.run_id, tags)
    except Exception as exc:
        context.log.warning(
            "Could not persist dbt failure classification for run %s: %s",
            context.run_id,
            exc,
        )


def execute_classified_dbt(
    *,
    context: dg.AssetExecutionContext,
    dbt: DbtCliResource,
    args: Sequence[str],
) -> Iterator:
    """执行一次 dbt 命令并保留 Dagster Events，同时对非零结果做结构化分类。
    
    先流式产出 dbt 事件；成功就返回。失败时读取 run_results、写 Failure Tags，再按 FailureClass 决定是否允许有界 Step Retry。
    """

    if not args:
        raise ValueError("dbt args are required")

    invocation = dbt.cli(list(args), context=context, raise_on_error=False)
    yield from invocation.stream()

    if invocation.is_successful():
        return

    classification = classify_dbt_failure(
        DbtFailureObservation(
            command_name=str(args[0]),
            command_succeeded=False,
            run_results=_safe_run_results(invocation),
        )
    )
    component = f"dbt:{args[0]}"
    _record_dbt_failure_tags(
        context,
        failure_class=classification.failure_class,
        source=classification.source,
        component=component,
        reason_code=classification.reason_code,
    )

    raise dg.Failure(
        description=(
            f"dbt {' '.join(args)} failed; structured classification="
            f"{classification.failure_class.value} ({classification.reason_code})"
        ),
        metadata={
            "failure_class": classification.failure_class.value,
            "classification_source": classification.source.value,
            "reason_code": classification.reason_code,
            "failed_test_count": len(classification.failed_test_ids),
        },
        allow_retries=allow_step_retry(classification.failure_class),
    )
