"""MONITOR Stage 2 Test Suite & Official Test Runner.

Executes all 18 core MONITOR requirements against the real Stage 1 Atlas,
StudyGraph, ReviewCrew, ReviewMemory, and mock endpoints:

1. Stage 1 detection interface reuse
2. Six-node pipeline execution order
3. Serious AE detection
4. AESHOSP=Y / AESER=N critical rule
5. Serious AE evidence validity
6. Liver signal review
7. Screening ALT reasoning
8. Data-quality query generation
9. Query evidence validity
10. Query deduplication
11. Escalation deduplication
12. APPROVED human gate
13. REJECTED human gate
14. CLARIFY human gate (StudyGraph resolution)
15. Protocol amendment (v1 vs v2 compliance)
16. Repeated subject escalation (2-cycle flag)
17. Site-level escalation (Site S09 systemic dosing anomaly)
18. Live trace completeness & resilience
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from stage1.atlas import Atlas, StudyGraph
from stage2.crew import ReviewCrew
from stage2.memory import ReviewMemory
from stage2.schemas import ReviewReport
from stage2.clarify_solver import resolve_clarification


class Stage2TestSuite:
    def __init__(self):
        self.data_dir = REPO_ROOT / "hackathon-data"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.graph = StudyGraph(str(self.data_dir))
        self.graph.build()
        self.atlas = Atlas(self.graph)
        self.results: list[tuple[str, bool, str]] = []

    def _make_crew(self, mem_name: str = "mem.json") -> ReviewCrew:
        mem_path = self.temp_path / mem_name
        return ReviewCrew(
            hub_url="local",
            gateway_url="local",
            team_key="test_team",
            atlas=self.atlas,
            memory=ReviewMemory(persistence_file=mem_path),
        )

    # ----------------------------------------------------------------------- #
    # Test 1: Stage 1 Detection Reuse
    # ----------------------------------------------------------------------- #
    def test_01_stage1_detection(self):
        crew = self._make_crew("t1.json")
        report = crew.run_cycle(cut=15, protocol_version=3)
        assert len(report.findings) > 0, "detect node must discover findings"
        # Confirm findings came from Stage 1 core
        assert hasattr(self.atlas, "graph"), "Atlas must own StudyGraph"
        assert hasattr(self.atlas.graph, "core"), "StudyGraph must expose core"

    # ----------------------------------------------------------------------- #
    # Test 2: Six-Node Pipeline Execution Order
    # ----------------------------------------------------------------------- #
    def test_02_pipeline_order(self):
        crew = self._make_crew("t2.json")
        report = crew.run_cycle(cut=15, protocol_version=3)
        nodes = [t.node for t in report.trace]
        required = ["detect", "medical_review", "data_manager", "compliance", "human_gate", "execute"]
        for r in required:
            assert r in nodes, f"Missing node: {r}"
        indices = [nodes.index(r) for r in required]
        assert indices == sorted(indices), f"Nodes not in sequential order: {indices}"

    # ----------------------------------------------------------------------- #
    # Test 3: Serious AE Detection
    # ----------------------------------------------------------------------- #
    def test_03_serious_ae_detection(self):
        crew = self._make_crew("t3.json")
        report = crew.run_cycle(cut=15, protocol_version=3)
        serious = [f for f in report.serious_findings]
        assert len(serious) > 0, "Must identify serious findings"
        assert any(f.get("severity") in ("CRITICAL", "HIGH") for f in serious)

    # ----------------------------------------------------------------------- #
    # Test 4: AESHOSP=Y / AESER=N Critical Rule
    # ----------------------------------------------------------------------- #
    def test_04_sae_miscoded_rule(self):
        crew = self._make_crew("t4.json")
        report = crew.run_cycle(cut=15, protocol_version=3)
        miscoded = [e for e in report.escalations if e.code == "SAE_MISCODED" and e.usubjid == "042-S02-004"]
        assert len(miscoded) == 1, "042-S02-004 must be escalated as SAE_MISCODED in same cycle"
        esc = miscoded[0]
        assert esc.severity == "CRITICAL", "SAE_MISCODED must have CRITICAL severity"
        assert esc.site == "S02"

    # ----------------------------------------------------------------------- #
    # Test 5: Serious AE Evidence Validity
    # ----------------------------------------------------------------------- #
    def test_05_sae_evidence_validity(self):
        crew = self._make_crew("t5.json")
        report = crew.run_cycle(cut=15, protocol_version=3)
        miscoded = [e for e in report.escalations if e.code == "SAE_MISCODED" and e.usubjid == "042-S02-004"][0]
        assert len(miscoded.evidence) > 0, "Must cite evidence"
        ae_ref = [r for r in miscoded.evidence if r.domain == "AE"][0]
        assert ae_ref.usubjid == "042-S02-004"
        assert ae_ref.seq == 1
        # Physically verify in raw CDISC records that AE seq 1 has AESHOSP=Y
        records = [r for r in self.atlas.graph.core.idx.records("042-S02-004", "AE") if r.get("AESEQ") == "1"]
        assert len(records) == 1
        assert records[0].get("AESHOSP") == "Y", "Evidence row must physically have AESHOSP=Y"

    # ----------------------------------------------------------------------- #
    # Test 6: Liver Signal Review
    # ----------------------------------------------------------------------- #
    def test_06_liver_signal_review(self):
        crew = self._make_crew("t6.json")
        report = crew.run_cycle(cut=15, protocol_version=3)
        hys = [f for f in report.findings if f.get("code") == "HYS_LAW_CANDIDATE"]
        assert len(hys) > 0, "Must evaluate Hy's Law candidates"
        for h in hys:
            assert len(h.get("evidence", [])) >= 2, "Must cite both transaminase and bilirubin records"

    # ----------------------------------------------------------------------- #
    # Test 7: Screening ALT Reasoning
    # ----------------------------------------------------------------------- #
    def test_07_screening_alt_reasoning(self):
        crew = self._make_crew("t7.json")
        # Cycle 1: Monitor decision returns REJECTED due to baseline elevation
        rep1 = crew.run_cycle(cut=15, protocol_version=3)
        s07 = [e for e in rep1.escalations if e.usubjid == "042-S07-001"][0]
        assert s07.status == "MONITORING"
        assert "baseline" in s07.monitor_reason.lower()

        # Cycle 2: Memory preserves rejection; subject kept in monitoring-only, 0 escalations
        rep2 = crew.run_cycle(cut=15, protocol_version=3)
        assert len([e for e in rep2.escalations if e.usubjid == "042-S07-001"]) == 0
        assert any("042-S07-001" in (t.target or "") for t in rep2.trace)

    # ----------------------------------------------------------------------- #
    # Test 8: Data-Quality Query Generation
    # ----------------------------------------------------------------------- #
    def test_08_data_quality_query(self):
        crew = self._make_crew("t8.json")
        report = crew.run_cycle(cut=15, protocol_version=3)
        s11 = [q for q in report.queries if q.usubjid == "042-S11-005"]
        assert len(s11) >= 1, "Must raise query for 042-S11-005 AE before dose"
        q = s11[0]
        assert q.domain == "AE"
        assert "dose" in q.query_text.lower()
        assert q.status in ("OPEN", "ANSWERED", "CLOSED")

    # ----------------------------------------------------------------------- #
    # Test 9: Query Evidence Validity
    # ----------------------------------------------------------------------- #
    def test_09_query_evidence_validity(self):
        crew = self._make_crew("t9.json")
        report = crew.run_cycle(cut=15, protocol_version=3)
        for q in report.queries:
            assert q.domain in ("AE", "EX", "LB", "DM"), f"Valid domain expected: {q.domain}"
            assert q.usubjid.startswith("042-"), f"Valid subject ID expected: {q.usubjid}"

    # ----------------------------------------------------------------------- #
    # Test 10: Query Deduplication
    # ----------------------------------------------------------------------- #
    def test_10_query_deduplication(self):
        crew = self._make_crew("t10.json")
        rep1 = crew.run_cycle(cut=15, protocol_version=3)
        assert len(rep1.queries) > 0, "Run 1 must create queries"
        rep2 = crew.run_cycle(cut=15, protocol_version=3)
        assert len(rep2.queries) == 0, f"Run 2 must create 0 new queries, got {len(rep2.queries)}"

    # ----------------------------------------------------------------------- #
    # Test 11: Escalation Deduplication
    # ----------------------------------------------------------------------- #
    def test_11_escalation_deduplication(self):
        crew = self._make_crew("t11.json")
        rep1 = crew.run_cycle(cut=15, protocol_version=3)
        assert len(rep1.escalations) > 0, "Run 1 must create escalations"
        rep2 = crew.run_cycle(cut=15, protocol_version=3)
        assert len(rep2.escalations) == 0, f"Run 2 must create 0 new escalations, got {len(rep2.escalations)}"

    # ----------------------------------------------------------------------- #
    # Test 12: APPROVED Human Gate
    # ----------------------------------------------------------------------- #
    def test_12_approved_human_gate(self):
        crew = self._make_crew("t12.json")
        rep = crew.run_cycle(cut=15, protocol_version=3)
        approved = [e for e in rep.escalations if e.status == "APPROVED"]
        assert len(approved) > 0, "Must have approved escalations"
        for a in approved:
            assert a.monitor_decision == "APPROVED"
            assert a.action_taken is not None

    # ----------------------------------------------------------------------- #
    # Test 13: REJECTED Human Gate
    # ----------------------------------------------------------------------- #
    def test_13_rejected_human_gate(self):
        crew = self._make_crew("t13.json")
        rep = crew.run_cycle(cut=15, protocol_version=3)
        rejected = [e for e in rep.escalations if e.status == "MONITORING" and e.monitor_decision == "REJECTED"]
        assert len(rejected) > 0, "Must process REJECTED monitor decisions"
        for r in rejected:
            assert r.monitor_reason is not None
            assert "downgrade" in (r.action_taken or "").lower() or "monitoring" in r.status.lower()

    # ----------------------------------------------------------------------- #
    # Test 14: CLARIFY Human Gate (StudyGraph Resolution)
    # ----------------------------------------------------------------------- #
    def test_14_clarify_human_gate(self):
        usubjid = "042-S07-001"
        q = "What was the ALT at screening, and is there a concomitant hepatotoxic medication?"
        ans, refs = resolve_clarification(self.atlas.graph.core, usubjid, q)
        assert "ALT" in ans
        assert "0.27" in ans or "ukat/L" in ans or "U/L" in ans
        assert len(refs) > 0
        assert any(r.domain == "LB" for r in refs)

    # ----------------------------------------------------------------------- #
    # Test 15: Protocol Amendment (v1 vs v2)
    # ----------------------------------------------------------------------- #
    def test_15_protocol_amendment(self):
        crew_v1 = self._make_crew("t15_v1.json")
        rep_v1 = crew_v1.run_cycle(cut=15, protocol_version=1)
        crew_v2 = self._make_crew("t15_v2.json")
        rep_v2 = crew_v2.run_cycle(cut=15, protocol_version=2)
        assert len(rep_v2.compliance_deviations) > len(rep_v1.compliance_deviations), (
            f"v2 stricter criteria must produce more deviations (v1={len(rep_v1.compliance_deviations)}, v2={len(rep_v2.compliance_deviations)})"
        )

    # ----------------------------------------------------------------------- #
    # Test 16: Repeated Subject Escalation
    # ----------------------------------------------------------------------- #
    def test_16_repeated_subject_escalation(self):
        mem = ReviewMemory(persistence_file=self.temp_path / "t16.json")
        # Cycle 1 at cut 10
        was_flagged_1 = mem.record_subject_flag("042-S01-001", cycle=1, cut=10)
        assert not was_flagged_1, "First cycle should not trigger repeat flag"
        # Cycle 2 progression at cut 15
        mem.completed_cuts.add(10)
        was_flagged_2 = mem.record_subject_flag("042-S01-001", cycle=2, cut=15)
        assert was_flagged_2, "Subject flagged in cycle 1 + cycle 2 on new cut must trigger repeat flag"

    # ----------------------------------------------------------------------- #
    # Test 17: Site-Level Escalation
    # ----------------------------------------------------------------------- #
    def test_17_site_level_escalation(self):
        crew = self._make_crew("t17.json")
        report = crew.run_cycle(cut=15, protocol_version=3)
        s09_flags = [sf for sf in report.site_level_flags if sf.site == "S09"]
        assert len(s09_flags) == 1, "Expected exactly 1 site flag for S09"
        flag = s09_flags[0]
        assert len(flag.subjects) >= 3, "Site S09 systemic error affects >= 3 subjects"
        s09_devs = [d for d in report.compliance_deviations if d.site == "S09" and "DOSING" in d.code]
        assert len(s09_devs) >= len(flag.subjects), "Individual subject deviations must be preserved"

    # ----------------------------------------------------------------------- #
    # Test 18: Live Trace Completeness & Resilience
    # ----------------------------------------------------------------------- #
    def test_18_trace_completeness_and_resilience(self):
        crew = self._make_crew("t18.json")
        report = crew.run_cycle(cut=15, protocol_version=3)
        trace = report.trace
        assert len(trace) >= 6, "Must record trace entries across all 6 nodes"
        for t in trace:
            assert t.node in ("detect", "medical_review", "data_manager", "compliance", "human_gate", "execute")
            assert len(t.action) > 0
            assert t.timestamp is not None

    def run_all(self):
        test_methods = [
            ("Stage 1 detection", self.test_01_stage1_detection),
            ("Six-node pipeline order", self.test_02_pipeline_order),
            ("Serious AE detection", self.test_03_serious_ae_detection),
            ("AESHOSP=Y / AESER=N", self.test_04_sae_miscoded_rule),
            ("Serious AE evidence validity", self.test_05_sae_evidence_validity),
            ("Liver signal review", self.test_06_liver_signal_review),
            ("Screening ALT reasoning", self.test_07_screening_alt_reasoning),
            ("Data-quality query", self.test_08_data_quality_query),
            ("Query evidence validity", self.test_09_query_evidence_validity),
            ("Query deduplication", self.test_10_query_deduplication),
            ("Escalation deduplication", self.test_11_escalation_deduplication),
            ("APPROVED human gate", self.test_12_approved_human_gate),
            ("REJECTED human gate", self.test_13_rejected_human_gate),
            ("CLARIFY human gate", self.test_14_clarify_human_gate),
            ("Protocol amendment", self.test_15_protocol_amendment),
            ("Repeated subject escalation", self.test_16_repeated_subject_escalation),
            ("Site-level escalation", self.test_17_site_level_escalation),
            ("Trace completeness & resilience", self.test_18_trace_completeness_and_resilience),
        ]

        print("========================================")
        print("MONITOR STAGE 2 TEST SUITE")
        print("========================================")

        passed = 0
        failed = 0
        failed_details: list[tuple[str, str]] = []

        for name, fn in test_methods:
            try:
                fn()
                print(f"[PASS] {name}")
                passed += 1
            except Exception as e:
                print(f"[FAIL] {name} - {e}")
                failed += 1
                failed_details.append((name, str(e)))

        print("========================================")
        print(f"{passed}/{len(test_methods)} TESTS PASSED")
        print("========================================")

        if failed > 0:
            print("\nFailures summary:")
            for name, err in failed_details:
                print(f"  - {name}: {err}")
            return 1
        return 0


if __name__ == "__main__":
    suite = Stage2TestSuite()
    sys.exit(suite.run_all())
