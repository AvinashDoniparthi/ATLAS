"""Node 6 — EXECUTE

Assembles the final ReviewReport, summarizes completed actions, calculates
metrics, records cycle completion in ReviewMemory, and finalizes the audit trace.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, List

from stage2.memory import ReviewMemory
from stage2.schemas import (
    ComplianceDeviation,
    MedicalEscalation,
    ReviewReport,
    SiteFlag,
    SiteQuery,
    TraceEntry,
)

log = logging.getLogger("stage2.execute")


def execute_node(
    cycle: int,
    cut: int,
    protocol_version: int,
    findings: List[dict],
    serious_findings: List[dict],
    monitoring_only: List[dict],
    queries: List[SiteQuery],
    escalations: List[MedicalEscalation],
    deviations: List[ComplianceDeviation],
    site_flags: List[SiteFlag],
    memory: ReviewMemory,
    trace: List[TraceEntry],
) -> ReviewReport:
    """Executes Node 6: EXECUTE.

    Returns:
        ReviewReport: The complete cycle report.
    """
    ts = datetime.datetime.now().isoformat()

    # Collect completed actions and decisions
    completed_actions: List[str] = []
    monitor_decisions_list: List[dict] = []
    unresolved_issues: List[dict] = []

    approved_count = 0
    rejected_count = 0
    clarified_count = 0

    for esc in escalations:
        monitor_decisions_list.append({
            "escalation_id": esc.escalation_id,
            "code": esc.code,
            "usubjid": esc.usubjid,
            "site": esc.site,
            "decision": esc.monitor_decision,
            "reason": esc.monitor_reason,
            "clarification_requested": esc.clarification_requested,
            "clarification_response": esc.clarification_response,
            "status": esc.status,
        })
        if esc.action_taken:
            completed_actions.append(f"[{esc.code} - {esc.usubjid or esc.site}] {esc.action_taken}")
        if esc.status == "APPROVED":
            approved_count += 1
        elif esc.status in ("REJECTED", "MONITORING"):
            rejected_count += 1
        if esc.clarification_requested:
            clarified_count += 1

    # Track queries
    closed_queries = 0
    open_queries = 0
    for q in queries:
        if q.status in ("CLOSED", "ANSWERED"):
            closed_queries += 1
        else:
            open_queries += 1
            unresolved_issues.append({
                "type": "OPEN_QUERY",
                "query_id": q.query_id,
                "usubjid": q.usubjid,
                "domain": q.domain,
                "seq": q.seq,
                "text": q.query_text,
            })

    # Summary statistics
    stats = {
        "cycle": cycle,
        "cut": cut,
        "protocol_version": protocol_version,
        "total_findings": len(findings),
        "serious_findings_count": len(serious_findings),
        "monitoring_only_count": len(monitoring_only),
        "queries_total": len(queries),
        "queries_closed": closed_queries,
        "queries_open": open_queries,
        "escalations_total": len(escalations),
        "escalations_approved": approved_count,
        "escalations_rejected": rejected_count,
        "escalations_clarified": clarified_count,
        "compliance_deviations_total": len(deviations),
        "site_flags_total": len(site_flags),
    }

    # Final trace entry
    trace.append(
        TraceEntry(
            timestamp=ts,
            cycle=cycle,
            node="execute",
            action=f"Cycle {cycle} complete: {len(completed_actions)} actions executed, {open_queries} open queries tracked",
            target=f"cut={cut}|pv={protocol_version}",
            reason="ReviewReport compiled successfully",
        )
    )

    memory.record_cycle_completed(cycle, cut)

    report = ReviewReport(
        cycle=cycle,
        cut=cut,
        protocol_version=protocol_version,
        findings=findings,
        serious_findings=serious_findings,
        monitoring_only_findings=monitoring_only,
        queries=queries,
        escalations=escalations,
        compliance_deviations=deviations,
        site_level_flags=site_flags,
        monitor_decisions=monitor_decisions_list,
        completed_actions=completed_actions,
        unresolved_issues=unresolved_issues,
        trace=trace,
        statistics=stats,
    )
    return report
