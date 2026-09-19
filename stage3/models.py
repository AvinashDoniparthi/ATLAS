"""Pydantic v2 schemas and models for Stage 3 — WATCH.

Reuses RecordRef from starter.schemas and TraceEntry / AlternativeConsidered from stage2.schemas.
Documented as reconstructed compatibility models following the Stage 1 & 2 conventions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from starter.schemas import RecordRef
from stage2.schemas import AlternativeConsidered, TraceEntry


class WatchTraceEntry(TraceEntry):
    """Extended audit trace entry with decision linkages and cut sequencing."""
    trace_id: str
    decision_id: Optional[str] = None
    cut: int = 1
    sequence: int = 1
    state: Optional[str] = None
    alternatives: List[AlternativeConsidered] = Field(default_factory=list)
    status: Optional[str] = None


class Decision(BaseModel):
    """A deterministic auditable surveillance decision made at a cut."""
    decision_id: str
    cut: int
    decision_type: str  # FINDING | ESCALATION | QUERY | HUMAN_GATE | LAB_UNIT_SHIFT | SITE_QUARANTINE | DOCUMENT_CHANGE | DEGRADATION | CORRECTION_RETRACT | STANDING_LIMITS
    target: str
    code: str
    status: str  # CONFIRMED | PENDING | UNRESOLVED | QUARANTINED | REJECTED | APPROVED | CLARIFY | STANDING_LIMITS | DATA_INTEGRITY | RESOLVED
    reason: str
    evidence: List[RecordRef] = Field(default_factory=list)
    alternatives: List[AlternativeConsidered] = Field(default_factory=list)
    timestamp: str
    status_history: List[Dict[str, Any]] = Field(default_factory=list)


class CutResult(BaseModel):
    """Execution metrics and summary for a single surveillance cut."""
    cut: int
    protocol_version: int
    new_records: int
    corrections: int
    findings_count: int
    serious_findings_count: int
    escalations_count: int
    queries_count: int
    budget_ms_used: float
    degradation_tier: str
    quarantined_sites: List[str] = Field(default_factory=list)
    untrusted_labs: int = 0


class BudgetState(BaseModel):
    """Resource consumption and degradation status."""
    total_ms: float
    spent_ms: float
    remaining_ms: float
    fraction_used: float
    tier: str  # FULL | REDUCED | ESSENTIAL


class SiteRisk(BaseModel):
    """Statistical evaluation of site data integrity / anomaly."""
    site: str
    cv_score: float
    regular_signals: List[str] = Field(default_factory=list)
    quarantined: bool = False
    rationale: str = ""


class LabIntegrityEvent(BaseModel):
    """Lab distribution or unit-shift anomaly event."""
    site: str
    analyte: str
    cut: int
    baseline_ratio: float
    current_ratio: float
    fold_change: float
    matched_factor: Optional[float] = None
    untrusted_keys: List[str] = Field(default_factory=list)


class DocumentChange(BaseModel):
    """Document tampering or amendment transition event."""
    document: str
    cut: int
    old_hash: Optional[str] = None
    new_hash: str
    instruction_like_sentences: List[str] = Field(default_factory=list)
    executed: bool = False


class HumanGateItem(BaseModel):
    """Item tracked in the human medical monitor state machine."""
    escalation_id: str
    fingerprint: str
    code: str
    target: str
    detected_at_cut: int
    escalated_at_cut: int
    submitted_cuts: List[int] = Field(default_factory=list)
    reply_cut: Optional[int] = None
    decision: str  # PENDING | APPROVED | REJECTED | CLARIFY | STANDING_LIMITS
    reason: Optional[str] = None
    cuts_pending: int = 0
    clarification_question: Optional[str] = None
    clarification_answer: Optional[str] = None
    action_taken: Optional[str] = None


class Explanation(BaseModel):
    """Trace-backed explanation for a decision."""
    decision_id: str
    evidence: List[RecordRef] = Field(default_factory=list)
    evidence_lines: List[str] = Field(default_factory=list)
    alternatives: List[AlternativeConsidered] = Field(default_factory=list)
    why: str
    consistent_with_trace: bool
    cut: int
    status: str
    trace_ids: List[str] = Field(default_factory=list)
    evidence_available: bool = False


class SurveillanceReport(BaseModel):
    """Comprehensive Stage 3 surveillance report across all cuts."""
    period: str
    cuts_evaluated: List[int] = Field(default_factory=list)
    protocol_transitions: List[Dict[str, Any]] = Field(default_factory=list)
    total_findings: int = 0
    serious_findings: int = 0
    escalations_by_status: Dict[str, int] = Field(default_factory=dict)
    queries_raised: int = 0
    quarantined_sites: List[str] = Field(default_factory=list)
    untrusted_records_count: int = 0
    document_changes: List[DocumentChange] = Field(default_factory=list)
    budget_summary: Dict[str, Any] = Field(default_factory=dict)
    cut_summaries: List[CutResult] = Field(default_factory=list)
    timestamp: str = ""
