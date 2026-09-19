"""Test adversarial detectors: lab unit shift, regular site anomaly, and document directives."""
from __future__ import annotations

import tempfile
from pathlib import Path

from stage3.config import WatchConfig
from stage3.watch import StudyWatch

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "hackathon-data"


def test_lab_unit_shift_detected_on_real_data():
    """Verify glucose unit shift detected at cut 8 for Site S04, records marked untrusted, queries raised."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        # Run cuts 7 and 8
        cuts = [7, 8]
        report = watch.run_period(cuts=cuts)

        # Untrusted records count must be positive
        assert report.untrusted_records_count > 0

        # Verify LAB_UNIT_SHIFT decision created
        lab_decs = watch.decision_store.filter(cut=8, code="LAB_UNIT_SHIFT")
        assert len(lab_decs) > 0
        assert lab_decs[0].status == "DATA_INTEGRITY"
        assert lab_decs[0].target == "S04"

        # Verify gateway client dispatched a query for this shift
        lab_queries = [
            q for q in watch.crew.memory.queries.values()
            if "LAB_SHIFT" in q.get("fingerprint", "")
        ]
        assert len(lab_queries) > 0


def test_site_anomaly_and_quarantine_preserves_data():
    """Verify Site S11 detected as implausibly regular at cut 6, quarantined without data deletion."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        cuts = [5, 6]
        report = watch.run_period(cuts=cuts)

        # Site S11 must be quarantined
        assert "S11" in watch.state.quarantined_sites
        assert "S11" in report.quarantined_sites

        # Verify data is NOT deleted: subjects and records still exist in index
        s11_subjs = watch.graph.core.idx.subjects_at("S11")
        assert len(s11_subjs) > 0

        # Verify quarantine decision exists
        quar_decs = watch.decision_store.filter(cut=6, code="IMPLAUSIBLE_SITE_PATTERN")
        assert len(quar_decs) > 0
        assert quar_decs[0].status == "QUARANTINED"


def test_document_integrity_directive_not_executed():
    """Verify document instruction-like text is detected and explicitly traced as NOT executed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        watch.run_period(cuts=[1])

        doc_traces = [
            t for t in watch.trace_store.all_entries()
            if "instruction_like_text_not_executed" in t.action
        ]
        assert len(doc_traces) > 0
        assert any(e.domain == "DOC" for t in doc_traces for e in t.evidence)
