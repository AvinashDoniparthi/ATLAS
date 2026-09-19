"""Deterministic in-memory indexes over a cut view."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from backend.graph.nodes import Record, RecordKey, site_from_usubjid
from backend.normalization.dates import ParsedDate, parse_date

# Which column carries the record date / visit / test code per domain. Discovered
# generically: any column ending in DTC (start date preferred), VISIT, *TESTCD.
_DATE_PREFERENCE = ("STDTC", "DTC")


def date_column(headers: Iterable[str], domain: str) -> Optional[str]:
    hs = list(headers)
    for suffix in _DATE_PREFERENCE:
        for h in hs:
            if h.upper().startswith(domain.upper()) and h.upper().endswith(suffix):
                return h
    for h in hs:
        if h.upper().endswith("DTC") and not h.upper().startswith("BRTH"):
            return h
    return None


def testcd_column(headers: Iterable[str], domain: str) -> Optional[str]:
    for h in headers:
        if h.upper() == f"{domain.upper()}TESTCD":
            return h
    for h in headers:
        if h.upper().endswith("TESTCD"):
            return h
    return None


@dataclass
class Indexes:
    by_ref: dict[tuple[str, str, Optional[int]], Record] = field(default_factory=dict)
    by_subject: dict[str, dict[str, list[Record]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(list)))
    by_site: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    by_domain: dict[str, list[Record]] = field(default_factory=lambda: defaultdict(list))
    by_subject_visit: dict[tuple[str, str], dict[str, list[Record]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(list))
    )
    by_subject_test: dict[tuple[str, str, str], list[Record]] = field(default_factory=lambda: defaultdict(list))
    by_test: dict[tuple[str, str], list[Record]] = field(default_factory=lambda: defaultdict(list))
    by_subject_date: dict[tuple[str, str], list[tuple[date, Record]]] = field(default_factory=lambda: defaultdict(list))
    dates: dict[tuple[str, str, Optional[int]], ParsedDate] = field(default_factory=dict)
    date_cols: dict[str, Optional[str]] = field(default_factory=dict)
    test_cols: dict[str, Optional[str]] = field(default_factory=dict)
    subject_site: dict[str, str] = field(default_factory=dict)
    subjects: list[str] = field(default_factory=list)
    visits_seen: set[str] = field(default_factory=set)
    domains: list[str] = field(default_factory=list)
    unparseable_dates: int = 0

    # ----------------------------------------------------------------- build
    def add_domain(self, domain: str, records: list[Record], headers: list[str]) -> None:
        self.domains.append(domain)
        dcol = date_column(headers, domain)
        tcol = testcd_column(headers, domain)
        self.date_cols[domain] = dcol
        self.test_cols[domain] = tcol
        has_visit = "VISIT" in headers
        for r in records:
            key = r.key.as_tuple()
            if key in self.by_ref and r.seq is not None:
                # duplicate (domain, usubjid, seq): keep the first, mark both
                self.by_ref[key].issues.append("duplicate_key")
                r.issues.append("duplicate_key_dropped_from_by_ref")
            else:
                self.by_ref[key] = r
            self.by_subject[r.usubjid][domain].append(r)
            self.by_domain[domain].append(r)
            site = r.site
            if site:
                self.by_site[site].add(r.usubjid)
                if domain == "DM" or r.usubjid not in self.subject_site:
                    self.subject_site[r.usubjid] = site
            if has_visit:
                v = (r.get("VISIT") or "").upper().replace(" ", "")
                if v:
                    self.by_subject_visit[(r.usubjid, v)][domain].append(r)
                    self.visits_seen.add(v)
            if tcol:
                t = (r.get(tcol) or "").upper()
                if t:
                    self.by_subject_test[(r.usubjid, domain, t)].append(r)
                    self.by_test[(domain, t)].append(r)
            if dcol:
                pdate = parse_date(r.get(dcol))
                self.dates[key] = pdate
                if pdate.ok and pdate.date is not None:
                    self.by_subject_date[(r.usubjid, domain)].append((pdate.date, r))
                elif r.get(dcol):
                    self.unparseable_dates += 1
                    r.issues.append(f"unparseable_date:{dcol}")

    def append_records(self, domain: str, records: list[Record], headers: list[str]) -> None:
        if domain not in self.domains:
            self.domains.append(domain)
        if domain not in self.date_cols:
            self.date_cols[domain] = date_column(headers, domain)
        if domain not in self.test_cols:
            self.test_cols[domain] = testcd_column(headers, domain)
        dcol = self.date_cols[domain]
        tcol = self.test_cols[domain]
        has_visit = "VISIT" in headers
        for r in records:
            key = r.key.as_tuple()
            if key in self.by_ref and r.seq is not None:
                self.by_ref[key].issues.append("duplicate_key")
                r.issues.append("duplicate_key_dropped_from_by_ref")
            else:
                self.by_ref[key] = r
            self.by_subject[r.usubjid][domain].append(r)
            self.by_domain[domain].append(r)
            site = r.site
            if site:
                self.by_site[site].add(r.usubjid)
                if domain == "DM" or r.usubjid not in self.subject_site:
                    self.subject_site[r.usubjid] = site
            if has_visit:
                v = (r.get("VISIT") or "").upper().replace(" ", "")
                if v:
                    self.by_subject_visit[(r.usubjid, v)][domain].append(r)
                    self.visits_seen.add(v)
            if tcol:
                t = (r.get(tcol) or "").upper()
                if t:
                    self.by_subject_test[(r.usubjid, domain, t)].append(r)
                    self.by_test[(domain, t)].append(r)
            if dcol:
                pdate = parse_date(r.get(dcol))
                self.dates[key] = pdate
                if pdate.ok and pdate.date is not None:
                    self.by_subject_date[(r.usubjid, domain)].append((pdate.date, r))
                elif r.get(dcol):
                    self.unparseable_dates += 1
                    r.issues.append(f"unparseable_date:{dcol}")

    def finalize(self) -> None:
        for lst in self.by_subject_date.values():
            lst.sort(key=lambda t: (t[0], t[1].seq if t[1].seq is not None else 0))
        for dom in self.by_domain.values():
            dom.sort(key=lambda r: (r.usubjid, r.seq if r.seq is not None else 0))
        subj = set(self.by_subject.keys())
        self.subjects = sorted(subj)
        for u in self.subjects:
            if u not in self.subject_site:
                s = site_from_usubjid(u)
                if s:
                    self.subject_site[u] = s
                    self.by_site[s].add(u)

    # ----------------------------------------------------------------- query
    def get(self, domain: str, usubjid: str, seq: Optional[int]) -> Optional[Record]:
        return self.by_ref.get((domain, usubjid, seq))

    def get_key(self, key: RecordKey) -> Optional[Record]:
        return self.by_ref.get(key.as_tuple())

    def records(self, usubjid: str, domain: str) -> list[Record]:
        return self.by_subject.get(usubjid, {}).get(domain, [])

    def record_date(self, r: Record) -> Optional[date]:
        pd = self.dates.get(r.key.as_tuple())
        return pd.date if pd and pd.ok else None

    def parsed_date(self, r: Record) -> Optional[ParsedDate]:
        return self.dates.get(r.key.as_tuple())

    def site_of(self, usubjid: str) -> Optional[str]:
        return self.subject_site.get(usubjid)

    def subjects_at(self, site: str) -> list[str]:
        return sorted(self.by_site.get(site.upper(), set()))

    def tests_for(self, usubjid: str, domain: str, testcd: str) -> list[Record]:
        return self.by_subject_test.get((usubjid, domain, testcd.upper()), [])

    def records_in_window(self, usubjid: str, domain: str, start: date, end: date) -> list[Record]:
        return [r for d, r in self.by_subject_date.get((usubjid, domain), []) if start <= d <= end]

    def visit_records(self, usubjid: str, visit: str) -> dict[str, list[Record]]:
        return self.by_subject_visit.get((usubjid, visit.upper().replace(" ", "")), {})
