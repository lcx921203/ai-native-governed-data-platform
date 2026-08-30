from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class SemanticQueryStatus(str, Enum):
    READY="READY"; COMPLETE="COMPLETE"; DEFERRED="DEFERRED"; BLOCKED="BLOCKED"; ERROR="ERROR"; CLARIFICATION_REQUIRED="CLARIFICATION_REQUIRED"

class SemanticFilterOperator(str, Enum):
    EQ="EQ"

@dataclass(frozen=True)
class SemanticDimensionFilter:
    dimension: str
    operator: SemanticFilterOperator
    value: str
    source: str = "governed_value_alias"
    def to_dict(self)->dict[str,Any]:
        return {"dimension":self.dimension,"operator":self.operator.value,"value":self.value,"source":self.source}

@dataclass(frozen=True)
class SemanticQuerySpec:
    metric: str
    start_time: str
    end_time: str
    metrics: tuple[str,...]=()
    group_by: tuple[str,...]=()
    filters: tuple[SemanticDimensionFilter,...]=()
    limit: int=20
    @property
    def metric_names(self)->tuple[str,...]:
        return self.metrics or (self.metric,)
    def to_dict(self)->dict[str,Any]:
        return {"metric":self.metric,"metrics":list(self.metric_names),"start_time":self.start_time,"end_time":self.end_time,"group_by":list(self.group_by),"filters":[f.to_dict() for f in self.filters],"limit":self.limit}

@dataclass(frozen=True)
class SemanticQueryClarification:
    kind: str
    raw_value: str
    dimension_hint: str|None
    candidates: tuple[dict[str,Any],...]
    evidence: str
    source_mode: str
    prompt: str
    def to_dict(self)->dict[str,Any]:
        return {"kind":self.kind,"raw_value":self.raw_value,"dimension_hint":self.dimension_hint,"candidates":[dict(x) for x in self.candidates],"evidence":self.evidence,"source_mode":self.source_mode,"prompt":self.prompt}

@dataclass
class SemanticQueryPlan:
    status: SemanticQueryStatus
    question: str
    spec: SemanticQuerySpec|None=None
    warnings: list[str]=field(default_factory=list)
    command_preview: list[str]=field(default_factory=list)
    continuation_spec: SemanticQuerySpec|None=None
    clarification: SemanticQueryClarification|None=None
    def to_dict(self)->dict[str,Any]:
        return {"status":self.status.value,"question":self.question,"spec":self.spec.to_dict() if self.spec else None,"warnings":list(self.warnings),"requires_metricflow_explain": self.status is SemanticQueryStatus.READY,"command_preview":list(self.command_preview),"continuation_spec":self.continuation_spec.to_dict() if self.continuation_spec else None,"clarification":self.clarification.to_dict() if self.clarification else None}

@dataclass
class SemanticQueryResult:
    status: SemanticQueryStatus
    evidence: str
    plan: SemanticQueryPlan
    rows: list[dict[str,str]]=field(default_factory=list)
    columns: list[str]=field(default_factory=list)
    warnings: list[str]=field(default_factory=list)
    validation: str=""
    def to_dict(self)->dict[str,Any]:
        return {"status":self.status.value,"evidence":self.evidence,"plan":self.plan.to_dict(),"rows":self.rows,"columns":self.columns,"row_count":len(self.rows),"warnings":list(self.warnings),"validation":self.validation}
