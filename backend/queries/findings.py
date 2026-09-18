"""FINDING questions: subjects that satisfy a deterministic rule, with evidence."""
from __future__ import annotations

import logging
from typing import Optional

from backend.graph.nodes import Finding

log = logging.getLogger("atlas.queries")

KNOWN_CODES = [
    "HYS_LAW_CANDIDATE", "SERIOUS_AE", "AE_BEFORE_FIRST_DOSE", "DOSING_ERROR", "MISSING_DOSE",
    "VISIT_WINDOW_DEVIATION", "PROHIBITED_MEDICATION", "ELIGIBILITY_VIOLATION", "DUPLICATE_SUBJECT",
]

LABELS = {
    "HYS_LAW_CANDIDATE": "potential Hy's law candidates",
    "SERIOUS_AE": "serious adverse events",
    "AE_BEFORE_FIRST_DOSE": "adverse events starting before first dose",
    "DOSING_ERROR": "dosing errors",
    "MISSING_DOSE": "missing dose records",
    "VISIT_WINDOW_DEVIATION": "visit-window deviations",
    "PROHIBITED_MEDICATION": "prohibited concomitant medications",
    "ELIGIBILITY_VIOLATION": "eligibility violations",
    "DUPLICATE_SUBJECT": "duplicate subject enrolments",
}


def label(code: str) -> str:
    return LABELS.get(code, code.replace("_", " ").lower())


def _run(core, code: str) -> tuple[list[Finding], tuple[bool, str]]:
    try:
        from backend.rules import run_rule
        from backend.rules.common import rule_availability
    except ImportError as exc:  # rules package not present
        return [], (False, f"rules module unavailable: {exc}")
    try:
        avail = rule_availability(core, code)
    except Exception as exc:  # noqa: BLE001
        avail = (False, f"rule availability check failed: {type(exc).__name__}")
    try:
        findings = run_rule(core, code) or []
    except Exception as exc:  # noqa: BLE001 - a broken rule must not crash a question
        log.exception("rule %s failed", code)
        return [], (False, f"rule {code} failed: {type(exc).__name__}")
    return list(findings), tuple(avail)


def finding_subjects(core, code: str, site: Optional[str] = None, subcode: Optional[str] = None,
                     usubjid: Optional[str] = None) -> tuple[list[str], list[Finding], tuple[bool, str]]:
    """(sorted subject ids, matching findings, (rule_available, reason))."""
    findings, availability = _run(core, code)
    out: list[Finding] = []
    for f in findings:
        if subcode and str(f.details.get("subcode", "")).upper() != subcode.upper():
            continue
        if site:
            fsite = f.site or (core.site_of(f.usubjid) if f.usubjid else None)
            if (fsite or "").upper() != site.upper():
                continue
        if usubjid and f.usubjid != usubjid:
            continue
        out.append(f)
    subjects = sorted({f.usubjid for f in out if f.usubjid})
    out.sort(key=lambda f: (f.usubjid or "", f.code, str(f.details.get("subcode", ""))))
    return subjects, out, availability


def all_findings_for_subject(core, usubjid: str) -> dict[str, list[Finding]]:
    out: dict[str, list[Finding]] = {}
    for code in KNOWN_CODES:
        _, fs, avail = finding_subjects(core, code, usubjid=usubjid)
        if fs:
            out[code] = fs
    return out


def finding_summary(f: Finding) -> dict:
    from backend.evidence.record_refs import ref_tuple

    return {
        "code": f.code,
        "usubjid": f.usubjid,
        "site": f.site,
        "summary": f.summary,
        "subcode": f.details.get("subcode"),
        "details": {k: v for k, v in f.details.items() if k != "subcode"},
        "confidence": f.confidence,
        "status": f.status,
        "flags": list(f.flags),
        "evidence": [{"ref": ref_tuple(e.ref), "role": e.role, "why": e.why} for e in f.evidence],
    }
