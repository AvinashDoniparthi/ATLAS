"""Node 2 — MEDICAL REVIEW

Evaluates findings from detect with clinical precision:
  - Critical SAE rule: AESHOSP=Y and AESER=N -> SAE_MISCODED, severity=CRITICAL, escalate same cycle
  - Liver rule: Hy's law candidate with screening/baseline elevation -> monitor only, do not escalate
  - Formulates MedicalEscalation objects with alternatives considered and rationale
  - Integrates two-cycle subject history and escalation deduplication from ReviewMemory
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, List, Tuple

from stage2.memory import ReviewMemory
from stage2.schemas import AlternativeConsidered, MedicalEscalation, TraceEntry

log = logging.getLogger("stage2.medical_review")


def medical_review_node(
    findings: List[dict],
    core: Any,
    memory: ReviewMemory,
    cut: int,
    cycle: int,
    trace: List[TraceEntry],
) -> Tuple[List[MedicalEscalation], List[dict], List[dict], List[dict], List[dict]]:
    """Executes Node 2: MEDICAL REVIEW.

    Returns:
        (escalations, serious_findings, monitoring_only, data_quality_findings, compliance_findings)
    """
    ts = datetime.datetime.now().isoformat()
    escalations: List[MedicalEscalation] = []
    serious_findings: List[dict] = []
    monitoring_only: List[dict] = []
    data_quality_findings: List[dict] = []
    compliance_findings: List[dict] = []

    for f in findings:
        code = f["code"]
        u = f.get("usubjid") or ""
        site = f.get("site") or (core.site_of(u) if u else None)
        details = f.get("details") or {}
        flags = f.get("flags") or []

        # 1. Separate Data Quality and Compliance categories
        if code in ("AE_BEFORE_FIRST_DOSE", "MISSING_DOSE", "DUPLICATE_SUBJECT"):
            data_quality_findings.append(f)
            continue
        if code in ("VISIT_WINDOW_DEVIATION", "PROHIBITED_MEDICATION", "ELIGIBILITY_VIOLATION", "DOSING_ERROR"):
            compliance_findings.append(f)
            # Dosing errors also produce medical evaluation below if severe

        # 2. Evaluate Serious Adverse Events
        if code in ("SERIOUS_AE", "SAE_MISCODED"):
            subcode = details.get("subcode") or ("SAE_MISCODED" if code == "SAE_MISCODED" else None)
            if subcode == "SAE_MISCODED" or code == "SAE_MISCODED":
                # CRITICAL RULE: AESHOSP=Y but AESER=N -> must escalate in the same cycle
                f["severity"] = "CRITICAL"
                serious_findings.append(f)
                fp = memory.escalation_fingerprint("SAE_MISCODED", u)
                if not memory.is_rejected(fp) and not memory.has_escalation(fp):
                    alts = [
                        AlternativeConsidered(
                            alternative="Accept investigator coding as non-serious (AESER=N)",
                            rejected_reason="Protocol specification dictates AESHOSP=Y constitutes an SAE regardless of site-coded AESER.",
                        ),
                        AlternativeConsidered(
                            alternative="Defer query to routine site monitoring visit",
                            rejected_reason="Expedited safety reporting requires immediate medical monitor escalation within 24 hours.",
                        ),
                    ]
                    esc = MedicalEscalation(
                        escalation_id=f"ESC-SAE-{u}-{cycle}",
                        code="SAE_MISCODED",
                        usubjid=u,
                        site=site,
                        severity="CRITICAL",
                        summary=f"{details.get('AETERM', 'Event')} has AESHOSP=Y (hospitalization) but AESER=N — site miscoding of serious adverse event",
                        evidence=f.get("evidence", []),
                        alternatives=alts,
                        status="PENDING",
                        cycle=cycle,
                    )
                    escalations.append(esc)
                    trace.append(
                        TraceEntry(
                            timestamp=ts,
                            cycle=cycle,
                            node="medical_review",
                            action=f"Drafted CRITICAL escalation for SAE_MISCODED on {u}",
                            target=u,
                            evidence=f.get("evidence", []),
                            reason="Hospitalized event (AESHOSP=Y) miscoded as AESER=N; immediate escalation required.",
                        )
                    )
                else:
                    monitoring_only.append(f)
            else:
                f["severity"] = "HIGH"
                serious_findings.append(f)
                fp = memory.escalation_fingerprint("SERIOUS_AE", u)
                if not memory.is_rejected(fp) and not memory.has_escalation(fp):
                    alts = [
                        AlternativeConsidered(
                            alternative="Downgrade to observational monitoring",
                            rejected_reason="Event meets protocol seriousness criteria (AESER=Y); medical monitor review mandated.",
                        ),
                    ]
                    esc = MedicalEscalation(
                        escalation_id=f"ESC-SAE-{u}-{cycle}",
                        code="SERIOUS_AE",
                        usubjid=u,
                        site=site,
                        severity="HIGH",
                        summary=f"Serious Adverse Event: {details.get('AETERM')} ({details.get('AESEV')})",
                        evidence=f.get("evidence", []),
                        alternatives=alts,
                        status="PENDING",
                        cycle=cycle,
                    )
                    escalations.append(esc)
                else:
                    monitoring_only.append(f)
            continue

        # 3. Evaluate Potential Hy's Law (Liver Signal)
        if code == "HYS_LAW_CANDIDATE":
            # Check for pre-existing baseline elevation
            is_baseline_elevated = (
                "baseline_elevated" in flags
                or memory.is_rejected(memory.escalation_fingerprint("HYS_LAW_CANDIDATE", u))
            )
            if is_baseline_elevated:
                # Keep as monitoring only; DO NOT escalate
                f["severity"] = "MONITOR"
                monitoring_only.append(f)
                trace.append(
                    TraceEntry(
                        timestamp=ts,
                        cycle=cycle,
                        node="medical_review",
                        action=f"Hy's Law candidate {u} retained in monitoring only",
                        target=u,
                        evidence=f.get("evidence", []),
                        reason="Baseline transaminases were already elevated; monitor, do not escalate to safety.",
                    )
                )
            else:
                f["severity"] = "CRITICAL"
                serious_findings.append(f)
                fp = memory.escalation_fingerprint("HYS_LAW_CANDIDATE", u)
                if not memory.is_rejected(fp) and not memory.has_escalation(fp):
                    alts = [
                        AlternativeConsidered(
                            alternative="Continue routine monitoring without escalation",
                            rejected_reason="Concurrent ALT/AST > 3x ULN and TBIL > 2x ULN meets Hy's Law threshold for severe DILI.",
                        ),
                        AlternativeConsidered(
                            alternative="Attribute liver enzyme elevation to pre-existing hepatic disease",
                            rejected_reason="Screening transaminases were within normal limits; acute drug-induced liver injury cannot be excluded.",
                        ),
                    ]
                    esc = MedicalEscalation(
                        escalation_id=f"ESC-HYS-{u}-{cycle}",
                        code="HYS_LAW_CANDIDATE",
                        usubjid=u,
                        site=site,
                        severity="CRITICAL",
                        summary=f"Hy's Law Signal: {f['summary']}",
                        evidence=f.get("evidence", []),
                        alternatives=alts,
                        status="PENDING",
                        cycle=cycle,
                    )
                    escalations.append(esc)
                    trace.append(
                        TraceEntry(
                            timestamp=ts,
                            cycle=cycle,
                            node="medical_review",
                            action=f"Drafted CRITICAL escalation for Hy's Law on {u}",
                            target=u,
                            evidence=f.get("evidence", []),
                            reason="Normal screening transaminases followed by concurrent ALT > 3x ULN and BILI > 2x ULN.",
                        )
                    )
                else:
                    monitoring_only.append(f)
            continue

        # 4. Check Two-Cycle Recurring Subject Flag (Rule 3)
        if u:
            was_flagged_prev = memory.record_subject_flag(u, cycle, cut)
            if was_flagged_prev and code in ("DOSING_ERROR", "PROHIBITED_MEDICATION"):
                fp = memory.escalation_fingerprint("TWO_CYCLE_RECURRING", u)
                if not memory.is_rejected(fp) and not memory.has_escalation(fp):
                    esc = MedicalEscalation(
                        escalation_id=f"ESC-2CYCLE-{u}-{cycle}",
                        code="TWO_CYCLE_RECURRING",
                        usubjid=u,
                        site=site,
                        severity="HIGH",
                        summary=f"Subject {u} flagged with recurring protocol anomalies across consecutive cycles ({cycle-1} and {cycle})",
                        evidence=f.get("evidence", []),
                        alternatives=[
                            AlternativeConsidered(
                                alternative="Treat cycle findings independently",
                                rejected_reason="Persistent protocol non-compliance across cycles requires centralized medical monitor intervention.",
                            )
                        ],
                        status="PENDING",
                        cycle=cycle,
                    )
                    escalations.append(esc)
                    trace.append(
                        TraceEntry(
                            timestamp=ts,
                            cycle=cycle,
                            node="medical_review",
                            action=f"Automatic escalation for recurring subject {u}",
                            target=u,
                            evidence=f.get("evidence", []),
                            reason=f"Subject was flagged in cycle {cycle-1} and flagged again in cycle {cycle}.",
                        )
                    )

    # Log summary trace
    trace.append(
        TraceEntry(
            timestamp=ts,
            cycle=cycle,
            node="medical_review",
            action=f"{len(escalations)} escalation drafts, {len(monitoring_only)} kept monitor-only",
            target=f"cycle={cycle}",
            reason="Medical plausibility assessment and baseline elevation filtering",
        )
    )

    return escalations, serious_findings, monitoring_only, data_quality_findings, compliance_findings
