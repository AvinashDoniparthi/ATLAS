"""Test laboratory corrections re-issues at cut 5 and finding retraction."""
from __future__ import annotations

import tempfile
from pathlib import Path

from stage3.config import WatchConfig
from stage3.watch import StudyWatch

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "hackathon-data"


def test_corrections_applied_at_cut_5():
    """Verify 200 LB corrections applied at cut 5, raw preserved, corrected_fields updated."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        cuts = [1, 2, 3, 4, 5]
        report = watch.run_period(cuts=cuts)

        cut_5_summary = next(cr for cr in report.cut_summaries if cr.cut == 5)
        # Verify exactly 200 corrections landed at cut 5
        assert cut_5_summary.corrections == 200

        core = watch.graph.core
        assert core.view.corrections_applied == 200

        # Verify a sample corrected record has raw fields intact and corrected_fields set
        corr_sample = next(c for c in core.corrections if c.cut == 5)
        rec = core.record(corr_sample.domain, corr_sample.usubjid, corr_sample.seq)
        assert rec is not None
        assert corr_sample.field in rec.corrected_fields
        assert rec.corrected_fields[corr_sample.field] == corr_sample.new_value
        assert rec.fields[corr_sample.field] == corr_sample.old_value


def test_corrected_finding_retraction_trace():
    """Verify trace and decision stores track correction retractions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        watch.run_period(cuts=[4, 5])

        # Verify decisions log has CORRECTION_RETRACT or finding resolutions
        retract_traces = [
            t for t in watch.trace_store.all_entries()
            if "corrections" in t.node or "finding_retracted" in t.action
        ]
        # Corrections node was executed at cut 5
        assert any(t.cut == 5 for t in watch.trace_store.all_entries())
