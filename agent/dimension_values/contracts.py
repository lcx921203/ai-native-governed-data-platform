from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
class DimensionValueStatus(str,Enum):
    READY="READY"; COMPLETE="COMPLETE"; DEFERRED="DEFERRED"; BLOCKED="BLOCKED"; ERROR="ERROR"; CLARIFICATION_REQUIRED="CLARIFICATION_REQUIRED"
@dataclass(frozen=True)
class DimensionValueSpec:
    metrics: tuple[str,...]; dimension: str; start_time: str|None=None; end_time: str|None=None; limit:int=25
    def to_dict(self): return {"metrics":list(self.metrics),"dimension":self.dimension,"start_time":self.start_time,"end_time":self.end_time,"limit":self.limit}
@dataclass
class DimensionValuePlan:
    status:DimensionValueStatus; question:str=""; spec:DimensionValueSpec|None=None; warnings:list[str]=field(default_factory=list); command_preview:list[str]=field(default_factory=list)
    def to_dict(self): return {"status":self.status.value,"question":self.question,"spec":self.spec.to_dict() if self.spec else None,"warnings":list(self.warnings),"command_preview":list(self.command_preview)}
@dataclass
class DimensionValueResult:
    status:DimensionValueStatus; evidence:str; plan:DimensionValuePlan; values:list[str]=field(default_factory=list); source_mode:str=""; warnings:list[str]=field(default_factory=list); truncated:bool=False; validation:str=""
    def to_dict(self): return {"status":self.status.value,"evidence":self.evidence,"plan":self.plan.to_dict(),"values":list(self.values),"source_mode":self.source_mode,"warnings":list(self.warnings),"truncated":self.truncated,"validation":self.validation}
