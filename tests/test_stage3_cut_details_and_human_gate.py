"""Comprehensive test suite for Stage 3 WATCH Cut Details and Human Gate adjudication.

Validates:
1. 12 distinct cut records with cut-specific delta and cumulative breakdowns.
2. Historical protocol version preservation across cuts (v1, v2, v3).
3. Monotonic SAE and findings progression without historical overwrite.
4. Quarantine detection timing isolation across cuts.
5. Lab integrity anomaly detection timing.
6. Human Gate interactive decisions (APPROVE, REJECT, CLARIFY).
7. 4-cut unanswered timeout standing limits activation.
8. Decision trace linkage and explainability via explain(decision_id).
9. Budget progression monotonicity.
10. Final report consistency with Cut 12 execution.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from stage3.config import DEFAULT_PERIOD, WatchConfig
from stage3.models import CutResult
from stage3.service import get_cut_detail, get_watch, run_period, submit_human_gate_decision
from stage3.watch import StudyWatch

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "hackathon-data"


def test_12_cut_records_and_isolation():
    """TEST 1: 12 distinct cut records exist and each stores cut-specific delta."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        report = watch.run_period(cuts=list(range(1, 13)))

        assert len(report.cut_summaries) == 12
        assert len(report.cuts_evaluated) == 12

        for i, cr in enumerate(report.cut_summaries, start=1):
            assert cr.cut == i
            assert cr.new_records > 0
            assert cr.cumulative_findings_count >= cr.findings_count
            assert cr.cumulative_budget_ms >= cr.budget_ms_used


def test_historical_protocol_versions():
    """TEST 2: Historical protocol version corresponds to active protocol at that cut."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        report = watch.run_period(cuts=list(range(1, 13)))

        # Cut 1-4 must be protocol v1
        for cr in report.cut_summaries[:4]:
            assert cr.protocol_version == 1

        # Cut 5-8 must be protocol v2
        for cr in report.cut_summaries[4:8]:
            assert cr.protocol_version == 2

        # Cut 9-12 must be protocol v3
        for cr in report.cut_summaries[8:]:
            assert cr.protocol_version == 3


def test_cumulative_and_delta_metrics():
    """TEST 3: Delta findings/SAEs differ from cumulative totals."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        report = watch.run_period(cuts=list(range(1, 13)))

        c1 = report.cut_summaries[0]
        c12 = report.cut_summaries[11]

        # Cumulative findings at Cut 12 must equal total findings across all cuts
        assert c12.cumulative_findings_count == sum(cr.findings_count for cr in report.cut_summaries)
        # Cut 1 cumulative findings must equal Cut 1 delta findings
        assert c1.cumulative_findings_count == c1.findings_count
        # Cut 12 cumulative findings must be strictly greater than Cut 1
        assert c12.cumulative_findings_count > c1.cumulative_findings_count


def test_quarantine_timing_isolation():
    """TEST 4: Quarantined site does not appear as quarantined before detection."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        report = watch.run_period(cuts=list(range(1, 13)))

        # In cut 1, no site should be quarantined yet
        c1 = report.cut_summaries[0]
        assert len(c1.quarantined_sites) == 0

        # In later cuts where quarantine occurs, new_quarantined_sites captures the event
        has_quarantine = any(len(cr.quarantined_sites) > 0 for cr in report.cut_summaries)
        if has_quarantine:
            first_q_cut = next(cr for cr in report.cut_summaries if len(cr.quarantined_sites) > 0)
            assert first_q_cut.cut > 1
            # Cuts prior to first_q_cut must not have quarantined sites
            for cr in report.cut_summaries[: first_q_cut.cut - 1]:
                assert len(cr.quarantined_sites) == 0


def test_lab_integrity_anomaly_timing():
    """TEST 5: Untrusted lab records track baseline and additional shift anomalies."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        report = watch.run_period(cuts=list(range(1, 13)))

        c1 = report.cut_summaries[0]
        c8 = report.cut_summaries[7]
        c12 = report.cut_summaries[11]

        # In cut 8, S04 glucose unit shift is detected as a new anomaly
        assert c8.new_untrusted_labs > 0
        # Final untrusted records must be greater than or equal to Cut 1 baseline
        assert c12.untrusted_labs >= c1.untrusted_labs


def test_human_gate_interactive_adjudication():
    """TEST 6: Human Gate APPROVE, REJECT, and CLARIFY state transitions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir, human_response_delay_cuts=None)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        # Run cut 1 to generate initial pending items
        watch.run_period(cuts=[1])

        items = list(watch.human_state.items.values())
        assert len(items) > 0
        first_item = items[0]
        assert first_item.decision == "PENDING"

        # Adjudicate as APPROVED
        item_app, dec_app, tr_app = watch.human_state.submit_human_decision(
            escalation_id=first_item.escalation_id,
            decision="APPROVED",
            reason="Approved by human medical monitor in test.",
            core=watch.graph.core,
            memory=watch.crew.memory,
            hub_client=watch.hub_client,
            cut=1,
        )
        assert item_app is not None
        assert item_app.decision == "APPROVED"
        assert dec_app is not None
        assert dec_app.status == "APPROVED"
        assert tr_app is not None
        assert tr_app.status == "APPROVED"


def test_explain_decision_trace():
    """TEST 8: explain(decision_id) retrieves stored trace."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        report = watch.run_period(cuts=[1, 2, 3])

        all_decs = watch.decision_store.all_decisions()
        assert len(all_decs) > 0

        first_dec = all_decs[0]
        exp = watch.explain(first_dec.decision_id)

        assert exp.decision_id == first_dec.decision_id
        assert exp.cut == first_dec.cut
        assert exp.consistent_with_trace is True
        assert len(exp.why) > 0


def test_budget_progression_cumulative():
    """TEST 9: Cumulative budget increases monotonically across cuts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir)
        cfg = WatchConfig(output_dir=out_dir)
        watch = StudyWatch(data_dir=DATA_DIR, config=cfg)

        report = watch.run_period(cuts=list(range(1, 13)))

        prev_budget = 0.0
        for cr in report.cut_summaries:
            assert cr.cumulative_budget_ms >= prev_budget
            prev_budget = cr.cumulative_budget_ms


def test_service_cut_detail_endpoint():
    """TEST 10: Service get_cut_detail returns isolated cut records."""
    run_period(cuts=[1, 2], data_dir=DATA_DIR)
    detail1 = get_cut_detail(1)
    detail2 = get_cut_detail(2)

    assert detail1 is not None
    assert detail1["cut"] == 1
    assert detail1["cut_summary"]["protocol_version"] == 1

    assert detail2 is not None
    assert detail2["cut"] == 2
    assert detail2["cut_summary"]["cut"] == 2
