"""COUNT questions: subject-level and record-level counts with evidence.

Subject counts collapse duplicate enrolments (same person under two USUBJIDs)
to unique persons by default; the evidence still cites every matching record
so the collapse is visible. A site filter is applied to the specific USUBJID
(an enrolment at site X is counted for site X even if the same person is also
enrolled elsewhere).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from backend.graph.nodes import EvidenceItem, Record
from backend.normalization.values import parse_number
from backend.queries import adverse_events as AE
from backend.queries import disposition as DS
from backend.protocol.protocol_loader import match_prohibited, normalize_class_token


@dataclass
class CountFilters:
    site: Optional[str] = None
    arm: Optional[str] = None
    sex: Optional[str] = None
    disposition: Optional[str] = None          # DSDECOD token, e.g. DISCONTINUED / COMPLETED
    disposition_reason: Optional[str] = None   # substring of DSTERM, e.g. ADVERSE EVENT
    ae_serious: Optional[bool] = None
    ae_term: Optional[str] = None
    ae_severity: Optional[str] = None
    finding_code: Optional[str] = None
    finding_subcode: Optional[str] = None
    cm_class: Optional[str] = None
    prohibited_med: bool = False
    age_min: Optional[float] = None
    age_max: Optional[float] = None
    visit: Optional[str] = None
    testcd: Optional[str] = None
    unique_persons: bool = True
    country: Optional[str] = None

    def active(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, False) and k != "unique_persons"}


@dataclass
class CountResult:
    value: int
    subjects: list[str]
    evidence: list[EvidenceItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    records_inspected: int = 0
    status: str = "ok"   # ok | insufficient
    collapsed: int = 0


def _ev(rec: Record, why: str, check=None, role: str = "support") -> EvidenceItem:
    return EvidenceItem(ref=rec.key, why=why, check=check, role=role)


def _candidate_subjects(core, f: CountFilters) -> list[str]:
    if f.site:
        return core.idx.subjects_at(f.site)
    return list(core.idx.subjects)


def count_subjects(core, f: CountFilters) -> CountResult:
    subjects = _candidate_subjects(core, f)
    evidence_by_subject: dict[str, list[EvidenceItem]] = {}
    notes: list[str] = []
    inspected = 0
    status = "ok"

    # ---- demographic filters (DM) ----------------------------------------
    if f.arm or f.sex or f.age_min is not None or f.age_max is not None or f.country or f.site:
        kept = []
        for u in subjects:
            dm = core.dm(u)
            inspected += 1
            if dm is None:
                if f.arm or f.sex or f.age_min is not None or f.age_max is not None or f.country:
                    continue
                kept.append(u)
                continue
            ok = True
            if f.arm and f.arm.upper() not in (dm.get("ARM") or "").upper():
                ok = False
            if f.sex and (dm.get("SEX") or "").upper()[:1] != f.sex.upper()[:1]:
                ok = False
            if f.country and f.country.upper() != (dm.get("COUNTRY") or "").upper():
                ok = False
            age = parse_number(dm.get("AGE"))
            if f.age_min is not None and (age is None or age < f.age_min):
                ok = False
            if f.age_max is not None and (age is None or age > f.age_max):
                ok = False
            if ok:
                kept.append(u)
                if f.arm or f.sex or f.age_min is not None or f.age_max is not None or f.country:
                    evidence_by_subject.setdefault(u, []).append(
                        _ev(dm, "DM row satisfies demographic filter", role="support"))
        subjects = kept

    # ---- disposition ------------------------------------------------------
    if f.disposition or f.disposition_reason:
        kept = []
        for u in subjects:
            recs = DS.disposition_records(core, u)
            inspected += len(recs)
            hits = [r for r in recs if DS.matches_disposition(r, f.disposition, f.disposition_reason)]
            if hits:
                kept.append(u)
                for r in hits:
                    evidence_by_subject.setdefault(u, []).append(_ev(
                        r, f"DS record matches disposition={f.disposition} reason={f.disposition_reason}",
                        check=lambda r=r: DS.matches_disposition(r, f.disposition, f.disposition_reason)))
        subjects = kept

    # ---- adverse events ---------------------------------------------------
    if f.ae_serious is not None or f.ae_term or f.ae_severity:
        kept = []
        for u in subjects:
            recs = AE.ae_records(core, u)
            inspected += len(recs)
            hits = [r for r in recs if AE.matches_ae(core, r, f.ae_term, f.ae_serious, f.ae_severity)]
            if hits:
                kept.append(u)
                for r in hits:
                    evidence_by_subject.setdefault(u, []).append(_ev(
                        r, "AE record matches filter",
                        check=lambda r=r: AE.matches_ae(core, r, f.ae_term, f.ae_serious, f.ae_severity)))
        subjects = kept
        if f.ae_serious is not None and (core.rules is None or not core.rules.sae_hosp_flag_rule):
            notes.append("seriousness uses AESER only: no protocol hospitalisation rule available")

    # ---- concomitant medications ----------------------------------------
    if f.cm_class or f.prohibited_med:
        kept = []
        prohibited = list(getattr(core.rules, "prohibited_classes", []) or []) if core.rules else []
        if f.prohibited_med and not prohibited:
            status = "insufficient"
            notes.append("prohibited-medication list unavailable from protocol")
        want = normalize_class_token(f.cm_class) if f.cm_class else None
        for u in subjects:
            recs = core.idx.records(u, "CM")
            inspected += len(recs)
            hits = []
            for r in recs:
                cls = normalize_class_token(r.get("CMCLAS"))
                trt = normalize_class_token(r.get("CMTRT"))
                if want and not (want == cls or want in cls.split("_") or want == trt or want in trt):
                    continue
                if f.prohibited_med and match_prohibited(r.get("CMCLAS"), r.get("CMTRT"), prohibited) is None:
                    continue
                hits.append(r)
            if hits:
                kept.append(u)
                for r in hits:
                    evidence_by_subject.setdefault(u, []).append(_ev(
                        r, "CM record matches medication filter",
                        check=lambda r=r: (not f.prohibited_med) or match_prohibited(r.get("CMCLAS"), r.get("CMTRT"), prohibited) is not None))
        subjects = kept

    # ---- derived findings ------------------------------------------------
    if f.finding_code:
        from backend.queries.findings import finding_subjects

        subs, findings, availability = finding_subjects(core, f.finding_code, site=None, subcode=f.finding_subcode)
        if not availability[0]:
            status = "insufficient"
            notes.append(availability[1])
        allowed = set(subs)
        subjects = [u for u in subjects if u in allowed]
        for fd in findings:
            if fd.usubjid in subjects:
                evidence_by_subject.setdefault(fd.usubjid, []).extend(fd.evidence)

    # ---- visit / test presence -----------------------------------------
    if f.visit or f.testcd:
        kept = []
        for u in subjects:
            if f.visit:
                recs = core.idx.visit_records(u, f.visit)
                flat = [r for lst in recs.values() for r in lst]
                if f.testcd:
                    flat = [r for r in flat if (r.get(core.idx.test_cols.get(r.domain) or "") or "").upper() == f.testcd.upper()]
            else:
                flat = core.idx.tests_for(u, "LB", f.testcd)
            inspected += len(flat)
            if flat:
                kept.append(u)
                for r in flat:
                    evidence_by_subject.setdefault(u, []).append(_ev(r, "record present at visit/test"))
        subjects = kept

    # ---- default evidence: DM rows -------------------------------------
    for u in subjects:
        if u not in evidence_by_subject:
            dm = core.dm(u)
            if dm is not None:
                evidence_by_subject[u] = [_ev(dm, "subject enrolled (DM)")]

    # ---- collapse duplicate persons ------------------------------------
    collapsed = 0
    if f.unique_persons:
        seen_persons: set[str] = set()
        unique: list[str] = []
        for u in subjects:
            pid = core.person_of.get(u, u)
            if pid in seen_persons:
                collapsed += 1
                continue
            seen_persons.add(pid)
            unique.append(u)
        if collapsed:
            notes.append(f"{collapsed} duplicate enrolment(s) of the same person collapsed; DM rows for both cited")
            # cite both DM rows so the collapse is evidenced
            for u in subjects:
                if u not in unique:
                    dm = core.dm(u)
                    if dm is not None:
                        evidence_by_subject.setdefault(u, []).append(_ev(dm, "duplicate enrolment of counted person", role="context"))
        counted = unique
    else:
        counted = subjects

    evidence: list[EvidenceItem] = []
    for u in sorted(subjects):
        evidence.extend(evidence_by_subject.get(u, []))
    return CountResult(value=len(counted), subjects=sorted(counted), evidence=evidence, notes=notes,
                       records_inspected=inspected, status=status, collapsed=collapsed)


def count_records(core, domain: str, f: CountFilters) -> CountResult:
    """Count records (not subjects) in ``domain`` matching the filters."""
    recs = core.idx.by_domain.get(domain.upper(), [])
    subjects = set(_candidate_subjects(core, f))
    hits: list[Record] = []
    for r in recs:
        if r.usubjid not in subjects:
            continue
        if domain.upper() == "AE" and not AE.matches_ae(core, r, f.ae_term, f.ae_serious, f.ae_severity):
            continue
        if domain.upper() == "DS" and not DS.matches_disposition(r, f.disposition, f.disposition_reason):
            continue
        if f.visit and (r.get("VISIT") or "").upper().replace(" ", "") != f.visit.upper().replace(" ", ""):
            continue
        if f.testcd:
            col = core.idx.test_cols.get(domain.upper())
            if not col or (r.get(col) or "").upper() != f.testcd.upper():
                continue
        hits.append(r)
    evidence = [_ev(r, f"{domain} record matches filter") for r in hits]
    return CountResult(value=len(hits), subjects=sorted({r.usubjid for r in hits}), evidence=evidence,
                       records_inspected=len(recs))
