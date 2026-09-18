"""Prohibited medications: list comes from the protocol version in force at the cut."""
from __future__ import annotations

import pytest

from backend.protocol.protocol_loader import match_prohibited
from backend.rules import run_rule
from backend.rules.prohibited_meds import find_prohibited_meds
from tests.rules_fixtures import basic_rules, check_all_evidence, real_core, synthetic_core


@pytest.fixture(scope="module")
def core(data_dir):
    return real_core(data_dir)


def test_matches_independent_scan(core):
    fs = run_rule(core, "PROHIBITED_MEDICATION")
    expected = {r.key.as_tuple() for r in core.idx.by_domain["CM"]
                if match_prohibited(r.get("CMCLAS"), r.get("CMTRT"), core.rules.prohibited_classes)}
    assert {f.details["record"] for f in fs} == expected
    assert fs
    check_all_evidence(core, fs)
    for f in fs:
        assert f.details["matched_class"] in core.rules.prohibited_classes
        assert any(e.is_doc and e.ref.document == core.rules.document for e in f.evidence)


def test_protocol_version_changes_the_list(data_dir):
    cuts = real_core(data_dir).cut_table
    early = real_core(data_dir, cut=cuts[0].cut)
    late = real_core(data_dir)
    if set(early.rules.prohibited_classes) == set(late.rules.prohibited_classes):
        pytest.skip("prohibited list does not change between versions in this dataset")
    assert set(early.rules.prohibited_classes) < set(late.rules.prohibited_classes)
    early_recs = {f.details["record"] for f in run_rule(early, "PROHIBITED_MEDICATION")}
    late_recs = {f.details["record"] for f in run_rule(late, "PROHIBITED_MEDICATION")}
    # every early finding still holds under the wider list; the wider list adds findings on the same records
    late_on_early_records = {k for k in late_recs if k in early.idx.by_ref}
    assert early_recs < late_on_early_records


def _cm(u, seq, trt, clas):
    return {"USUBJID": u, "CMSEQ": str(seq), "CMTRT": trt, "CMCLAS": clas, "CMINDC": "", "CMSTDTC": "2026-02-01", "CMDOSE": "5"}


def test_synthetic_class_matching():
    rows = {"CM": [_cm("X-S01-001", 1, "Prednisolone", "SYSTEMIC_GLUCOCORTICOID"), _cm("X-S01-001", 2, "Cream", "GLUCOCORTICOID"),
                   _cm("X-S01-001", 3, "Glibenclamide", "SULFONYLUREA"), _cm("X-S01-001", 4, "Metformin", "")]}
    core = synthetic_core(rows, basic_rules(prohibited_classes=["SYSTEMIC_GLUCOCORTICOID"]))
    assert [f.details["record"][2] for f in find_prohibited_meds(core)] == [1]
    core2 = synthetic_core(rows, basic_rules(prohibited_classes=["SULFONYLUREA", "SYSTEMIC_GLUCOCORTICOID"]))
    assert [f.details["record"][2] for f in find_prohibited_meds(core2)] == [1, 3]
    assert find_prohibited_meds(synthetic_core(rows, basic_rules(prohibited_classes=[]))) == []
