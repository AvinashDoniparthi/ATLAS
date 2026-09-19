"""Node 4 — COMPLIANCE

Evaluates protocol compliance against the protocol version active for the data cut:
  - Subject-level protocol deviations (visit windows, prohibited meds, eligibility, dosing)
  - Site-level systemic escalation (e.g. multiple dosing errors at Site S09)
  - Memory recording for recurring site issues
"""
from __future__ import annotations

from collections import defaultdict
import datetime
import logging
from typing import Any, List, Tuple

from stage2.memory import ReviewMemory
from stage2.schemas import (
    AlternativeConsidered,
    ComplianceDeviation,
    MedicalEscalation,
    SiteFlag,
    TraceEntry,
)

log = logging.getLogger("stage2.compliance")


def compliance_node(
    compliance_findings: List[dict],
    core: Any,
    memory: ReviewMemory,
    protocol_version: int,
    cycle: int,
    trace: List[TraceEntry],
) -> Tuple[List[ComplianceDeviation], List[SiteFlag], List[MedicalEscalation]]:
    """Executes Node 4: COMPLIANCE.

    Returns:
        (deviations, site_flags, site_escalations)
    """
    ts = datetime.datetime.now().isoformat()
    deviations: List[ComplianceDeviation] = []
    site_dosing_subjects: dict[str, list[str]] = defaultdict(list)
    site_dosing_evidence: dict[str, list] = defaultdict(list)

    for f in compliance_findings:
        u = f.get("usubjid") or ""
        site = f.get("site") or core.site_of(u)
        code = f["code"]
        ev = f.get("evidence") or []

        dev = ComplianceDeviation(
            code=code,
            usubjid=u,
            site=site,
            summary=f["summary"],
            protocol_version=protocol_version,
            evidence=ev,
            cycle=cycle,
        )
        deviations.append(dev)

        # Track dosing errors by site for systemic pattern detection
        if code == "DOSING_ERROR" and site:
            if u not in site_dosing_subjects[site]:
                site_dosing_subjects[site].append(u)
            site_dosing_evidence[site].extend(ev)

    # Site-level aggregation (Section 12)
    site_flags: List[SiteFlag] = []
    site_escalations: List[MedicalEscalation] = []

    for site, subjects in site_dosing_subjects.items():
        count = len(subjects)
        memory.record_site_issue(site, "DOSING_ERROR", count)

        # If 2 or more subjects at the same site receive wrong dose -> one site escalation
        if count >= 2:
            unique_ev = []
            seen_refs = set()
            for r in site_dosing_evidence[site]:
                key = (r.domain, r.usubjid, r.seq, r.document, r.section)
                if key not in seen_refs:
                    seen_refs.add(key)
                    unique_ev.append(r)

            flag_summary = f"Site {site} demonstrates systemic dosing errors affecting {count} subjects ({', '.join(sorted(subjects))})"
            sflag = SiteFlag(
                site=site,
                issue_type="SYSTEMIC_DOSING_ERROR",
                subject_count=count,
                subjects=sorted(subjects),
                summary=flag_summary,
                evidence=unique_ev[:10],  # representative citations
                cycle=cycle,
            )
            site_flags.append(sflag)

            # Check if this site escalation has already been raised in memory
            fp = memory.escalation_fingerprint("SITE_DOSING_ERROR", site)
            if not memory.is_rejected(fp) and not memory.has_escalation(fp):
                alts = [
                    AlternativeConsidered(
                        alternative="Treat as isolated subject-level errors",
                        rejected_reason=f"{count} subjects affected at site {site} indicates site-wide pharmacy preparation or dispensation breakdown.",
                    ),
                    AlternativeConsidered(
                        alternative="Immediately close site to accrual",
                        rejected_reason="Requires medical monitor clinical review and audit before site closure.",
                    ),
                ]
                esc = MedicalEscalation(
                    escalation_id=f"ESC-SITE-DOSING-{site}-{cycle}",
                    code="SITE_DOSING_ERROR",
                    usubjid=subjects[0],  # representative index subject
                    site=site,
                    severity="HIGH",
                    summary=flag_summary,
                    evidence=unique_ev[:10],
                    alternatives=alts,
                    status="PENDING",
                    cycle=cycle,
                )
                site_escalations.append(esc)
                trace.append(
                    TraceEntry(
                        timestamp=ts,
                        cycle=cycle,
                        node="compliance",
                        action=f"Generated single site-level escalation for {site}",
                        target=site,
                        evidence=unique_ev[:5],
                        reason=f"Multiple ({count}) subjects at site {site} received incorrect doses.",
                    )
                )

    # Log summary trace
    trace.append(
        TraceEntry(
            timestamp=ts,
            cycle=cycle,
            node="compliance",
            action=f"{len(deviations)} deviations under v{protocol_version}, {len(site_flags)} site flag(s)",
            target=f"pv={protocol_version}",
            reason=f"Protocol compliance evaluated using protocol_v{protocol_version} rules.",
        )
    )

    return deviations, site_flags, site_escalations
