"""LOOKUP questions: records for a subject, optionally around a visit / date range."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

from backend.graph.nodes import Record, RecordKey
from backend.normalization.dates import parse_date

DOMAIN_WORDS: list[tuple[str, str]] = [
    (r"\blab(?:oratory|s)?\b|\blb\b|\bchemistry\b", "LB"),
    (r"\badverse[- ]?events?\b|\bae\b|\baes\b", "AE"),
    (r"\bvital(?:s| signs?)\b|\bvs\b", "VS"),
    (r"\bexposure\b|\bdos(?:e|es|ing)\b|\bex\b", "EX"),
    (r"\bcon(?:comitant)?[- ]?med(?:ication)?s?\b|\bmedications?\b|\bcm\b", "CM"),
    (r"\bdisposition\b|\bds\b", "DS"),
    (r"\bmedical history\b|\bhistory\b|\bmh\b", "MH"),
    (r"\becgs?\b|\beg\b|\bqtc", "EG"),
    (r"\bdemograph", "DM"),
]


def domains_from_text(text: str, available: Iterable[str]) -> list[str]:
    avail = {d.upper() for d in available}
    found: list[str] = []
    low = text.lower()
    for pat, dom in DOMAIN_WORDS:
        if re.search(pat, low) and dom in avail and dom not in found:
            found.append(dom)
    return found


@dataclass
class LookupResult:
    refs: list[RecordKey] = field(default_factory=list)
    records: list[Record] = field(default_factory=list)
    anchor_date: Optional[date] = None
    anchor_source: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    records_inspected: int = 0
    status: str = "ok"   # ok | no_subject | insufficient

    def by_domain(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.records:
            out[r.domain] = out.get(r.domain, 0) + 1
        return out


def visit_anchor(core, usubjid: str, visit: str) -> tuple[Optional[date], Optional[str], list[str]]:
    """Earliest parsed date among the subject's records at ``visit``; fallback = RFSTDTC + scheduled day."""
    notes: list[str] = []
    v = visit.upper().replace(" ", "")
    recs = core.idx.visit_records(usubjid, v)
    dates = []
    for dom, lst in recs.items():
        for r in lst:
            d = core.idx.record_date(r)
            if d is not None:
                dates.append((d, dom, r.seq))
    if dates:
        dates.sort()
        d, dom, seq = dates[0]
        return d, f"{dom} record at {v} (seq {seq})", notes
    sched = getattr(core.rules, "visit_schedule", {}) if core.rules else {}
    dm = core.dm(usubjid)
    if dm is not None and v in sched:
        ref = parse_date(dm.get("RFSTDTC"))
        if ref.ok and ref.date:
            notes.append(f"no dated records at {v}; anchor derived from RFSTDTC + scheduled day {sched[v]}")
            return ref.date + timedelta(days=int(sched[v])), "RFSTDTC + protocol schedule", notes
    notes.append(f"no anchor date could be determined for visit {v}")
    return None, None, notes


def records_for_subject(
    core,
    usubjid: str,
    domains: Optional[Iterable[str]] = None,
    visit: Optional[str] = None,
    window_days: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    testcd: Optional[str] = None,
    at_visit_only: bool = False,
) -> LookupResult:
    res = LookupResult()
    if usubjid not in core.idx.by_subject:
        res.status = "no_subject"
        res.notes.append(f"subject {usubjid} not present in the study at this cut")
        return res
    doms = [d.upper() for d in (domains or core.idx.domains)]

    if visit and window_days is not None:
        anchor, src, notes = visit_anchor(core, usubjid, visit)
        res.notes.extend(notes)
        res.anchor_date, res.anchor_source = anchor, src
        if anchor is None:
            res.status = "insufficient"
            return res
        date_from = anchor - timedelta(days=int(window_days))
        date_to = anchor + timedelta(days=int(window_days))

    unparseable = 0
    for dom in doms:
        if visit and (window_days is None or at_visit_only):
            recs = core.idx.visit_records(usubjid, visit).get(dom, [])
        else:
            recs = core.idx.records(usubjid, dom)
        res.records_inspected += len(recs)
        for r in recs:
            if testcd:
                col = core.idx.test_cols.get(dom)
                if not col or (r.get(col) or "").upper() != testcd.upper():
                    continue
            if date_from is not None or date_to is not None:
                d = core.idx.record_date(r)
                if d is None:
                    if core.idx.date_cols.get(dom):
                        unparseable += 1
                    continue
                if date_from is not None and d < date_from:
                    continue
                if date_to is not None and d > date_to:
                    continue
            res.records.append(r)
    if unparseable:
        res.notes.append(f"{unparseable} record(s) excluded: date missing or unparseable")
    res.records.sort(key=lambda r: (r.domain, r.seq if r.seq is not None else -1))
    res.refs = [r.key for r in res.records]
    return res


def records_at_visit(core, usubjid: str, visit: str, domains: Optional[Iterable[str]] = None) -> LookupResult:
    return records_for_subject(core, usubjid, domains, visit=visit, at_visit_only=True)
