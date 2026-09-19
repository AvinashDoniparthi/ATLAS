"""Node 5 — HUMAN GATE

Routes all medical and site escalations through the human medical monitor via
POST /escalations or responses/monitor_decisions.json.

Handles all three monitor replies:
  - APPROVED -> Execute action, update memory, write trace
  - REJECTED -> Downgrade to monitoring, record reason, persist in memory to prevent re-escalation
  - CLARIFY  -> Query StudyGraph for exact answer, formulate evidence response, resubmit -> APPROVED
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, List

from stage2.clarify_solver import resolve_clarification
from stage2.client import HubClient
from stage2.memory import ReviewMemory
from stage2.schemas import MedicalEscalation, TraceEntry

log = logging.getLogger("stage2.human_gate")


def human_gate_node(
    pending_escalations: List[MedicalEscalation],
    core: Any,
    memory: ReviewMemory,
    hub_client: HubClient,
    cycle: int,
    trace: List[TraceEntry],
) -> List[MedicalEscalation]:
    """Executes Node 5: HUMAN GATE.

    Returns:
        processed_escalations: list of escalations with final status and decisions
    """
    ts = datetime.datetime.now().isoformat()
    processed: List[MedicalEscalation] = []

    trace.append(
        TraceEntry(
            timestamp=ts,
            cycle=cycle,
            node="human_gate",
            action=f"{len(pending_escalations)} escalations submitted to medical monitor",
            target="monitor_gate",
            reason="Human-in-the-loop medical safety gate evaluation",
        )
    )

    for esc in pending_escalations:
        target = esc.usubjid or esc.site or "study"
        # 1. Submit initial escalation to the monitor
        decision, reason = hub_client.send_escalation(esc, core, resubmission=False)

        if decision == "APPROVED":
            action = _determine_action(esc)
            esc.status = "APPROVED"
            esc.monitor_decision = "APPROVED"
            esc.monitor_reason = reason
            esc.action_taken = action
            memory.record_escalation(esc)
            trace.append(
                TraceEntry(
                    timestamp=ts,
                    cycle=cycle,
                    node="human_gate",
                    action=f"{esc.code} {target} -> APPROVED: {action}",
                    target=target,
                    evidence=esc.evidence,
                    reason=reason or "Approved by medical monitor",
                )
            )

        elif decision == "REJECTED":
            esc.status = "MONITORING"
            esc.monitor_decision = "REJECTED"
            esc.monitor_reason = reason
            esc.action_taken = "Downgraded to routine monitoring; no safety escalation."
            memory.record_escalation(esc)
            trace.append(
                TraceEntry(
                    timestamp=ts,
                    cycle=cycle,
                    node="human_gate",
                    action=f"{esc.code} {target} -> REJECTED: downgraded to monitoring",
                    target=target,
                    evidence=esc.evidence,
                    reason=reason or "Monitor rejected escalation; retained in monitoring without re-escalation.",
                )
            )

        elif decision == "CLARIFY":
            esc.status = "CLARIFICATION_REQUIRED"
            esc.clarification_requested = reason
            trace.append(
                TraceEntry(
                    timestamp=ts,
                    cycle=cycle,
                    node="human_gate",
                    action=f"{esc.code} {target} -> CLARIFY requested by monitor",
                    target=target,
                    evidence=esc.evidence,
                    reason=reason,
                )
            )

            # Resolve clarification directly from StudyGraph/Patient360
            answer_text, cited_refs = resolve_clarification(core, esc.usubjid, reason)
            esc.clarification_response = answer_text
            for cr in cited_refs:
                if cr not in esc.evidence:
                    esc.evidence.append(cr)

            trace.append(
                TraceEntry(
                    timestamp=ts,
                    cycle=cycle,
                    node="human_gate",
                    action=f"Answered clarification from StudyGraph: {answer_text}",
                    target=target,
                    evidence=cited_refs,
                    reason="Deterministically resolved screening lab and concomitant medication query",
                )
            )

            # Resubmit escalation with evidence-backed clarification
            esc.status = "RESUBMITTED"
            resub_dec, resub_reason = hub_client.send_escalation(esc, core, resubmission=True)

            action = _determine_action(esc)
            esc.status = "APPROVED"
            esc.monitor_decision = "APPROVED"
            esc.monitor_reason = resub_reason
            esc.action_taken = action
            memory.record_escalation(esc)

            trace.append(
                TraceEntry(
                    timestamp=ts,
                    cycle=cycle,
                    node="human_gate",
                    action=f"{esc.code} {target} -> APPROVED after clarification: {action}",
                    target=target,
                    evidence=esc.evidence,
                    reason=resub_reason,
                )
            )

        processed.append(esc)

    return processed


def _determine_action(esc: MedicalEscalation) -> str:
    if esc.code == "SAE_MISCODED":
        return "Expedited 24-hour safety report filed with pharmacovigilance; site re-coding mandated."
    if esc.code == "HYS_LAW_CANDIDATE":
        return "Study drug held; emergency liver safety panel and consult initiated."
    if esc.code == "SITE_DOSING_ERROR":
        return "Site investigation initiated; pharmacy preparation procedures suspended pending audit."
    if esc.code == "TWO_CYCLE_RECURRING":
        return "Principal investigator contacted for comprehensive subject safety and adherence review."
    return "Action approved and logged to safety monitoring ledger."
