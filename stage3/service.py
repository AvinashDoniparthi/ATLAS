"""Plain-Python service facade for Stage 3 API and CLI integration."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from stage3.config import DEFAULT_PERIOD, WatchConfig
from stage3.models import CutResult, Explanation, SurveillanceReport
from stage3.watch import StudyWatch

log = logging.getLogger("stage3.service")

_service_watch: Optional[StudyWatch] = None
_service_data_dir: Path = Path("hackathon-data")


def get_watch(
    data_dir: Optional[Union[str, Path]] = None,
    budget_ms: Optional[float] = None,
    reset: bool = False,
) -> StudyWatch:
    """Returns or initializes the singleton StudyWatch instance."""
    global _service_watch, _service_data_dir
    if data_dir is not None:
        _service_data_dir = Path(data_dir)

    if _service_watch is None or reset:
        cfg = WatchConfig()
        if budget_ms is not None:
            cfg.budget_total_ms = budget_ms
        _service_watch = StudyWatch(data_dir=_service_data_dir, config=cfg)

    return _service_watch


def run_period(
    cuts: Optional[List[int]] = None,
    budget_ms: Optional[float] = None,
    data_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    watch = get_watch(data_dir=data_dir, budget_ms=budget_ms, reset=True)
    report: SurveillanceReport = watch.run_period(cuts=cuts)
    return report.model_dump()


def get_state() -> Dict[str, Any]:
    watch = get_watch()
    cuts_count = len(watch.cut_summaries) or len(watch.state.cut_history)
    return {
        "active_protocol_version": watch.state.active_protocol_version,
        "quarantined_sites": sorted(watch.state.quarantined_sites),
        "untrusted_lab_records": len(watch.state.untrusted_lab_keys),
        "known_sites": sorted(watch.state.known_sites),
        "known_domains": sorted(watch.state.known_domains),
        "total_findings_recorded": len(watch.state.findings_ledger),
        "cuts_completed": cuts_count,
    }


def get_cuts() -> List[Dict[str, Any]]:
    watch = get_watch()
    if watch.cut_summaries:
        return [cr.model_dump() for cr in watch.cut_summaries]
    return [c if isinstance(c, dict) else c.model_dump() for c in watch.state.cut_history]


def get_findings(cut: Optional[int] = None, serious_only: bool = False) -> List[Dict[str, Any]]:
    watch = get_watch()
    res = list(watch.state.findings_ledger.values())
    if cut is not None:
        res = [f for f in res if f.get("first_detected_cut") == cut or f.get("last_seen_cut") == cut]
    if serious_only:
        res = [f for f in res if f.get("severity") in ("CRITICAL", "HIGH")]
    return res


def get_decisions(
    cut: Optional[int] = None,
    status: Optional[str] = None,
    code: Optional[str] = None,
    target: Optional[str] = None,
) -> List[Dict[str, Any]]:
    watch = get_watch()
    decs = watch.decision_store.filter(cut=cut, status=status, code=code, target=target)
    return [d.model_dump() for d in decs]


def get_decision(decision_id: str) -> Optional[Dict[str, Any]]:
    watch = get_watch()
    d = watch.decision_store.get(decision_id)
    return d.model_dump() if d else None


def explain_decision(decision_id: str) -> Dict[str, Any]:
    watch = get_watch()
    exp: Explanation = watch.explain(decision_id)
    return exp.model_dump()


def get_queries(cut: Optional[int] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    watch = get_watch()
    queries = list(watch.crew.memory.queries.values())
    if cut is not None:
        queries = [q for q in queries if q.get("cut") == cut]
    if status is not None:
        queries = [q for q in queries if q.get("status") == status]
    return queries


def get_escalations(cut: Optional[int] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    watch = get_watch()
    escs = list(watch.crew.memory.escalations.values())
    if cut is not None:
        escs = [e for e in escs if e.get("cycle") == cut]
    if status is not None:
        escs = [e for e in escs if e.get("status") == status]
    return escs


def get_human_gate_items() -> List[Dict[str, Any]]:
    watch = get_watch()
    return [item.model_dump() for item in watch.human_state.items.values()]


def get_sites() -> List[Dict[str, Any]]:
    watch = get_watch()
    core = watch.graph.core
    sites_info = []
    for site in sorted(core.idx.by_site.keys()):
        sites_info.append({
            "site": site,
            "subjects_count": len(core.idx.subjects_at(site)),
            "quarantined": site in watch.state.quarantined_sites,
            "issue_counts": watch.crew.memory.site_issue_counts.get(site, {}),
        })
    return sites_info


def get_lab_integrity() -> Dict[str, Any]:
    watch = get_watch()
    return {
        "untrusted_records_count": len(watch.state.untrusted_lab_keys),
        "untrusted_records": [list(k) for k in watch.state.untrusted_lab_keys],
    }


def get_documents() -> Dict[str, Any]:
    watch = get_watch()
    return {
        "doc_hash_registry": watch.state.doc_hash_registry,
        "active_protocol_version": watch.state.active_protocol_version,
    }


def get_budget() -> Dict[str, Any]:
    watch = get_watch()
    if watch.budget_manager.spent_ms == 0.0:
        cuts = watch.cut_summaries or [CutResult(**c) if isinstance(c, dict) else c for c in watch.state.cut_history]
        if cuts:
            total_spent = sum(c.budget_ms_used for c in cuts)
            if total_spent > 0:
                watch.budget_manager.spent_ms = total_spent
                watch.budget_manager.current_tier = cuts[-1].degradation_tier
    return watch.budget_manager.state().model_dump()


def get_trace(cut: Optional[int] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    watch = get_watch()
    if cut is not None:
        entries = watch.trace_store.for_cut(cut)
    else:
        entries = watch.trace_store.all_entries()
    if limit is not None and limit > 0:
        entries = entries[-limit:]
    return [e.model_dump() for e in entries]


def get_memory() -> Dict[str, Any]:
    watch = get_watch()
    mem = watch.crew.memory
    return {
        "queries_count": len(mem.queries),
        "escalations_count": len(mem.escalations),
        "rejected_escalations_count": len(mem.rejected_escalations),
        "subject_cycles_count": len(mem.subject_cycles),
        "site_issue_counts": mem.site_issue_counts,
        "completed_cycles": mem.completed_cycles,
        "completed_cuts": list(mem.completed_cuts),
        "quarantined_sites": list(mem.quarantined_sites),
        "untrusted_labs_count": len(mem.untrusted_labs),
    }


def get_report() -> Dict[str, Any]:
    watch = get_watch()
    if watch.last_report:
        return watch.last_report.model_dump()
    stats_file = watch.output_dir / "run_stats.json"
    if stats_file.is_file():
        try:
            with open(stats_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def get_stats() -> Dict[str, Any]:
    watch = get_watch()
    cuts_count = len(watch.cut_summaries) or len(watch.state.cut_history)
    return {
        "cuts_completed": cuts_count,
        "decisions_total": len(watch.decision_store.decisions),
        "trace_entries_total": len(watch.trace_store.entries),
        "quarantined_sites": sorted(watch.state.quarantined_sites),
        "untrusted_lab_records": len(watch.state.untrusted_lab_keys),
        "budget": get_budget(),
    }


def get_cut_detail(cut: int) -> Optional[Dict[str, Any]]:
    watch = get_watch()
    # Find CutResult
    cr = None
    if watch.cut_summaries:
        for c in watch.cut_summaries:
            if c.cut == cut:
                cr = c
                break
    if cr is None and watch.state.cut_history:
        for c in watch.state.cut_history:
            c_cut = c.get("cut") if isinstance(c, dict) else getattr(c, "cut", None)
            if c_cut == cut:
                cr = CutResult(**c) if isinstance(c, dict) else c
                break

    if cr is None:
        return None

    # Cut specific findings, decisions, trace, and human gate items
    cut_findings = get_findings(cut=cut)
    cut_decs = get_decisions(cut=cut)
    cut_trace = get_trace(cut=cut)
    hg_items = [
        item.model_dump() for item in watch.human_state.items.values()
        if item.detected_at_cut == cut or item.escalated_at_cut == cut or item.reply_cut == cut or cut in item.submitted_cuts
    ]

    return {
        "cut": cut,
        "cut_summary": cr.model_dump(),
        "findings": cut_findings,
        "decisions": cut_decs,
        "trace": cut_trace,
        "human_gate_items": hg_items,
    }


def submit_human_gate_decision(
    escalation_id: str,
    decision: str,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    watch = get_watch()
    active_cut = len(watch.cut_summaries) or len(watch.state.cut_history) or 1
    core = watch.graph.core
    memory = watch.crew.memory
    hub_client = watch.hub_client

    item, dec, tr = watch.human_state.submit_human_decision(
        escalation_id=escalation_id,
        decision=decision,
        reason=reason,
        core=core,
        memory=memory,
        hub_client=hub_client,
        cut=active_cut,
    )

    if item is None:
        return {"success": False, "error": f"Escalation {escalation_id} not found in Human Gate"}

    if dec:
        watch.decision_store.add(dec)
        watch.decision_store.save(watch.output_dir / "decision_log.json")
    if tr:
        watch.trace_store.append(tr)
        watch.trace_store.save(watch.output_dir / "trace.jsonl")

    watch.state.save()
    memory.save()

    return {
        "success": True,
        "item": item.model_dump(),
        "decision": dec.model_dump() if dec else None,
        "trace": tr.model_dump() if tr else None,
    }
