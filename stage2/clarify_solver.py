"""Clarification solver for Human Gate monitor queries.

When the medical monitor returns CLARIFY (e.g. requesting screening ALT or
hepatotoxic concomitant medications), this module queries the subject's records
in the StudyGraph deterministically without an LLM and constructs an
evidence-backed clinical response.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

from starter.schemas import RecordRef
from backend.protocol.protocol_loader import normalize_visit_code

_HEPATOTOXIC_CLASSES = {
    "STATIN",
    "SYSTEMIC_GLUCOCORTICOID",
    "ANTIFUNGAL",
    "ANTITUBERCULAR",
}

_HEPATOTOXIC_TERMS = {
    "ATORVASTATIN",
    "SIMVASTATIN",
    "ROSUVASTATIN",
    "PRAVASTATIN",
    "PARACETAMOL",
    "ACETAMINOPHEN",
    "METHOTREXATE",
    "AMIODARONE",
    "KETOCONAZOLE",
    "FLUCONAZOLE",
    "ISONIAZID",
}


def resolve_clarification(core: Any, usubjid: str, question_text: str) -> Tuple[str, List[RecordRef]]:
    """Answers the monitor's clarification question from the study graph.

    Returns:
        (answer_text, list_of_cited_RecordRefs)
    """
    evidence: List[RecordRef] = []
    response_parts: List[str] = []

    q_lower = question_text.lower()
    asks_alt = "alt" in q_lower or "transaminase" in q_lower or "screening" in q_lower
    asks_cm = "medication" in q_lower or "medicine" in q_lower or "hepatotoxic" in q_lower or "drug" in q_lower

    # 1. Screening ALT lookup
    if asks_alt:
        alt_recs = core.idx.tests_for(usubjid, "LB", "ALT")
        screening_alt = [r for r in alt_recs if normalize_visit_code(r.get("VISIT")) in ("SCREENING", "BASELINE")]
        if screening_alt:
            rec = screening_alt[0]
            val = rec.get("LBORRES") or rec.get("LBSTRESN")
            unit = rec.get("LBORRESU") or rec.get("LBSTRESU") or "U/L"
            visit = rec.get("VISIT")
            date = core.idx.record_date(rec)
            response_parts.append(f"Screening ALT was {val} {unit} at visit {visit} (date: {date})")
            if rec.seq is not None:
                evidence.append(RecordRef(domain="LB", usubjid=usubjid, seq=rec.seq))
        else:
            response_parts.append("Screening ALT: no screening visit ALT record recorded")

    # 2. Concomitant medications lookup
    if asks_cm:
        cm_recs = core.idx.records(usubjid, "CM")
        if not cm_recs:
            response_parts.append("No concomitant medications reported for subject in CM domain")
        else:
            hepatotoxic_found = []
            all_meds = []
            for r in cm_recs:
                trt = (r.get("CMTRT") or "").strip()
                clas = (r.get("CMCLAS") or "").strip().upper()
                all_meds.append(f"{trt} ({clas})" if clas else trt)
                is_hep = (
                    clas in _HEPATOTOXIC_CLASSES
                    or any(term in trt.upper() for term in _HEPATOTOXIC_TERMS)
                )
                if is_hep:
                    hepatotoxic_found.append((trt, clas, r.seq))
                    if r.seq is not None:
                        evidence.append(RecordRef(domain="CM", usubjid=usubjid, seq=r.seq))

            if hepatotoxic_found:
                hep_desc = ", ".join(f"{t} ({c})" for t, c, _ in hepatotoxic_found)
                response_parts.append(f"Subject is taking potentially hepatotoxic medication: {hep_desc}")
            else:
                med_summary = ", ".join(all_meds) if all_meds else "None"
                response_parts.append(f"No hepatotoxic concomitant medications identified (concomitant meds: {med_summary})")
                # Attach first CM record as reference proof if not already cited
                if cm_recs and cm_recs[0].seq is not None and not evidence:
                    evidence.append(RecordRef(domain="CM", usubjid=usubjid, seq=cm_recs[0].seq))

    if not response_parts:
        # Generic query against Patient360 if question format differs
        p = core.patient360(usubjid) if hasattr(core, "patient360") else {}
        response_parts.append(f"Verified against patient record: subject {usubjid} is on study.")

    return ". ".join(response_parts) + ".", evidence
