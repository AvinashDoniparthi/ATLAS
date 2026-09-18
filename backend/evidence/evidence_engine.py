"""Assemble validated evidence for a set of findings and derive confidence.

Confidence is evidence-driven only: rule availability, evidence validity, and
finding flags move it; nothing an LLM says ever does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from backend.evidence.record_refs import dedupe_sorted
from backend.evidence.validator import ValidationResult, validate
from backend.graph.nodes import DocRefKey, EvidenceItem, Finding, RecordKey

BASE_CONFIDENCE = {
    "count": 0.9,
    "lookup": 0.9,
    "finding": 0.9,
    "doc": 0.85,
    "patient360": 0.9,
    "no_match": 0.85,
    "insufficient": 0.3,
    "ambiguous": 0.2,
    "error": 0.05,
}

CONF_MIN, CONF_MAX = 0.05, 0.95


def clamp(c: float) -> float:
    return max(CONF_MIN, min(CONF_MAX, round(c, 3)))


def confidence_for(
    kind: str,
    *,
    empty: bool = False,
    evidence_dropped: bool = False,
    flagged: bool = False,
    rules_missing: bool = False,
    insufficient: bool = False,
    ambiguous: bool = False,
    extra_penalty: float = 0.0,
) -> float:
    if ambiguous:
        base = BASE_CONFIDENCE["ambiguous"]
    elif insufficient:
        base = BASE_CONFIDENCE["insufficient"]
    elif empty:
        base = BASE_CONFIDENCE["no_match"]
    else:
        base = BASE_CONFIDENCE.get(kind, 0.7)
    if evidence_dropped:
        base -= 0.1
    if flagged:
        base -= 0.1
    if rules_missing:
        base -= 0.15
    base -= extra_penalty
    return clamp(base)


@dataclass
class Assembled:
    refs: list[RecordKey | DocRefKey] = field(default_factory=list)
    kept: list[Finding] = field(default_factory=list)
    dropped_findings: list[tuple[Finding, str]] = field(default_factory=list)
    validation: ValidationResult = field(default_factory=ValidationResult)
    flagged: bool = False

    @property
    def evidence_dropped(self) -> bool:
        return bool(self.validation.dropped) or bool(self.dropped_findings)

    @property
    def confidence_adjust(self) -> float:
        adj = 0.0
        if self.evidence_dropped:
            adj -= 0.1
        if self.flagged:
            adj -= 0.1
        return adj

    def summary(self) -> dict:
        d = self.validation.summary()
        d["findings_kept"] = len(self.kept)
        d["findings_dropped"] = len(self.dropped_findings)
        return d


def _ordered_refs(items: Iterable[EvidenceItem]) -> list[RecordKey | DocRefKey]:
    support = dedupe_sorted(i.ref for i in items if i.role == "support" and isinstance(i.ref, RecordKey))
    context = dedupe_sorted(i.ref for i in items if i.role != "support" and isinstance(i.ref, RecordKey))
    docs = dedupe_sorted(i.ref for i in items if isinstance(i.ref, DocRefKey))
    out: list = []
    seen: set = set()
    for group in (support, context, docs):
        for r in group:
            if r not in seen:
                seen.add(r)
                out.append(r)
    return out


def assemble(core, findings: Iterable[Finding], include_context: bool = True, include_docs: bool = True) -> Assembled:
    """Validate every finding's evidence; drop findings that lose all support."""
    out = Assembled()
    all_items: list[EvidenceItem] = []
    for f in findings:
        res = validate(core, f.evidence)
        out.validation.valid.extend(res.valid)
        out.validation.dropped.extend(res.dropped)
        support_records = [i for i in res.valid if i.role == "support" and isinstance(i.ref, RecordKey)]
        had_support = any(i.role == "support" and isinstance(i.ref, RecordKey) for i in f.evidence)
        if had_support and not support_records:
            out.dropped_findings.append((f, "no_valid_support_evidence"))
            continue
        if f.flags or f.status != "confirmed":
            out.flagged = True
        out.kept.append(f)
        for i in res.valid:
            if isinstance(i.ref, DocRefKey) and not include_docs:
                continue
            if isinstance(i.ref, RecordKey) and i.role != "support" and not include_context:
                continue
            all_items.append(i)
    out.refs = _ordered_refs(all_items)
    return out


def assemble_items(core, items: Iterable[EvidenceItem]) -> tuple[list[RecordKey | DocRefKey], ValidationResult]:
    """Validate a flat list of evidence items (counts/lookups) and return ordered refs."""
    res = validate(core, items)
    return _ordered_refs(res.valid), res
