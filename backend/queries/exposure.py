"""Exposure (EX) helpers."""
from __future__ import annotations

from typing import Optional

from backend.graph.nodes import Record
from backend.normalization.values import parse_number


def exposure_records(core, usubjid: str) -> list[Record]:
    return core.idx.records(usubjid, "EX")


def dose_of(rec: Record) -> Optional[float]:
    return parse_number(rec.get("EXDOSE"))


def arm_of(core, usubjid: str) -> Optional[str]:
    dm = core.dm(usubjid)
    return (dm.get("ARM") or "").strip().upper() if dm else None


def exposure_summary(core, rec: Record) -> dict:
    d = rec.as_dict()
    d["_dose"] = dose_of(rec)
    return d


def dosing_visits(core) -> list[str]:
    """Visit codes at which dosing is recorded anywhere in the study, in schedule order."""
    visits = {(r.get("VISIT") or "").upper().replace(" ", "") for r in core.idx.by_domain.get("EX", [])}
    visits.discard("")
    sched = getattr(core.rules, "visit_schedule", {}) if core.rules else {}
    return sorted(visits, key=lambda v: (sched.get(v, 10**6), v))
