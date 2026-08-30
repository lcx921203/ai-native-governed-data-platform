from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from agent.semantic_query.contracts import SemanticQuerySpec, SemanticQueryPlan, SemanticQueryResult
class ContinuationStatus(str,Enum):
    READY="READY"; COMPLETE="COMPLETE"; DEFERRED="DEFERRED"; BLOCKED="BLOCKED"; ERROR="ERROR"; CLARIFICATION_REQUIRED="CLARIFICATION_REQUIRED"; REJECTED="REJECTED"
@dataclass(frozen=True)
class ContinuationCandidate:
    id:str; dimension:str; value:str; score:float; mode:str; evidence:str; source_mode:str
    def to_dict(self): return {"id":self.id,"dimension":self.dimension,"value":self.value,"score":self.score,"mode":self.mode,"evidence":self.evidence,"source_mode":self.source_mode}
@dataclass(frozen=True)
class ClarificationContinuation:
    continuation_id:str; original_question:str; base_spec:SemanticQuerySpec; raw_value:str; dimension_hint:str|None; candidates:tuple[ContinuationCandidate,...]; clarification_prompt:str; evidence:str; source_mode:str; integrity_checksum:str; contract_version:int=1
    def to_dict(self): return {"continuation_id":self.continuation_id,"contract_version":self.contract_version,"original_question":self.original_question,"base_spec":self.base_spec.to_dict(),"raw_value":self.raw_value,"dimension_hint":self.dimension_hint,"candidates":[c.to_dict() for c in self.candidates],"clarification_prompt":self.clarification_prompt,"evidence":self.evidence,"source_mode":self.source_mode,"integrity_checksum":self.integrity_checksum}
@dataclass
class ContinuationResult:
    status:ContinuationStatus; continuation:ClarificationContinuation; user_reply:str; selected_candidate:ContinuationCandidate|None=None; plan:SemanticQueryPlan|None=None; query_result:SemanticQueryResult|None=None; warnings:tuple[str,...]=()
    def to_dict(self): return {"status":self.status.value,"continuation":self.continuation.to_dict(),"user_reply":self.user_reply,"selected_candidate":self.selected_candidate.to_dict() if self.selected_candidate else None,"plan":self.plan.to_dict() if self.plan else None,"query_result":self.query_result.to_dict() if self.query_result else None,"warnings":list(self.warnings)}
