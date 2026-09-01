"""Progressive Context Expansion（渐进式上下文扩展）。

思想：
    Complete Available Context
        !=
    Load Everything at Runtime

初始 Bundle 只加载 required planning context。
只有当“缺少转换逻辑/血缘解释”等受治理原因出现时，才扩展 optional Code Context。

Raw Code 是第二级 fallback：
    Semantic / Metadata
        -> Model Context Card
        -> bounded Raw Code snippet

任何一级够用就停止。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from agent.code_context import (
    GovernedModelContextRepository,
    ModelContextStatus,
)

from .budget import GovernedContextBudget
from .contracts import ContextSource
from .repository import GovernedContextRepository
from .runtime_contracts import (
    ContextBundle,
    ContextBundleStatus,
    ContextExpansionReason,
    ContextItem,
    ContextItemStatus,
)


class GovernedProgressiveContextExpander:
    """只允许 Plan 已批准的 optional Context 做渐进扩展。"""

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/context_loader_policy.yml").read_text(encoding="utf-8")
        )
        self.budget = GovernedContextBudget(self.root)
        self.context_repo = GovernedContextRepository(self.root)
        self.code_repo = GovernedModelContextRepository(self.root)

    def expand_code(
        self,
        bundle: ContextBundle,
        route: Any,
        *,
        reason: ContextExpansionReason,
        model: str | None = None,
    ) -> ContextBundle:
        """加载一张 Model Context Card；不读取 Raw Code。"""

        warning = self._guard_code_expansion(bundle, reason)
        if warning:
            return self._with_warning(bundle, warning)

        resolved_model, model_warning = self._resolve_model(bundle, route, model)
        if not resolved_model:
            return self._with_warning(bundle, model_warning)

        # 同一个 model 已经加载时保持幂等，避免重复 token。
        if any(
            item.source is ContextSource.CODE
            and item.key == f"model_context:{resolved_model}"
            and item.status is ContextItemStatus.LOADED
            for item in bundle.items
        ):
            return bundle

        resolution = self.code_repo.resolve(resolved_model)
        if resolution.status is not ModelContextStatus.RESOLVED or resolution.card is None:
            item = ContextItem(
                source=ContextSource.CODE,
                key=f"model_context:{resolved_model}",
                required=False,
                status=ContextItemStatus.BLOCKED,
                authority="model_context_card",
                evidence_mode=resolution.evidence_mode,
                warnings=tuple(resolution.warnings),
            )
            return self._append_optional_failure(bundle, item)

        item = ContextItem(
            source=ContextSource.CODE,
            key=f"model_context:{resolved_model}",
            required=False,
            status=ContextItemStatus.LOADED,
            payload=resolution.card,
            authority="EXECUTABLE_CODE+DBT_SEMANTICS",
            evidence_mode=resolution.evidence_mode,
            estimated_tokens=resolution.estimated_tokens,
            warnings=tuple(resolution.warnings),
        )
        return self._append_loaded(bundle, item)

    def expand_raw_code(
        self,
        bundle: ContextBundle,
        *,
        model: str,
        start_line: int,
        end_line: int,
        reason: ContextExpansionReason,
    ) -> ContextBundle:
        """Card 仍不足时，显式读取有限 Raw Code 片段。"""

        if reason is not ContextExpansionReason.MODEL_CONTEXT_CARD_INSUFFICIENT:
            return self._with_warning(
                bundle,
                "Raw Code expansion requires reason=MODEL_CONTEXT_CARD_INSUFFICIENT.",
            )
        if not bundle.context_plan.requires(ContextSource.CODE):
            return self._with_warning(
                bundle,
                "Context Plan did not authorize Code Context.",
            )
        if bundle.expansion_count >= self.budget.max_expansion_steps:
            return self._with_warning(
                bundle,
                "Context expansion step limit has been reached.",
            )
        if not any(
            item.key == f"model_context:{model}"
            and item.status is ContextItemStatus.LOADED
            for item in bundle.items
        ):
            return self._with_warning(
                bundle,
                "Raw Code cannot be loaded before a fresh Model Context Card for the same model.",
            )

        snippet = self.code_repo.raw_snippet(
            model,
            start_line=start_line,
            end_line=end_line,
            allow_raw_fallback=True,
        )
        if snippet.status is not ModelContextStatus.RESOLVED:
            item = ContextItem(
                source=ContextSource.CODE,
                key=f"raw_code:{model}:{start_line}-{end_line}",
                required=False,
                status=ContextItemStatus.BLOCKED,
                authority="executable_code",
                warnings=tuple(snippet.warnings),
            )
            return self._append_optional_failure(bundle, item)

        item = ContextItem(
            source=ContextSource.CODE,
            key=f"raw_code:{model}:{snippet.start_line}-{snippet.end_line}",
            required=False,
            status=ContextItemStatus.LOADED,
            payload={
                "model": model,
                "source_path": snippet.source_path,
                "start_line": snippet.start_line,
                "end_line": snippet.end_line,
                "content": snippet.content,
            },
            authority="EXECUTABLE_CODE",
            evidence_mode="BOUNDED_RAW_CODE",
            estimated_tokens=snippet.estimated_tokens,
        )
        return self._append_loaded(bundle, item)

    def _guard_code_expansion(
        self,
        bundle: ContextBundle,
        reason: ContextExpansionReason,
    ) -> str | None:
        if bundle.status not in {ContextBundleStatus.READY, ContextBundleStatus.PARTIAL}:
            return "Progressive expansion requires a READY/PARTIAL base Context Bundle."
        if ContextSource.CODE not in bundle.context_plan.optional_sources():
            return "Code Context is not an optional source in the current Context Plan."
        if bundle.expansion_count >= self.budget.max_expansion_steps:
            return "Context expansion step limit has been reached."

        allowed = {
            ContextExpansionReason(value)
            for value in self.policy["progressive_expansion"]["model_context_card"]["allowed_reasons"]
        }
        if reason not in allowed:
            return f"Expansion reason {reason.value} is not allowed for Model Context Card."
        return None

    def _resolve_model(
        self,
        bundle: ContextBundle,
        route: Any,
        explicit_model: str | None,
    ) -> tuple[str | None, str]:
        if explicit_model:
            return explicit_model, ""

        # Dataset/lineage 问题天然绑定一个模型。
        if str(getattr(route, "target_kind", "") or "") == "dataset":
            target = str(getattr(route, "target_id", "") or "")
            if target:
                return target, ""

        # Metric 问题从已经加载的 Semantic Context 中读 related_models。
        # 只有唯一候选才自动绑定；多候选必须让上层显式选择，不能猜。
        candidates: list[str] = []
        for item in bundle.loaded_items(ContextSource.SEMANTIC):
            payload = item.payload or {}
            if hasattr(payload, "to_dict"):
                payload = payload.to_dict()
            for model in payload.get("related_models", ()) or ():
                candidates.append(str(model))
        unique = tuple(dict.fromkeys(candidates))
        if len(unique) == 1:
            return unique[0], ""
        if not unique:
            return None, "No code model could be derived from already-loaded governed context."
        return None, (
            "More than one related model is available; explicit model selection is required: "
            + ", ".join(unique)
        )

    def _append_loaded(
        self,
        bundle: ContextBundle,
        item: ContextItem,
    ) -> ContextBundle:
        new_total = bundle.estimated_tokens + item.estimated_tokens
        if new_total > self.budget.expanded_limit:
            return self._with_warning(
                bundle,
                f"Expanded Context estimate {new_total} exceeds governed limit {self.budget.expanded_limit}.",
            )

        # 替换 CODE optional placeholder，避免 Bundle 里同时出现“未加载”和“已加载”造成歧义。
        items = tuple(
            existing
            for existing in bundle.items
            if not (
                existing.source is ContextSource.CODE
                and existing.status is ContextItemStatus.NOT_LOADED
                and existing.key == "optional:code"
            )
        )
        return replace(
            bundle,
            items=(*items, item),
            status=ContextBundleStatus.READY,
            estimated_tokens=new_total,
            expansion_count=bundle.expansion_count + 1,
        )

    @staticmethod
    def _append_optional_failure(
        bundle: ContextBundle,
        item: ContextItem,
    ) -> ContextBundle:
        return replace(
            bundle,
            items=(*bundle.items, item),
            status=ContextBundleStatus.PARTIAL
            if bundle.status is ContextBundleStatus.READY
            else bundle.status,
            expansion_count=bundle.expansion_count + 1,
            warnings=(*bundle.warnings, *item.warnings),
        )

    @staticmethod
    def _with_warning(bundle: ContextBundle, warning: str) -> ContextBundle:
        # 未能扩展 optional Context 不应该把原本 READY 的业务流程直接 BLOCK。
        return replace(
            bundle,
            status=ContextBundleStatus.PARTIAL
            if bundle.status is ContextBundleStatus.READY
            else bundle.status,
            warnings=(*bundle.warnings, warning),
        )
