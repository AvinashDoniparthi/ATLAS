"""Dosing rules: wrong dose vs protocol expectation, missing dose records."""
from __future__ import annotations

from collections import Counter

import pytest

from backend.normalization.values import parse_number
from backend.rules import run_rule
from backend.rules.common import rule_availability
from backend.rules.dosing import arm_key, dosing_visits, find_dosing_errors, find_missing_doses
from tests.rules_fixtures import basic_rules, check_all_evidence, real_core, synthetic_core


@pytest.fixture(scope="module")
def core(data_dir):
    return real_core(data_dir)


def test_rule_available(core):
    assert rule_availability(core, "DOSING_ERROR")[0]


def test_wrong_dose_matches_independent_scan(core):
    fs = run_rule(core, "DOSING_ERROR")
    r = core.rules
    expected: dict[str, set] = {}
    for rec in core.idx.by_domain["EX"]:
        key = arm_key(r, core.dm(rec.usubjid).get("ARM"))
        exp = r.expected_dose[key]
        act = parse_number(rec.get("EXDOSE"))
        if act is not None and abs(act - exp) > 1e-9:
            expected.setdefault(rec.usubjid, set()).add(rec.key.as_tuple())
    assert {f.usubjid: set(f.details["records"]) for f in fs} == expected
    assert expected, "practice data is expected to contain dosing errors"
    check_all_evidence(core, fs)
    for f in fs:
        assert all(e.ref.domain == "EX" for e in f.evidence if e.role == "support")
        assert any(e.is_doc for e in f.evidence)


def test_wrong_dose_site_pattern(core):
    fs = run_rule(core, "DOSING_ERROR")
    sites = Counter(f.site for f in fs)
    assert len(sites) == 1, "practice data plants dosing errors at a single site"
    bad_site = next(iter(sites))
    clean_site = sorted(s for s in core.idx.by_site if s != bad_site)[0]
    assert [f for f in fs if f.site == clean_site] == []


def test_missing_dose_and_no_exposure(core):
    fs = run_rule(core, "MISSING_DOSE")
    visits = dosing_visits(core)
    assert visits and visits[0] != "SCREENING"
    no_ex = {u for u in core.idx.subjects if not core.idx.records(u, "EX")}
    assert {f.usubjid for f in fs if f.details["subcode"] == "NO_EXPOSURE"} == no_ex
    gaps = [f for f in fs if f.details["subcode"] == "MISSING_DOSE"]
    assert gaps
    for f in gaps:
        have = {rec.get("VISIT").upper() for rec in core.idx.records(f.usubjid, "EX")}
        for m in f.details["missing_visits"]:
            assert m not in have
        assert any(e.ref.domain == "EX" for e in f.evidence)
    check_all_evidence(core, fs)


def test_arm_key_generic():
    r = basic_rules(expected_dose={"DRUG": 10.0, "PLACEBO": 0.0})
    assert arm_key(r, "DRUG") == "DRUG"
    assert arm_key(r, "placebo") == "PLACEBO"
    assert arm_key(r, "DRUG-042 10 MG") == "DRUG"
    assert arm_key(r, "ACTIVE") == "DRUG"
    assert arm_key(r, None) is None
    r2 = basic_rules(expected_dose={"A": 5.0, "B": 10.0, "PLACEBO": 0.0})
    assert arm_key(r2, "ACTIVE") is None  # ambiguous -> no guess


def _ex(u, seq, visit, dose, unit="mg", trt="DRUG"):
    return {"USUBJID": u, "EXSEQ": str(seq), "VISIT": visit, "EXSTDTC": "2026-02-01", "EXDOSE": dose, "EXDOSU": unit, "EXTRT": trt}


def test_synthetic_wrong_dose_and_units_and_bad_values():
    rows = {"DM": [{"USUBJID": "X-S01-001", "SITEID": "S01", "ARM": "DRUG", "RFSTDTC": "2026-01-15", "AGE": "40"},
                   {"USUBJID": "X-S01-002", "SITEID": "S01", "ARM": "PLACEBO", "RFSTDTC": "2026-01-15", "AGE": "40"}],
            "EX": [_ex("X-S01-001", 1, "BASELINE", "10"), _ex("X-S01-001", 2, "WEEK2", "20"), _ex("X-S01-001", 3, "WEEK4", "abc"),
                   _ex("X-S01-002", 1, "BASELINE", "0", trt="PLACEBO"), _ex("X-S01-002", 2, "WEEK2", "10", trt="PLACEBO"),
                   _ex("X-S01-002", 3, "WEEK4", "0", unit="g", trt="PLACEBO")]}
    core = synthetic_core(rows, basic_rules())
    fs = find_dosing_errors(core)
    assert {f.usubjid: [k[2] for k in f.details["records"]] for f in fs} == {"X-S01-001": [2], "X-S01-002": [2]}
    assert fs[0].details["unparseable_doses"] == 1


def test_synthetic_expected_dose_from_protocol():
    rows = {"DM": [{"USUBJID": "X-S01-001", "SITEID": "S01", "ARM": "DRUG", "RFSTDTC": "2026-01-15", "AGE": "40"}],
            "EX": [_ex("X-S01-001", 1, "BASELINE", "20")]}
    assert find_dosing_errors(synthetic_core(rows, basic_rules(expected_dose={"DRUG": 20.0, "PLACEBO": 0.0}))) == []
    assert len(find_dosing_errors(synthetic_core(rows, basic_rules()))) == 1


def test_synthetic_missing_dose():
    rows = {"DM": [{"USUBJID": "X-S01-001", "SITEID": "S01", "ARM": "DRUG", "RFSTDTC": "2026-01-15", "AGE": "40"},
                   {"USUBJID": "X-S01-002", "SITEID": "S01", "ARM": "DRUG", "RFSTDTC": "2026-01-15", "AGE": "40"},
                   {"USUBJID": "X-S01-003", "SITEID": "S01", "ARM": "DRUG", "RFSTDTC": "2026-01-15", "AGE": "40"}],
            "EX": [_ex("X-S01-001", 1, "BASELINE", "10"), _ex("X-S01-001", 2, "WEEK4", "10"),
                   _ex("X-S01-002", 1, "BASELINE", "10"), _ex("X-S01-002", 2, "WEEK2", "10"), _ex("X-S01-002", 3, "WEEK4", "10")],
            "DS": [{"USUBJID": "X-S01-002", "DSSEQ": "1", "DSDECOD": "COMPLETED", "DSSTDTC": "2026-03-01", "DSTERM": ""}]}
    core = synthetic_core(rows, basic_rules())
    fs = find_missing_doses(core)
    by = {f.usubjid: f.details for f in fs}
    assert by["X-S01-001"]["missing_visits"] == ["WEEK2"]
    assert "X-S01-002" not in by
    assert by["X-S01-003"]["subcode"] == "NO_EXPOSURE"
