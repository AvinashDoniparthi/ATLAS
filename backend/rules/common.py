"""Shared helpers for the deterministic rule modules.

Every rule reads its parameters from ``core.rules`` (the ProtocolRules parsed
from the protocol version in force at the built cut). Nothing study-specific
is hard-coded here: thresholds, windows, doses, classes and visit days all come
from the documents; subjects, sites and records all come from the graph.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from backend.graph.nodes import DocRefKey, EvidenceItem, Record, RecordKey
from backend.normalization.dates import parse_date
from backend.protocol.protocol_loader import normalize_visit_code

# Clinical vocabulary used to find records; these are CDISC test codes, not study values.
TRANSAMINASE_TESTS = ("ALT", "AST")
BILIRUBIN_TESTS = ("BILI", "TBIL")
CREATININE_TESTS = ("CREAT",)
HBA1C_TESTS = ("HBA1C",)
SCREENING_CODE = "SCREENING"
BASELINE_CODE = "BASELINE"


def rules_of(core) -> Any:
    return getattr(core, "rules", None)


def rule_availability(core, code: str) -> tuple[bool, str]:
    """Whether the protocol parameters needed by ``code`` are available.

    Lets the query layer distinguish "no findings" (honest empty) from
    "rule unavailable" (insufficient data).
    """
    r = rules_of(core)
    if r is None:
        return False, "no protocol rules resolved for this cut"
    if code == "HYS_LAW_CANDIDATE":
        if r.hys_transaminase_multiple is None or r.hys_bili_multiple is None:
            return False, "Hy's law multiples not found in protocol"
        if r.hys_window_days is None:
            return False, "Hy's law time window not found in protocol"
        if not getattr(core.ref_ranges, "loaded", False):
            return False, "reference ranges unavailable"
        return True, "ok"
    if code == "SERIOUS_AE":
        return True, "ok"  # AESER coding is always usable; AESHOSP rule adds to it
    if code == "AE_BEFORE_FIRST_DOSE":
        return True, "ok"
    if code in ("DOSING_ERROR", "MISSING_DOSE"):
        if not r.expected_dose:
            return False, "expected doses not found in protocol"
        return True, "ok"
    if code == "VISIT_WINDOW_DEVIATION":
        if r.visit_window_days is None:
            return False, "visit window not found in protocol"
        if not r.visit_schedule:
            return False, "visit schedule not found in protocol"
        return True, "ok"
    if code == "PROHIBITED_MEDICATION":
        if "prohibited" not in r.refs and not r.prohibited_classes:
            return False, "prohibited medication section not found in protocol"
        return True, "ok"
    if code == "ELIGIBILITY_VIOLATION":
        if r.age_min is None and r.hba1c_min is None and r.hepatic_uln_multiple is None:
            return False, "no eligibility criteria found in protocol"
        return True, "ok"
    if code == "DUPLICATE_SUBJECT":
        return True, "ok"
    return False, f"unknown rule {code}"


def protocol_ref(core, rule_name: str) -> Optional[DocRefKey]:
    r = rules_of(core)
    if r is None:
        return None
    ref = r.refs.get(rule_name)
    if ref is None:
        return None
    return DocRefKey(document=ref.document, section=ref.section)


def protocol_evidence(core, rule_name: str, why: str) -> list[EvidenceItem]:
    ref = protocol_ref(core, rule_name)
    if ref is None:
        return []
    return [EvidenceItem(ref=ref, why=why, check=lambda: True, role="rule")]


def lab_manual_units_ref(core) -> Optional[DocRefKey]:
    """Citation for the applicable lab manual's unit statement (when a conversion was applied)."""
    resolver = getattr(core, "resolver", None)
    if resolver is None:
        return None
    try:
        manual = resolver.lab_manual_for_cut(core.cut())
    except Exception:  # noqa: BLE001
        return None
    if manual is None:
        return None
    sec = manual.section_for("units")
    return DocRefKey(document=manual.name, section=sec.key if sec else "body")


def rec_evidence(rec: Record, why: str, check, role: str = "support") -> EvidenceItem:
    return EvidenceItem(ref=rec.key, why=why, check=check, role=role)


def first_dose_date(core, usubjid: str) -> tuple[Optional[date], Optional[Record]]:
    """Reference start date for a subject: DM.RFSTDTC (or earliest EX date as fallback)."""
    dm = core.dm(usubjid)
    if dm is not None:
        pd = parse_date(dm.get("RFSTDTC"))
        if pd.ok and pd.date:
            return pd.date, dm
    ex_dates = core.idx.by_subject_date.get((usubjid, "EX"), [])
    if ex_dates:
        return ex_dates[0][0], ex_dates[0][1]
    return None, dm


def visit_code(rec: Record) -> str:
    return normalize_visit_code(rec.get("VISIT"))


def arm_of(core, usubjid: str) -> Optional[str]:
    dm = core.dm(usubjid)
    if dm is None:
        return None
    return (dm.get("ARM") or dm.get("ACTARM") or dm.get("TRT") or "").strip().upper() or None


def sort_findings(findings: list) -> list:
    def k(f):
        seqs = tuple(sorted((r.seq if r.seq is not None else -1) for r in f.record_refs()))
        return (f.usubjid or "", f.code, seqs)

    return sorted(findings, key=k)


def key_tuple(k: RecordKey) -> tuple:
    return k.as_tuple()
