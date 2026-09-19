"""Command-line interface for Stage 3 — WATCH."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stage3.config import DEFAULT_PERIOD, WatchConfig
from stage3.watch import StudyWatch


def parse_cuts_arg(cuts_str: str) -> list[int]:
    cuts_str = cuts_str.strip()
    if "-" in cuts_str:
        parts = cuts_str.split("-", 1)
        return list(range(int(parts[0]), int(parts[1]) + 1))
    if "," in cuts_str:
        return [int(p.strip()) for p in cuts_str.split(",") if p.strip()]
    return [int(cuts_str)]


def main() -> None:
    parser = argparse.ArgumentParser(description="ATLAS StudyWatch Stage 3 Surveillance Engine")
    parser.add_argument("--data", default="hackathon-data", help="Path to trial data directory")
    parser.add_argument("--out", default="outputs/stage3", help="Output directory for reports and traces")
    parser.add_argument("--cuts", default="1-12", help="Cuts to surveil, e.g. 1-12 or 1,2,3")
    parser.add_argument("--budget-ms", type=float, default=120000.0, help="Total execution budget in ms")
    args = parser.parse_args()

    data_dir = Path(args.data).resolve()
    out_dir = Path(args.out).resolve()
    target_cuts = parse_cuts_arg(args.cuts)

    cfg = WatchConfig(
        cuts=range(target_cuts[0], target_cuts[-1] + 1) if target_cuts else DEFAULT_PERIOD,
        budget_total_ms=args.budget_ms,
        output_dir=out_dir,
    )

    print("=" * 70)
    print("ATLAS STUDY WATCH — LONG-TERM SURVEILLANCE")
    print(f"Data directory: {data_dir}")
    print(f"Output directory: {out_dir}")
    print(f"Cuts: {target_cuts}")
    print(f"Budget: {args.budget_ms:.0f} ms")
    print("=" * 70)

    watch = StudyWatch(data_dir=data_dir, config=cfg)
    report = watch.run_period(cuts=target_cuts)

    print("\nSurveillance Run Complete:")
    print(f"  Total Findings: {report.total_findings}")
    print(f"  Serious Findings: {report.serious_findings}")
    print(f"  Dispatched Queries: {report.queries_raised}")
    print(f"  Quarantined Sites: {report.quarantined_sites}")
    print(f"  Untrusted Lab Records: {report.untrusted_records_count}")
    print(f"  Final Budget Tier: {report.budget_summary.get('tier')}")

    # Pick up to 5 decision IDs and print explanation verification
    sample_decs = watch.decision_store.all_decisions()[:5]
    if sample_decs:
        print("\nSample Decision Explanations:")
        for d in sample_decs:
            exp = watch.explain(d.decision_id)
            print(f"  [{exp.status}] {exp.decision_id}: {exp.why[:70]}... (consistent={exp.consistent_with_trace})")

    print(f"\nArtifacts generated in {out_dir}:")
    print(f"  - surveillance_report.md")
    print(f"  - decision_log.json")
    print(f"  - trace.jsonl")
    print(f"  - run_stats.json")
    print(f"  - state.json")


if __name__ == "__main__":
    main()
