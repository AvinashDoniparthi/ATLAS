"""Test artifact generation and internal consistency across output files."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from stage3.config import WatchConfig
from stage3.watch import StudyWatch

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "hackathon-data"


def test_output_artifacts_generated_and_consistent():
    """Verify all 5 Stage 3 output files exist, are non-empty, and consistent."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        cuts = [1, 2]
        report = watch.run_period(cuts=cuts)

        report_md = out_dir / "surveillance_report.md"
        dec_json = out_dir / "decision_log.json"
        trace_jsonl = out_dir / "trace.jsonl"
        stats_json = out_dir / "run_stats.json"
        state_json = out_dir / "state.json"

        assert report_md.is_file()
        assert dec_json.is_file()
        assert trace_jsonl.is_file()
        assert stats_json.is_file()
        assert state_json.is_file()

        # Check report markdown content
        md_text = report_md.read_text(encoding="utf-8")
        assert "Study Sentinel" in md_text
        assert "Cut-by-Cut Progression Ledger" in md_text
        assert "| 1 |" in md_text

        # Check decision log JSON
        with open(dec_json, "r", encoding="utf-8") as f:
            decs = json.load(f)
        assert isinstance(decs, list)
        assert len(decs) == len(watch.decision_store.all_decisions())

        # Check trace JSONL
        trace_lines = trace_jsonl.read_text(encoding="utf-8").strip().splitlines()
        assert len(trace_lines) == len(watch.trace_store.all_entries())

        # Check run_stats JSON
        with open(stats_json, "r", encoding="utf-8") as f:
            stats = json.load(f)
        assert stats["total_findings"] == report.total_findings
        assert stats["serious_findings"] == report.serious_findings
        assert stats["queries_raised"] == report.queries_raised
