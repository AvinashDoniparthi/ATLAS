"""Pydantic schemas and dataclasses for Stage 2 — MONITOR.

Defines the contract for the six-node review crew:
  - Medical escalations and alternatives
  - Site queries and deduplication
  - Compliance deviations and site flags
  - Live audit trace entries
  - Final ReviewReport
"""
from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, Field

from starter.schemas import RecordRef


class AlternativeConsidered(BaseModel):
    """An alternative clinical option considered and why it was accepted or rejected."""
    alternative: str
    rejected_reason: str


class MedicalEscalation(BaseModel):
    """An issue escalated to the human medical monitor."""
    escalation_id: str
    code: str
    usubjid: str
    site: Optional[str] = None
    severity: str = "HIGH"  # CRITICAL | HIGH | MEDIUM | LOW | MONITOR
    summary: str
    evidence: List[RecordRef] = Field(default_factory=list)
    alternatives: List[AlternativeConsidered] = Field(default_factory=list)
    status: str = "PENDING"  # PENDING | APPROVED | REJECTED | CLARIFICATION_REQUIRED | RESUBMITTED | MONITORING | COMPLETED
    monitor_decision: Optional[str] = None  # APPROVED | REJECTED | CLARIFY
    monitor_reason: Optional[str] = None
    clarification_requested: Optional[str] = None
    clarification_response: Optional[str] = None
    cycle: int = 1
    action_taken: Optional[str] = None


class SiteQuery(BaseModel):
    """A data-quality query dispatched to an investigational site."""
    query_id: str
    usubjid: str
    site: Optional[str] = None
    domain: str
    seq: Optional[int] = None
    cut: int
    query_text: str
    status: str = "OPEN"  # OPEN | CLOSED | ANSWERED | DUPLICATE
    reply: Optional[str] = None
    fingerprint: str = ""
    cycle: int = 1


class ComplianceDeviation(BaseModel):
    """A subject-level protocol deviation evaluated under a specific protocol version."""
    code: str
    usubjid: str
    site: Optional[str] = None
    summary: str
    protocol_version: int
    evidence: List[RecordRef] = Field(default_factory=list)
    cycle: int = 1


class SiteFlag(BaseModel):
    """A site-level recurring or systemic issue aggregating multiple subject events."""
    site: str
    issue_type: str
    subject_count: int
    subjects: List[str] = Field(default_factory=list)
    summary: str
    evidence: List[RecordRef] = Field(default_factory=list)
    cycle: int = 1


class TraceEntry(BaseModel):
    """A single real-time audit trace entry emitted by a crew node."""
    timestamp: str
    cycle: int
    node: str  # detect | medical_review | data_manager | compliance | human_gate | execute
    action: str
    target: Optional[str] = None  # subject or site if applicable
    evidence: List[RecordRef] = Field(default_factory=list)
    reason: Optional[str] = None


class ReviewReport(BaseModel):
    """Final comprehensive report produced at the end of each review cycle."""
    cycle: int
    cut: int
    protocol_version: int
    findings: List[dict] = Field(default_factory=list)
    serious_findings: List[dict] = Field(default_factory=list)
    monitoring_only_findings: List[dict] = Field(default_factory=list)
    queries: List[SiteQuery] = Field(default_factory=list)
    escalations: List[MedicalEscalation] = Field(default_factory=list)
    compliance_deviations: List[ComplianceDeviation] = Field(default_factory=list)
    site_level_flags: List[SiteFlag] = Field(default_factory=list)
    monitor_decisions: List[dict] = Field(default_factory=list)
    completed_actions: List[str] = Field(default_factory=list)
    unresolved_issues: List[dict] = Field(default_factory=list)
    trace: List[TraceEntry] = Field(default_factory=list)
    statistics: dict[str, Any] = Field(default_factory=dict)
