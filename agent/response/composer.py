"""把受治理工具/诊断结果投影成 ResponseEnvelope / Claim Ledger。

Composer 只允许把已有结构化 evidence 变成有限 claim；LLM 不参与 claim authority 判定。
"""

from __future__ import annotations

from pathlib import Path

from .contracts import AnswerStatus, Claim, ClaimKind, ResponseEnvelope


class GovernedResponseComposer:
    """根据 Tool execution 与治理策略生成 Claim Ledger、warnings、limitations 与 answer status。"""
    def __init__(self, project_root: Path | str):
        self.root = Path(project_root).resolve()

    def compose(self, execution) -> ResponseEnvelope:
        """把结构化工具结果组合成 ResponseEnvelope。
        
        每条 claim 记录 kind、statement、evidence、runtime_observed 等字段；数值运行事实必须满足 RUNTIME_VERIFIED。
        """
        status_map = {
            "DEFERRED": AnswerStatus.DEFERRED,
            "BLOCKED": AnswerStatus.BLOCKED,
            "ERROR": AnswerStatus.ERROR,
            "CLARIFICATION_REQUIRED": AnswerStatus.CLARIFICATION_REQUIRED,
            "NEEDS_DISCOVERY": AnswerStatus.NEEDS_DISCOVERY,
            "COMPLETE": AnswerStatus.ANSWERED,
            "STOPPED": AnswerStatus.PARTIAL,
            "PLANNED": AnswerStatus.PARTIAL,
        }
        status = status_map.get(execution.status.value, AnswerStatus.PARTIAL)
        claims: list[Claim] = []
        limitations: list[str] = []
        sources: list[dict[str, str]] = []
        cid = 1

        def add(kind: ClaimKind, text: str, *, evidence: str = "STATIC_CONTRACT", source_locations=(), runtime_observed=False):
            """处理 add 对应的受治理工程步骤。
            
            输入输出沿用当前模块契约；不得绕过既有 Runtime gate、证据等级或生产写入边界。
            """
            nonlocal cid
            claims.append(
                Claim(
                    f"C{cid:02d}",
                    kind,
                    text,
                    evidence=evidence,
                    source_locations=tuple(source_locations),
                    runtime_observed=runtime_observed,
                )
            )
            cid += 1

        for result in execution.results:
            tool = result.get("tool")
            payload = result.get("payload") or {}
            locations = tuple(s.get("location", "") for s in result.get("sources", []) if s.get("location"))
            sources.extend(result.get("sources") or [])

            plan = payload.get("plan") or {}
            spec = plan.get("spec") or plan.get("continuation_spec") or {}
            if tool in {"query_semantic_metric", "query_semantic_metrics"} and spec:
                metrics = spec.get("metrics") or [spec.get("metric")]
                filters = spec.get("filters", [])
                ftxt = ", ".join(f"{f['dimension']} {f['operator']} {f['value']}" for f in filters) or "(none)"
                gtxt = ",".join(spec.get("group_by") or []) or "(none)"
                add(
                    ClaimKind.SEMANTIC_QUERY_PLAN,
                    f"Semantic query plan: metrics={','.join(metrics)}; time=[{spec.get('start_time')}, {spec.get('end_time')}]; group_by={gtxt}; filters={ftxt}; limit={spec.get('limit', 20)}.",
                    source_locations=locations,
                )
            if tool in {"query_semantic_metric", "query_semantic_metrics"} and result.get("status") == "COMPLETE" and payload.get("rows"):
                add(
                    ClaimKind.QUERY_RESULT,
                    f"Runtime semantic query returned {len(payload['rows'])} row(s): {payload['rows']}",
                    evidence="RUNTIME_VERIFIED",
                    source_locations=locations,
                    runtime_observed=True,
                )

            if tool == "get_metric_context" and result.get("status") == "ANSWERED":
                definition = payload.get("definition") or {}
                add(ClaimKind.DEFINITION, f"{payload.get('name', payload.get('id'))}: {payload.get('description', '')}".strip(), source_locations=locations)
                metric_type = definition.get("type")
                if metric_type == "derived" and definition.get("expr"):
                    add(ClaimKind.FORMULA, f"{payload['id']} = {' '.join(str(definition['expr']).split())}", source_locations=(definition.get("source_file", ""),))
                elif metric_type == "ratio":
                    add(ClaimKind.FORMULA, f"{payload['id']} = {definition.get('numerator')} / {definition.get('denominator')}", source_locations=(definition.get("source_file", ""),))
                elif metric_type == "conversion":
                    base = definition.get("base_metric")
                    conversion = definition.get("conversion_metric")
                    base_name = base.get("name") if isinstance(base, dict) else base
                    conversion_name = conversion.get("name") if isinstance(conversion, dict) else conversion
                    window = definition.get("window") or "unbounded"
                    add(
                        ClaimKind.FORMULA,
                        f"{payload['id']}: {base_name} -> {conversion_name}; entity={definition.get('entity')}; window={window}; calculation={definition.get('calculation')}",
                        source_locations=(definition.get("source_file", ""),),
                    )
                elif metric_type == "simple":
                    add(ClaimKind.FORMULA, f"{payload['id']}: {definition.get('agg')}({definition.get('expr')})", source_locations=(definition.get("source_file", ""),))
                if payload.get("related_models"):
                    add(ClaimKind.RELATIONSHIP, "Related semantic models: " + ", ".join(payload["related_models"]), source_locations=locations)

            if tool == "get_entity_context" and result.get("status") == "ANSWERED":
                add(ClaimKind.DEFINITION, f"{payload.get('name')}: {payload.get('description', '')}".strip(), source_locations=locations)
                add(
                    ClaimKind.RELATIONSHIP,
                    "Semantic participation: primary=" + ", ".join(payload.get("primary_models", [])) + "; referenced_by=" + ", ".join(payload.get("referenced_by_models", [])),
                    source_locations=locations,
                )

            if tool == "get_dataset_context" and result.get("status") == "ANSWERED":
                domain = payload.get("domain") or {}
                owners = payload.get("owners") or {}
                business = (owners.get("business") or {}).get("display_name", "")
                technical = (owners.get("technical") or {}).get("display_name", "")
                add(ClaimKind.GOVERNANCE, f"{payload.get('model')} belongs to Domain: {domain.get('name', domain.get('id', ''))}", source_locations=locations)
                add(ClaimKind.GOVERNANCE, f"{payload.get('model')} ownership: business={business}; technical={technical}", source_locations=locations)
                props = payload.get("structured_properties") or {}
                if props:
                    add(ClaimKind.GOVERNANCE, f"{payload.get('model')} governance properties: " + ", ".join(f"{k.split('.')[-1]}={v}" for k, v in props.items()), source_locations=locations)

            if tool == "get_lineage_context" and result.get("status") == "ANSWERED":
                edge_text = "; ".join(f"{e['from']} -> {e['to']} (hop {e['hop']})" for e in payload.get("edges", [])) or "No bounded lineage edge found."
                add(ClaimKind.LINEAGE, f"{payload.get('direction')} lineage: {edge_text}", source_locations=locations)

            if tool == "get_runtime_context":
                contract = payload.get("automation_contract") or {}
                if contract:
                    add(
                        ClaimKind.AUTOMATION_CONTRACT,
                        f"{contract.get('dataset')} automation contract: job={contract.get('job')}, schedule={contract.get('schedule')}, freshness_deadline={contract.get('freshness_deadline')}, freshness_budget_minutes={contract.get('freshness_budget_minutes')}.",
                        source_locations=locations,
                    )

            if tool == "search_metadata":
                found = payload.get("results") or []
                if found:
                    add(ClaimKind.DISCOVERY, "Governed metadata candidates: " + ", ".join(f"{x['kind']}:{x['id']}" for x in found), source_locations=locations)

            if tool == "search_knowledge" and result.get("status") == "ANSWERED":
                found = payload.get("results") or []
                if found:
                    add(
                        ClaimKind.KNOWLEDGE_EVIDENCE,
                        "Knowledge candidates resolved from governed corpus: "
                        + ", ".join(f"{x.get('document_id')}#{x.get('section')}" for x in found[:5]),
                        evidence="RETRIEVED_KNOWLEDGE",
                        source_locations=locations,
                        runtime_observed=False,
                    )

            if tool == "fetch_knowledge" and result.get("status") == "ANSWERED":
                content = str(payload.get("content", "")).strip()
                if content:
                    add(
                        ClaimKind.KNOWLEDGE_EVIDENCE,
                        f"{payload.get('title')} · {payload.get('section')}: {content}",
                        evidence="RETRIEVED_KNOWLEDGE",
                        source_locations=locations,
                        runtime_observed=False,
                    )

            if result.get("status") == "CLARIFICATION_REQUIRED":
                clarification = plan.get("clarification") or {}
                prompt = clarification.get("prompt") or (result.get("warnings") or ["请确认查询条件。"])[0]
                add(ClaimKind.CLARIFICATION_REQUEST, prompt, source_locations=locations)

            for warning in result.get("warnings") or payload.get("warnings") or []:
                if warning not in limitations:
                    limitations.append(warning)

        if status is AnswerStatus.DEFERRED and not any("runtime" in item.lower() for item in limitations):
            limitations.append(
                "Runtime evidence is not available; static contracts may be described, but actual runtime facts or numeric business results must not be inferred."
            )
        if status is AnswerStatus.NEEDS_DISCOVERY and not limitations:
            limitations.append("No governed target was resolved; do not auto-select an unverified search result.")

        for limitation in limitations:
            add(ClaimKind.LIMITATION, limitation, evidence="DEFERRED")

        return ResponseEnvelope(
            question=execution.plan.question,
            intent=execution.plan.intent.value,
            status=status,
            subject={
                "kind": execution.plan.target_kind,
                "id": execution.plan.target_id,
                "matched_alias": execution.plan.target_match,
            },
            claims=claims,
            limitations=limitations,
            sources=sources,
            tool_trace=[
                {"tool": r.get("tool"), "status": r.get("status"), "evidence": r.get("evidence")}
                for r in execution.results
            ],
            evidence_levels=sorted({r.get("evidence") for r in execution.results if r.get("evidence")}),
        )
