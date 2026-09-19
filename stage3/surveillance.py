"""Single-cut orchestration for Stage 3 — WATCH."""
from __future__ import annotations

import logging
from typing import Any, List, Optional, Set, Tuple

from starter.schemas import RecordRef
from stage2.nodes.human_gate import human_gate_node
from stage2.schemas import AlternativeConsidered, ReviewReport
from stage3.amendment import check_amendment
from stage3.budget import BudgetManager
from stage3.config import WatchConfig
from stage3.corrections import process_corrections
from stage3.cuts import CutDelta, compute_cut_delta
from stage3.decisions import DecisionStore
from stage3.document_integrity import check_document_integrity
from stage3.human_state import HumanState
from stage3.lab_integrity import check_lab_integrity
from stage3.models import CutResult, Decision, WatchTraceEntry
from stage3.site_anomaly import check_site_anomalies
from stage3.state import WatchState
from stage3.trace import TraceStore

log = logging.getLogger("stage3.surveillance")


def run_surveillance_cut(
    cut: int,
    crew: Any,
    graph: Any,
    hub_client: Any,
    gateway_client: Any,
    state: WatchState,
    human_state: HumanState,
    budget_manager: BudgetManager,
    decision_store: DecisionStore,
    trace_store: TraceStore,
    config: WatchConfig,
) -> CutResult:
    """Executes the complete deterministic surveillance pipeline for a single cut."""
    log.info("Starting Stage 3 surveillance for cut %d", cut)
    core = graph.core

    # 1. Budget manager start
    deg_dec, deg_trace = budget_manager.start_cut(cut)
    if deg_dec and deg_trace:
        decision_store.add(deg_dec)
        trace_store.append(deg_trace)

    # 2. Cut delta
    delta: CutDelta = compute_cut_delta(core, cut, state)
    pv = delta.protocol_version

    # Update state known sites and domains
    state.known_sites.update(delta.new_sites)
    state.known_domains.update(delta.new_domains)

    # 3. Document integrity
    doc_changes, doc_decs, doc_traces = check_document_integrity(cut, pv, state, core)
    decision_store.extend(doc_decs)
    trace_store.extend(doc_traces)

    # 4. Amendment check
    amend_rules, amend_traces = check_amendment(cut, state.active_protocol_version, pv, core)
    state.active_protocol_version = pv
    trace_store.extend(amend_traces)

    # 5. Incremental graph advancement
    # Calling graph.build(cut, pv) invokes core.advance_to_cut(cut, pv) via IncrementalStudyGraph
    graph.build(cut=cut, protocol_version=pv)

    # 6. Lab integrity detector
    lab_events, lab_decs, lab_traces, lab_queries = check_lab_integrity(
        cut, core, state, config, gateway_client, memory=crew.memory
    )
    decision_store.extend(lab_decs)
    trace_store.extend(lab_traces)

    # 7. Site anomaly detector
    site_risks, site_decs, site_traces, site_escalations = check_site_anomalies(
        cut, core, state, config
    )
    decision_store.extend(site_decs)
    trace_store.extend(site_traces)

    # 8. Crew cycle execution (Stage 2)
    hub_client.set_cut(cut)
    crew_report: ReviewReport = crew.run_cycle(cut=cut, protocol_version=pv)

    # Ingest site escalations through human_gate_node if any
    if site_escalations:
        processed_site_esc = human_gate_node(
            site_escalations, core, crew.memory, hub_client, cut, crew_report.trace
        )
        crew_report.escalations.extend(processed_site_esc)

    # Ingest crew trace entries into trace store
    trace_store.ingest_crew_trace(crew_report.trace, cut)

    # 9. Corrections evaluation & retracted finding resolution
    corr_decs, corr_traces = process_corrections(
        cut, delta.corrections, state, crew_report.findings, core
    )
    decision_store.extend(corr_decs)
    trace_store.extend(corr_traces)

    # 10. Post-crew filter: quarantine untrusted findings, serious-event same-cut assertion
    for f in crew_report.findings:
        is_new, fp = state.record_finding(f, cut)
        f_site = f.get("site") or core.site_of(f.get("usubjid", ""))

        # Check if finding rests on untrusted lab values or quarantined site
        ev = f.get("evidence", [])
        rests_on_untrusted = False
        for item in ev:
            d = getattr(item, "domain", None) or (item.get("domain") if isinstance(item, dict) else "")
            u = getattr(item, "usubjid", None) or (item.get("usubjid") if isinstance(item, dict) else "")
            s = getattr(item, "seq", None) if not isinstance(item, dict) else item.get("seq")
            if state.is_lab_untrusted(d, u, s):
                rests_on_untrusted = True
                break

        is_quarantined_site = f_site in state.quarantined_sites
        if rests_on_untrusted or is_quarantined_site:
            f["status"] = "QUARANTINED"
            if fp in state.findings_ledger:
                state.findings_ledger[fp]["status"] = "QUARANTINED"

        # Record finding as a Decision if new
        if is_new:
            dec_status = "QUARANTINED" if (rests_on_untrusted or is_quarantined_site) else "CONFIRMED"
            dec_id = f"D-{cut}-FINDING-{abs(hash(fp)) % 1000000:06d}"
            ev_refs = []
            for item in ev:
                if isinstance(item, RecordRef):
                    ev_refs.append(item)
                elif isinstance(item, dict):
                    ev_refs.append(RecordRef(**item))
                elif hasattr(item, "ref"):
                    ev_refs.append(item.ref)

            dec = Decision(
                decision_id=dec_id,
                cut=cut,
                decision_type="FINDING",
                target=f.get("usubjid") or f_site or "study",
                code=f.get("code", ""),
                status=dec_status,
                reason=f.get("rationale") or f.get("summary") or "CDISC finding detected",
                evidence=ev_refs,
                alternatives=[],
                timestamp=core.loaded_signature or "",
                status_history=[{"cut": cut, "status": dec_status}],
            )
            decision_store.add(dec)

            trace_store.append(
                WatchTraceEntry(
                    timestamp=core.loaded_signature or "",
                    cycle=cut,
                    cut=cut,
                    trace_id=f"T-{cut}-FINDING-{abs(hash(fp)) % 1000000:06d}",
                    decision_id=dec_id,
                    node="surveillance",
                    action=f"finding_recorded: {f.get('code')} for {f.get('usubjid')}",
                    target=f.get("usubjid"),
                    evidence=ev_refs,
                    reason=f.get("rationale") or f.get("summary") or "",
                    status=dec_status,
                )
            )

    # Serious-event same-cut assertion: every serious finding has escalation
    for sf in crew_report.serious_findings:
        sf_subj = sf.get("usubjid")
        sf_site = sf.get("site") or core.site_of(sf_subj)
        # Escalated in this cut, or already escalated / rejected in an earlier
        # cut (Stage 2 memory dedup) — a serious event must never wait for a
        # later cut, but must not be re-escalated either.
        sf_code = sf.get("code", "")
        esc_codes = {sf_code, "SERIOUS_AE", "SAE_MISCODED", "HYS_LAW_CANDIDATE"}
        fps = {crew.memory.escalation_fingerprint(c, sf_subj or sf_site or "") for c in esc_codes}
        has_matching_esc = any(
            (e.usubjid == sf_subj or (sf_subj is None and e.site == sf_site))
            for e in crew_report.escalations
        ) or any(crew.memory.has_escalation(fp) or crew.memory.is_rejected(fp) for fp in fps)
        trace_store.append(
            WatchTraceEntry(
                timestamp=core.loaded_signature or "",
                cycle=cut,
                cut=cut,
                trace_id=f"T-{cut}-SERIOUS-ASSERT-{abs(hash(sf_subj or sf_site or '')) % 1000000:06d}",
                node="surveillance",
                action=f"serious_event_same_cut_assertion for {sf_subj or sf_site}",
                target=sf_subj or sf_site,
                evidence=[],
                reason=f"Asserted safety escalation filed at cut {cut} (escalated={has_matching_esc})",
                status="CONFIRMED" if has_matching_esc else "UNRESOLVED",
            )
        )

    # 11. Ingest newly raised escalations into human_state
    for esc in crew_report.escalations:
        h_dec, h_trace = human_state.ingest_new_escalation(esc, cut, core, crew.memory, hub_client)
        if h_dec:
            decision_store.add(h_dec)
        if h_trace:
            trace_store.append(h_trace)

    # Process carried-over pending items
    pen_decs, pen_traces = human_state.process_pending(cut, core, crew.memory, hub_client)
    decision_store.extend(pen_decs)
    trace_store.extend(pen_traces)

    # 12. Record queries as decisions
    for q in crew_report.queries + lab_queries:
        q_dec_id = f"D-{cut}-QUERY-{abs(hash(q.fingerprint or q.query_id)) % 1000000:06d}"
        q_dec = Decision(
            decision_id=q_dec_id,
            cut=cut,
            decision_type="QUERY",
            target=q.site or q.usubjid,
            code="DATA_QUALITY_QUERY",
            status="CONFIRMED",
            reason=q.query_text,
            evidence=[RecordRef(domain=q.domain, usubjid=q.usubjid, seq=q.seq)],
            alternatives=[],
            timestamp=core.loaded_signature or "",
            status_history=[{"cut": cut, "status": "OPEN"}],
        )
        decision_store.add(q_dec)

    # 13. End cut timing and record result
    ms_used = budget_manager.end_cut(cut)
    bstate = budget_manager.state()

    cut_res = CutResult(
        cut=cut,
        protocol_version=pv,
        new_records=delta.new_records_count,
        corrections=delta.corrections_count,
        findings_count=len(crew_report.findings),
        serious_findings_count=len(crew_report.serious_findings),
        escalations_count=len(crew_report.escalations),
        queries_count=len(crew_report.queries) + len(lab_queries),
        budget_ms_used=ms_used,
        degradation_tier=bstate.tier,
        quarantined_sites=sorted(state.quarantined_sites),
        untrusted_labs=len(state.untrusted_lab_keys),
    )

    state.cut_history.append(cut_res.model_dump())
    state.save()
    crew.memory.save()

    return cut_res
