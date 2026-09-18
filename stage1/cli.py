"""Developer CLI for the ATLAS backend.

    python -m stage1.cli build  [--data hackathon-data] [--cut N]
    python -m stage1.cli p360   <usubjid> [--json]
    python -m stage1.cli ask    "<question>" [--cut N] [--debug]
    python -m stage1.cli stats  [--out graph_stats.json]
    python -m stage1.cli public [--questions starter/public_questions.json] [--out stage1_public.json]
    python -m stage1.cli bench
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:  # Windows consoles default to cp1252; never crash on a µ or an em dash
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass


def _dump(obj) -> str:
    return json.dumps(obj, indent=2, default=str, ensure_ascii=False)


# --------------------------------------------------------------------------- #
def cmd_build(args) -> int:
    from stage1.atlas import StudyGraph

    g = StudyGraph(args.data)
    stats = g.build(cut=args.cut)
    core = g.core
    print("== files ==")
    for dom, rep in sorted(core.load_reports.items()):
        s = rep.summary()
        print(f"  {dom:<4} {s['file']:<10} rows={s['rows_read']:>6} kept={s['rows_kept']:>6} malformed={s['malformed']:>3} dup_keys={s['duplicate_keys']:>3}")
    print(f"  corrections: {len(core.corrections)} rows ({len(core.corrections_bad)} malformed)")
    print(f"  reference ranges: {len(core.ref_ranges.ranges)} rows, labs={sorted(core.ref_ranges.labs)}, tests={core.ref_ranges.tests()}")
    print(f"  documents: {sorted(core.documents)}")
    print("== cuts ==")
    for c in core.cut_table:
        print(f"  cut {c.cut:>2} -> protocol v{c.protocol_version} (new_records={c.new_records}, corrections={c.corrections})")
    print("== graph ==")
    for key in ("cut", "cut_requested", "protocol_version", "subjects", "unique_persons", "duplicate_subjects", "sites",
                "records", "records_excluded_by_cut", "corrections_applied", "nodes", "edges", "build_ms"):
        print(f"  {key:<24} {stats.get(key)}")
    print("  node_types:", _dump(stats.get("node_types")).replace("\n", " "))
    print("  edge_types:", _dump(stats.get("edge_types")).replace("\n", " "))
    print("  domains:   ", _dump(stats.get("domains")).replace("\n", " "))
    if stats.get("warnings"):
        print("== warnings ==")
        for w in stats["warnings"]:
            print("  -", w)
    return 0


def cmd_p360(args) -> int:
    from stage1.atlas import StudyGraph

    g = StudyGraph(args.data)
    g.build(cut=args.cut)
    p = g.patient360(args.usubjid)
    if args.json:
        print(_dump(p))
        return 0
    print(f"subject {p.get('usubjid')} found={p.get('found')} site={p.get('site')} protocol_version={p.get('protocol_version')} cut={p.get('cut')}")
    for key, val in p.items():
        if key in ("usubjid", "found", "site", "protocol_version", "cut"):
            continue
        if isinstance(val, list):
            print(f"  {key:<18} {len(val)} items")
        elif isinstance(val, dict):
            print(f"  {key:<18} {len(val)} keys")
        else:
            print(f"  {key:<18} {val}")
    return 0


def cmd_ask(args) -> int:
    from stage1.atlas import Atlas, StudyGraph
    from starter.schemas import Question

    g = StudyGraph(args.data)
    g.build(cut=args.cut)
    atlas = Atlas(g)
    t0 = time.perf_counter()
    ans = atlas.answer(Question(question_id=args.qid, text=args.question))
    ms = (time.perf_counter() - t0) * 1000
    print(_dump(ans.model_dump()))
    print(f"# {ms:.1f} ms")
    if args.debug and atlas.last_trace is not None:
        print("# trace:", _dump(atlas.last_trace.as_dict()))
        if atlas.last_internal is not None and atlas.last_internal.payload:
            print("# payload:", _dump(atlas.last_internal.payload))
    return 0


def cmd_stats(args) -> int:
    from scripts.generate_graph_stats import generate

    stats = generate(args.data, args.cut, args.out)
    print(f"wrote {args.out}: nodes={stats['nodes']} edges={stats['edges']} subjects={stats['subjects']} build_ms={stats['build_ms']}")
    return 0


def cmd_public(args) -> int:
    from scripts.run_public_questions import run

    res = run(args.data, args.questions, args.out, args.cut)
    for a, t in zip(res["answers"], res["timing"]):
        ans = a["answer"]
        summary = f"list[{len(ans)}]" if isinstance(ans, list) else ans
        print(f"{a['question_id']:<18} {t['ms']:>8.1f} ms conf={a['confidence']:.2f} answer={summary} evidence={len(a['evidence'])}")
    print(f"wrote {args.out}")
    return 0


def cmd_bench(args) -> int:
    from stage1.atlas import Atlas, StudyGraph
    from starter.schemas import Question

    def timed(label, fn):
        t0 = time.perf_counter()
        out = fn()
        print(f"  {label:<28} {(time.perf_counter() - t0) * 1000:>9.1f} ms")
        return out

    g = StudyGraph(args.data)
    timed("build()", lambda: g.build(cut=args.cut))
    core = g.core
    subj = core.idx.subjects[0]
    site = sorted(core.idx.by_site)[0]
    visit = sorted(core.idx.visits_seen)[0] if core.idx.visits_seen else "BASELINE"
    atlas = Atlas(g)
    timed("patient360()", lambda: g.patient360(subj))
    qs = [
        ("count", f"How many subjects at site {site} discontinued due to an adverse event?"),
        ("lookup", f"List the laboratory and adverse-event records for {subj} within 7 days of the {visit} visit"),
        ("finding", "Which subjects meet the Hy's law criteria?"),
        ("finding (cached)", "Which subjects meet the Hy's law criteria?"),
    ]
    for label, q in qs:
        timed(f"answer[{label}]", lambda q=q: atlas.answer(Question(question_id="bench", text=q)))
    return 0


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stage1.cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="hackathon-data")
    ap.add_argument("--cut", type=int, default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build").set_defaults(fn=cmd_build)
    p = sub.add_parser("p360"); p.add_argument("usubjid"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_p360)
    p = sub.add_parser("ask"); p.add_argument("question"); p.add_argument("--qid", default="cli"); p.add_argument("--debug", action="store_true"); p.set_defaults(fn=cmd_ask)
    p = sub.add_parser("stats"); p.add_argument("--out", default="graph_stats.json"); p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("public"); p.add_argument("--questions", default=str(ROOT / "starter" / "public_questions.json")); p.add_argument("--out", default="stage1_public.json"); p.set_defaults(fn=cmd_public)
    sub.add_parser("bench").set_defaults(fn=cmd_bench)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
