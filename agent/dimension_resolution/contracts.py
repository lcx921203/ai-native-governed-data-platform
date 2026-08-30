from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
class DimensionResolutionStatus(str,Enum):
    RESOLVED="RESOLVED"; CLARIFICATION_REQUIRED="CLARIFICATION_REQUIRED"; NOT_FOUND="NOT_FOUND"; BLOCKED="BLOCKED"; ERROR="ERROR"
class DimensionResolutionMode(str,Enum):
    NONE="NONE"; CANONICAL_EXACT="CANONICAL_EXACT"; NORMALIZED_EXACT="NORMALIZED_EXACT"; ALIAS_EXACT="ALIAS_EXACT"; FUZZY_CANDIDATE="FUZZY_CANDIDATE"
@dataclass(frozen=True)
class DimensionValueCandidate:
    dimension:str; value:str; score:float; mode:DimensionResolutionMode; evidence:str; source_mode:str
    def to_dict(self): return {"dimension":self.dimension,"value":self.value,"score":self.score,"mode":self.mode.value,"evidence":self.evidence,"source_mode":self.source_mode}
@dataclass
class DimensionResolutionResult:
    status:DimensionResolutionStatus; raw_value:str; metrics:tuple[str,...]; dimension_hint:str|None=None; resolved_dimension:str|None=None; resolved_value:str|None=None; mode:DimensionResolutionMode=DimensionResolutionMode.NONE; evidence:str="STATIC_CONTRACT"; source_mode:str=""; candidates:list[DimensionValueCandidate]=field(default_factory=list); warnings:list[str]=field(default_factory=list)
    def to_dict(self): return {"status":self.status.value,"raw_value":self.raw_value,"metrics":list(self.metrics),"dimension_hint":self.dimension_hint,"resolved_dimension":self.resolved_dimension,"resolved_value":self.resolved_value,"mode":self.mode.value,"evidence":self.evidence,"source_mode":self.source_mode,"candidates":[c.to_dict() for c in self.candidates],"warnings":list(self.warnings)}
