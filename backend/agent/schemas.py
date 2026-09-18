"""Backend-internal answer model. Independent of starter/schemas.py.

``stage1/atlas.py`` is the only place that converts these to the official
``Answer``/``RecordRef``; swapping the starter touches that one function.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Union

from backend.graph.nodes import DocRefKey, RecordKey

InternalRef = Union[RecordKey, DocRefKey]

# answer-status vocabulary (see plan §18)
STATUS_OK = "ok"                      # non-empty factual answer
STATUS_NO_MATCH = "no_match"          # searched, nothing qualifies -> honest empty
STATUS_INSUFFICIENT = "insufficient"  # data cannot support a determination
STATUS_AMBIGUOUS = "ambiguous"        # question not understood / underspecified
STATUS_ERROR = "error"                # internal failure (still schema-valid)


@dataclass
class QueryTrace:
    kind: str = "unknown"
    tools: list[str] = field(default_factory=list)
    cut: Optional[int] = None
    protocol_version: Optional[int] = None
    records_inspected: int = 0
    evidence_selected: int = 0
    evidence_dropped: int = 0
    validation: dict[str, Any] = field(default_factory=dict)
    filters: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    ms: float = 0.0
    llm_used: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "tools": list(self.tools),
            "cut": self.cut,
            "protocol_version": self.protocol_version,
            "records_inspected": self.records_inspected,
            "evidence_selected": self.evidence_selected,
            "evidence_dropped": self.evidence_dropped,
            "validation": dict(self.validation),
            "filters": dict(self.filters),
            "notes": list(self.notes),
            "ms": round(self.ms, 2),
            "llm_used": self.llm_used,
        }


@dataclass
class InternalAnswer:
    question_id: str
    kind: str                                  # count | lookup | finding | trap | doc | patient360 | unknown
    status: str                                # STATUS_* above
    answer: Any                                # int | list[str] | list[InternalRef] | str | None
    text: str
    evidence: list[InternalRef] = field(default_factory=list)
    confidence: float = 0.0
    steps_used: int = 0
    tokens_used: int = 0
    trace: QueryTrace = field(default_factory=QueryTrace)
    payload: dict[str, Any] = field(default_factory=dict)  # machine-readable finding chain for demos/debug
