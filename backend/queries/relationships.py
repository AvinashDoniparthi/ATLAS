"""StudyGraph Relationship Query Layer.

Given an entity associated with a patient, finds all subjects connected to that entity
in the StudyGraph, along with site, relationship type, and context summary.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.evidence.record_refs import ref_tuple
from backend.graph.nodes import Record
from backend.queries.findings import finding_subjects, label as finding_label

log = logging.getLogger("atlas.relationships")


@dataclass
class RelatedSubject:
    usubjid: str
    site: str
    relationship: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    evidence_ref: Optional[list | tuple | dict] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "usubjid": self.usubjid,
            "site": self.site,
            "relationship": self.relationship,
            "summary": self.summary,
            "details": self.details,
            "evidence_ref": self.evidence_ref,
        }


@dataclass
class RelationshipResult:
    entity_type: str
    entity_id: str
    entity_label: str
    count: int
    subjects: list[dict[str, Any]]
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_label": self.entity_label,
            "count": self.count,
            "subjects": self.subjects,
            "message": self.message or ("No connected patients found." if self.count == 0 else f"{self.count} connected subjects found."),
        }


def _norm(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip().upper()


def get_related_subjects(core: Any, entity_type: str, entity_id: str, source_usubjid: Optional[str] = None) -> dict[str, Any]:
    """Query the StudyGraph for all subjects connected to a specific entity."""
    etype = (entity_type or "").strip().lower()
    eid = (entity_id or "").strip()
    norm_eid = _norm(eid)

    if not eid:
        return RelationshipResult(entity_type=etype, entity_id=eid, entity_label=eid, count=0, subjects=[]).as_dict()

    subjects_map: dict[str, RelatedSubject] = {}

    # 1. MEDICATION / CONMED (CM domain or EX domain)
    if etype in ("medication", "conmed", "concomitant_medication", "cm"):
        entity_label = f"Medication: {eid}"
        for r in core.idx.by_domain.get("CM", []):
            cmtrt = r.get("CMTRT")
            if _norm(cmtrt) == norm_eid or norm_eid in _norm(cmtrt):
                u = r.usubjid
                site = core.site_of(u) or r.site or "—"
                cmclas = r.get("CMCLAS") or ""
                dose = r.get("CMDOG") or r.get("CMDOSE") or ""
                summary_parts = []
                if cmclas:
                    summary_parts.append(f"Class: {cmclas}")
                if dose:
                    summary_parts.append(f"Dose: {dose}")
                rdate = core.idx.record_date(r)
                if rdate:
                    summary_parts.append(rdate.isoformat())
                summary = " | ".join(summary_parts) if summary_parts else "Concomitant medication"
                
                ref = r.key.as_tuple() if hasattr(r, "key") else None
                if u not in subjects_map:
                    subjects_map[u] = RelatedSubject(
                        usubjid=u,
                        site=site,
                        relationship="Concomitant medication",
                        summary=summary,
                        details={"cmtrt": cmtrt, "cmclas": cmclas, "dose": dose},
                        evidence_ref=ref,
                    )

    # 2. LABORATORY TEST (LB domain)
    elif etype in ("lab", "lab_test", "laboratory", "lb"):
        entity_label = f"Laboratory Test: {eid}"
        # Direct lookup by testcd if available
        matching_records: list[Record] = []
        if ("LB", norm_eid) in core.idx.by_test:
            matching_records = core.idx.by_test[("LB", norm_eid)]
        else:
            # Check by LBTEST or testcd in LB domain
            for r in core.idx.by_domain.get("LB", []):
                tcd = _norm(r.get("LBTESTCD"))
                tname = _norm(r.get("LBTEST"))
                if tcd == norm_eid or tname == norm_eid or norm_eid in tname:
                    matching_records.append(r)

        for r in matching_records:
            u = r.usubjid
            site = core.site_of(u) or r.site or "—"
            val = r.get("LBORRES")
            unit = r.get("LBORRESU") or ""
            visit = r.get("VISIT") or ""
            flag = r.get("LBNRIND") or ""
            rdate = core.idx.record_date(r)
            
            summary_parts = []
            if val is not None:
                summary_parts.append(f"Result: {val} {unit}".strip())
            if visit:
                summary_parts.append(f"Visit: {visit}")
            if flag:
                summary_parts.append(f"Flag: {flag}")
            if rdate:
                summary_parts.append(rdate.isoformat())
            summary = " | ".join(summary_parts) if summary_parts else "Laboratory measurement"

            ref = r.key.as_tuple() if hasattr(r, "key") else None
            if u not in subjects_map:
                subjects_map[u] = RelatedSubject(
                    usubjid=u,
                    site=site,
                    relationship="Laboratory test record",
                    summary=summary,
                    details={"testcd": r.get("LBTESTCD"), "visit": visit, "value": val, "unit": unit},
                    evidence_ref=ref,
                )

    # 3. ADVERSE EVENT (AE domain)
    elif etype in ("adverse_event", "ae", "aeterm"):
        entity_label = f"Adverse Event: {eid}"
        for r in core.idx.by_domain.get("AE", []):
            aeterm = r.get("AETERM")
            if _norm(aeterm) == norm_eid or norm_eid in _norm(aeterm):
                u = r.usubjid
                site = core.site_of(u) or r.site or "—"
                sev = r.get("AESEV") or ""
                ser = r.get("AESER") or ""
                rdate = core.idx.record_date(r)
                summary_parts = []
                if sev:
                    summary_parts.append(f"Severity: {sev}")
                if ser and _norm(ser) in ("Y", "YES"):
                    summary_parts.append("Serious: Yes")
                if rdate:
                    summary_parts.append(rdate.isoformat())
                summary = " | ".join(summary_parts) if summary_parts else "Adverse event"

                ref = r.key.as_tuple() if hasattr(r, "key") else None
                if u not in subjects_map:
                    subjects_map[u] = RelatedSubject(
                        usubjid=u,
                        site=site,
                        relationship="Adverse event",
                        summary=summary,
                        details={"aeterm": aeterm, "severity": sev, "serious": ser},
                        evidence_ref=ref,
                    )

    # 4. VISIT (Across study domains)
    elif etype in ("visit", "sv", "tv"):
        entity_label = f"Visit: {eid}"
        clean_v = eid.upper().replace(" ", "")
        for (u, v), doms in core.idx.by_subject_visit.items():
            if v == clean_v or _norm(v) == norm_eid:
                site = core.site_of(u) or "—"
                dom_list = sorted(doms.keys())
                rec_count = sum(len(lst) for lst in doms.values())
                first_ref = None
                for lst in doms.values():
                    if lst and hasattr(lst[0], "key"):
                        first_ref = lst[0].key.as_tuple()
                        break
                
                summary = f"Recorded domains: {', '.join(dom_list)} ({rec_count} records)"
                subjects_map[u] = RelatedSubject(
                    usubjid=u,
                    site=site,
                    relationship="Visit attendance",
                    summary=summary,
                    details={"visit": v, "domains": dom_list, "records": rec_count},
                    evidence_ref=first_ref,
                )

    # 5. DOSING / EXPOSURE (EX domain)
    elif etype in ("dosing", "exposure", "treatment", "ex"):
        entity_label = f"Treatment / Dose: {eid}"
        for r in core.idx.by_domain.get("EX", []):
            extrt = r.get("EXTRT")
            exdose = r.get("EXDOSE")
            exdosu = r.get("EXDOSU") or ""
            full_dose = f"{extrt} {exdose} {exdosu}".strip()
            
            # match on treatment, dose, or combined
            if (_norm(extrt) == norm_eid or 
                _norm(exdose) == norm_eid or 
                norm_eid in _norm(full_dose) or
                _norm(extrt) in norm_eid):
                u = r.usubjid
                site = core.site_of(u) or r.site or "—"
                visit = r.get("VISIT") or ""
                rdate = core.idx.record_date(r)
                summary_parts = []
                if extrt:
                    summary_parts.append(f"Regimen: {extrt}")
                if exdose:
                    summary_parts.append(f"Dose: {exdose} {exdosu}".strip())
                if visit:
                    summary_parts.append(f"Visit: {visit}")
                if rdate:
                    summary_parts.append(rdate.isoformat())
                summary = " | ".join(summary_parts) if summary_parts else "Exposure / Dosing"

                ref = r.key.as_tuple() if hasattr(r, "key") else None
                if u not in subjects_map:
                    subjects_map[u] = RelatedSubject(
                        usubjid=u,
                        site=site,
                        relationship="Exposure / Dosing",
                        summary=summary,
                        details={"extrt": extrt, "exdose": exdose, "visit": visit},
                        evidence_ref=ref,
                    )

    # 6. MEDICAL HISTORY (MH domain)
    elif etype in ("medical_history", "mh", "mhterm"):
        entity_label = f"Medical History: {eid}"
        for r in core.idx.by_domain.get("MH", []):
            mhterm = r.get("MHTERM")
            if _norm(mhterm) == norm_eid or norm_eid in _norm(mhterm):
                u = r.usubjid
                site = core.site_of(u) or r.site or "—"
                mhcat = r.get("MHCAT") or ""
                summary = f"Category: {mhcat}" if mhcat else "Medical history"
                ref = r.key.as_tuple() if hasattr(r, "key") else None
                if u not in subjects_map:
                    subjects_map[u] = RelatedSubject(
                        usubjid=u,
                        site=site,
                        relationship="Medical history",
                        summary=summary,
                        details={"mhterm": mhterm, "mhcat": mhcat},
                        evidence_ref=ref,
                    )

    # 7. DISPOSITION (DS domain)
    elif etype in ("disposition", "ds", "dsdecod"):
        entity_label = f"Disposition: {eid}"
        for r in core.idx.by_domain.get("DS", []):
            dsdecod = r.get("DSDECOD") or r.get("DSTERM")
            if _norm(dsdecod) == norm_eid or norm_eid in _norm(dsdecod):
                u = r.usubjid
                site = core.site_of(u) or r.site or "—"
                rdate = core.idx.record_date(r)
                summary_parts = [f"Status: {dsdecod}"]
                if rdate:
                    summary_parts.append(rdate.isoformat())
                summary = " | ".join(summary_parts)
                ref = r.key.as_tuple() if hasattr(r, "key") else None
                if u not in subjects_map:
                    subjects_map[u] = RelatedSubject(
                        usubjid=u,
                        site=site,
                        relationship="Disposition",
                        summary=summary,
                        details={"dsdecod": dsdecod},
                        evidence_ref=ref,
                    )

    # 8. FINDINGS (Rule findings)
    elif etype in ("finding", "finding_code", "rule"):
        entity_label = f"Finding: {finding_label(eid)}"
        subjs, findings_list, _ = finding_subjects(core, eid)
        for f in findings_list:
            u = f.usubjid
            if not u:
                continue
            site = f.site or core.site_of(u) or "—"
            first_ref = ref_tuple(f.evidence[0].ref) if f.evidence else None
            if u not in subjects_map:
                subjects_map[u] = RelatedSubject(
                    usubjid=u,
                    site=site,
                    relationship="Safety / Protocol Finding",
                    summary=f.summary or finding_label(eid),
                    details={"code": f.code, "status": f.status, "flags": list(f.flags)},
                    evidence_ref=first_ref,
                )

    # Fallback generic search across all domains
    else:
        entity_label = f"{etype.title()}: {eid}"
        for dom, recs in core.idx.by_domain.items():
            for r in recs:
                for col, val in r.data.items():
                    if val and _norm(val) == norm_eid:
                        u = r.usubjid
                        site = core.site_of(u) or r.site or "—"
                        ref = r.key.as_tuple() if hasattr(r, "key") else None
                        if u not in subjects_map:
                            subjects_map[u] = RelatedSubject(
                                usubjid=u,
                                site=site,
                                relationship=f"{dom} Record Match",
                                summary=f"Matched field {col} in domain {dom}",
                                details={"domain": dom, "column": col},
                                evidence_ref=ref,
                            )
                        break

    # Sort subjects: source_usubjid first if present, then sorted by usubjid
    sorted_subjects = sorted(
        subjects_map.values(),
        key=lambda s: (0 if s.usubjid == source_usubjid else 1, s.usubjid)
    )

    result_subjects = [s.as_dict() for s in sorted_subjects]
    return RelationshipResult(
        entity_type=etype,
        entity_id=eid,
        entity_label=entity_label,
        count=len(result_subjects),
        subjects=result_subjects,
    ).as_dict()
