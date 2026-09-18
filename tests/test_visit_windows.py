"""Visit-window deviations depend on the protocol version in force at the cut."""
from __future__ import annotations

import pytest

from backend.normalization.dates import parse_date
from backend.rules import run_rule
from backend.rules.visit_windows import find_visit_window_deviations, visit_actual_date
from tests.rules_fixtures import basic_rules, check_all_evidence, real_core, synthetic_core


@pytest.fixture(scope="module")
def core(data_dir):
    return real_core(data_dir)


def test_deviations_match_independent_computation(core):
    fs = run_rule(core, "VISIT_WINDOW_DEVIATION")
    r = core.rules
    expected = set()
    for (u, v), _ in core.idx.by_subject_visit.items():
        if v not in r.visit_schedule:
            continue
        ref = parse_date(core.dm(u).get("RFSTDTC"))
        actual, _, _ = visit_actual_date(core, u, v)
        if not ref.ok or actual is None:
            continue
        if abs((actual - ref.date).days - r.visit_schedule[v]) > r.visit_window_days:
            expected.add((u, v))
    assert {(f.usubjid, f.details["visit"]) for f in fs} == expected
    assert fs
    for f in fs:
        assert abs(f.details["offset_days"]) > f.details["window"]
        assert any(e.ref.domain == "DM" for e in f.evidence)
    check_all_evidence(core, fs)


def test_window_changes_with_protocol_version(data_dir):
    """The earliest cut's protocol has a wider window than the latest; compare on the same subjects."""
    early = real_core(data_dir, cut=min(c.cut for c in real_core(data_dir).cut_table))
    late = real_core(data_dir)
    if early.rules.visit_window_days == late.rules.visit_window_days:
        pytest.skip("no window change between versions in this dataset")
    assert early.rules.visit_window_days > late.rules.visit_window_days
    early_f = run_rule(early, "VISIT_WINDOW_DEVIATION")
    late_f = run_rule(late, "VISIT_WINDOW_DEVIATION")
    early_keys = {(f.usubjid, f.details["visit"]) for f in early_f}
    late_keys = {(f.usubjid, f.details["visit"]) for f in late_f if (f.usubjid, f.details["visit"]) in early.idx.by_subject_visit}
    assert early_keys <= late_keys


def _rec(dom, u, seq, visit, d):
    return {"USUBJID": u, f"{dom}SEQ": str(seq), "VISIT": visit, f"{dom}DTC": d, f"{dom}TESTCD": "X", f"{dom}ORRES": "1", f"{dom}ORRESU": "u"}


def test_synthetic_window_and_bad_dates():
    rows = {"DM": [{"USUBJID": "X-S01-001", "SITEID": "S01", "ARM": "DRUG", "RFSTDTC": "2026-01-01", "AGE": "40"}],
            "VS": [_rec("VS", "X-S01-001", 1, "WEEK2", "2026-01-15"),      # day 14 exactly
                   _rec("VS", "X-S01-001", 2, "WEEK4", "05-FEB-2026"),     # day 35 -> +7 (dd-MON format)
                   _rec("VS", "X-S01-001", 3, "SCREENING", "not a date")]}
    core = synthetic_core(rows, basic_rules(visit_window_days=3))
    fs = find_visit_window_deviations(core)
    assert [(f.details["visit"], f.details["offset_days"]) for f in fs] == [("WEEK4", 7)]
    assert find_visit_window_deviations(synthetic_core(rows, basic_rules(visit_window_days=7))) == []
