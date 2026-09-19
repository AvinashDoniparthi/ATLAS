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
from typing import Any, Optional

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
        crew.memory.record_rejection(target.code, target.usubjid, target.site, req.reason or "Rejected")
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
