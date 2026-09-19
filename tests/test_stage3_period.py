"""Test 12-cut execution, custom cut ranges, and incremental advancement."""
from __future__ import annotations

import tempfile
from pathlib import Path

from stage3.adapters import IncrementalStudyGraph, make_crew
from stage3.config import DEFAULT_PERIOD, WatchConfig
from stage3.watch import StudyWatch

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "hackathon-data"


def test_custom_period_incremental():
    """Verify custom cuts [1, 2, 3] execute incrementally with monotonic record growth."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        cuts = [1, 2, 3]
        report = watch.run_period(cuts=cuts)

        assert len(report.cut_summaries) == len(cuts)
        # Verify monotonic growth in records
        rec_counts = [cr.new_records for cr in report.cut_summaries]
        assert all(r > 0 for r in rec_counts)

        # Verify incremental graph used
        assert isinstance(watch.graph, IncrementalStudyGraph)
        assert watch.graph.core.view.effective_cut == cuts[-1]


def test_full_12_cut_period_and_serious_assertion():
    """Verify full default 12-cut run and serious event same-cut assertion."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        report = watch.run_period(cuts=list(DEFAULT_PERIOD))

        assert len(report.cuts_evaluated) == len(list(DEFAULT_PERIOD))
        assert report.total_findings > 0
        assert report.serious_findings > 0

        # Verify trace has serious event assertions
        serious_traces = [
            t for t in watch.trace_store.all_entries()
            if "serious_event_same_cut_assertion" in t.action
        ]
        assert len(serious_traces) > 0
        # All serious assertions must be confirmed
        assert all(t.status == "CONFIRMED" for t in serious_traces)
