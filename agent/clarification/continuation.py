"""受治理多轮澄清与不可变查询计划续接。

业务逻辑：第一次问题信息不足时保存 continuation spec / candidates；用户下一轮只允许选择或确认受治理候选。
工程边界：resume 不重新调用自由规划器，不允许 follow-up 偷换 Metric / 时间范围 / raw predicate。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from agent.dimension_resolution import normalize_value
from agent.semantic_query.contracts import (
    SemanticDimensionFilter,
    SemanticFilterOperator,
    SemanticQueryPlan,
    SemanticQuerySpec,
    SemanticQueryStatus,
)
from agent.semantic_query.executor import MetricFlowSemanticQueryExecutor
from agent.semantic_query.planner import GovernedSemanticQueryPlanner

from agent.clarification.contracts import (
    ClarificationContinuation,
    ContinuationCandidate,
    ContinuationResult,
    ContinuationStatus,
)


class GovernedClarificationContinuation:
    """管理一个待澄清 SemanticQueryPlan 的完整性与继续执行。
    
    通过 checksum / fingerprint 防止上下文被修改；只有合法候选选择才能恢复成 READY plan。
    """
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/clarification_policy.yml").read_text(encoding="utf-8")
        )
        self.semantic_policy = yaml.safe_load(
            (self.root / "agent/contracts/semantic_query_policy.yml").read_text(encoding="utf-8")
        )
        self.planner = GovernedSemanticQueryPlanner(self.root)
        self.executor = MetricFlowSemanticQueryExecutor(self.root)

    def prepare(self, plan: SemanticQueryPlan) -> ClarificationContinuation:
        """把 CLARIFICATION_REQUIRED plan 固化成可续接对象，并计算候选与计划指纹。"""
        if plan.status is not SemanticQueryStatus.CLARIFICATION_REQUIRED:
            raise ValueError("Continuation requires a CLARIFICATION_REQUIRED semantic query plan")
        if plan.continuation_spec is None or plan.clarification is None:
            raise ValueError("Semantic query clarification is not resumable; no frozen continuation state exists")
        if plan.clarification.kind != "DIMENSION_VALUE_CONFIRMATION":
            raise ValueError(f"Unsupported clarification kind: {plan.clarification.kind}")

        max_candidates = int(self.policy["limits"]["max_candidates"])
        raw_candidates = list(plan.clarification.candidates)
        if not raw_candidates or len(raw_candidates) > max_candidates:
            raise ValueError("Clarification candidate count is outside the governed continuation contract")

        candidates = tuple(
            ContinuationCandidate(
                id=f"CAND{index:02d}",
                dimension=str(item["dimension"]),
                value=str(item["value"]),
                score=float(item.get("score", 1.0)),
                mode=str(item.get("mode", "UNKNOWN")),
                evidence=str(item.get("evidence", plan.clarification.evidence)),
                source_mode=str(item.get("source_mode", plan.clarification.source_mode)),
            )
            for index, item in enumerate(raw_candidates, start=1)
        )

        payload = self._fingerprint_payload(
            original_question=plan.question,
            base_spec=plan.continuation_spec,
            raw_value=plan.clarification.raw_value,
            dimension_hint=plan.clarification.dimension_hint,
            candidates=candidates,
            clarification_prompt=plan.clarification.prompt,
            evidence=plan.clarification.evidence,
            source_mode=plan.clarification.source_mode,
        )
        checksum = self._checksum(payload)
        return ClarificationContinuation(
            continuation_id=f"cont_{checksum[:16]}",
            original_question=plan.question,
            base_spec=plan.continuation_spec,
            raw_value=plan.clarification.raw_value,
            dimension_hint=plan.clarification.dimension_hint,
            candidates=candidates,
            clarification_prompt=plan.clarification.prompt,
            evidence=plan.clarification.evidence,
            source_mode=plan.clarification.source_mode,
            integrity_checksum=checksum,
            contract_version=int(self.policy["version"]),
        )

    def resume(
        self,
        continuation: ClarificationContinuation,
        *,
        user_reply: str,
        execute: bool = False,
    ) -> ContinuationResult:
        """消费用户 follow-up，验证 continuation 完整性并绑定候选；不能唯一确认时继续要求澄清。"""
        integrity_error = self._validate_integrity(continuation)
        if integrity_error:
            return ContinuationResult(
                status=ContinuationStatus.BLOCKED,
                continuation=continuation,
                user_reply=user_reply,
                warnings=(integrity_error,),
            )

        reply = user_reply.strip()
        if not reply:
            return self._needs_confirmation(continuation, user_reply, "A non-empty confirmation reply is required.")

        if self._matches_marker(reply, self.policy["reply_markers"]["reject"]):
            return ContinuationResult(
                status=ContinuationStatus.REJECTED,
                continuation=continuation,
                user_reply=user_reply,
                warnings=(
                    "The candidate was rejected. The original query remains unexecuted; provide a corrected governed filter value.",
                ),
            )

        candidate = self._select_candidate(continuation, reply)
        if candidate is None:
            return self._needs_confirmation(
                continuation,
                user_reply,
                "The reply did not uniquely select one stored governed candidate.",
            )

        ready_plan = self._build_ready_plan(continuation, candidate)
        if ready_plan is None:
            return ContinuationResult(
                status=ContinuationStatus.BLOCKED,
                continuation=continuation,
                user_reply=user_reply,
                selected_candidate=candidate,
                warnings=("Confirmed candidate would violate the governed filter contract.",),
            )

        if not execute:
            return ContinuationResult(
                status=ContinuationStatus.READY,
                continuation=continuation,
                user_reply=user_reply,
                selected_candidate=candidate,
                plan=ready_plan,
            )

        query_result = self.executor.execute(ready_plan)
        mapped = {
            SemanticQueryStatus.COMPLETE: ContinuationStatus.COMPLETE,
            SemanticQueryStatus.DEFERRED: ContinuationStatus.DEFERRED,
            SemanticQueryStatus.BLOCKED: ContinuationStatus.BLOCKED,
            SemanticQueryStatus.ERROR: ContinuationStatus.ERROR,
            SemanticQueryStatus.CLARIFICATION_REQUIRED: ContinuationStatus.CLARIFICATION_REQUIRED,
            SemanticQueryStatus.READY: ContinuationStatus.READY,
        }[query_result.status]
        return ContinuationResult(
            status=mapped,
            continuation=continuation,
            user_reply=user_reply,
            selected_candidate=candidate,
            plan=ready_plan,
            query_result=query_result,
            warnings=tuple(query_result.warnings),
        )

    def from_dict(self, payload: dict[str, Any]) -> ClarificationContinuation:
        """从持久化字典恢复 continuation，并重新验证必要字段，供跨请求会话使用。"""
        spec_payload = payload["base_spec"]
        filters = tuple(
            SemanticDimensionFilter(
                dimension=str(item["dimension"]),
                operator=SemanticFilterOperator(str(item["operator"])),
                value=str(item["value"]),
                source=str(item.get("source", "governed_value_alias")),
            )
            for item in spec_payload.get("filters", [])
        )
        spec = SemanticQuerySpec(
            metric=str(spec_payload["metric"]),
            metrics=tuple(spec_payload.get("metrics") or [spec_payload["metric"]]),
            start_time=str(spec_payload["start_time"]),
            end_time=str(spec_payload["end_time"]),
            group_by=tuple(spec_payload.get("group_by", [])),
            filters=filters,
            limit=int(spec_payload.get("limit", 20)),
        )
        candidates = tuple(ContinuationCandidate(**item) for item in payload["candidates"])
        return ClarificationContinuation(
            continuation_id=str(payload["continuation_id"]),
            contract_version=int(payload.get("contract_version", 1)),
            original_question=str(payload["original_question"]),
            base_spec=spec,
            raw_value=str(payload["raw_value"]),
            dimension_hint=payload.get("dimension_hint"),
            candidates=candidates,
            clarification_prompt=str(payload["clarification_prompt"]),
            evidence=str(payload["evidence"]),
            source_mode=str(payload["source_mode"]),
            integrity_checksum=str(payload["integrity_checksum"]),
        )

    def _build_ready_plan(
        self,
        continuation: ClarificationContinuation,
        candidate: ContinuationCandidate,
    ) -> SemanticQueryPlan | None:
        """在候选已经安全确认后，把原 continuation spec 补成新的 READY SemanticQueryPlan。"""
        base = continuation.base_spec
        max_filters = int(self.semantic_policy["limits"]["max_filters"])
        if len(base.filters) + 1 > max_filters:
            return None
        if any(item.dimension == candidate.dimension for item in base.filters):
            return None

        confirmed = SemanticDimensionFilter(
            dimension=candidate.dimension,
            operator=SemanticFilterOperator.EQ,
            value=candidate.value,
            source=f"user_confirmed:{candidate.mode}:{candidate.evidence}",
        )
        spec = replace(base, filters=tuple([*base.filters, confirmed]))
        return SemanticQueryPlan(
            status=SemanticQueryStatus.READY,
            question=continuation.original_question,
            spec=spec,
            command_preview=self.planner.command_args(spec),
            warnings=[
                f"Resumed from {continuation.continuation_id}; user confirmed {candidate.dimension}={candidate.value}."
            ],
        )

    def _select_candidate(
        self,
        continuation: ClarificationContinuation,
        reply: str,
    ) -> ContinuationCandidate | None:
        """根据用户 follow-up 在受治理候选中做确定性选择；不会用近似文本自动猜唯一值。"""
        candidates = list(continuation.candidates)
        normalized = normalize_value(reply)

        # A plain affirmative is safe only when there is exactly one candidate.
        if len(candidates) == 1 and self._matches_marker(reply, self.policy["reply_markers"]["affirm"]):
            return candidates[0]

        # Explicit candidate id / ordinal.
        compact = re.sub(r"\s+", "", reply).upper()
        for index, candidate in enumerate(candidates, start=1):
            ordinal_tokens = {
                candidate.id.upper(),
                str(index),
                f"第{index}个",
                f"第{index}项",
            }
            if compact in {re.sub(r"\s+", "", token).upper() for token in ordinal_tokens}:
                return candidate

        # Exact canonical value among the stored candidates only.
        exact_values = [candidate for candidate in candidates if normalize_value(candidate.value) == normalized]
        if len(exact_values) == 1:
            return exact_values[0]

        # If values are identical across dimensions, the user may clarify the dimension.
        by_dimension: list[ContinuationCandidate] = []
        for candidate in candidates:
            aliases = self.semantic_policy["structured_filter_dimensions"].get(candidate.dimension, {}).get(
                "dimension_aliases", []
            )
            if any(normalize_value(alias) == normalized for alias in [candidate.dimension, *aliases]):
                by_dimension.append(candidate)
        if len(by_dimension) == 1:
            return by_dimension[0]
        return None

    def _validate_integrity(self, continuation: ClarificationContinuation) -> str | None:
        """重算 checksum / fingerprint，阻止 continuation 内容或候选集合被静默篡改。"""
        if continuation.contract_version != int(self.policy["version"]):
            return "Continuation contract version does not match the active Phase 5E policy."
        payload = self._fingerprint_payload(
            original_question=continuation.original_question,
            base_spec=continuation.base_spec,
            raw_value=continuation.raw_value,
            dimension_hint=continuation.dimension_hint,
            candidates=continuation.candidates,
            clarification_prompt=continuation.clarification_prompt,
            evidence=continuation.evidence,
            source_mode=continuation.source_mode,
        )
        checksum = self._checksum(payload)
        if checksum != continuation.integrity_checksum:
            return "Continuation integrity checksum mismatch; refuse to resume a mutated query state."
        if continuation.continuation_id != f"cont_{checksum[:16]}":
            return "Continuation id does not match the frozen query-state checksum."
        return None

    def _needs_confirmation(
        self,
        continuation: ClarificationContinuation,
        user_reply: str,
        warning: str,
    ) -> ContinuationResult:
        """判断当前候选是否仍需要用户显式确认。"""
        return ContinuationResult(
            status=ContinuationStatus.CLARIFICATION_REQUIRED,
            continuation=continuation,
            user_reply=user_reply,
            warnings=(warning, continuation.clarification_prompt),
        )

    @staticmethod
    def _matches_marker(reply: str, markers: list[str]) -> bool:
        """判断 follow-up 是否包含确认/拒绝等受治理标记。"""
        normalized = normalize_value(reply)
        return normalized in {normalize_value(marker) for marker in markers}

    @staticmethod
    def _checksum(payload: dict[str, Any]) -> str:
        """对规范化 continuation payload 计算 SHA-256，用于检测内容变化。"""
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _fingerprint_payload(
        *,
        original_question: str,
        base_spec: SemanticQuerySpec,
        raw_value: str,
        dimension_hint: str | None,
        candidates: tuple[ContinuationCandidate, ...],
        clarification_prompt: str,
        evidence: str,
        source_mode: str,
    ) -> dict[str, Any]:
        """构造用于 fingerprint 的稳定字段集合，排除非语义性的瞬时元数据。"""
        return {
            "original_question": original_question,
            "base_spec": base_spec.to_dict(),
            "raw_value": raw_value,
            "dimension_hint": dimension_hint,
            "candidates": [item.to_dict() for item in candidates],
            "clarification_prompt": clarification_prompt,
            "evidence": evidence,
            "source_mode": source_mode,
        }
