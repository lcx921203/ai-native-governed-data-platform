"""受治理 Agent 的确定性工具路由。

业务逻辑：先从受治理别名与意图标记解析 Metric / Dataset / Entity / Dimension，再生成有限工具计划。
工程边界：拒绝任意 SQL、raw predicate 与未验证目标；Router 只规划，不拥有数据计算或生产写入权。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import yaml

from .contracts import Intent, PlanStatus, ToolPlan, ToolStep


_SQL_RE = re.compile(r"\b(select|delete|drop|truncate|update|insert|merge|alter|create)\b", re.I)
_DATE_RE = re.compile(r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日)")


_ROOT = Path(__file__).resolve().parents[2]
_ROUTING_CONTRACT = yaml.safe_load((_ROOT / "agent/contracts/intent_routing.yml").read_text(encoding="utf-8"))
METRIC_ALIASES = {key: tuple([key, *value]) for key, value in _ROUTING_CONTRACT["metric_aliases"].items()}


class DeterministicToolRouter:
    """把自然语言问题绑定到受治理 Intent 与有限 ToolPlan。
    
    输入是用户问题；输出是 PLANNED / NEEDS_DISCOVERY / BLOCKED 等明确状态。
    框架边界：这里只做确定性路由，不调用 LLM 猜测实体，也不直接执行工具。
    """
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.contract = yaml.safe_load(
            (self.root / "agent/contracts/intent_routing.yml").read_text(encoding="utf-8")
        )

    @staticmethod
    def _matches(question: str, aliases: Iterable[str]) -> tuple[str, ...]:
        """在问题中匹配一组受治理别名，返回实际命中的别名集合。"""
        q = question.casefold()
        return tuple(alias for alias in aliases if str(alias).casefold() in q)

    def _resolve_many(self, question: str, mapping_key: str) -> list[tuple[str, str]]:
        """按配置中的 alias 顺序解析多个受治理目标，并按文本出现位置稳定排序。"""
        q = question.casefold()
        found: list[tuple[int, int, str, str]] = []
        for target, aliases in self.contract[mapping_key].items():
            candidates = [target, *aliases]
            positions = []
            for alias in candidates:
                alias_text = str(alias)
                pos = q.find(alias_text.casefold())
                if pos >= 0:
                    positions.append((pos, -len(alias_text), alias_text))
            if positions:
                pos, neg_len, alias = min(positions)
                found.append((pos, neg_len, target, alias))
        found.sort(key=lambda item: (item[0], item[1], item[2]))
        return [(target, alias) for _, _, target, alias in found]

    def plan(self, question: str) -> ToolPlan:
        """根据问题生成受治理工具计划。
        
        先阻断 SQL/raw predicate，再按 runtime、lineage、governance、metric query、knowledge 等优先级路由。
        输出 ToolPlan，不在此处执行查询。
        """
        q = question.strip()
        q_lower = q.casefold()
        markers = self.contract["markers"]

        if _SQL_RE.search(q) or "--where" in q_lower or "{{ dimension(" in q_lower:
            return ToolPlan(
                q,
                Intent.UNKNOWN,
                PlanStatus.BLOCKED,
                warnings=["Arbitrary SQL / raw predicates are outside the governed Agent tool surface."],
            )

        metrics = self._resolve_many(q, "metric_aliases")
        datasets = self._resolve_many(q, "dataset_aliases")
        entities = self._resolve_many(q, "entity_aliases")
        dimensions = self._resolve_many(q, "dimension_aliases")

        has_metric_definition_marker = bool(self._matches(q, markers["metric_definition"]))
        has_dimension_values_marker = bool(self._matches(q, markers["dimension_values"]))
        has_lineage_marker = bool(self._matches(q, markers["lineage"]))
        has_runtime_marker = bool(self._matches(q, markers["runtime"]))
        has_governance_marker = bool(self._matches(q, markers["dataset_governance"]))
        has_entity_marker = bool(self._matches(q, markers["entity_context"]))
        has_query_marker = bool(self._matches(q, markers["metric_query"]))
        knowledge_runbook_matches = self._matches(q, markers["knowledge_runbook"])
        knowledge_design_matches = self._matches(q, markers["knowledge_design"])
        knowledge_glossary_matches = self._matches(q, markers["knowledge_glossary"])
        has_date = _DATE_RE.search(q) is not None

        # Dimension values are metric-path dependent. Do not invent metric context.
        if has_dimension_values_marker and dimensions:
            if not metrics:
                return ToolPlan(
                    q,
                    Intent.DIMENSION_VALUE_DISCOVERY,
                    PlanStatus.NEEDS_DISCOVERY,
                    target_kind="dimension",
                    target_id=dimensions[0][0],
                    target_match=dimensions[0][1],
                    warnings=["Dimension-value discovery requires an explicit governed metric context."],
                )
            metric_ids = [item[0] for item in metrics][:3]
            dimension = dimensions[0]
            return ToolPlan(
                q,
                Intent.DIMENSION_VALUE_DISCOVERY,
                PlanStatus.PLANNED,
                target_kind="dimension",
                target_id=dimension[0],
                target_match=dimension[1],
                steps=[
                    ToolStep(
                        "get_dimension_values",
                        {"metrics": metric_ids, "dimension": dimension[0], "question": q, "limit": 25},
                        "Discover only governed values for the resolved metric/dimension path.",
                    )
                ],
            )

        # Runtime / lineage / dataset governance intentionally win over generic metric words.
        if datasets and has_runtime_marker:
            dataset, alias = datasets[0]
            return ToolPlan(
                q,
                Intent.RUNTIME_DIAGNOSIS,
                PlanStatus.PLANNED,
                "dataset",
                dataset,
                alias,
                [
                    ToolStep("get_dataset_context", {"dataset": dataset}, "Read exact governed Dataset context."),
                    ToolStep("get_runtime_context", {"dataset": dataset}, "Read runtime facts only when runtime evidence exists."),
                ],
            )
        if datasets and has_lineage_marker:
            dataset, alias = datasets[0]
            direction = "downstream" if any(x in q_lower for x in ["下游", "downstream"]) else "upstream"
            return ToolPlan(
                q,
                Intent.LINEAGE_QUERY,
                PlanStatus.PLANNED,
                "dataset",
                dataset,
                alias,
                [ToolStep("get_lineage_context", {"dataset": dataset, "direction": direction, "max_hops": 2}, "Read bounded lineage only.")],
            )
        if datasets and has_governance_marker:
            dataset, alias = datasets[0]
            return ToolPlan(
                q,
                Intent.DATASET_GOVERNANCE,
                PlanStatus.PLANNED,
                "dataset",
                dataset,
                alias,
                [ToolStep("get_dataset_context", {"dataset": dataset}, "Read governed Dataset identity/ownership/classification.")],
            )

        if entities and has_entity_marker:
            entity, alias = entities[0]
            return ToolPlan(
                q,
                Intent.ENTITY_CONTEXT,
                PlanStatus.PLANNED,
                "entity",
                entity,
                alias,
                [ToolStep("get_entity_context", {"entity": entity}, "Read governed entity meaning and semantic-model participation.")],
            )

        # An explicit metric-definition question that does not resolve a governed metric must
        # never fall through to a similarly-named entity.
        if has_metric_definition_marker and not metrics:
            return self._discovery(q, "Explicit metric intent was detected, but no governed metric was resolved.")

        if metrics and (has_date or has_query_marker) and not has_metric_definition_marker:
            metric_ids = [item[0] for item in metrics][:3]
            tool = "query_semantic_metrics" if len(metric_ids) > 1 else "query_semantic_metric"
            arguments = {"question": q, "limit": 20}
            if len(metric_ids) > 1:
                arguments["metrics"] = metric_ids
            else:
                arguments["metric"] = metric_ids[0]
            return ToolPlan(
                q,
                Intent.METRIC_QUERY,
                PlanStatus.PLANNED,
                "metric_set" if len(metric_ids) > 1 else "metric",
                ",".join(metric_ids),
                metrics[0][1] if len(metric_ids) == 1 else ",".join(item[1] for item in metrics[:3]),
                [ToolStep(tool, arguments, "Query governed MetricFlow metric(s) through the bounded semantic-query contract.")],
            )

        if metrics:
            metric, alias = metrics[0]
            return ToolPlan(
                q,
                Intent.METRIC_DEFINITION,
                PlanStatus.PLANNED,
                "metric",
                metric,
                alias,
                [ToolStep("get_metric_context", {"metric": metric}, "Read governed metric meaning and dbt/MetricFlow definition.")],
            )


        # Why / Design / SOP 只有在结构化权威没有先命中时才进入 Knowledge RAG。
        # 例如“orders 昨天为什么没更新”仍属于 Dagster Runtime；“gross_sales 怎么算”仍属于 MetricFlow。
        if knowledge_runbook_matches or knowledge_design_matches or knowledge_glossary_matches:
            if knowledge_runbook_matches:
                scopes = ["runbook"]
                matched = knowledge_runbook_matches[0]
            elif knowledge_design_matches:
                scopes = ["architecture", "modeling", "governance", "business"]
                matched = knowledge_design_matches[0]
            else:
                scopes = ["glossary"]
                matched = knowledge_glossary_matches[0]
            return ToolPlan(
                q,
                Intent.KNOWLEDGE_QUERY,
                PlanStatus.PLANNED,
                "knowledge",
                ",".join(scopes),
                matched,
                [
                    ToolStep(
                        "search_knowledge",
                        {"query": q, "scopes": scopes, "top_k": 5},
                        "在受治理 Knowledge Corpus 中检索候选切片；Retrieved Knowledge 不能覆盖结构化权威。",
                    ),
                    ToolStep(
                        "fetch_top_knowledge_hits",
                        {"search_result_index": 0, "top_n": int(self.contract["limits"].get("knowledge_fetch_top_n", 2))},
                        "只按 Search 返回的 exact chunk_id 取回前 N 个完整切片，禁止任意文件读取。",
                    ),
                ],
            )

        if datasets:
            dataset, alias = datasets[0]
            return ToolPlan(
                q,
                Intent.DATASET_GOVERNANCE,
                PlanStatus.PLANNED,
                "dataset",
                dataset,
                alias,
                [ToolStep("get_dataset_context", {"dataset": dataset}, "Read bounded governed Dataset context.")],
            )
        if entities:
            entity, alias = entities[0]
            return ToolPlan(
                q,
                Intent.ENTITY_CONTEXT,
                PlanStatus.PLANNED,
                "entity",
                entity,
                alias,
                [ToolStep("get_entity_context", {"entity": entity}, "Read bounded governed Entity context.")],
            )
        return self._discovery(q, "No deterministic governed target matched.")

    def _discovery(self, question: str, warning: str) -> ToolPlan:
        """无法安全绑定唯一目标时生成 metadata discovery 计划，要求后续从治理注册表发现而不是自动猜。"""
        return ToolPlan(
            question,
            Intent.METADATA_DISCOVERY,
            PlanStatus.NEEDS_DISCOVERY,
            target_kind="any",
            target_id=None,
            target_match=None,
            steps=[
                ToolStep(
                    "search_metadata",
                    {"query": question, "limit": 10},
                    "Search only the governed registry; do not infer or auto-select an unverified target.",
                    stop_on_status=("BLOCKED", "ERROR"),
                )
            ],
            warnings=[warning],
        )
