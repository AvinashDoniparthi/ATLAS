"""Patient 360 — everything the graph holds for one subject, with evidence refs."""
from __future__ import annotations

from typing import Any

from backend.evidence.record_refs import ref_tuple
from backend.graph.nodes import Record
from backend.protocol.protocol_loader import match_prohibited
from backend.queries import adverse_events as AE
from backend.queries import disposition as DS
from backend.queries import exposure as EX
from backend.queries.findings import all_findings_for_subject, finding_summary
from backend.queries.laboratory import lab_chain


def _rec(core, r: Record) -> dict[str, Any]:
    d = r.as_dict()
    dcol = core.idx.date_cols.get(r.domain)
    if dcol:
        dt = core.idx.record_date(r)
        d["_date"] = dt.isoformat() if dt else None
    d["_ref"] = r.key.as_tuple()
    return d


def _empty(usubjid: str, core) -> dict[str, Any]:
    return {
        "usubjid": usubjid,
        "found": False,
        "site": None,
        "demographics": None,
        "person": None,
        "protocol_version": core.protocol_version(),
        "cut": core.cut(),
        "visits": {},
        "labs": [],
        "vitals": [],
        "ecg": [],
        "exposure": [],
        "adverse_events": [],
        "conmeds": [],
        "medical_history": [],
        "disposition": None,
        "findings": {},
        "evidence_refs": [],
        "site_replies": {},
        "monitor_decisions": {},
        "notes": [f"subject {usubjid} is not present in the study at cut {core.cut()}"],
    }


def patient360(core, usubjid: str) -> dict[str, Any]:
    usubjid = (usubjid or "").strip()
    if usubjid not in core.idx.by_subject:
        return _empty(usubjid, core)

    dm = core.dm(usubjid)
    pid = core.person_of.get(usubjid)
    cluster = core.persons.get(pid) if pid else None
    person = None
    if cluster:
        person = {
            "person_id": cluster.person_id,
            "canonical": cluster.canonical,
            "duplicates": [u for u in cluster.usubjids if u != usubjid],
            "duplicate_of": cluster.canonical if cluster.canonical != usubjid else None,
            "matched_on": list(cluster.matched_on),
        }

    prohibited = list(getattr(core.rules, "prohibited_classes", []) or []) if core.rules else []
    evidence_refs: set = set()

    visits: dict[str, Any] = {}
    for (u, v), doms in core.idx.by_subject_visit.items():
        if u != usubjid:
            continue
        dates = [core.idx.record_date(r) for lst in doms.values() for r in lst]
        dates = [d for d in dates if d is not None]
        visits[v] = {
            "date": min(dates).isoformat() if dates else None,
            "domains": {dom: [r.key.as_tuple() for r in lst] for dom, lst in doms.items()},
        }
    sched = getattr(core.rules, "visit_schedule", {}) if core.rules else {}
    visits = dict(sorted(visits.items(), key=lambda kv: (sched.get(kv[0], 10**6), kv[0])))

    labs = [lab_chain(core, r) for r in core.idx.records(usubjid, "LB")]
    vitals = [_rec(core, r) for r in core.idx.records(usubjid, "VS")]
    ecg = [_rec(core, r) for r in core.idx.records(usubjid, "EG")]
    exposure = [EX.exposure_summary(core, r) | {"_ref": r.key.as_tuple()} for r in EX.exposure_records(core, usubjid)]
    aes = []
    for r in AE.ae_records(core, usubjid):
        d = AE.ae_summary(core, r)
        d["_ref"] = r.key.as_tuple()
        d["_date"] = (core.idx.record_date(r).isoformat() if core.idx.record_date(r) else None)
        aes.append(d)
    conmeds = []
    for r in core.idx.records(usubjid, "CM"):
        d = _rec(core, r)
        d["_prohibited_match"] = match_prohibited(r.get("CMCLAS"), r.get("CMTRT"), prohibited)
        conmeds.append(d)
    mh = [_rec(core, r) for r in core.idx.records(usubjid, "MH")]
    disposition = DS.subject_disposition(core, usubjid)

    findings_raw = all_findings_for_subject(core, usubjid)
    findings: dict[str, list[dict]] = {}
    for code, fs in findings_raw.items():
        findings[code] = [finding_summary(f) for f in fs]
        for f in fs:
            for e in f.evidence:
                evidence_refs.add(ref_tuple(e.ref))

    replies = {}
    sr = getattr(core, "site_replies", None)
    if sr is not None and sr.loaded:
        for k, v in sr.replies.items():
            parts = k.split("|")
            if len(parts) >= 2 and parts[1] == usubjid:
                replies[k] = v
    decisions = {}
    md = getattr(core, "monitor_decisions", None)
    if md is not None and md.loaded:
        for k, v in md.decisions.items():
            if k.endswith("|" + usubjid):
                decisions[k] = v

    return {
        "usubjid": usubjid,
        "found": True,
        "site": core.site_of(usubjid),
        "demographics": _rec(core, dm) if dm is not None else None,
        "person": person,
        "protocol_version": core.protocol_version(),
        "cut": core.cut(),
        "visits": visits,
        "labs": labs,
        "vitals": vitals,
        "ecg": ecg,
        "exposure": exposure,
        "adverse_events": aes,
        "conmeds": conmeds,
        "medical_history": mh,
        "disposition": disposition,
        "findings": findings,
        "evidence_refs": sorted(evidence_refs, key=lambda t: tuple(str(x) for x in t)),
        "site_replies": replies,
        "monitor_decisions": decisions,
        "notes": [] if dm is not None else ["no DM row for this subject"],
    }
