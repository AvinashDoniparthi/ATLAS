"""Serious AE (protocol-derived) and AE-before-first-dose rules."""
from __future__ import annotations

import pytest

from backend.normalization.dates import parse_date
from backend.rules import run_rule
from backend.rules.serious_ae import find_ae_before_first_dose, find_serious_ae, is_serious
from tests.rules_fixtures import basic_rules, check_all_evidence, real_core, synthetic_core


@pytest.fixture(scope="module")
def core(data_dir):
    return real_core(data_dir)


def test_serious_matches_independent_scan(core):
    fs = run_rule(core, "SERIOUS_AE")
    expected = {r.key.as_tuple() for r in core.idx.by_domain["AE"]
                if (r.get("AESER") or "").upper() == "Y" or (r.get("AESHOSP") or "").upper() == "Y"}
    assert {f.details["record"] for f in fs} == expected
    check_all_evidence(core, fs)


def test_miscoded_sae_detected(core):
    fs = run_rule(core, "SERIOUS_AE")
    mis = [f for f in fs if f.details["subcode"] == "SAE_MISCODED"]
    raw = [r for r in core.idx.by_domain["AE"] if (r.get("AESHOSP") or "").upper() == "Y" and (r.get("AESER") or "").upper() != "Y"]
    assert {f.details["record"] for f in mis} == {r.key.as_tuple() for r in raw}
    assert raw, "practice data is expected to contain a miscoded SAE"
    for f in mis:
        assert any(e.is_doc and e.ref.section == core.rules.refs["sae_hosp"].section for e in f.evidence)


def test_ae_before_first_dose(core):
    fs = run_rule(core, "AE_BEFORE_FIRST_DOSE")
    expected = set()
    for r in core.idx.by_domain["AE"]:
        dm = core.dm(r.usubjid)
        a, b = parse_date(r.get("AESTDTC")), parse_date(dm.get("RFSTDTC"))
        if a.ok and b.ok and a.date < b.date:
            expected.add(r.key.as_tuple())
    assert {f.details["record"] for f in fs} == expected
    for f in fs:
        assert f.details["days_before"] > 0
        assert any(e.ref.domain == "DM" and e.role == "context" for e in f.evidence)
    check_all_evidence(core, fs)


def _ae(u, seq, ser, hosp, start="2026-02-01"):
    return {"USUBJID": u, "AESEQ": str(seq), "AETERM": "Rash", "AESEV": "MILD", "AESER": ser, "AESHOSP": hosp,
            "AESTDTC": start, "AEENDTC": start, "AEOUT": "RECOVERED"}


def test_synthetic_hosp_rule_on_and_off():
    rows = {"AE": [_ae("X-S01-001", 1, "N", "Y"), _ae("X-S01-001", 2, "Y", "N"), _ae("X-S01-001", 3, "N", "N")],
            "DM": [{"USUBJID": "X-S01-001", "SITEID": "S01", "RFSTDTC": "2026-01-15", "ARM": "DRUG", "AGE": "40"}]}
    core = synthetic_core(rows, basic_rules())
    fs = find_serious_ae(core)
    assert [(f.details["record"][2], f.details["subcode"]) for f in fs] == [(1, "SAE_MISCODED"), (2, "SAE_CODED")]
    core2 = synthetic_core(rows, basic_rules(sae_hosp_flag_rule=False))
    assert [f.details["record"][2] for f in find_serious_ae(core2)] == [2]
    assert is_serious(core2, core2.idx.get("AE", "X-S01-001", 1)) == (False, "")


def test_synthetic_before_first_dose_and_bad_dates():
    rows = {"AE": [_ae("X-S01-001", 1, "N", "N", "10-JAN-2026"), _ae("X-S01-001", 2, "N", "N", "2026-01-15"), _ae("X-S01-001", 3, "N", "N", "garbage")],
            "DM": [{"USUBJID": "X-S01-001", "SITEID": "S01", "RFSTDTC": "2026-01-15", "ARM": "DRUG", "AGE": "40"}]}
    core = synthetic_core(rows, basic_rules())
    fs = find_ae_before_first_dose(core)
    assert [f.details["record"][2] for f in fs] == [1]
    assert fs[0].details["days_before"] == 5
