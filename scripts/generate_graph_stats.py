#!/usr/bin/env python
"""Generate graph_stats.json from a real StudyGraph.build() — never hand-written.

    python scripts/generate_graph_stats.py --data hackathon-data [--cut N] [--out graph_stats.json]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def generate(data_dir: str, cut: int | None = None, out: str = "graph_stats.json") -> dict:
    from stage1.atlas import StudyGraph

    graph = StudyGraph(str(data_dir))
    stats = dict(graph.build(cut=cut))
    stats["generated_by"] = "stage1.atlas StudyGraph.build()"
    stats["data_dir"] = Path(data_dir).name
    stats["timestamp"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=2, default=str)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", default="hackathon-data")
    ap.add_argument("--cut", type=int, default=None)
    ap.add_argument("--out", default="graph_stats.json")
    args = ap.parse_args()
    stats = generate(args.data, args.cut, args.out)
    brief = {k: stats[k] for k in ("nodes", "edges", "subjects", "build_ms", "cut", "protocol_version") if k in stats}
    print(f"wrote {args.out}: {json.dumps(brief)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
