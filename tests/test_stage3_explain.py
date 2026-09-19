"""Test trace-backed explainability interface."""
from __future__ import annotations

import tempfile
from pathlib import Path

from stage3.config import WatchConfig
from stage3.watch import StudyWatch

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "hackathon-data"


def test_every_decision_explainable_and_trace_consistent():
    """Verify explain() matches trace evidence and reports consistency."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        watch.run_period(cuts=[1, 2])

        all_decs = watch.decision_store.all_decisions()
        assert len(all_decs) > 0

        for dec in all_decs[:10]:
            exp = watch.explain(dec.decision_id)
            assert exp.decision_id == dec.decision_id
            assert exp.status == dec.status
            assert exp.why == dec.reason

            # Verify consistent_with_trace is True for trace-linked decisions
            entries = watch.trace_store.for_decision(dec.decision_id)
            if entries:
                assert exp.consistent_with_trace is True

            if dec.evidence:
                assert exp.evidence_available is True
                assert len(exp.evidence_lines) == len(dec.evidence)


def test_unknown_decision_id_honest_handling():
    """Verify unknown decision id reports honest evidence_available=False."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        exp = watch.explain("D-UNKNOWN-999")
        assert exp.evidence_available is False
        assert exp.consistent_with_trace is False
        assert "no stored decision" in exp.why
