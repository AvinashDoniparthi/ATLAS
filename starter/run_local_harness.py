#!/usr/bin/env python
"""
LOCAL RECONSTRUCTED HARNESS — NOT OFFICIAL ORGANIZER HARNESS

The Problem 1 specification names ``starter/run_local_harness.py`` but the
organiser's file was not available. This is a local validation harness that
follows the documented contract as closely as the specification allows:

    python starter/run_local_harness.py --module stage1.atlas --data hackathon-data

It imports the module, builds the graph, answers every question in the question
file, validates each Answer against the reconstructed schema, checks every cited
RecordRef structurally and (independently of the graph) against the raw CSVs and
document files, measures wall time per question, and writes ``stage1_public.json``
and ``graph_stats.json``.

Passing this harness does NOT guarantee passing the competition harness.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import json
import os
import sys
import time
import traceback
from pathlib import Path

RECONSTRUCTED_STARTER = True

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from starter.schemas import Answer, Question, RecordRef  # noqa: E402


# --------------------------------------------------------------------------- #
# Independent evidence index (does not trust the graph under test)
# --------------------------------------------------------------------------- #
def _index_raw_records(data_dir: Path) -> tuple[dict[str, set[tuple[str, int | None]]], set[str]]:
    """Return {domain: {(usubjid, seq)}} read straight from the CSVs, plus doc stems."""
    idx: dict[str, set[tuple[str, int | None]]] = {}
    for path in sorted((data_dir / "data").glob("*.csv")):
        domain = path.stem
        if not domain.isupper():
            continue  # reference_ranges / corrections / cuts are not citable domains
        seq_col = f"{domain}SEQ"
        keys: set[tuple[str, int | None]] = set()
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                usubjid = (row.get("USUBJID") or "").strip()
                if not usubjid:
                    continue
                raw_seq = (row.get(seq_col) or "").strip() if seq_col in row else ""
                try:
                    seq: int | None = int(float(raw_seq)) if raw_seq else None
                except ValueError:
                    seq = None
                keys.add((usubjid, seq))
        idx[domain] = keys
    docs = {p.stem for p in (data_dir / "documents").glob("*")}
    return idx, docs


def _check_ref(ref: RecordRef, idx, docs) -> str | None:
    if ref.domain == "DOC":
        if ref.document not in docs and Path(ref.document or "").stem not in docs:
            return f"document '{ref.document}' not found"
        return None
    keys = idx.get(ref.domain)
    if keys is None:
        return f"unknown domain '{ref.domain}'"
    if (ref.usubjid, ref.seq) in keys:
        return None
    if ref.seq is None and any(u == ref.usubjid for u, _ in keys):
        return None
    return f"{ref.domain}/{ref.usubjid}/{ref.seq} does not exist"


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", default="stage1.atlas")
    ap.add_argument("--data", default="hackathon-data")
    ap.add_argument("--questions", default=str(Path(__file__).parent / "public_questions.json"))
    ap.add_argument("--cut", type=int, default=None)
    ap.add_argument("--out", default="stage1_public.json")
    ap.add_argument("--stats-out", default="graph_stats.json")
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds per question (reporting only)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    print("=" * 78)
    print("LOCAL RECONSTRUCTED HARNESS — NOT OFFICIAL ORGANIZER HARNESS")
    print("=" * 78)

    data_dir = Path(args.data).resolve()
    if not data_dir.exists():
        print(f"[FATAL] data dir not found: {data_dir}")
        return 2

    # --- import + build -------------------------------------------------- #
    try:
        mod = importlib.import_module(args.module)
        StudyGraph, Atlas = mod.StudyGraph, mod.Atlas
    except Exception:
        print("[FATAL] could not import module:", args.module)
        traceback.print_exc()
        return 2

    try:
        graph = StudyGraph(str(data_dir))
        t0 = time.perf_counter()
        stats = graph.build(cut=args.cut)
        build_ms = (time.perf_counter() - t0) * 1000
    except Exception:
        print("[FATAL] build() crashed")
        traceback.print_exc()
        return 2

    print(f"build(): {json.dumps(stats, default=str)}  (harness-measured {build_ms:.0f} ms)")
    with open(args.stats_out, "w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=2, default=str)
    print(f"wrote {args.stats_out}")

    try:
        atlas = Atlas(graph)
    except Exception:
        print("[FATAL] Atlas() crashed")
        traceback.print_exc()
        return 2

    # --- questions -------------------------------------------------------- #
    with open(args.questions, encoding="utf-8") as fh:
        qdoc = json.load(fh)
    raw_questions = qdoc["questions"] if isinstance(qdoc, dict) else qdoc
    questions = [Question.model_validate({k: v for k, v in q.items() if k in Question.model_fields}) for q in raw_questions]

    raw_idx, docs = _index_raw_records(data_dir)

    results = []
    n_crash = n_schema = n_evidence = n_breach = 0
    for q in questions:
        row = {"question_id": q.question_id, "ms": None, "crash": None, "schema_error": None, "evidence_errors": []}
        t0 = time.perf_counter()
        try:
            ans = atlas.answer(q)
        except Exception as exc:  # noqa: BLE001
            row["crash"] = f"{type(exc).__name__}: {exc}"
            row["ms"] = (time.perf_counter() - t0) * 1000
            n_crash += 1
            print(f"[CRASH] {q.question_id}: {row['crash']}")
            if args.verbose:
                traceback.print_exc()
            results.append(row)
            continue
        row["ms"] = (time.perf_counter() - t0) * 1000
        if row["ms"] > args.timeout * 1000:
            n_breach += 1

        try:
            validated = Answer.model_validate(ans.model_dump() if hasattr(ans, "model_dump") else ans)
        except Exception as exc:  # noqa: BLE001
            row["schema_error"] = str(exc)
            n_schema += 1
            print(f"[SCHEMA] {q.question_id}: {exc}")
            results.append(row)
            continue
        if validated.question_id != q.question_id:
            row["schema_error"] = f"question_id mismatch: {validated.question_id!r} != {q.question_id!r}"
            n_schema += 1

        for ref in validated.evidence:
            err = _check_ref(ref, raw_idx, docs)
            if err:
                row["evidence_errors"].append(err)
        if isinstance(validated.answer, list):
            for item in validated.answer:
                if isinstance(item, RecordRef):
                    err = _check_ref(item, raw_idx, docs)
                    if err:
                        row["evidence_errors"].append("answer:" + err)
        if row["evidence_errors"]:
            n_evidence += 1

        row["answer"] = validated.model_dump()
        results.append(row)

        summary = validated.answer if not isinstance(validated.answer, list) else f"list[{len(validated.answer)}]"
        flag = " EVIDENCE-FAIL" if row["evidence_errors"] else ""
        print(f"[OK] {q.question_id:<18} {row['ms']:>8.1f} ms  conf={validated.confidence:.2f}  "
              f"answer={summary}  evidence={len(validated.evidence)}{flag}")
        if args.verbose:
            print("     text:", validated.text)
            for e in row["evidence_errors"]:
                print("     evidence error:", e)

    # --- outputs ---------------------------------------------------------- #
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "reconstructed_starter": True,
                "note": "Generated by the LOCAL RECONSTRUCTED harness, not the organiser harness.",
                "data_dir": str(data_dir),
                "cut": args.cut,
                "answers": [r["answer"] for r in results if r.get("answer")],
            },
            fh, indent=2, default=str,
        )
    print(f"wrote {args.out}")

    n = len(questions)
    print("-" * 78)
    print(f"questions: {n}  crashes: {n_crash}  schema failures: {n_schema}  "
          f"evidence failures: {n_evidence}  time breaches(>{args.timeout:.0f}s): {n_breach} "
          f"({(100.0 * n_breach / n) if n else 0:.0f}%)")
    times = [r["ms"] for r in results if r["ms"] is not None]
    if times:
        print(f"per-question ms: min={min(times):.1f} max={max(times):.1f} mean={sum(times)/len(times):.1f}")
    print("This is the reconstructed local harness; passing it is not a competition guarantee.")
    return 1 if (n_crash or n_schema) else 0


if __name__ == "__main__":
    os.chdir(ROOT)
    sys.exit(main())
