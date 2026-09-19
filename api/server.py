"""ATLAS Study Sentinel — FastAPI server.

Serves the frontend (frontend/index.html) and exposes four endpoints:

  GET  /api/stats          — graph build statistics
  POST /api/ask            — answer a question
  GET  /api/patient360/{id} — Patient 360 for a subject
  GET  /api/subjects       — list of all subject IDs
  GET  /api/rebuild        — force a graph rebuild (amendment resilience)

The graph is built once at startup and rebuilt automatically whenever the
data files change (mid-stage amendment detection via data_signature()).

No API key required. All clinical logic is deterministic.

Usage:
    python -m api.server                    # default: hackathon-data, port 8000
    python -m api.server --data <dir>
    python -m api.server --port 8080
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

log = logging.getLogger("atlas.api")
logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")

# --------------------------------------------------------------------------- #
# App singleton state
# --------------------------------------------------------------------------- #
_graph = None
_atlas = None
_data_dir: Path = ROOT / "hackathon-data"
_build_stats: dict = {}


def _ensure_built() -> None:
    global _graph, _atlas, _build_stats
    if _graph is None:
        _init()
    else:
        _graph.ensure_built()
        _build_stats = _graph.stats()


def _init(data_dir: Optional[Path] = None) -> None:
    global _graph, _atlas, _data_dir, _build_stats
    if data_dir:
        _data_dir = data_dir
    from stage1.atlas import Atlas, StudyGraph

    log.info("building graph from %s …", _data_dir)
    t0 = time.perf_counter()
    _graph = StudyGraph(str(_data_dir))
    _build_stats = _graph.build()
    _atlas = Atlas(_graph)
    log.info("graph ready in %.0f ms", (time.perf_counter() - t0) * 1000)


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(application: FastAPI):
    _init()
    yield


app = FastAPI(title="ATLAS Study Sentinel", version="1.0.0", docs_url="/api/docs", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND = ROOT / "frontend"


# Static files — serve frontend
# --------------------------------------------------------------------------- #
@app.get("/", include_in_schema=False)
@app.head("/", include_in_schema=False)
async def root():
    index = FRONTEND / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"error": "frontend/index.html not found"}, status_code=404)


# --------------------------------------------------------------------------- #
# API endpoints
# --------------------------------------------------------------------------- #
@app.get("/api/stats")
async def stats() -> dict:
    _ensure_built()
    s = dict(_build_stats)
    # add subject list count explicitly for status bar
    try:
        s["subject_list"] = list(_graph.core.idx.subjects)[:10]  # first 10 as sample
        s["tables"] = list(_graph.core.idx.domains)
    except Exception:  # noqa: BLE001
        pass
    return s


@app.get("/api/subjects")
async def subjects() -> dict:
    _ensure_built()
    try:
        subs = sorted(_graph.core.idx.subjects)
        sites = {u: (_graph.core.site_of(u) or "") for u in subs}
        return {"subjects": subs, "sites": sites, "count": len(subs)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


class AskRequest(BaseModel):
    question: str
    question_id: str = "frontend"
    kind: Optional[str] = None


def _serialise_ref(ref: Any) -> dict:
    from backend.graph.nodes import DocRefKey, RecordKey

    if isinstance(ref, RecordKey):
        return {"domain": ref.domain, "usubjid": ref.usubjid, "seq": ref.seq}
    if isinstance(ref, DocRefKey):
        return {"domain": "DOC", "document": ref.document, "section": ref.section}
    # pydantic RecordRef from starter.schemas (already a dict-like model)
    try:
        return ref.model_dump()
    except Exception:  # noqa: BLE001
        return {"raw": str(ref)}


@app.post("/api/ask")
async def ask(req: AskRequest) -> dict:
    _ensure_built()
    try:
        from starter.schemas import Question

        q = Question(question_id=req.question_id, text=req.question, kind=req.kind)
        t0 = time.perf_counter()
        ans = _atlas.answer(q)
        ms = (time.perf_counter() - t0) * 1000

        answer_val = ans.answer
        if isinstance(answer_val, list):
            answer_list = [_serialise_ref(a) if hasattr(a, "domain") else str(a) for a in answer_val]
        else:
            answer_list = answer_val

        evidence_list = [_serialise_ref(e) for e in ans.evidence]

        trace: dict = {}
        if _atlas.last_trace:
            trace = _atlas.last_trace.as_dict()

        return {
            "question_id": ans.question_id,
            "answer": answer_list,
            "text": ans.text,
            "evidence": evidence_list,
            "confidence": ans.confidence,
            "steps_used": ans.steps_used,
            "tokens_used": ans.tokens_used,
            "ms": round(ms, 1),
            "trace": trace,
        }
    except Exception as e:  # noqa: BLE001
        log.exception("ask() failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/patient360/{usubjid:path}")
async def patient360(usubjid: str) -> dict:
    _ensure_built()
    try:
        p = _graph.patient360(usubjid.strip())
        return p
    except Exception as e:  # noqa: BLE001
        log.exception("patient360() failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/related")
async def graph_related(
    entity_type: str,
    entity_id: str,
    source_usubjid: Optional[str] = None
) -> dict:
    _ensure_built()
    try:
        res = _graph.related_subjects(entity_type=entity_type, entity_id=entity_id, source_usubjid=source_usubjid)
        return res
    except Exception as e:  # noqa: BLE001
        log.exception("graph_related() failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/graph/visual")
async def graph_visual() -> dict:
    _ensure_built()
    try:
        core = _graph.core

        # 1. Site nodes and distribution
        site_nodes = []
        site_distribution = []
        for site_id in sorted(core.idx.by_site.keys()):
            subjs = core.idx.by_site[site_id]
            ae_count = 0
            serious_ae_count = 0
            for u in subjs:
                for r in core.idx.records(u, "AE"):
                    ae_count += 1
                    if str(r.get("AESER", "")).upper() in ("Y", "YES"):
                        serious_ae_count += 1
            site_distribution.append({
                "site": site_id,
                "subjects": len(subjs),
                "ae_count": ae_count,
                "serious_ae_count": serious_ae_count,
            })
            site_nodes.append({
                "id": f"SITE:{site_id}",
                "label": f"Site {site_id}",
                "kind": "SITE",
                "site_id": site_id,
                "subject_count": len(subjs),
                "ae_count": ae_count,
                "serious_ae_count": serious_ae_count,
            })

        # 2. Domain breakdown and stats
        domain_distribution = []
        domain_nodes = []
        for dom, recs in sorted(core.view.domains.items(), key=lambda x: -len(x[1])):
            cnt = len(recs)
            subj_count = len(set(r.usubjid for r in recs if r.usubjid))
            domain_distribution.append({
                "domain": dom,
                "records": cnt,
                "subjects": subj_count,
            })
            domain_nodes.append({
                "id": f"DOMAIN:{dom}",
                "label": f"{dom} ({cnt})",
                "kind": "DOMAIN",
                "domain": dom,
                "record_count": cnt,
                "subject_count": subj_count,
            })

        # 3. AE severity distribution
        ae_severity_counts = {"MILD": 0, "MODERATE": 0, "SEVERE": 0, "SERIOUS": 0, "OTHER": 0}
        for r in core.idx.by_domain.get("AE", []):
            sev = (r.get("AESEV") or "OTHER").upper()
            ser = (r.get("AESER") or "N").upper()
            if ser in ("Y", "YES"):
                ae_severity_counts["SERIOUS"] += 1
            if sev in ae_severity_counts:
                ae_severity_counts[sev] += 1
            else:
                ae_severity_counts["OTHER"] += 1

        # 4. Visit distribution
        visit_counts: dict[str, int] = {}
        for (u, v), doms in core.idx.by_subject_visit.items():
            clean_v = v.strip().upper() or "UNSCHEDULED"
            rec_cnt = sum(len(lst) for lst in doms.values())
            visit_counts[clean_v] = visit_counts.get(clean_v, 0) + rec_cnt
        sorted_visits = sorted(visit_counts.items(), key=lambda x: -x[1])[:10]
        visit_distribution = [{"visit": v, "records": c} for v, c in sorted_visits]

        # 5. Core topology nodes and edges for illustrative rendering
        nodes = []
        edges = []

        study_id = f"STUDY:{core.data_dir.name}"
        nodes.append({
            "id": study_id,
            "label": f"Study {core.data_dir.name}",
            "kind": "STUDY",
            "details": {
                "cut": core.cut(),
                "protocol_version": core.protocol_version(),
                "total_subjects": len(core.idx.subjects),
                "total_sites": len(core.idx.by_site),
                "total_records": core.view.record_count,
            }
        })

        # Protocol node
        pv = core.protocol_version()
        if pv is not None:
            proto_id = f"PROTOCOL:{pv}"
            nodes.append({
                "id": proto_id,
                "label": f"Protocol v{pv}",
                "kind": "PROTOCOL",
                "version": pv,
            })
            edges.append({
                "source": study_id,
                "target": proto_id,
                "type": "GOVERNED_BY",
                "label": "Governed by",
            })

        # Add Site nodes and link to Study
        for sn in site_nodes:
            nodes.append(sn)
            edges.append({
                "source": study_id,
                "target": sn["id"],
                "type": "HAS_SITE",
                "label": "Has Site",
            })

        # Add Domain nodes and link to Study
        for dn in domain_nodes:
            nodes.append(dn)
            edges.append({
                "source": study_id,
                "target": dn["id"],
                "type": "CONTAINS_DOMAIN",
                "label": "Contains Domain",
            })

        # Representative key subjects across all sites
        all_subjects = sorted(core.idx.subjects)
        selected_subjects = set()

        # Include duplicate enrolled subjects
        for u1, u2 in core.duplicate_pairs:
            selected_subjects.add(u1)
            selected_subjects.add(u2)

        # Include subjects with serious AEs
        for r in core.idx.by_domain.get("AE", []):
            if str(r.get("AESER", "")).upper() in ("Y", "YES") and r.usubjid:
                selected_subjects.add(r.usubjid)
                if len(selected_subjects) >= 25:
                    break

        # Ensure all 12 sites have subjects represented
        for site_id, subjs in sorted(core.idx.by_site.items()):
            site_reps = [u for u in subjs if u in selected_subjects]
            if len(site_reps) < 2:
                for u in subjs:
                    selected_subjects.add(u)
                    if len([x for x in subjs if x in selected_subjects]) >= 2:
                        break

        for u in sorted(selected_subjects):
            site_id = core.site_of(u) or ""
            subj_records = core.idx.by_subject.get(u, {})
            dom_counts = {d: len(lst) for d, lst in subj_records.items()}
            ae_list = [r.get("AETERM") for r in subj_records.get("AE", []) if r.get("AETERM")]
            has_sae = any(str(r.get("AESER", "")).upper() in ("Y", "YES") for r in subj_records.get("AE", []))
            is_dup = core.is_duplicate_enrolment(u)

            s_node_id = f"SUBJECT:{u}"
            nodes.append({
                "id": s_node_id,
                "label": u,
                "kind": "SUBJECT",
                "usubjid": u,
                "site": site_id,
                "has_sae": has_sae,
                "is_duplicate": is_dup,
                "record_count": sum(dom_counts.values()),
                "domain_counts": dom_counts,
                "ae_terms": ae_list[:3],
            })

            if site_id:
                edges.append({
                    "source": f"SITE:{site_id}",
                    "target": s_node_id,
                    "type": "ENROLLED",
                    "label": "Enrolled",
                })

        # Link duplicate subjects
        for u1, u2 in core.duplicate_pairs:
            if u1 in selected_subjects and u2 in selected_subjects:
                edges.append({
                    "source": f"SUBJECT:{u1}",
                    "target": f"SUBJECT:{u2}",
                    "type": "POSSIBLE_DUPLICATE_OF",
                    "label": "Possible Duplicate",
                })

        return {
            "nodes": nodes,
            "edges": edges,
            "total_nodes_in_graph": len(core.graph.nodes),
            "total_edges_in_graph": core.graph.edge_count,
            "total_subjects": len(core.idx.subjects),
            "total_sites": len(core.idx.by_site),
            "total_records": core.view.record_count,
            "data_insights": {
                "site_distribution": site_distribution,
                "domain_distribution": domain_distribution,
                "ae_severity": ae_severity_counts,
                "visit_distribution": visit_distribution,
            }
        }
    except Exception as e:  # noqa: BLE001
        log.exception("graph_visual() failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/rebuild")
async def rebuild() -> dict:
    global _build_stats
    try:
        _build_stats = _graph.build()
        return {"ok": True, "stats": _build_stats}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))



# --------------------------------------------------------------------------- #
# Gateway & Hub Mock Endpoints (POST /queries, POST /escalations)
# --------------------------------------------------------------------------- #
@app.post("/queries")
async def mock_gateway_query(req: dict) -> dict:
    _ensure_built()
    domain = req.get("domain", "")
    usubjid = req.get("usubjid", "")
    seq = req.get("seq")
    if hasattr(_graph.core, "site_replies") and _graph.core.site_replies.loaded:
        reply_list, exact = _graph.core.site_replies.lookup(domain, usubjid, seq)
        status = reply_list[0] if reply_list else "ANSWERED"
        reply_text = reply_list[1] if len(reply_list) > 1 else ""
        return {"status": status, "reply": reply_text, "exact": exact}
    return {"status": "ANSWERED", "reply": "Source documents verified; query addressed."}


@app.post("/escalations")
async def mock_hub_escalation(req: dict) -> dict:
    _ensure_built()
    code = req.get("code", "")
    usubjid = req.get("usubjid", "")
    site = req.get("site", "")
    if hasattr(_graph.core, "monitor_decisions") and _graph.core.monitor_decisions.loaded:
        reply = _graph.core.monitor_decisions.lookup(code, usubjid) or _graph.core.monitor_decisions.lookup(code, site)
        if reply and len(reply) >= 2:
            return {"decision": reply[0], "reason": reply[1]}
    return {"decision": "APPROVED", "reason": "Consistent with safety criteria; hold dosing pending review."}


# --------------------------------------------------------------------------- #
# Stage 2 MONITOR Service & API Endpoints
# --------------------------------------------------------------------------- #
_crew = None


def _ensure_crew():
    global _crew
    _ensure_built()
    if _crew is None:
        from stage2.crew import ReviewCrew
        _crew = ReviewCrew(
            hub_url="local",
            gateway_url="local",
            team_key="atlas",
            atlas=_atlas,
        )
        if not _crew.reports:
            try:
                _crew.reset_memory()
                _crew.run_cycle(cut=15, protocol_version=3)
            except Exception as e:
                log.warning("Initial run_cycle failed: %s", e)
    return _crew


class RunCycleRequest(BaseModel):
    cut: int = 15
    protocol_version: int = 3


class DecisionRequest(BaseModel):
    escalation_id: str
    decision: str  # APPROVED | REJECTED | CLARIFY
    reason: Optional[str] = None


@app.post("/api/monitor/run-cycle")
async def run_cycle(req: RunCycleRequest) -> dict:
    crew = _ensure_crew()
    try:
        report = crew.run_cycle(cut=req.cut, protocol_version=req.protocol_version)
        return report.model_dump()
    except Exception as e:  # noqa: BLE001
        log.exception("run_cycle failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/monitor/summary")
@app.get("/api/monitor/status")
async def monitor_summary() -> dict:
    crew = _ensure_crew()
    last_report = crew.reports[-1] if crew.reports else None
    return {
        "cycle_count": crew.cycle_count,
        "completed_cycles": list(crew.memory.completed_cycles),
        "completed_cuts": list(crew.memory.completed_cuts),
        "total_queries_raised": len(crew.memory.queries),
        "total_escalations_made": len(crew.memory.escalations),
        "total_rejected_escalations": len(crew.memory.rejected_escalations),
        "has_last_report": last_report is not None,
        "last_report_cut": last_report.cut if last_report else None,
    }


@app.get("/api/monitor/report")
async def monitor_report(cycle: Optional[int] = None) -> dict:
    crew = _ensure_crew()
    if not crew.reports:
        raise HTTPException(status_code=404, detail="No review cycle report available. Run a cycle first.")
    if cycle is not None:
        target = [r for r in crew.reports if r.cycle == cycle]
        if not target:
            raise HTTPException(status_code=404, detail=f"Cycle {cycle} report not found")
        return target[0].model_dump()
    for rep in reversed(crew.reports):
        if rep.escalations or rep.queries:
            return rep.model_dump()
    return crew.reports[-1].model_dump()


@app.get("/api/monitor/escalations")
async def monitor_escalations(status: Optional[str] = None) -> dict:
    crew = _ensure_crew()
    if not crew.reports:
        return {"escalations": []}
    escs = []
    for rep in reversed(crew.reports):
        if rep.escalations:
            escs = rep.escalations
            break
    if not escs and crew.reports:
        escs = crew.reports[-1].escalations
    if status:
        escs = [e for e in escs if e.status.upper() == status.upper()]
    return {"escalations": [e.model_dump() for e in escs]}


@app.get("/api/monitor/escalations/{esc_id}")
async def monitor_escalation_detail(esc_id: str) -> dict:
    crew = _ensure_crew()
    if not crew.reports:
        raise HTTPException(status_code=404, detail="No reports available")
    for rep in reversed(crew.reports):
        for e in rep.escalations:
            if e.escalation_id == esc_id or e.usubjid == esc_id or e.code == esc_id:
                return e.model_dump()
    raise HTTPException(status_code=404, detail=f"Escalation {esc_id} not found")


@app.post("/api/monitor/decision")
async def monitor_submit_decision(req: DecisionRequest) -> dict:
    crew = _ensure_crew()
    if not crew.reports:
        raise HTTPException(status_code=400, detail="No active review cycle")
    target = None
    for rep in reversed(crew.reports):
        for e in rep.escalations:
            if e.escalation_id == req.escalation_id or e.usubjid == req.escalation_id or e.code == req.escalation_id:
                target = e
                break
        if target:
            break
    if not target:
        raise HTTPException(status_code=404, detail=f"Escalation {req.escalation_id} not found")

    target.monitor_decision = req.decision
    if req.reason:
        target.monitor_reason = req.reason
    if req.decision == "APPROVED":
        target.status = "APPROVED"
        target.action_taken = "Approved by monitor; action dispatched."
    elif req.decision == "REJECTED":
        target.status = "MONITORING"
        target.action_taken = "Downgraded to monitoring by medical monitor."
        crew.memory.update_escalation_decision(target.escalation_id, "REJECTED", req.reason or "Rejected")
    elif req.decision == "CLARIFY":
        target.status = "CLARIFICATION_REQUESTED"
        target.clarification_requested = req.reason or ""
        from stage2.clarify_solver import resolve_clarification
        ans_text, ans_refs = resolve_clarification(_atlas.graph.core, target.usubjid, req.reason or "")
        target.clarification_response = ans_text
        target.status = "CLARIFICATION_SUBMITTED"
        target.action_taken = f"Clarification answered: {ans_text[:100]}... Resubmitted."
    return target.model_dump()


@app.get("/api/monitor/queries")
async def monitor_queries(usubjid: Optional[str] = None) -> dict:
    crew = _ensure_crew()
    if not crew.reports:
        return {"queries": []}
    queries = []
    for rep in reversed(crew.reports):
        if rep.queries:
            queries = rep.queries
            break
    if not queries and crew.reports:
        queries = crew.reports[-1].queries
    if usubjid:
        queries = [q for q in queries if q.usubjid == usubjid]
    return {"queries": [q.model_dump() for q in queries]}


@app.get("/api/monitor/queries/{query_id}")
async def monitor_query_detail(query_id: str) -> dict:
    crew = _ensure_crew()
    if not crew.reports:
        raise HTTPException(status_code=404, detail="No reports available")
    for q in crew.reports[-1].queries:
        if q.query_id == query_id:
            return q.model_dump()
    raise HTTPException(status_code=404, detail=f"Query {query_id} not found")


@app.get("/api/monitor/trace")
async def monitor_trace() -> dict:
    crew = _ensure_crew()
    if not crew.reports:
        return {"trace": []}
    return {"trace": [t.model_dump() for t in crew.reports[-1].trace]}


@app.get("/api/monitor/memory")
async def monitor_memory() -> dict:
    crew = _ensure_crew()
    mem = crew.memory
    return {
        "completed_cycles": list(mem.completed_cycles),
        "completed_cuts": list(mem.completed_cuts),
        "raised_queries_count": len(mem.queries),
        "made_escalations_count": len(mem.escalations),
        "rejected_escalations_count": len(mem.rejected_escalations),
        "subject_flags_count": len(mem.subject_cycles),
        "site_escalations_count": len(mem.site_issue_counts),
    }


@app.get("/api/monitor/findings")
async def monitor_findings(serious_only: bool = False) -> dict:
    crew = _ensure_crew()
    if not crew.reports:
        return {"findings": []}
    rep = crew.reports[-1]
    findings = rep.serious_findings if serious_only else rep.findings
    return {"findings": findings, "count": len(findings)}


@app.get("/api/monitor/metadata")
async def monitor_metadata() -> dict:
    _ensure_built()
    cuts_file = _data_dir / "data" / "cuts.csv"
    cuts_info = []
    if cuts_file.exists():
        import csv
        with open(cuts_file, encoding="utf-8-sig") as f:
            cuts_info = list(csv.DictReader(f))
    return {
        "study": "STUDY-042",
        "cuts_available": cuts_info,
        "current_protocol_versions": {c.get("cut"): c.get("protocol_version") for c in cuts_info},
    }


@app.post("/api/monitor/reset")
async def monitor_reset() -> dict:
    crew = _ensure_crew()
    crew.reset_memory()
    return {"ok": True, "message": "Stage 2 memory and cycle state reset."}


# --------------------------------------------------------------------------- #
# Stage 3 — WATCH routes
# --------------------------------------------------------------------------- #
from stage3 import service as watch_service


class WatchRunPeriodRequest(BaseModel):
    cuts: Optional[List[int]] = None
    budget_ms: Optional[float] = None

WatchRunPeriodRequest.model_rebuild()


@app.post("/api/watch/run-period")
async def watch_run_period(req: Optional[WatchRunPeriodRequest] = None) -> dict:
    cuts = req.cuts if req else None
    budget_ms = req.budget_ms if req else None
    return watch_service.run_period(cuts=cuts, budget_ms=budget_ms, data_dir=_data_dir)


@app.get("/api/watch/state")
async def watch_get_state() -> dict:
    return watch_service.get_state()


@app.get("/api/watch/cuts")
async def watch_get_cuts() -> dict:
    return {"cuts": watch_service.get_cuts()}


@app.get("/api/watch/findings")
async def watch_get_findings(cut: Optional[int] = None, serious_only: bool = False) -> dict:
    findings = watch_service.get_findings(cut=cut, serious_only=serious_only)
    return {"findings": findings, "count": len(findings)}


@app.get("/api/watch/decisions")
async def watch_get_decisions(
    cut: Optional[int] = None,
    status: Optional[str] = None,
    code: Optional[str] = None,
    target: Optional[str] = None,
) -> dict:
    decs = watch_service.get_decisions(cut=cut, status=status, code=code, target=target)
    return {"decisions": decs, "count": len(decs)}


@app.get("/api/watch/decisions/{decision_id}")
async def watch_get_decision(decision_id: str) -> dict:
    dec = watch_service.get_decision(decision_id)
    if dec is None:
        raise HTTPException(status_code=404, detail=f"Decision {decision_id} not found")
    return dec


@app.get("/api/watch/decisions/{decision_id}/explain")
async def watch_explain_decision(decision_id: str) -> dict:
    exp = watch_service.explain_decision(decision_id)
    return exp


@app.get("/api/watch/queries")
async def watch_get_queries(cut: Optional[int] = None, status: Optional[str] = None) -> dict:
    return {"queries": watch_service.get_queries(cut=cut, status=status)}


@app.get("/api/watch/escalations")
async def watch_get_escalations(cut: Optional[int] = None, status: Optional[str] = None) -> dict:
    return {"escalations": watch_service.get_escalations(cut=cut, status=status)}


@app.get("/api/watch/human-gate")
async def watch_get_human_gate() -> dict:
    return {"items": watch_service.get_human_gate_items()}


@app.get("/api/watch/sites")
async def watch_get_sites() -> dict:
    return {"sites": watch_service.get_sites()}


@app.get("/api/watch/lab-integrity")
async def watch_get_lab_integrity() -> dict:
    return watch_service.get_lab_integrity()


@app.get("/api/watch/documents")
async def watch_get_documents() -> dict:
    return watch_service.get_documents()


@app.get("/api/watch/budget")
async def watch_get_budget() -> dict:
    return watch_service.get_budget()


@app.get("/api/watch/trace")
async def watch_get_trace(cut: Optional[int] = None, limit: Optional[int] = None) -> dict:
    return {"trace": watch_service.get_trace(cut=cut, limit=limit)}


@app.get("/api/watch/memory")
async def watch_get_memory() -> dict:
    return watch_service.get_memory()


@app.get("/api/watch/report")
async def watch_get_report() -> dict:
    return watch_service.get_report()


@app.get("/api/watch/stats")
async def watch_get_stats() -> dict:
    return watch_service.get_stats()



# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser(description="ATLAS API server")
    ap.add_argument("--data", default=str(ROOT / "hackathon-data"))
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    global _data_dir
    _data_dir = Path(args.data).resolve()
    uvicorn.run("api.server:app", host=args.host, port=args.port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
