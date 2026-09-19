"""Trace-backed decision explanations for Stage 3."""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from starter.schemas import RecordRef
from stage3.decisions import DecisionStore
from stage3.models import Explanation
from stage3.trace import TraceStore

log = logging.getLogger("stage3.explanations")


def _render_ref(ref: Any, core: Any) -> str:
    """Renders a human-readable display line for an evidence citation."""
    domain = getattr(ref, "domain", "")
    if not domain and isinstance(ref, dict):
        domain = ref.get("domain", "")

    if domain == "DOC":
        doc = getattr(ref, "document", None) or (ref.get("document") if isinstance(ref, dict) else "")
        sec = getattr(ref, "section", None) or (ref.get("section") if isinstance(ref, dict) else "")
        return f"DOC: {doc} §{sec}"

    usubjid = getattr(ref, "usubjid", None) or (ref.get("usubjid") if isinstance(ref, dict) else "")
    seq = getattr(ref, "seq", None) if not isinstance(ref, dict) else ref.get("seq")

    if core and hasattr(core, "record"):
        r = core.record(domain, usubjid, seq)
        if r is not None:
            details = [
                f"{k}={v}" for k, v in r.fields.items()
                if k not in ("USUBJID", "DOMAIN", "STUDYID", "cut_available", "corrected_at_cut")
            ][:3]
            det_str = f" ({', '.join(details)})" if details else ""
            return f"{domain}: {usubjid} seq {seq}{det_str}"

    return f"{domain}: {usubjid} seq {seq} [cited at decision time]"


def _ref_key(ref: Any) -> tuple:
    if hasattr(ref, "domain"):
        return (ref.domain, ref.usubjid, ref.seq, ref.document, ref.section)
    elif isinstance(ref, dict):
        return (ref.get("domain"), ref.get("usubjid"), ref.get("seq"), ref.get("document"), ref.get("section"))
    return (str(ref), None, None, None, None)


def build_explanation(
    decision_id: str,
    decision_store: DecisionStore,
    trace_store: TraceStore,
    core: Optional[Any] = None,
) -> Explanation:
    """Formulates a trace-backed explanation from stored decision and audit entries only."""
    d = decision_store.get(decision_id)
    if d is None:
        return Explanation(
            decision_id=decision_id,
            evidence=[],
            evidence_lines=[],
            alternatives=[],
            why="no stored decision found with this identifier",
            consistent_with_trace=False,
            cut=0,
            status="UNKNOWN",
            trace_ids=[],
            evidence_available=False,
        )

    entries = trace_store.for_decision(decision_id)
    evidence_lines = [_render_ref(ref, core) for ref in d.evidence]

    trace_refs = {_ref_key(r) for e in entries for r in e.evidence}
    dec_refs = {_ref_key(r) for r in d.evidence}

    # Consistency check:
    # 1. Has trace entries
    # 2. Decision evidence is a subset of trace evidence (or decision has no evidence and trace exists)
    # 3. Decision cut matches the trace entries' cut
    has_entries = len(entries) > 0
    evidence_subset = dec_refs <= trace_refs if dec_refs else True
    cut_matches = entries[0].cut == d.cut if has_entries else False

    consistent = has_entries and evidence_subset and cut_matches

    return Explanation(
        decision_id=d.decision_id,
        evidence=d.evidence,
        evidence_lines=evidence_lines,
        alternatives=d.alternatives,
        why=d.reason,
        consistent_with_trace=consistent,
        cut=d.cut,
        status=d.status,
        trace_ids=[e.trace_id for e in entries],
        evidence_available=bool(d.evidence),
    )
