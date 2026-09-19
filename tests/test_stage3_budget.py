"""Test global budget accumulation and graceful degradation."""
from __future__ import annotations

import tempfile
from pathlib import Path

from stage3.config import WatchConfig
from stage3.watch import StudyWatch

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "hackathon-data"


def test_tight_budget_completes_all_cuts_with_degradation():
    """Verify tight budget (1.0 ms) immediately degrades to ESSENTIAL but completes without crashing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        # Budget of 1.0 ms forces ESSENTIAL tier immediately
        cfg = WatchConfig(output_dir=out_dir, budget_total_ms=1.0)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        report = watch.run_period(cuts=[1, 2, 3])

        assert len(report.cut_summaries) == 3
        # Final tier must be ESSENTIAL
        assert report.budget_summary.get("tier") == "ESSENTIAL"

        # Verify degradation decisions and traces recorded
        deg_decs = [d for d in watch.decision_store.all_decisions() if d.decision_type == "DEGRADATION"]
        assert len(deg_decs) > 0

        deg_traces = [t for t in watch.trace_store.all_entries() if "budget_degradation" in t.action]
        assert len(deg_traces) > 0


def test_budget_gate_non_essential_work():
    """Verify allow() disallows optional work when in ESSENTIAL tier."""
    cfg = WatchConfig(budget_total_ms=10.0)
    from stage3.budget import BudgetManager
    bm = BudgetManager(cfg)
    bm.record_usage(15.0)  # Exceeds total
    bm.start_cut(1)

    assert bm.state().tier == "ESSENTIAL"
    # Essential work allowed
    assert bm.allow("SAFETY") is True
    assert bm.allow("TRACE") is True
    assert bm.allow("HUMAN_GATE") is True
    # Non-essential work blocked
    assert bm.allow("EXTENDED_NARRATIVE") is False
    assert bm.allow("SECONDARY_ANALYSIS") is False
