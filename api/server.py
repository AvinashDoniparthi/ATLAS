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
# Stage 2 — MONITOR API endpoints
# --------------------------------------------------------------------------- #
_crew = None


def _ensure_crew():
    global _crew
    _ensure_built()
    if _crew is None:
        from stage2.crew import ReviewCrew
        _crew = ReviewCrew(hub_url="local", gateway_url="local", team_key="team-reve", atlas=_atlas)
    return _crew


class RunCycleRequest(BaseModel):
    cut: int = 15
    protocol_version: int = 3


@app.post("/api/monitor/run-cycle")
async def monitor_run_cycle(req: RunCycleRequest) -> dict:
    crew = _ensure_crew()
    try:
        t0 = time.perf_counter()
        report = crew.run_cycle(cut=req.cut, protocol_version=req.protocol_version)
        ms = (time.perf_counter() - t0) * 1000
        d = report.model_dump()
        d["execution_ms"] = round(ms, 1)
        d["report"] = dict(d)
        return d
    except Exception as e:  # noqa: BLE001
        log.exception("run_cycle failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/monitor/status")
async def monitor_status() -> dict:
    crew = _ensure_crew()
    latest = crew.reports[-1].model_dump() if crew.reports else None
    return {
        "cycle_count": crew.cycle_count,
        "completed_cycles": crew.memory.completed_cycles,
        "queries_in_memory": len(crew.memory.queries),
        "escalations_in_memory": len(crew.memory.escalations),
        "rejected_count": len(crew.memory.rejected_escalations),
        "open_queries_count": len(crew.memory.open_query_ids),
        "latest_report": latest,
        "last_cycle": latest,
    }


@app.get("/api/monitor/history")
async def monitor_history() -> list[dict]:
    crew = _ensure_crew()
    return [r.model_dump() for r in crew.reports]


class EscalationActionRequest(BaseModel):
    escalation_id: str
    action: str  # APPROVED | REJECTED | CLARIFY
    reason: Optional[str] = None


@app.post("/api/monitor/escalation-action")
async def monitor_escalation_action(req: EscalationActionRequest) -> dict:
    crew = _ensure_crew()
    action = req.action.upper()
    if action not in ("APPROVED", "REJECTED", "CLARIFY"):
        raise HTTPException(status_code=400, detail="Action must be APPROVED, REJECTED, or CLARIFY")

    # Find the escalation in latest report or memory
    target_esc = None
    if crew.reports:
        for esc in crew.reports[-1].escalations:
            if esc.escalation_id == req.escalation_id:
                target_esc = esc
                break

    clarification_res = None
    if action == "CLARIFY" and target_esc:
        from stage2.clarify_solver import resolve_clarification
        q_text = req.reason or target_esc.clarification_requested or "Screening ALT and concomitant medications"
        ans_text, cited_refs = resolve_clarification(_graph.core, target_esc.usubjid, q_text)
        clarification_res = {
            "question": q_text,
            "answer": ans_text,
            "evidence": [r.model_dump() for r in cited_refs],
        }
        target_esc.clarification_requested = q_text
        target_esc.clarification_response = ans_text
        for cr in cited_refs:
            if cr not in target_esc.evidence:
                target_esc.evidence.append(cr)
        target_esc.status = "CLARIFICATION_REQUIRED"
    elif action == "APPROVED" and target_esc:
        target_esc.status = "APPROVED"
        target_esc.monitor_decision = "APPROVED"
        target_esc.monitor_reason = req.reason or "Approved by medical monitor via UI"
        target_esc.action_taken = "Approved by human monitor; safety actions executed."
    elif action == "REJECTED" and target_esc:
        target_esc.status = "MONITORING"
        target_esc.monitor_decision = "REJECTED"
        target_esc.monitor_reason = req.reason or "Rejected by medical monitor; downgraded to monitoring"
        target_esc.action_taken = "Downgraded to monitoring; no safety escalation."

    crew.memory.update_escalation_decision(req.escalation_id, action, req.reason)
    return {
        "ok": True,
        "escalation_id": req.escalation_id,
        "action": action,
        "status": target_esc.status if target_esc else action,
        "clarification": clarification_res,
    }


@app.post("/api/monitor/reset")
async def monitor_reset() -> dict:
    crew = _ensure_crew()
    crew.reset_memory()
    return {"ok": True, "message": "Crew memory reset successfully"}


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
