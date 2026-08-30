from pathlib import Path

from agent.knowledge.hybrid import ClaimAuthorityMatrix

ROOT=Path(__file__).resolve().parents[1]


def test_structured_authority_wins_over_rag_for_owner():
    matrix=ClaimAuthorityMatrix(ROOT)
    decision=matrix.decide('ownership', [
        {'source':'knowledge_rag','value':'old owner'},
        {'source':'datahub','value':'current owner'},
    ])
    assert decision.accepted and decision.primary_source == 'datahub'


def test_rag_cannot_fill_missing_runtime_status():
    matrix=ClaimAuthorityMatrix(ROOT)
    decision=matrix.decide('runtime_status', [{'source':'knowledge_rag','value':'healthy'}])
    assert decision.accepted is False


def test_rag_owns_design_decision():
    matrix=ClaimAuthorityMatrix(ROOT)
    decision=matrix.decide('design_decision', [{'source':'knowledge_rag','value':'why'}])
    assert decision.accepted and decision.primary_source == 'knowledge_rag'
