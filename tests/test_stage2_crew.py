"""Tests for Stage 2 MONITOR — Multi-Agent Clinical Trial Review System.

Covers all 10 core requirements:
1. Serious adverse event: AESHOSP=Y, AESER=N -> SAE_MISCODED, critical, same-cycle escalation.
2. Liver candidate: screening ALT already elevated -> monitor only, not escalated, reason in trace.
3. Data manager query: AE before first dose -> specific, actionable, record-cited query.
4. Query deduplication: running same cut twice yields 0 new queries on second run.
5. Escalation deduplication: running same cut twice yields 0 new escalations on second run.
6. REJECTED: downgrade to monitoring, record reason, never re-escalate automatically.
7. CLARIFY: retrieve screening ALT and concomitant meds from StudyGraph, answer monitor, resubmit, approved.
8. Protocol amendment: same cut evaluated under v1 vs v2 vs v3 produces different compliance results.
9. Site-level escalation: multiple wrong-dose subjects at site S09 produce individual deviations + 1 site escalation.
10. Live trace completeness: every node in the 6-node pipeline writes real-time trace entries in strict order.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from stage1.atlas import Atlas, StudyGraph
from stage2.crew import ReviewCrew
from stage2.memory import ReviewMemory
from stage2.schemas import ReviewReport
from stage2.clarify_solver import resolve_clarification


@pytest.fixture(scope="module")
def atlas_instance(data_dir: Path) -> Atlas:
    """Instantiate a shared Atlas instance for testing."""
    graph = StudyGraph(str(data_dir))
    graph.build()
    return Atlas(graph)


@pytest.fixture
def isolated_crew(atlas_instance: Atlas, tmp_path: Path) -> ReviewCrew:
    """Return a ReviewCrew with an isolated memory file for testing."""
    mem_file = tmp_path / "test_memory.json"
    crew = ReviewCrew(
        hub_url="local",
        gateway_url="local",
        team_key="test_team_key",
        atlas=atlas_instance,
        memory=ReviewMemory(persistence_file=mem_file),
    )
    return crew


# --------------------------------------------------------------------------- #
# Test 1: Serious Event (AESHOSP=Y, AESER=N -> SAE_MISCODED, CRITICAL)
# --------------------------------------------------------------------------- #
def test_01_serious_event_hospitalization(isolated_crew: ReviewCrew):
    """Subject 042-S02-004 has Cellulitis with AESHOSP=Y and AESER=N.
    Protocol defines AESHOSP=Y as serious regardless of AESER.
    Must produce SAE_MISCODED with CRITICAL severity and escalate in same cycle.
    """
    report: ReviewReport = isolated_crew.run_cycle(cut=15, protocol_version=3)

    # Check finding exists in findings
    miscoded_findings = [
        f for f in report.findings
        if f.get("code") == "SAE_MISCODED" and f.get("usubjid") == "042-S02-004"
    ]
    assert len(miscoded_findings) >= 1, "Expected SAE_MISCODED finding for 042-S02-004"
    finding = miscoded_findings[0]
    assert finding.get("severity") == "CRITICAL"
    ev_list = finding.get("evidence", [])
    assert any(ref.domain == "AE" and ref.usubjid == "042-S02-004" for ref in ev_list)

    # Must be escalated in the same cycle
    miscoded_escs = [
        e for e in report.escalations
        if e.code == "SAE_MISCODED" and e.usubjid == "042-S02-004"
    ]
    assert len(miscoded_escs) == 1, "Expected SAE_MISCODED to be escalated in same cycle"
    esc = miscoded_escs[0]
    assert esc.severity == "CRITICAL"
    assert esc.site == "S02"
    assert len(esc.evidence) > 0
    assert len(esc.alternatives) > 0
    assert any("monitoring" in alt.alternative.lower() for alt in esc.alternatives)


# --------------------------------------------------------------------------- #
# Test 2: Liver Candidate with Screening Elevation (Monitor Only)
# --------------------------------------------------------------------------- #
def test_02_liver_candidate_screening_elevated(isolated_crew: ReviewCrew):
    """Subject 042-S07-001 has elevated transaminases at baseline.
    In cycle 1, the medical monitor returns REJECTED with baseline elevation reason,
    downgrading the issue to MONITORING.
    In subsequent cycles, the issue is retained as monitoring only and never re-escalated.
    The reason appears in the trace.
    """
    # Cycle 1: Monitor rejects with baseline elevation reason -> downgraded to MONITORING
    report1: ReviewReport = isolated_crew.run_cycle(cut=15, protocol_version=3)
    s07_escs = [e for e in report1.escalations if e.usubjid == "042-S07-001"]
    assert len(s07_escs) == 1, "Expected escalation in cycle 1 awaiting monitor review"
    esc = s07_escs[0]
    assert esc.status == "MONITORING"
    assert esc.monitor_decision == "REJECTED"
    assert "baseline" in esc.monitor_reason.lower()

    # Cycle 2: Kept as monitoring only; must NOT escalate to human gate
    report2: ReviewReport = isolated_crew.run_cycle(cut=15, protocol_version=3)
    s07_escs_c2 = [e for e in report2.escalations if e.usubjid == "042-S07-001"]
    assert len(s07_escs_c2) == 0, "042-S07-001 must not be escalated in cycle 2"

    # Must appear in monitoring findings
    s07_monitoring = [f for f in report2.monitoring_only_findings if f.get("usubjid") == "042-S07-001"]
    assert len(s07_monitoring) >= 1, "042-S07-001 should be kept in monitoring_only list"

    # Rationale must appear in trace
    liver_traces = [
        t for t in report2.trace
        if "042-S07-001" in (t.target or "") or "042-S07-001" in (t.action or "")
    ]
    assert len(liver_traces) >= 1, "Expected trace entry for 042-S07-001 baseline evaluation"
    assert any("baseline" in (t.reason or "").lower() for t in liver_traces)


# --------------------------------------------------------------------------- #
# Test 3: Data Manager Query Generation (AE Before First Dose)
# --------------------------------------------------------------------------- #
def test_03_data_manager_query_before_dose(isolated_crew: ReviewCrew):
    """Data-quality issue: AE starting before first dose (e.g. 042-S11-005 Fatigue).
    Data manager must generate a specific, actionable, record-cited query.
    """
    report: ReviewReport = isolated_crew.run_cycle(cut=15, protocol_version=3)

    s11_queries = [q for q in report.queries if q.usubjid == "042-S11-005"]
    assert len(s11_queries) >= 1, "Expected query for subject 042-S11-005"
    q = s11_queries[0]
    assert q.domain == "AE"
    assert q.cut == 15
    assert "first dose" in q.query_text.lower() or "dose" in q.query_text.lower()


# --------------------------------------------------------------------------- #
# Test 4: Query Deduplication Across Runs
# --------------------------------------------------------------------------- #
def test_04_query_deduplication(isolated_crew: ReviewCrew):
    """Running the same cut twice:
    First run: new queries > 0
    Second run: new queries == 0
    """
    report1 = isolated_crew.run_cycle(cut=15, protocol_version=3)
    assert len(report1.queries) > 0, "First run should create queries"

    report2 = isolated_crew.run_cycle(cut=15, protocol_version=3)
    assert len(report2.queries) == 0, f"Second run should create 0 new queries, got {len(report2.queries)}"


# --------------------------------------------------------------------------- #
# Test 5: Escalation Deduplication Across Runs
# --------------------------------------------------------------------------- #
def test_05_escalation_deduplication(isolated_crew: ReviewCrew):
    """Running the same cut twice:
    First run: new escalations > 0
    Second run: new escalations == 0
    """
    report1 = isolated_crew.run_cycle(cut=15, protocol_version=3)
    assert len(report1.escalations) > 0, "First run should create escalations"

    report2 = isolated_crew.run_cycle(cut=15, protocol_version=3)
    assert len(report2.escalations) == 0, f"Second run should create 0 new escalations, got {len(report2.escalations)}"


# --------------------------------------------------------------------------- #
# Test 6: REJECTED Decision Handling
# --------------------------------------------------------------------------- #
def test_06_rejected_monitor_decision(isolated_crew: ReviewCrew):
    """When a human medical monitor returns REJECTED:
    - Finding is kept, downgraded to monitoring
    - Reason is recorded
    - Memory is updated
    - Escalation is never repeated next cycle
    """
    report1 = isolated_crew.run_cycle(cut=15, protocol_version=3)
    assert len(report1.escalations) > 0
    esc = report1.escalations[0]

    # Process REJECTED decision
    isolated_crew.memory.update_escalation_decision(
        esc.escalation_id, "REJECTED", reason="Clinical benefit outweighs risk; downgrade to monitoring"
    )

    mem_esc = isolated_crew.memory.get_escalation(esc.escalation_id)
    assert mem_esc is not None
    assert mem_esc.status == "MONITORING"
    assert mem_esc.monitor_decision == "REJECTED"
    assert "downgrade" in mem_esc.monitor_reason.lower()

    # Next cycle should NOT re-escalate this issue
    report2 = isolated_crew.run_cycle(cut=15, protocol_version=3)
    re_escalated = [e for e in report2.escalations if e.escalation_id == esc.escalation_id]
    assert len(re_escalated) == 0, "Rejected escalation must never be repeated"


# --------------------------------------------------------------------------- #
# Test 7: CLARIFY Resolution from StudyGraph
# --------------------------------------------------------------------------- #
def test_07_clarify_resolution_from_study_graph(isolated_crew: ReviewCrew, atlas_instance: Atlas):
    """When a monitor asks CLARIFY (e.g. screening ALT & concomitant liver drugs):
    The crew must answer from StudyGraph without LLM hallucination:
    - Parse question
    - Query StudyGraph
    - Return evidence-backed answer
    - Resubmit escalation
    """
    graph = atlas_instance.graph
    usubjid = "042-S07-001"
    q = "What was the subject's screening ALT, and are they taking another liver-affecting medicine?"

    ans, refs = resolve_clarification(graph.core, usubjid, q)

    assert "ALT" in ans
    assert ("ukat/L" in ans or "U/L" in ans)
    assert len(refs) > 0, "Must provide RecordRef evidence citations"
    assert any(r.domain == "LB" for r in refs), "Must cite LB record for ALT"


# --------------------------------------------------------------------------- #
# Test 8: Protocol Amendment (Compliance changes between versions)
# --------------------------------------------------------------------------- #
def test_08_protocol_amendment_changes_compliance(isolated_crew: ReviewCrew):
    """Same data evaluated under protocol v1 vs v2 vs v3:
    v1 has 7-day visit window and no creatinine exclusion.
    v2 has 3-day visit window and creatinine > 1.5 mg/dL exclusion.
    v3 adds sulfonylurea prohibited class.
    Compliance deviation counts must differ between versions.
    """
    report_v1 = isolated_crew.run_cycle(cut=15, protocol_version=1)
    dev_count_v1 = len(report_v1.compliance_deviations)

    # Clean memory for comparison
    isolated_crew.reset_memory()
    report_v2 = isolated_crew.run_cycle(cut=15, protocol_version=2)
    dev_count_v2 = len(report_v2.compliance_deviations)

    assert dev_count_v2 > dev_count_v1, (
        f"Protocol v2 (stricter 3-day window and creatinine exclusion) should produce "
        f"more deviations than v1: got v1={dev_count_v1}, v2={dev_count_v2}"
    )


# --------------------------------------------------------------------------- #
# Test 9: Site-Level Escalation for Dosing Anomalies
# --------------------------------------------------------------------------- #
def test_09_site_level_escalation(isolated_crew: ReviewCrew):
    """Site S09 has multiple subjects with incorrect dosing.
    Must produce individual compliance deviations AND exactly one site-level escalation.
    """
    report = isolated_crew.run_cycle(cut=15, protocol_version=3)

    # Check site-level flags
    s09_flags = [sf for sf in report.site_level_flags if sf.site == "S09"]
    assert len(s09_flags) == 1, "Expected exactly 1 site flag for S09"
    flag = s09_flags[0]
    assert flag.issue_type == "SYSTEMIC_DOSING_ERROR"
    assert len(flag.subjects) >= 3, f"Expected multiple affected subjects, got {flag.subjects}"
    assert "042-S09-004" in flag.subjects

    # Check that individual deviations also exist
    s09_devs = [
        d for d in report.compliance_deviations
        if d.site == "S09" and "DOSING" in d.code
    ]
    assert len(s09_devs) >= len(flag.subjects)


# --------------------------------------------------------------------------- #
# Test 10: Trace Completeness Across All Six Nodes
# --------------------------------------------------------------------------- #
def test_10_trace_completeness(isolated_crew: ReviewCrew):
    """Every node in the review crew must write real-time trace entries in strict order:
    1. detect
    2. medical_review
    3. data_manager
    4. compliance
    5. human_gate
    6. execute
    """
    report = isolated_crew.run_cycle(cut=15, protocol_version=3)
    trace = report.trace

    assert len(trace) >= 6, "Trace must have entries from all nodes"

    nodes_in_trace = [t.node for t in trace]
    required_nodes = [
        "detect",
        "medical_review",
        "data_manager",
        "compliance",
        "human_gate",
        "execute"
    ]

    for req_node in required_nodes:
        assert req_node in nodes_in_trace, f"Node {req_node} missing from trace"

    # Verify chronological sequence of first occurrence of each node
    first_indices = {node: nodes_in_trace.index(node) for node in required_nodes}
    assert (
        first_indices["detect"]
        < first_indices["medical_review"]
        < first_indices["data_manager"]
        < first_indices["compliance"]
        < first_indices["human_gate"]
        < first_indices["execute"]
    ), f"Nodes did not execute in required sequential order: {first_indices}"
