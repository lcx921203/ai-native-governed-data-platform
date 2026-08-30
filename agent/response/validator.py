"""最终 AnswerDraft 的证据边界验证器。

验证 renderer 只能引用 Envelope 中存在的 claim，并强制 runtime_observed=true 的 claim 使用 RUNTIME_VERIFIED evidence。
"""

def validate_answer_draft(envelope, draft):
    """校验最终答案 draft 与 Claim Ledger 的引用、状态和 limitations。
    
    本函数不判断答案“语言好不好”，只负责证据与治理契约是否被越过。
    """
    if len(envelope.claims) > 20:
        raise ValueError("Envelope exceeds governed claim limit")
    valid = {c.id for c in envelope.claims}
    unknown = [x for x in draft.used_claim_ids if x not in valid]
    if unknown:
        raise ValueError(f"Unknown claim ids: {unknown}")
    if len(set(draft.used_claim_ids)) != len(draft.used_claim_ids):
        raise ValueError("Duplicate claim ids")
    if len(draft.used_claim_ids) > 8:
        raise ValueError("Renderer may cite at most 8 governed claims")
    for claim in envelope.claims:
        if claim.runtime_observed and claim.evidence != "RUNTIME_VERIFIED":
            raise ValueError(
                f"Runtime-observed claim {claim.id} must carry RUNTIME_VERIFIED evidence"
            )
    for limitation in draft.acknowledged_limitations:
        if limitation not in envelope.limitations:
            raise ValueError("Acknowledged limitation not present in envelope")
    if envelope.status.value in {"DEFERRED", "BLOCKED", "CLARIFICATION_REQUIRED"} and envelope.limitations:
        missing = [x for x in envelope.limitations if x not in draft.acknowledged_limitations]
        if missing and envelope.status.value == "DEFERRED":
            raise ValueError("Deferred answer must preserve limitations")
    return True
