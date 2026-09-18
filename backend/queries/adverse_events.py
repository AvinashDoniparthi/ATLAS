"""Adverse event (AE) helpers with protocol-derived seriousness."""
from __future__ import annotations

from typing import Optional

from backend.graph.nodes import Record


def _yes(v: Optional[str]) -> bool:
    return (v or "").strip().upper() in {"Y", "YES", "TRUE", "1"}


def ae_records(core, usubjid: str) -> list[Record]:
    return core.idx.records(usubjid, "AE")


def is_serious(core, rec: Record) -> tuple[bool, str]:
    """(serious, subcode). Serious if AESER=Y, or AESHOSP=Y when the protocol says so.

    Falls back to AESER alone when no protocol rule is available (subcode notes it).
    """
    coded = _yes(rec.get("AESER"))
    hosp = _yes(rec.get("AESHOSP"))
    rules = getattr(core, "rules", None)
    hosp_rule = bool(getattr(rules, "sae_hosp_flag_rule", False)) if rules is not None else False
    if hosp_rule and hosp and not coded:
        return True, "SAE_MISCODED"
    if coded:
        return True, "SAE_CODED"
    if hosp and not hosp_rule:
        return False, "HOSP_FLAG_NO_RULE"
    return False, "NOT_SERIOUS"


def term(rec: Record) -> str:
    return (rec.get("AETERM") or "").strip()


def matches_ae(core, rec: Record, term_substr: Optional[str] = None, serious: Optional[bool] = None,
               severity: Optional[str] = None) -> bool:
    if term_substr and term_substr.upper() not in term(rec).upper():
        return False
    if serious is not None and is_serious(core, rec)[0] != serious:
        return False
    if severity and severity.upper() not in (rec.get("AESEV") or "").upper():
        return False
    return True


def ae_summary(core, rec: Record) -> dict:
    serious, sub = is_serious(core, rec)
    d = rec.as_dict()
    d["_serious_derived"] = serious
    d["_serious_subcode"] = sub
    return d
