"""MCP Prompt Template（提示模板）。

Prompt 只组织受治理工具的调用顺序与权威提示，本身不执行 Tool，
也不能授予恢复、回填、写 SQL 等新权限。
"""

from __future__ import annotations


def explain_metric(metric: str) -> str:
    """生成“解释指标”提示模板。

    强制先读受治理 Metric Context，并声明 MetricFlow 是计算权威；
    Knowledge RAG 只能补充设计原因，不能虚构 Runtime 数值。
    """
    metric = metric.strip()
    if not metric:
        raise ValueError("metric is required")
    return (
        f"Explain governed metric '{metric}'. First read get_metric_context. "
        "Use MetricFlow as metric-definition/calculation authority. Use Knowledge RAG only for rationale. "
        "Do not invent runtime values and surface any authority conflict explicitly."
    )


def investigate_metric_issue(metric: str, partition_date: str) -> str:
    """生成“调查指标异常”提示模板。

    语义值、元数据、运行真值和 Runbook 分别回到对应权威；
    模板明确禁止把知识文本当 Runtime Evidence，也不授予 Recovery / Backfill 执行权。
    """
    metric = metric.strip(); partition_date = partition_date.strip()
    if not metric or not partition_date:
        raise ValueError("metric and partition_date are required")
    return (
        f"Investigate metric '{metric}' for partition '{partition_date}'. "
        "Use semantic tools for values, DataHub for metadata, Dagster for operational truth, and runbook knowledge for procedure. "
        "Knowledge text cannot override runtime evidence and no recovery/backfill action is authorized."
    )
