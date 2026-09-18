"""Problem 1 — ATLAS public contract.

    class StudyGraph:
        def __init__(self, data_dir: str): ...
        def build(self, cut: int | None = None) -> dict: ...
        def patient360(self, usubjid: str) -> dict: ...

    class Atlas:
        def __init__(self, graph: StudyGraph): ...
        def answer(self, question: Question) -> Answer: ...

This module is the ONLY place that imports ``starter.schemas``. All clinical
logic lives in ``backend/`` and speaks ``backend.agent.schemas.InternalAnswer``;
``_to_official`` converts at the boundary. If the organiser's ``schemas.py``
differs from the reconstructed one, adapt ``_to_official`` / ``_from_official``.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:  # allow `python starter/run_local_harness.py` from anywhere
    sys.path.insert(0, str(_ROOT))

from starter.schemas import Answer, Question, RecordRef  # noqa: E402

from backend.agent.schemas import STATUS_ERROR, InternalAnswer, QueryTrace  # noqa: E402
from backend.graph.nodes import DocRefKey, RecordKey  # noqa: E402
from backend.graph.study_graph import StudyGraphCore  # noqa: E402

log = logging.getLogger("atlas")
if os.environ.get("ATLAS_DEBUG"):
    logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")


class StudyGraph:
    """Official facade over :class:`backend.graph.study_graph.StudyGraphCore`."""

    def __init__(self, data_dir: str):
        self.data_dir = str(data_dir)
        self.core = StudyGraphCore(self.data_dir)
        self.last_stats: dict[str, Any] = {}

    def build(self, cut: int | None = None) -> dict:
        stats = self.core.build(cut)
        self.last_stats = stats.as_dict()
        return self.last_stats

    def ensure_built(self) -> None:
        """Rebuild when nothing is built yet or the data on disk changed (mid-stage change)."""
        if self.core.view is None:
            self.build(None)
        elif self.core.data_changed():
            log.warning("data directory changed on disk — rebuilding graph at cut=%s", self.core.build_key.cut)
            self.build(self.core.build_key.cut)

    def patient360(self, usubjid: str) -> dict:
        self.ensure_built()
        from backend.queries.patient360 import patient360 as _p360

        return _p360(self.core, usubjid)

    def stats(self) -> dict:
        return dict(self.last_stats)


class Atlas:
    def __init__(self, graph: StudyGraph):
        self.graph = graph
        from backend.agent.router import Router

        self.router = Router(self.graph.core)
        self.llm = None
        # LLM is OPTIONAL. With no GEMINI_API_KEY set the system runs 100%
        # deterministically: all clinical logic (units, thresholds, evidence)
        # is computed without any model call. The LLM only polishes the `text`
        # field when a key IS available.
        if os.environ.get("GEMINI_API_KEY"):
            try:
                from backend.agent.gemini import GeminiAdapter

                self.llm = GeminiAdapter()
                log.info("GeminiAdapter loaded — LLM text polishing enabled")
            except Exception as exc:  # noqa: BLE001 - LLM is optional
                log.warning("Gemini adapter unavailable (running deterministically): %s", exc)
        else:
            log.info("No GEMINI_API_KEY set — running in fully deterministic mode (no LLM calls)")
        self.last_trace: Optional[QueryTrace] = None
        self.last_internal: Optional[InternalAnswer] = None

    def answer(self, question: Question) -> Answer:
        t0 = time.perf_counter()
        qid, text, kind = _from_official(question)
        try:
            self.graph.ensure_built()
            internal = self.router.answer(qid, text, kind=kind, llm=self.llm)
        except Exception as exc:  # noqa: BLE001 - never crash the harness
            log.exception("answer() failed for %s", qid)
            internal = InternalAnswer(
                question_id=qid,
                kind="unknown",
                status=STATUS_ERROR,
                answer=None,
                text=f"Unable to answer: internal error ({type(exc).__name__}).",
                confidence=0.0,
            )
        internal.trace.ms = (time.perf_counter() - t0) * 1000
        self.last_internal = internal
        self.last_trace = internal.trace
        log.debug("trace %s", internal.trace.as_dict())
        return _to_official(internal, self.graph.core)


# --------------------------------------------------------------------------- #
# adapter boundary
# --------------------------------------------------------------------------- #
def _from_official(question: Any) -> tuple[str, str, Optional[str]]:
    if isinstance(question, dict):
        qid = str(question.get("question_id") or question.get("id") or "")
        text = str(question.get("text") or question.get("question") or "")
        kind = question.get("kind")
    else:
        qid = str(getattr(question, "question_id", getattr(question, "id", "")))
        text = str(getattr(question, "text", getattr(question, "question", "")))
        kind = getattr(question, "kind", None)
    return qid, text, kind


def _ref_to_official(ref: Any, core: Optional[Any] = None) -> Optional[RecordRef]:
    try:
        if isinstance(ref, DocRefKey):
            if core is not None:
                from backend.evidence.validator import doc_ref_valid
                ok, _ = doc_ref_valid(core, ref)
                if not ok:
                    return None
            return RecordRef(domain="DOC", document=ref.document, section=ref.section)
        if isinstance(ref, RecordKey):
            if core is not None and not core.exists(ref):
                return None
            return RecordRef(domain=ref.domain, usubjid=ref.usubjid, seq=ref.seq)
    except Exception as exc:  # noqa: BLE001
        log.warning("dropping unrepresentable ref %r: %s", ref, exc)
    return None


def _to_official(ia: InternalAnswer, core: Optional[Any] = None) -> Answer:
    evidence = [r for r in (_ref_to_official(e, core) for e in ia.evidence) if r is not None]
    ans = ia.answer
    if isinstance(ans, list):
        converted = []
        for item in ans:
            if isinstance(item, (RecordKey, DocRefKey)):
                r = _ref_to_official(item, core)
                if r is not None:
                    converted.append(r)
            else:
                converted.append(str(item))
        ans = converted
    elif isinstance(ans, bool):
        ans = str(ans)
    elif ans is not None and not isinstance(ans, (int, str)):
        ans = str(ans)
    conf = max(0.0, min(1.0, float(ia.confidence)))
    return Answer(
        question_id=ia.question_id,
        answer=ans,
        text=ia.text,
        evidence=evidence,
        confidence=conf,
        steps_used=int(ia.steps_used),
        tokens_used=int(ia.tokens_used),
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ATLAS StudyGraph CLI")
    parser.add_argument("--data", default="hackathon-data", help="Path to clinical data directory")
    parser.add_argument("--cut", type=int, default=None, help="Optional data cut to evaluate")
    args = parser.parse_args()

    print(f"Building StudyGraph from {args.data}...")
    sg = StudyGraph(args.data)
    stats = sg.build(cut=args.cut)
    print(f"Graph built successfully in {stats.get('build_ms', 0):.1f} ms:")
    print(f"  Nodes:             {stats.get('nodes'):,}")
    print(f"  Edges:             {stats.get('edges'):,}")
    print(f"  Subjects:          {stats.get('subjects'):,}")
    print(f"  Sites:             {stats.get('sites'):,}")
    print(f"  Current Cut:       {stats.get('cut')}")
    print(f"  Protocol Version:  v{stats.get('protocol_version')}")
    print(f"  Records Processed: {stats.get('records'):,}")
