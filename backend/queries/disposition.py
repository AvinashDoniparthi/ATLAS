"""Disposition (DS) helpers."""
from __future__ import annotations

from typing import Optional

from backend.graph.nodes import Record


def disposition_records(core, usubjid: str) -> list[Record]:
    return core.idx.records(usubjid, "DS")


def status_token(rec: Record) -> str:
    return (rec.get("DSDECOD") or "").strip().upper()


def reason_text(rec: Record) -> str:
    return (rec.get("DSTERM") or "").strip().upper()


def matches_disposition(rec: Record, status: Optional[str] = None, reason: Optional[str] = None) -> bool:
    """status: token compared to DSDECOD (substring, upper); reason: substring of DSTERM."""
    if status:
        if status.upper() not in status_token(rec):
            return False
    if reason:
        if reason.upper() not in reason_text(rec):
            return False
    return True


def subject_disposition(core, usubjid: str) -> Optional[dict]:
    recs = disposition_records(core, usubjid)
    if not recs:
        return None
    rec = recs[-1]
    return {
        "status": status_token(rec),
        "reason": reason_text(rec),
        "date": core.idx.record_date(rec).isoformat() if core.idx.record_date(rec) else rec.get("DSSTDTC"),
        "record": rec.key.as_tuple(),
    }
