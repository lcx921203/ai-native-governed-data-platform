"""Phase 6F 的 Human Approval 状态机与审计完整性。

只处理 HUMAN_REQUIRED action 的 PENDING→APPROVED/REJECTED/EXPIRED；审批绑定 exact incident/response evidence fingerprint。
核心边界：APPROVED ≠ EXECUTED，Agent 不能自批，也没有生产执行权。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from agent.incident_drilldown import IncidentDrilldownResult
from agent.incident_response import ApprovalBoundary, IncidentResponsePlan

from .contracts import (
    ApprovalActor,
    ApprovalActorType,
    ApprovalAuditEvent,
    ApprovalAuthorizationCheck,
    ApprovalAuthorizationStatus,
    ApprovalCase,
    ApprovalEventType,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalWorkflowBundle,
    ApprovalWorkflowStatus,
)


class ApprovalTransitionError(ValueError):
    """审批状态机发生非法状态转换或身份/证据校验失败时抛出的受控异常。"""
    pass


def _canonical_json(value: Any) -> str:
    """把审批证据序列化为键顺序稳定的 JSON，供 SHA-256 fingerprint 使用。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    """计算审批/审计对象的 SHA-256 内容指纹。"""
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _utc(dt: datetime | None = None) -> datetime:
    """把 datetime 规范化为 UTC；无时区输入按契约拒绝或统一处理。"""
    value = dt or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    """把 UTC datetime 序列化为稳定 ISO-8601 文本。"""
    return _utc(dt).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    """把审批契约中的 ISO 时间解析为 timezone-aware datetime。"""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class GovernedApprovalWorkflow:
    """管理 Human Approval 请求、状态转换、hash-chain audit 与 external-execution eligibility。
    
    该类只产生授权状态，不包含 Dagster/backfill/SQL write executor。
    """

    FORBIDDEN_EXECUTION_SYMBOLS = (
        "DagsterInstance",
        "RunRequest(",
        "submit_run(",
        "create_run(",
        "execute_job(",
        "execute_in_process(",
    )

    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/approval_workflow_policy.yml").read_text(encoding="utf-8")
        )
        runtime = self.policy.get("runtime", {})
        if runtime.get("production_action_writes_enabled") is not False:
            raise ValueError("Phase 6F must keep production_action_writes_enabled=false")

    def prepare(
        self,
        incident: IncidentDrilldownResult,
        response_plan: IncidentResponsePlan,
        *,
        now: datetime | None = None,
    ) -> ApprovalWorkflowBundle:
        """从 Phase 6E HUMAN_REQUIRED 步骤创建 PENDING ApprovalCase，并绑定 evidence fingerprint 与 expiry。"""
        gate = self.policy["runtime"]["allow_env"]
        if os.getenv(gate, "false").lower() != "true":
            return ApprovalWorkflowBundle(
                status=ApprovalWorkflowStatus.DEFERRED,
                evidence="STATIC_CONTRACT",
                warnings=[f"Approval workflow is disabled; set {gate}=true only in the intended approval service."],
                validation="NOT_EXECUTED",
            )

        if incident.evidence != "RUNTIME_VERIFIED" or response_plan.evidence != "RUNTIME_VERIFIED":
            return ApprovalWorkflowBundle(
                status=ApprovalWorkflowStatus.DEFERRED,
                evidence="STATIC_CONTRACT",
                warnings=[
                    "Approval requests require RUNTIME_VERIFIED incident evidence and a RUNTIME_VERIFIED Phase 6E response plan."
                ],
                validation="RUNTIME_EVIDENCE_REQUIRED",
            )

        incident_by_partition = {item.partition_key: item for item in incident.partitions}
        pending_steps: list[tuple[Any, Any, Any]] = []
        for partition_plan in response_plan.partitions:
            incident_part = incident_by_partition.get(partition_plan.partition_key)
            if incident_part is None:
                return ApprovalWorkflowBundle(
                    status=ApprovalWorkflowStatus.BLOCKED,
                    evidence="RUNTIME_VERIFIED",
                    warnings=[f"Response partition {partition_plan.partition_key} has no matching Phase 6D incident evidence."],
                    validation="INCIDENT_RESPONSE_PARTITION_MISMATCH",
                )
            for step in partition_plan.steps:
                if step.approval_boundary is ApprovalBoundary.HUMAN_REQUIRED:
                    pending_steps.append((incident_part, partition_plan, step))

        if not pending_steps:
            return ApprovalWorkflowBundle(
                status=ApprovalWorkflowStatus.NO_APPROVAL_REQUIRED,
                evidence="RUNTIME_VERIFIED",
                validation="NO_HUMAN_REQUIRED_RESPONSE_STEP",
            )

        limit = int(self.policy["limits"]["max_approval_requests_per_bundle"])
        if len(pending_steps) > limit:
            return ApprovalWorkflowBundle(
                status=ApprovalWorkflowStatus.BLOCKED,
                evidence="RUNTIME_VERIFIED",
                warnings=[f"Approval bundle supports at most {limit} human-required actions."],
                validation="APPROVAL_REQUEST_LIMIT_EXCEEDED",
            )

        now_dt = _utc(now)
        ttl = timedelta(minutes=int(self.policy["limits"]["default_ttl_minutes"]))
        cases = tuple(
            self._create_case(incident_part, partition_plan, step, requested_at=now_dt, expires_at=now_dt + ttl)
            for incident_part, partition_plan, step in pending_steps
        )
        return ApprovalWorkflowBundle(
            status=ApprovalWorkflowStatus.PENDING,
            evidence="RUNTIME_VERIFIED",
            cases=cases,
            validation="PENDING_APPROVAL_REQUESTS_CREATED_NO_PRODUCTION_ACTION_EXECUTED",
        )

    def approve(
        self,
        case: ApprovalCase,
        *,
        actor: ApprovalActor,
        now: datetime | None = None,
        reason: str = "approved",
        expected_evidence_fingerprint: str | None = None,
    ) -> ApprovalCase:
        """由已认证 HUMAN_OPERATOR 把 PENDING 请求转为 APPROVED，并追加 hash-chain audit event。"""
        return self._human_decision(
            case,
            event_type=ApprovalEventType.APPROVED,
            target=ApprovalStatus.APPROVED,
            actor=actor,
            now=now,
            reason=reason,
            expected_evidence_fingerprint=expected_evidence_fingerprint,
        )

    def reject(
        self,
        case: ApprovalCase,
        *,
        actor: ApprovalActor,
        now: datetime | None = None,
        reason: str,
        expected_evidence_fingerprint: str | None = None,
    ) -> ApprovalCase:
        """由已认证 HUMAN_OPERATOR 拒绝 PENDING 请求，并把 REJECTED 作为终态写入审计链。"""
        return self._human_decision(
            case,
            event_type=ApprovalEventType.REJECTED,
            target=ApprovalStatus.REJECTED,
            actor=actor,
            now=now,
            reason=reason,
            expected_evidence_fingerprint=expected_evidence_fingerprint,
        )

    def expire(self, case: ApprovalCase, *, now: datetime | None = None) -> ApprovalCase:
        """在超过 expiry 后把仍为 PENDING 的请求转成 EXPIRED；不会自动重新申请。"""
        self.assert_integrity(case)
        if case.status is not ApprovalStatus.PENDING:
            raise ApprovalTransitionError(f"Only PENDING approval can expire; current status={case.status.value}")
        at = _utc(now)
        if at < _parse_iso(case.request.expires_at):
            raise ApprovalTransitionError("Approval is not yet due for expiry")
        actor = ApprovalActor(
            subject_id="phase6f-expiry-policy",
            actor_type=ApprovalActorType.SYSTEM,
            authenticated=True,
            identity_source="SYSTEM_POLICY",
        )
        return self._append_event(
            case,
            event_type=ApprovalEventType.EXPIRED,
            target=ApprovalStatus.EXPIRED,
            actor=actor,
            at=at,
            reason="approval_ttl_expired",
        )

    def validate_for_external_execution(
        self,
        case: ApprovalCase,
        *,
        current_incident: IncidentDrilldownResult,
        current_response_plan: IncidentResponsePlan,
        now: datetime | None = None,
    ) -> ApprovalAuthorizationCheck:
        """外部执行前重新读取当前 6D/6E 证据并校验 fingerprint。
        
        成功只返回 ELIGIBLE_FOR_EXTERNAL_EXECUTION；agent_execution_allowed 永远为 false。
        """
        try:
            self.assert_integrity(case)
        except ApprovalTransitionError as exc:
            return ApprovalAuthorizationCheck(
                status=ApprovalAuthorizationStatus.INVALID_AUDIT_CHAIN,
                approval_id=case.request.approval_id,
                eligible_for_external_execution=False,
                agent_execution_allowed=False,
                reason=str(exc),
            )

        at = _utc(now)
        if case.status is ApprovalStatus.PENDING:
            if at >= _parse_iso(case.request.expires_at):
                status = ApprovalAuthorizationStatus.EXPIRED
                reason = "Approval TTL has elapsed; pending approval cannot authorize an external action."
            else:
                status = ApprovalAuthorizationStatus.NOT_APPROVED
                reason = "Approval is still PENDING."
            return ApprovalAuthorizationCheck(status, case.request.approval_id, False, False, reason)
        if case.status is ApprovalStatus.EXPIRED:
            return ApprovalAuthorizationCheck(
                ApprovalAuthorizationStatus.EXPIRED,
                case.request.approval_id,
                False,
                False,
                "Approval is EXPIRED.",
            )
        if case.status is not ApprovalStatus.APPROVED:
            return ApprovalAuthorizationCheck(
                ApprovalAuthorizationStatus.NOT_APPROVED,
                case.request.approval_id,
                False,
                False,
                f"Approval is {case.status.value}, not APPROVED.",
            )

        current = self._find_current_fingerprint(case.request, current_incident, current_response_plan)
        if current is None:
            return ApprovalAuthorizationCheck(
                ApprovalAuthorizationStatus.ACTION_NO_LONGER_PRESENT,
                case.request.approval_id,
                False,
                False,
                "The exact partition/action is no longer present in the current governed response plan.",
            )
        if current != case.request.evidence_fingerprint:
            return ApprovalAuthorizationCheck(
                ApprovalAuthorizationStatus.EVIDENCE_CHANGED,
                case.request.approval_id,
                False,
                False,
                "Current incident/response evidence differs from the evidence approved by the human operator.",
            )
        return ApprovalAuthorizationCheck(
            ApprovalAuthorizationStatus.ELIGIBLE_FOR_EXTERNAL_EXECUTION,
            case.request.approval_id,
            True,
            False,
            "Human approval is valid for the unchanged governed action package. External execution authority must still re-read current truth and execute outside the Agent.",
        )

    def assert_integrity(self, case: ApprovalCase) -> None:
        """重算 request hash 与 audit hash chain，检测工程验证范围内的记录篡改。"""
        request_body = self._request_body(case.request)
        expected_request_hash = _sha256(request_body)
        if expected_request_hash != case.request.request_hash:
            raise ApprovalTransitionError("Approval request hash mismatch")
        previous_hash = ""
        previous_status: str | None = None
        for expected_sequence, event in enumerate(case.events, start=1):
            if event.sequence != expected_sequence:
                raise ApprovalTransitionError("Approval audit event sequence is not contiguous")
            if event.request_hash != case.request.request_hash:
                raise ApprovalTransitionError("Approval audit event references a different request hash")
            if event.previous_event_hash != previous_hash:
                raise ApprovalTransitionError("Approval audit hash chain previous hash mismatch")
            if event.previous_status != previous_status:
                raise ApprovalTransitionError("Approval audit previous_status chain mismatch")
            expected_hash = _sha256(self._event_body(event))
            if event.event_hash != expected_hash:
                raise ApprovalTransitionError("Approval audit event hash mismatch")
            previous_hash = event.event_hash
            previous_status = event.new_status.value
        if not case.events or case.events[0].event_type is not ApprovalEventType.REQUESTED:
            raise ApprovalTransitionError("Approval case must start with REQUESTED event")

    def _create_case(self, incident_part, partition_plan, step, *, requested_at: datetime, expires_at: datetime) -> ApprovalCase:
        """创建新的 ApprovalCase 与初始审计事件，保证 request/evidence hash 一致。"""
        evidence_fingerprint = self._fingerprint(incident_part, partition_plan, step)
        seed = {
            "partition_key": partition_plan.partition_key,
            "action": step.action.value,
            "authority": step.authority.value,
            "policy_action": partition_plan.policy_action,
            "policy_reason": partition_plan.policy_reason,
            "evidence_fingerprint": evidence_fingerprint,
            "requested_at": _iso(requested_at),
            "expires_at": _iso(expires_at),
            "execution_authorized_by_agent": False,
        }
        approval_id = f"approval_{_sha256(seed)[:20]}"
        request_without_hash = {"approval_id": approval_id, **seed}
        request_hash = _sha256(request_without_hash)
        request = ApprovalRequest(request_hash=request_hash, **request_without_hash)
        system_actor = ApprovalActor(
            subject_id="phase6f-approval-workflow",
            actor_type=ApprovalActorType.SYSTEM,
            authenticated=True,
            identity_source="SYSTEM_POLICY",
        )
        event = self._make_event(
            sequence=1,
            request=request,
            event_type=ApprovalEventType.REQUESTED,
            previous_status=None,
            target=ApprovalStatus.PENDING,
            occurred_at=requested_at,
            actor=system_actor,
            reason="human_required_response_step_created",
            previous_event_hash="",
        )
        return ApprovalCase(request=request, events=(event,))

    def _human_decision(
        self,
        case: ApprovalCase,
        *,
        event_type: ApprovalEventType,
        target: ApprovalStatus,
        actor: ApprovalActor,
        now: datetime | None,
        reason: str,
        expected_evidence_fingerprint: str | None,
    ) -> ApprovalCase:
        """实现 approve/reject 共用的终态转换逻辑，并强制 only-PENDING 规则。"""
        self.assert_integrity(case)
        if case.status is not ApprovalStatus.PENDING:
            raise ApprovalTransitionError(
                f"Approval terminal state is immutable; current status={case.status.value}"
            )
        at = _utc(now)
        if at >= _parse_iso(case.request.expires_at):
            raise ApprovalTransitionError("Approval request is expired and cannot be approved/rejected")
        self._validate_human_actor(actor)
        if not reason.strip():
            raise ApprovalTransitionError("Human approval/rejection reason must be non-empty")
        if expected_evidence_fingerprint and expected_evidence_fingerprint != case.request.evidence_fingerprint:
            raise ApprovalTransitionError("Caller expected evidence fingerprint does not match approval request")
        return self._append_event(case, event_type=event_type, target=target, actor=actor, at=at, reason=reason.strip())

    def _validate_human_actor(self, actor: ApprovalActor) -> None:
        """验证 actor_type=HUMAN_OPERATOR、authenticated=true、identity_source=AUTHENTICATED_UPSTREAM。"""
        if actor.actor_type is not ApprovalActorType.HUMAN_OPERATOR:
            raise ApprovalTransitionError("Only HUMAN_OPERATOR may approve/reject a human approval request")
        if actor.authenticated is not True:
            raise ApprovalTransitionError("Approval actor must be authenticated by a trusted upstream identity provider")
        allowed_sources = set(self.policy["identity"]["trusted_identity_sources"])
        if actor.identity_source not in allowed_sources:
            raise ApprovalTransitionError("Approval actor identity source is not trusted by policy")
        if not actor.subject_id.strip():
            raise ApprovalTransitionError("Approval actor subject_id must be non-empty")

    def _append_event(self, case, *, event_type, target, actor, at, reason):
        """向现有 ApprovalCase 追加一个审计事件并更新当前状态。"""
        previous = case.events[-1]
        event = self._make_event(
            sequence=len(case.events) + 1,
            request=case.request,
            event_type=event_type,
            previous_status=case.status.value,
            target=target,
            occurred_at=at,
            actor=actor,
            reason=reason,
            previous_event_hash=previous.event_hash,
        )
        new_case = replace(case, events=(*case.events, event))
        self.assert_integrity(new_case)
        return new_case

    def _make_event(
        self,
        *,
        sequence,
        request,
        event_type,
        previous_status,
        target,
        occurred_at,
        actor,
        reason,
        previous_event_hash,
    ):
        """根据前一事件 hash 构造新的 ApprovalAuditEvent，形成 append-only SHA-256 链。"""
        prototype = ApprovalAuditEvent(
            sequence=sequence,
            approval_id=request.approval_id,
            event_type=event_type,
            previous_status=previous_status,
            new_status=target,
            occurred_at=_iso(occurred_at),
            actor=actor,
            reason=reason,
            request_hash=request.request_hash,
            previous_event_hash=previous_event_hash,
            event_hash="",
        )
        return replace(prototype, event_hash=_sha256(self._event_body(prototype)))

    @staticmethod
    def _request_body(request: ApprovalRequest) -> dict[str, Any]:
        """构造 ApprovalRequest 参与 request_hash 的稳定字段集合。"""
        data = request.to_dict()
        data.pop("request_hash", None)
        return data

    @staticmethod
    def _event_body(event: ApprovalAuditEvent) -> dict[str, Any]:
        """构造 ApprovalAuditEvent 参与 event_hash 的稳定字段集合。"""
        data = event.to_dict()
        data.pop("event_hash", None)
        return data

    @staticmethod
    def _fingerprint(incident_part, partition_plan, step) -> str:
        """对 exact incident + response + step 证据计算 approval evidence fingerprint。"""
        return _sha256({
            "incident_partition": incident_part.to_dict(),
            "partition_response_plan": partition_plan.to_dict(),
            "approval_step": step.to_dict(),
        })

    def _find_current_fingerprint(self, request: ApprovalRequest, incident, response_plan) -> str | None:
        """在当前 Phase 6E plan 中重新定位同一 action，并重算当前证据 fingerprint 供执行前比对。"""
        incident_part = next((x for x in incident.partitions if x.partition_key == request.partition_key), None)
        partition_plan = next((x for x in response_plan.partitions if x.partition_key == request.partition_key), None)
        if incident_part is None or partition_plan is None:
            return None
        step = next(
            (
                x for x in partition_plan.steps
                if x.action.value == request.action
                and x.authority.value == request.authority
                and x.approval_boundary is ApprovalBoundary.HUMAN_REQUIRED
            ),
            None,
        )
        if step is None:
            return None
        return self._fingerprint(incident_part, partition_plan, step)
