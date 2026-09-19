"""Test human medical monitor state machine, delay policy, and standing limits."""
from __future__ import annotations

import tempfile
from pathlib import Path

from stage3.adapters import WatchHubClient
from stage3.config import WatchConfig
from stage3.watch import StudyWatch

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "hackathon-data"


def test_human_monitor_delay_policy():
    """Verify 1-cut response delay: PENDING at cut raised, resolved at cut+1."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        # default human_response_delay_cuts is 1
        cfg = WatchConfig(output_dir=out_dir, human_response_delay_cuts=1)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        # Run cut 1
        watch.run_period(cuts=[1])
        # In cut 1, escalations are submitted; under delay=1, newly raised items are PENDING
        items_c1 = list(watch.human_state.items.values())
        assert len(items_c1) > 0
        assert all(it.detected_at_cut == 1 for it in items_c1)

        # Run cut 2
        watch.run_period(cuts=[2])
        # In cut 2, carried-over items have reached cut >= raised_cut + 1, so decisions land
        answered_c2 = [it for it in watch.human_state.items.values() if it.decision in ("APPROVED", "REJECTED")]
        assert len(answered_c2) > 0


def test_standing_limits_after_4_unanswered_cuts():
    """Verify transition to STANDING_LIMITS after 4 unanswered cuts when monitor never replies."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        # Never reply: human_response_delay_cuts = None, standing_limit_cuts = 4
        cfg = WatchConfig(output_dir=out_dir, human_response_delay_cuts=None, standing_limit_cuts=4)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        # Run cuts 1 through 5
        watch.run_period(cuts=[1, 2, 3, 4, 5])

        limits_items = [
            it for it in watch.human_state.items.values()
            if it.decision == "STANDING_LIMITS"
        ]
        assert len(limits_items) > 0

        # Verify trace entry for STANDING_LIMITS exists
        limit_traces = [
            t for t in watch.trace_store.all_entries()
            if t.status == "STANDING_LIMITS"
        ]
        assert len(limit_traces) > 0
        assert any(t.decision_id and "LIMITS" in t.decision_id for t in limit_traces)
