from .contracts import AnswerDraft, ClaimKind


def render_deterministic(envelope):
    visible = {
        ClaimKind.SEMANTIC_QUERY_PLAN,
        ClaimKind.QUERY_RESULT,
        ClaimKind.CLARIFICATION_REQUEST,
        ClaimKind.DEFINITION,
        ClaimKind.FORMULA,
        ClaimKind.RELATIONSHIP,
        ClaimKind.GOVERNANCE,
        ClaimKind.LINEAGE,
        ClaimKind.AUTOMATION_CONTRACT,
        ClaimKind.DISCOVERY,
        ClaimKind.SESSION_STATE,
        ClaimKind.ANOMALY_OBSERVATION,
        ClaimKind.OPERATIONAL_HEALTH,
        ClaimKind.DIAGNOSTIC_CLASSIFICATION,
        ClaimKind.DRIVER_ATTRIBUTION,
        ClaimKind.INCIDENT_EVIDENCE,
        ClaimKind.RECOVERY_STATUS,
        ClaimKind.INCIDENT_RESPONSE_PLAN,
        ClaimKind.ACTION_AUTHORITY,
        ClaimKind.APPROVAL_STATUS,
        ClaimKind.APPROVAL_AUDIT,
    }
    texts = []
    ids = []
    for claim in envelope.claims:
        if claim.kind in visible:
            texts.append(claim.text)
            ids.append(claim.id)
    if envelope.limitations:
        texts.extend(envelope.limitations)
    return AnswerDraft(
        answer="\n\n".join(dict.fromkeys(texts)) or envelope.status.value,
        used_claim_ids=tuple(ids[:8]),
        acknowledged_limitations=tuple(envelope.limitations),
    )
