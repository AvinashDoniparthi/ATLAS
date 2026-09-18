"""Visit-window deviations: actual visit day vs protocol schedule ± window (version-specific)."""
from __future__ import annotations

from datetime import date
from typing import Optional

from backend.graph.nodes import EvidenceItem, Finding, Record
from backend.protocol.protocol_loader import normalize_visit_code
from backend.rules.common import first_dose_date, protocol_evidence, rec_evidence, rule_availability, rules_of, sort_findings

CODE = "VISIT_WINDOW_DEVIATION"


def visit_actual_date(core, usubjid: str, visit_key: str) -> tuple[Optional[date], list[Record], int]:
    """Earliest parsed date across every record of that (subject, visit); also the records carrying it."""
    domains = core.idx.by_subject_visit.get((usubjid, visit_key), {})
    best: Optional[date] = None
    carriers: list[Record] = []
    unparseable = 0
    for recs in domains.values():
        for rec in recs:
            d = core.idx.record_date(rec)
            if d is None:
                if core.idx.date_cols.get(rec.domain) and rec.get(core.idx.date_cols[rec.domain]):
                    unparseable += 1
                continue
            if best is None or d < best:
                best, carriers = d, [rec]
            elif d == best:
                carriers.append(rec)
    return best, carriers, unparseable


def _offset(core, usubjid: str, visit_key: str, scheduled: int) -> Optional[int]:
    ref, _ = first_dose_date(core, usubjid)
    actual, _, _ = visit_actual_date(core, usubjid, visit_key)
    if ref is None or actual is None:
        return None
    return (actual - ref).days - scheduled


def find_visit_window_deviations(core) -> list[Finding]:
    ok, _ = rule_availability(core, CODE)
    if not ok:
        return []
    r = rules_of(core)
    window: int = r.visit_window_days
    sched = {normalize_visit_code(k): v for k, v in r.visit_schedule.items()}
    findings: list[Finding] = []
    for (usubjid, visit_key), _domains in core.idx.by_subject_visit.items():
        code = normalize_visit_code(visit_key)
        if code not in sched:
            continue
        scheduled = sched[code]
        ref, ref_rec = first_dose_date(core, usubjid)
        if ref is None or ref_rec is None:
            continue
        actual, carriers, unparseable = visit_actual_date(core, usubjid, visit_key)
        if actual is None:
            continue
        offset = (actual - ref).days - scheduled
        if abs(offset) <= window:
            continue
        evidence: list[EvidenceItem] = []
        for rec in sorted(carriers, key=lambda x: (x.domain, x.seq or 0))[:3]:
            def check(rec=rec, u=usubjid, vk=visit_key, s=scheduled, w=window):
                o = _offset(core, u, vk, s)
                return o is not None and abs(o) > w and core.idx.record_date(rec) is not None

            evidence.append(rec_evidence(rec, f"{code} performed {offset:+d} days from scheduled day {scheduled} (window ±{window})", check))

        def check_ref(rr=ref_rec, u=usubjid, d=ref):
            return first_dose_date(core, u)[0] == d

        evidence.append(EvidenceItem(ref=ref_rec.key, why="reference start date (RFSTDTC)", check=check_ref, role="context"))
        evidence += protocol_evidence(core, "visit_window", "protocol visit window")
        findings.append(
            Finding(
                code=CODE,
                usubjid=usubjid,
                site=core.site_of(usubjid),
                summary=f"{code} on {actual} is {offset:+d} day(s) from scheduled day {scheduled} (window ±{window})",
                details={
                    "visit": code,
                    "scheduled_day": scheduled,
                    "actual_day": (actual - ref).days,
                    "actual_date": str(actual),
                    "reference_date": str(ref),
                    "offset_days": offset,
                    "window": window,
                    "unparseable_dates_at_visit": unparseable,
                    "protocol_version": core.protocol_version(),
                },
                evidence=evidence,
                confidence=0.9,
            )
        )
    return sort_findings(findings)
