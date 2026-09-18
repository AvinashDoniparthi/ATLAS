"""Eligibility and duplicate-subject rules."""
from __future__ import annotations

import pytest

from backend.normalization.values import parse_number
from backend.rules import run_rule
from backend.rules.eligibility import find_eligibility_violations
from backend.rules.duplicates import find_duplicate_subjects
from tests.rules_fixtures import basic_rules, check_all_evidence, real_core, synthetic_core


@pytest.fixture(scope="module")
def core(data_dir):
    return real_core(data_dir)


def test_age_violations(core):
    fs = [f for f in run_rule(core, "ELIGIBILITY_VIOLATION") if f.details["subcode"] == "AGE_OUT_OF_RANGE"]
    r = core.rules
    expected = {u for u in core.idx.subjects
                if (a := parse_number(core.dm(u).get("AGE"))) is not None and not (r.age_min <= a <= r.age_max)}
    assert {f.usubjid for f in fs} == expected
    assert expected, "practice data is expected to contain under-age subjects"
    check_all_evidence(core, fs)


def test_creatinine_only_when_rule_present(data_dir, core):
    late = core
    fs = [f for f in run_rule(late, "ELIGIBILITY_VIOLATION") if f.details["subcode"] == "CREATININE_SCREENING"]
    if late.rules.creatinine_max is None:
        assert fs == []
    else:
        assert fs
        for f in fs:
            assert f.details["value"] > late.rules.creatinine_max
            assert all(e.ref.domain == "LB" for e in f.evidence if e.role == "support")
        check_all_evidence(late, fs)
    early = real_core(data_dir, cut=late.cut_table[0].cut)
    early_fs = [f for f in run_rule(early, "ELIGIBILITY_VIOLATION") if f.details["subcode"] == "CREATININE_SCREENING"]
    if early.rules.creatinine_max is None:
        assert early_fs == []


def test_duplicate_subjects(core):
    fs = run_rule(core, "DUPLICATE_SUBJECT")
    clusters = [pc for pc in core.persons.values() if len(pc.usubjids) > 1]
    assert len(fs) == len(clusters) == 1
    assert len(fs[0].details["usubjids"]) == 2
    assert {e.ref.domain for e in fs[0].evidence} == {"DM"}
    check_all_evidence(core, fs)


def _dm(u, age, hba1c="8.0"):
    return {"USUBJID": u, "SITEID": u.split("-")[1], "ARM": "DRUG", "RFSTDTC": "2026-01-15", "AGE": age, "SCR_HBA1C": hba1c, "SEX": "F", "DMINIT": "ABC", "BRTHDTC": "1980-01-01"}


def _lb(u, seq, test, val, unit, visit="SCREENING"):
    return {"USUBJID": u, "LBSEQ": str(seq), "VISIT": visit, "LBDTC": "2026-01-01", "LBTESTCD": test, "LBORRES": val, "LBORRESU": unit}


RANGES = [("ALT", "U/L", 7, 56, "CENTRAL"), ("AST", "U/L", 10, 40, "CENTRAL")]


def test_synthetic_eligibility_criteria():
    rows = {"DM": [_dm("X-S01-001", "17"), _dm("X-S01-002", "76"), _dm("X-S01-003", "40", "11.0"), _dm("X-S01-004", "40"), _dm("X-S01-005", "n/a")],
            "LB": [_lb("X-S01-004", 1, "ALT", "150", "U/L"), _lb("X-S01-004", 2, "CREAT", "2.0", "mg/dL"), _lb("X-S01-004", 3, "ALT", "300", "U/L", "WEEK4"),
                   _lb("X-S01-003", 1, "CREAT", "<5", "mg/dL")],
            "MH": [{"USUBJID": "X-S01-002", "MHSEQ": "1", "MHTERM": "Pregnancy"}]}
    rules = basic_rules(hba1c_min=7.0, hba1c_max=10.5, hepatic_uln_multiple=2.0, creatinine_max=1.5, creatinine_unit="mg/dL", pregnancy_excluded=True)
    core = synthetic_core(rows, rules, RANGES)
    fs = find_eligibility_violations(core)
    got = sorted((f.usubjid, f.details["subcode"]) for f in fs)
    assert got == [("X-S01-001", "AGE_OUT_OF_RANGE"), ("X-S01-002", "AGE_OUT_OF_RANGE"), ("X-S01-002", "PREGNANCY"),
                   ("X-S01-003", "HBA1C_OUT_OF_RANGE"), ("X-S01-004", "CREATININE_SCREENING"), ("X-S01-004", "HEPATIC_SCREENING")]
    # only the screening ALT is cited for the hepatic criterion, not the week-4 value
    hep = [f for f in fs if f.details["subcode"] == "HEPATIC_SCREENING"][0]
    assert [e.ref.seq for e in hep.evidence if not e.is_doc] == [1]
    # no creatinine findings when the version has no such rule
    core2 = synthetic_core(rows, basic_rules(hepatic_uln_multiple=2.0), RANGES)
    assert not [f for f in find_eligibility_violations(core2) if f.details["subcode"] == "CREATININE_SCREENING"]


def test_synthetic_duplicates_empty_when_none():
    core = synthetic_core({"DM": [_dm("X-S01-001", "40")]}, basic_rules())
    assert find_duplicate_subjects(core) == []
