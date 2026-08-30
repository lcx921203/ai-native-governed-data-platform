"""Agent 面向元数据的受治理只读 Tool Surface。

工具只组合已有权威来源或调用受治理 Planner / Executor，不创建第二套 Metadata / Semantic Truth。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from agent.context import GovernedContextRepository
from agent.dimension_resolution import GovernedDimensionValueResolver
from agent.dimension_values import GovernedDimensionValuePlanner, MetricFlowDimensionValueExecutor


class GovernedMetadataTools:
    """Agent 的受治理、只读元数据 Tool Surface（工具入口）。

    每个方法要么组合 source-owned metadata，要么委托给已治理的 Phase 5 Planner / Executor。
    不暴露任意 DataHub Graph Query、mutation 或 arbitrary SQL，避免 Agent 绕过 Authority Boundary。
    """

    def __init__(self, project_root: Path | str):
        """初始化 Agent 的受治理元数据读取工具集合。
        
        输入：项目根目录。
        输出：绑定 GovernedContextRepository 与 DimensionValueResolver。
        工程边界：这里只组合已有权威来源，不创建第二套 Metric / Entity / DataHub truth。"""
        self.root = Path(project_root).resolve()
        self.repo = GovernedContextRepository(self.root)
        self.resolver = GovernedDimensionValueResolver(self.root)

    @staticmethod
    def _source(kind: str, location: str, owner: str, runtime_verified: bool = False) -> dict[str, Any]:
        """构造统一的 evidence source 描述。
        
        输入：来源类型、位置、权威 owner 与 runtime_verified 标记。
        输出：供 Agent answer/claim ledger 使用的 source 字典。"""
        return {
            "kind": kind,
            "location": location,
            "owner": owner,
            "runtime_verified": runtime_verified,
        }

    def search_metadata(self, *, query: str, limit: int = 10) -> dict[str, Any]:
        """在受治理 registry 中做有界元数据发现。
        
        输入：自然语言 query 与 1–25 的 limit。
        输出：匹配的 metric / entity / dataset 列表，证据标为 STATIC_CONTRACT。
        工程边界：这是 Git registry 搜索，不是任意 DataHub graph search，也不产生 runtime identity binding。"""
        limit = max(1, min(int(limit), 25))
        q = query.strip().lower()
        tokens = [t for t in q.replace("_", " ").replace("?", " ").replace("？", " ").split() if t]
        results: list[dict[str, Any]] = []

        def score(text: str) -> int:
            """给单个候选文本计算轻量静态匹配分数。
            
            规则：完整 query 命中得高分，否则按 token 命中数计分。
            工程边界：分数只用于受治理 registry 的 discovery，不可作为 DataHub exact identity 证明。"""
            hay = text.lower().replace("_", " ")
            if q and q in hay:
                return 100
            return sum(1 for token in tokens if token in hay)

        for metric_id in self.repo.governed_metric_ids():
            ctx = self.repo.metric_context(metric_id) or {}
            hay = " ".join([metric_id, str(ctx.get("name", "")), str(ctx.get("description", ""))])
            s = score(hay)
            if s:
                results.append({"kind": "metric", "id": metric_id, "name": ctx.get("name", metric_id), "score": s})
        for entity_id, item in self.repo.entity_registry().items():
            gloss = self.repo.glossary().get(item.get("glossary_term", ""), {})
            hay = " ".join([entity_id, str(gloss.get("name", "")), str(gloss.get("description", ""))])
            s = score(hay)
            if s:
                results.append({"kind": "entity", "id": entity_id, "name": gloss.get("name", entity_id), "score": s})
        for model in self.repo.asset_policy_index():
            ctx = self.repo.dataset_context(model) or {}
            domain = ctx.get("domain") or {}
            hay = " ".join([model, str(domain.get("name", "")), str(domain.get("description", ""))])
            s = score(hay)
            if s:
                results.append({"kind": "dataset", "id": model, "name": model, "score": s})

        results.sort(key=lambda item: (-item["score"], item["kind"], item["id"]))
        results = results[:limit]
        return {
            "tool": "search_metadata",
            "query": {"query": query, "limit": limit},
            "status": "ANSWERED" if results else "NOT_FOUND",
            "evidence": "STATIC_CONTRACT",
            "payload": {"results": results, "count": len(results)},
            "sources": [self._source("governance", "metadata/datahub/governance", "governed_metadata")],
            "warnings": [],
        }

    def get_metric_context(self, *, metric: str) -> dict[str, Any]:
        """读取一个 governed Metric 的上下文，同时保持公式权威在 dbt / MetricFlow。
        
        输入：metric id。
        输出：公式定义、治理 registry、Glossary context 与 evidence sources。
        工程边界：dbt 中存在但未进入 governed metric_registry 的 Metric 返回 BLOCKED，不被 Agent 静默暴露。"""
        ctx = self.repo.metric_context(metric)
        if ctx is None:
            technical = self.repo.metric_definitions().get(metric)
            status = "BLOCKED" if technical else "NOT_FOUND"
            warning = (
                "Metric exists in dbt / MetricFlow but is not in the governed metric registry."
                if technical
                else "Metric was not found in the governed registry."
            )
            return {
                "tool": "get_metric_context",
                "query": {"metric": metric},
                "status": status,
                "evidence": "STATIC_CONTRACT",
                "payload": {"metric": metric},
                "warnings": [warning],
                "sources": [],
            }
        source_file = ctx["definition"]["source_file"]
        return {
            "tool": "get_metric_context",
            "query": {"metric": metric},
            "status": "ANSWERED",
            "evidence": "STATIC_CONTRACT",
            "payload": ctx,
            "sources": [
                self._source("metric_definition", source_file, "dbt_metricflow"),
                self._source("governance", "metadata/datahub/governance/metric_registry.yml", "governed_metadata"),
                self._source("metric_lifecycle", "metadata/datahub/governance/metric_lifecycle.yml", "governed_metadata"),
                self._source("glossary", "metadata/datahub/governance/glossary.yml", "governed_metadata"),
            ],
            "warnings": [],
        }

    def get_entity_context(self, *, entity: str) -> dict[str, Any]:
        """读取一个 governed Entity 的语义与治理上下文。
        
        输入：entity id。
        输出：Semantic Model 中的 primary/foreign 关系 + glossary/governance mapping。
        工程边界：Entity relationship 仍来自 dbt / MetricFlow，不从 Glossary prose 推断 Join。"""
        ctx = self.repo.entity_context(entity)
        if ctx is None:
            return {
                "tool": "get_entity_context",
                "query": {"entity": entity},
                "status": "NOT_FOUND",
                "evidence": "STATIC_CONTRACT",
                "payload": {"entity": entity},
                "warnings": ["Entity was not found in the governed entity registry."],
                "sources": [],
            }
        return {
            "tool": "get_entity_context",
            "query": {"entity": entity},
            "status": "ANSWERED",
            "evidence": "STATIC_CONTRACT",
            "payload": ctx,
            "sources": [
                self._source("semantic", "dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml", "dbt_metricflow"),
                self._source("governance", "metadata/datahub/governance/entity_registry.yml", "governed_metadata"),
                self._source("glossary", "metadata/datahub/governance/glossary.yml", "governed_metadata"),
            ],
            "warnings": [],
        }

    def get_dataset_context(self, *, dataset: str) -> dict[str, Any]:
        """读取一个 governed Dataset 的静态治理上下文。
        
        输入：canonical dbt model name。
        输出：Domain、Owners、Tags、Terms、Properties 与 expected identity。
        工程边界：身份未 RESOLVED 时必须给出 warning，并保持 evidence=STATIC_CONTRACT；不能把 expected URN 当 live DataHub entity。"""
        ctx = self.repo.dataset_context(dataset)
        if ctx is None:
            return {
                "tool": "get_dataset_context",
                "query": {"dataset": dataset},
                "status": "NOT_FOUND",
                "evidence": "STATIC_CONTRACT",
                "payload": {"dataset": dataset},
                "warnings": ["Dataset was not found in the governed consumer-asset policy."],
                "sources": [],
            }
        warnings = []
        if ctx["identity"].get("status") != "RESOLVED":
            warnings.append(
                "DataHub dataset identity/runtime is not verified; returning labeled Git/static governance context."
            )
        return {
            "tool": "get_dataset_context",
            "query": {"dataset": dataset},
            "status": "ANSWERED",
            "evidence": "STATIC_CONTRACT",
            "payload": ctx,
            "warnings": warnings,
            "sources": [
                self._source("governance", "metadata/datahub/governance/asset_policy.yml", "governed_metadata"),
                self._source("identity", "metadata/datahub/generated/dataset_identity_resolution.json", "datahub_identity", False),
            ],
        }

    def get_lineage_context(self, *, dataset: str, direction: str = "upstream", max_hops: int = 2) -> dict[str, Any]:
        """读取 Dataset 的有界 lineage context。
        
        输入：dataset、direction、max_hops。
        当前输出：Runtime DataHub lineage 不可用时，从 dbt ref/source 解析静态血缘并明确标记 STATIC_CONTRACT。
        工程边界：最大 hop 受合同限制；静态 lineage 不能升级为 RUNTIME_VERIFIED。"""
        if dataset not in self.repo.asset_policy_index():
            return {
                "tool": "get_lineage_context",
                "query": {"dataset": dataset, "direction": direction, "max_hops": max_hops},
                "status": "NOT_FOUND",
                "evidence": "STATIC_CONTRACT",
                "payload": {},
                "warnings": ["Dataset is not a governed consumer asset."],
                "sources": [],
            }
        try:
            payload = self.repo.static_lineage(dataset, direction=direction, max_hops=int(max_hops))
        except ValueError as exc:
            return {
                "tool": "get_lineage_context",
                "query": {"dataset": dataset, "direction": direction, "max_hops": max_hops},
                "status": "BLOCKED",
                "evidence": "STATIC_CONTRACT",
                "payload": {},
                "warnings": [str(exc)],
                "sources": [],
            }
        payload["lineage_source"] = "dbt_sql_static"
        return {
            "tool": "get_lineage_context",
            "query": {"dataset": dataset, "direction": direction, "max_hops": max_hops},
            "status": "ANSWERED",
            "evidence": "STATIC_CONTRACT",
            "payload": payload,
            "warnings": ["Runtime DataHub lineage is unavailable; returning bounded dbt SQL lineage."],
            "sources": [self._source("dbt_lineage", "dbt/mercaso_dbt/models", "dbt", False)],
        }

    def get_runtime_context(self, *, dataset: str) -> dict[str, Any]:
        """返回 Dataset 的运行合同，但不伪造真实 Run / Failure / Recovery。
        
        输入：governed dataset。
        输出：Schedule/Freshness/Recovery 等静态 automation contract，latest_run/failure/recovery 为空。
        权威边界：Operational Runtime Truth 属于 Dagster；没有真实 Dagster/DataHub evidence 时状态固定 DEFERRED。"""
        if dataset not in self.repo.asset_policy_index():
            return {
                "tool": "get_runtime_context",
                "query": {"dataset": dataset},
                "status": "NOT_FOUND",
                "evidence": "STATIC_CONTRACT",
                "payload": {},
                "warnings": ["Dataset is not a governed consumer asset."],
                "sources": [],
            }
        contract = self.repo.automation_contract(dataset)
        payload = {
            "automation_contract": contract,
            "latest_run": None,
            "latest_failure": None,
            "latest_recovery": None,
        }
        return {
            "tool": "get_runtime_context",
            "query": {"dataset": dataset},
            "status": "DEFERRED",
            "evidence": "STATIC_CONTRACT",
            "payload": payload,
            "warnings": [
                "Real Dagster/DataHub run history is unavailable in the current environment. Automation policy is shown, but no runtime fact is inferred from it."
            ],
            "sources": [
                self._source("automation_timing_contract", "orchestration/dagster/commerce_dagster/automation_policy.py", "dagster", False),
                self._source("consumer_sla_contract", "orchestration/dagster/commerce_dagster/consumer_sla.py", "dagster", False),
            ],
        }

    def get_dimension_values(
        self,
        *,
        metrics: Iterable[str],
        dimension: str,
        question: str = "",
        limit: int = 25,
    ) -> dict[str, Any]:
        """委托 Phase 5 受治理规划器执行 Dimension Value 查询。
        
        输入：metrics、dimension、question、limit。
        输出：Planner/MetricFlow executor 的结构化结果。
        工程边界：这里只暴露既有 governed capability，不让 Metadata Tool 自己拼任意 SQL。"""
        planner = GovernedDimensionValuePlanner(self.root)
        plan = planner.plan(metrics=metrics, dimension=dimension, question=question, limit=limit)
        result = MetricFlowDimensionValueExecutor(self.root).execute(plan)
        return {
            "tool": "get_dimension_values",
            "query": {"metrics": list(metrics), "dimension": dimension, "question": question, "limit": limit},
            "status": result.status.value,
            "evidence": result.evidence,
            "payload": result.to_dict(),
            "warnings": list(result.warnings),
            "sources": [self._source("semantic", "agent/contracts/dimension_value_policy.yml", "governed_agent", False)],
        }

    def resolve_dimension_value(self, *, metrics, raw_value, dimension=None, question=""):
        """把用户输入的维度值映射到受治理候选。
        
        输入：metrics、raw_value、可选 dimension hint 与 question。
        输出：GovernedDimensionValueResolver 的结构化解析结果。
        工程边界：解析策略来自独立 contract；本方法只封装统一 tool response。"""
        result = self.resolver.resolve(
            metrics=metrics,
            raw_value=raw_value,
            dimension_hint=dimension,
            question=question,
        )
        return {
            "tool": "resolve_dimension_value",
            "status": result.status.value,
            "evidence": result.evidence,
            "query": {
                "metrics": list(metrics),
                "raw_value": raw_value,
                "dimension": dimension,
                "question": question,
            },
            "payload": result.to_dict(),
            "warnings": list(result.warnings),
            "sources": [self._source("semantic", "agent/contracts/dimension_resolution_policy.yml", "governed_agent", False)],
        }
