"""Test protocol amendment version transitions and affected rule recomputation."""
from __future__ import annotations

import tempfile
from pathlib import Path

from stage3.config import WatchConfig
from stage3.watch import StudyWatch

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "hackathon-data"


def test_amendment_transitions_recorded():
    """Verify v1->v2 (cut 5) and v2->v3 (cut 9) transitions are detected and traced with DocRefs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        cuts = [4, 5, 6, 7, 8, 9]
        report = watch.run_period(cuts=cuts)

        # Transitions should capture cut 5 (v1->v2) and cut 9 (v2->v3)
        assert len(report.protocol_transitions) >= 2
        t_cut5 = next((t for t in report.protocol_transitions if t["cut"] == 5), None)
        assert t_cut5 is not None
        assert t_cut5["from_version"] == 1
        assert t_cut5["to_version"] == 2

        t_cut9 = next((t for t in report.protocol_transitions if t["cut"] == 9), None)
        assert t_cut9 is not None
        assert t_cut9["from_version"] == 2
        assert t_cut9["to_version"] == 3

        # Verify amendment trace contains DocRefs
        amend_traces = [
            t for t in watch.trace_store.all_entries()
            if t.node == "amendment"
        ]
        assert len(amend_traces) >= 2
        for at in amend_traces:
            assert len(at.evidence) > 0
            assert any(e.domain == "DOC" for e in at.evidence)
