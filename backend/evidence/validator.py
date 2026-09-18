"""Evidence validator — every citation is checked before it leaves the system.

A record citation is valid only if the record exists in the active cut view AND
its claim predicate (``EvidenceItem.check``) still holds. A document citation is
valid only if the document and section are loaded. Anything else is dropped and
reported; it is never returned to the caller.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from backend.graph.nodes import DocRefKey, EvidenceItem, RecordKey

log = logging.getLogger("atlas.evidence")


@dataclass
class ValidationResult:
    valid: list[EvidenceItem] = field(default_factory=list)
    dropped: list[tuple[EvidenceItem, str]] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        total = len(self.valid) + len(self.dropped)
        return 1.0 if total == 0 else len(self.valid) / total

    @property
    def ok(self) -> bool:
        return not self.dropped

    def summary(self) -> dict:
        return {"valid": len(self.valid), "dropped": len(self.dropped), "ratio": round(self.ratio, 3),
                "reasons": sorted({r for _, r in self.dropped})}


def doc_ref_valid(core, ref: DocRefKey) -> tuple[bool, str]:
    docs = getattr(core, "documents", {}) or {}
    doc = docs.get(ref.document)
    if doc is None:
        # tolerate a name given with a suffix / different case
        for name, d in docs.items():
            if name.lower() == str(ref.document).lower():
                doc = d
                break
    if doc is None:
        return False, "document_not_loaded"
    sections = getattr(doc, "sections", {}) or {}
    if ref.section in sections:
        return True, ""
    if hasattr(doc, "section_for") and doc.section_for(ref.section) is not None:
        return True, ""
    return False, "section_not_found"


def record_ref_valid(core, ref: RecordKey) -> tuple[bool, str]:
    if not core.exists(ref):
        return False, "record_not_in_cut_view"
    return True, ""


def validate(core, items: Iterable[EvidenceItem]) -> ValidationResult:
    res = ValidationResult()
    for item in items:
        ref = item.ref
        if isinstance(ref, DocRefKey):
            ok, why = doc_ref_valid(core, ref)
        elif isinstance(ref, RecordKey):
            ok, why = record_ref_valid(core, ref)
            if ok and item.check is not None:
                try:
                    ok = bool(item.check())
                    why = "" if ok else "claim_not_supported_by_record"
                except Exception as exc:  # noqa: BLE001 - a failing check is a drop, not a crash
                    ok, why = False, f"check_raised:{type(exc).__name__}"
        else:
            ok, why = False, "unknown_ref_type"
        if ok:
            res.valid.append(item)
        else:
            res.dropped.append((item, why))
            log.debug("evidence dropped %s: %s", ref, why)
    return res


def validate_refs(core, refs: Iterable[RecordKey | DocRefKey]) -> tuple[list, list[tuple]]:
    """Existence-only validation for bare references. Returns (valid, dropped(ref, reason))."""
    valid: list = []
    dropped: list[tuple] = []
    for ref in refs:
        if isinstance(ref, DocRefKey):
            ok, why = doc_ref_valid(core, ref)
        elif isinstance(ref, RecordKey):
            ok, why = record_ref_valid(core, ref)
        else:
            ok, why = False, "unknown_ref_type"
        (valid.append(ref) if ok else dropped.append((ref, why)))
    return valid, dropped
