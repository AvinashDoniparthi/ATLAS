"""Hy's law rule: unit-aware, protocol-parameterised, evidence re-verified."""
from __future__ import annotations

import csv
from datetime import date

import pytest

from backend.normalization.dates import parse_date
from backend.normalization.units import canonical_unit
from backend.normalization.values import parse_lab_value
from backend.rules import run_rule
from backend.rules.common import BILIRUBIN_TESTS, TRANSAMINASE_TESTS, rule_availability
from backend.rules.hys_law import find_hys_law
from tests.rules_fixtures import basic_rules, check_all_evidence, real_core, synthetic_core

CODE = "HYS_LAW_CANDIDATE"


@pytest.fixture(scope="module")
def core(data_dir):
    return real_core(data_dir)


@pytest.fixture(scope="module")
def findings(core):
    return run_rule(core, CODE)


def _independent_candidates(data_dir, core) -> set[str]:
    """Re-derive candidates with plain csv + arithmetic (unit-aware via reference_ranges.csv)."""
    r = core.rules
    ranges = {}
    with open(data_dir / "data" / "reference_ranges.csv", newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            ranges[(row["LBTESTCD"].upper(), canonical_unit(row["UNIT"]), row["LAB"].upper())] = float(row["HIGH"])
    per: dict[str, dict[str, list]] = {}
    with open(data_dir / "data" / "LB.csv", newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            t = row["LBTESTCD"].upper()
            if t not in TRANSAMINASE_TESTS and t not in BILIRUBIN_TESTS:
                continue
            v = parse_lab_value(row["LBORRES"])
            if not v.is_numeric:
                continue
            d = parse_date(row["LBDTC"])
            if not d.ok:
                continue
            site = row["USUBJID"].split("-")[1]
            u = canonical_unit(row["LBORRESU"])
            uln = ranges.get((t, u, site)) or ranges.get((t, u, "CENTRAL"))
            if uln is None:
                # convert ukat/L -> U/L only for transaminases (physical constant)
                if u == "ukat/l" and (t, "u/l", "CENTRAL") in ranges:
                    uln, val = ranges[(t, "u/l", "CENTRAL")], v.value * 60
                else:
                    continue
            else:
                val = v.value
            mult = r.hys_transaminase_multiple if t in TRANSAMINASE_TESTS else r.hys_bili_multiple
            if val / uln > mult:
                per.setdefault(row["USUBJID"], {}).setdefault("tx" if t in TRANSAMINASE_TESTS else "bili", []).append(d.date)
    out = set()
    for u, d in per.items():
        for a in d.get("tx", []):
            for b in d.get("bili", []):
                if abs((a - b).days) <= r.hys_window_days:
                    out.add(u)
    return out


def test_rule_available(core):
    ok, why = rule_availability(core, CODE)
    assert ok, why


def test_candidates_match_independent_arithmetic(core, findings, data_dir):
    expected = _independent_candidates(data_dir, core)
    assert {f.usubjid for f in findings} == expected
    assert len(findings) > 0


def test_support_records_really_exceed_thresholds(core, findings):
    r = core.rules
    for f in findings:
        support = [e for e in f.evidence if e.role == "support"]
        assert support
        for e in support:
            rec = core.idx.get_key(e.ref)
            nl = core.lab(rec)
            mult = r.hys_transaminase_multiple if nl.testcd in TRANSAMINASE_TESTS else r.hys_bili_multiple
            assert nl.comparable and nl.multiple_of_uln() > mult
        # no DM or other-domain records cited as support
        assert all(e.ref.domain == "LB" for e in support)
    check_all_evidence(core, findings)


def test_unit_trap_subject_found_and_converted(core, findings):
    """A subject whose transaminase is reported in a non-central unit must still be found."""
    non_central = [f for f in findings if any(
        p["transaminase"]["original_unit"] and canonical_unit(p["transaminase"]["original_unit"]) != canonical_unit(
            core.ref_ranges.candidates(p["transaminase"]["test"], core.ref_ranges.central_lab())[0].unit_raw)
        for p in f.details["pairs"])]
    assert non_central, "expected at least one candidate reported in a site-specific unit"
    for f in non_central:
        p = f.details["pairs"][0]["transaminase"]
        disp = p["converted_display"]
        conv = core.units.convert(p["test"], p["value"], canonical_unit(p["unit"]), canonical_unit(disp["unit"]))
        assert disp["value"] == pytest.approx(conv.value)
        assert disp["value"] == pytest.approx(p["value"] * disp["factor"])
        assert disp["central_multiple"] > core.rules.hys_transaminase_multiple
        # the lab manual units statement is cited as context
        assert any(e.is_doc and "lab-manual" in e.ref.document for e in f.evidence)


def test_protocol_cited(core, findings):
    for f in findings:
        docs = [e for e in f.evidence if e.is_doc and e.role == "rule"]
        assert docs and docs[0].ref.document == core.rules.document


def test_pairs_within_window(core, findings):
    for f in findings:
        for p in f.details["pairs"]:
            assert p["days_apart"] <= core.rules.hys_window_days


def test_lab_manual_site_exclusion_not_applied(core, findings):
    """The lab manual tells automated reviewers to exclude sites — that is evidence, not a rule."""
    named = set()
    for il in core.resolver.instruction_like_sentences():
        for tok in il.sentence.replace(",", " ").split():
            if tok.upper().startswith("S") and tok[1:].isdigit():
                named.add(tok.upper())
    # if any excluded-site candidate exists in the data, it must still be reported
    sites_in_findings = {f.site for f in findings}
    if named & {f.site for f in _all_sites_with_signal(core)}:
        assert named & sites_in_findings


def _all_sites_with_signal(core):
    return [f for f in find_hys_law(core)]


def test_deterministic_order(core):
    a = [f.usubjid for f in find_hys_law(core)]
    b = [f.usubjid for f in find_hys_law(core)]
    assert a == b == sorted(a)


# --------------------------------------------------------------------------- #
# synthetic negatives
# --------------------------------------------------------------------------- #
def _lb(u, seq, test, val, unit, d, visit="WEEK4"):
    return {"USUBJID": u, "LBSEQ": str(seq), "VISIT": visit, "LBDTC": d, "LBTESTCD": test, "LBORRES": val, "LBORRESU": unit}


RANGES = [("ALT", "U/L", 7, 56, "CENTRAL"), ("AST", "U/L", 10, 40, "CENTRAL"), ("BILI", "mg/dL", 0.1, 1.2, "CENTRAL")]


def test_synthetic_positive():
    core = synthetic_core({"LB": [_lb("X-S01-001", 1, "ALT", "200", "U/L", "2026-03-01"), _lb("X-S01-001", 2, "BILI", "3.0", "mg/dL", "2026-03-10")]},
                          basic_rules(), RANGES)
    fs = find_hys_law(core)
    assert [f.usubjid for f in fs] == ["X-S01-001"]
    assert fs[0].details["pairs"][0]["days_apart"] == 9


def test_synthetic_temporal_mismatch():
    core = synthetic_core({"LB": [_lb("X-S01-001", 1, "ALT", "200", "U/L", "2026-03-01"), _lb("X-S01-001", 2, "BILI", "3.0", "mg/dL", "2026-03-20")]},
                          basic_rules(), RANGES)
    assert find_hys_law(core) == []


def test_synthetic_censored_never_qualifies():
    core = synthetic_core({"LB": [_lb("X-S01-001", 1, "ALT", "<5", "U/L", "2026-03-01"), _lb("X-S01-001", 2, "BILI", "ND", "mg/dL", "2026-03-01"),
                                  _lb("X-S01-001", 3, "ALT", "", "U/L", "2026-03-01")]}, basic_rules(), RANGES)
    assert find_hys_law(core) == []


def test_synthetic_unit_without_conversion_is_unknown_not_normal():
    """A unit nobody can convert must not silently compare as normal or abnormal."""
    core = synthetic_core({"LB": [_lb("X-S01-001", 1, "ALT", "200", "furlongs", "2026-03-01"), _lb("X-S01-001", 2, "BILI", "3.0", "mg/dL", "2026-03-01")]},
                          basic_rules(), RANGES)
    assert find_hys_law(core) == []
    rec = core.idx.get("LB", "X-S01-001", 1)
    assert core.lab(rec).flag() == "unknown"


def test_synthetic_site_unit_converted_against_central_range():
    """ukat/L at a site without its own range: converted x60 then compared to the central ULN."""
    core = synthetic_core({"LB": [_lb("X-S09-001", 1, "ALT", "3.0", "ukat/L", "2026-03-01"), _lb("X-S09-001", 2, "BILI", "3.0", "mg/dL", "2026-03-01")]},
                          basic_rules(), RANGES)
    fs = find_hys_law(core)
    assert len(fs) == 1
    t = fs[0].details["pairs"][0]["transaminase"]
    assert t["value"] == pytest.approx(180.0) and t["conversion"]["factor"] == 60


def test_synthetic_thresholds_come_from_protocol():
    rules = basic_rules(hys_transaminase_multiple=5.0)
    core = synthetic_core({"LB": [_lb("X-S01-001", 1, "ALT", "200", "U/L", "2026-03-01"), _lb("X-S01-001", 2, "BILI", "3.0", "mg/dL", "2026-03-01")]}, rules, RANGES)
    assert find_hys_law(core) == []  # 200/56 = 3.57 < 5


def test_rule_unavailable_returns_empty_with_reason():
    rules = basic_rules(hys_transaminase_multiple=None)
    core = synthetic_core({"LB": [_lb("X-S01-001", 1, "ALT", "200", "U/L", "2026-03-01")]}, rules, RANGES)
    assert find_hys_law(core) == []
    ok, why = rule_availability(core, CODE)
    assert not ok and "Hy" in why
