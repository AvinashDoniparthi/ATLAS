"""Evidence validator / engine tests on the real practice data (nothing hard-coded)."""
from __future__ import annotations

import pytest

from backend.evidence.evidence_engine import assemble, confidence_for
from backend.evidence.record_refs import dedupe_sorted
from backend.evidence.validator import validate, validate_refs
from backend.graph.nodes import DocRefKey, EvidenceItem, Finding, RecordKey
from backend.graph.study_graph import StudyGraphCore


@pytest.fixture(scope="module")
def core(data_dir):
    c = StudyGraphCore(data_dir)
    c.build()
    return c


def _some_lb(core):
    return core.idx.by_domain["LB"][0]


def test_valid_record_ref(core):
    rec = _some_lb(core)
    res = validate(core, [EvidenceItem(ref=rec.key, why="exists")])
    assert res.ok and len(res.valid) == 1 and res.ratio == 1.0


def test_nonexistent_ref_dropped(core):
    rec = _some_lb(core)
    bad = RecordKey(rec.domain, rec.usubjid, 10**6)
    res = validate(core, [EvidenceItem(ref=bad, why="fake")])
    assert not res.valid and res.dropped[0][1] == "record_not_in_cut_view"


def test_wrong_domain_dropped(core):
    rec = _some_lb(core)
    bad = RecordKey("ZZ", rec.usubjid, rec.seq)
    res = validate(core, [EvidenceItem(ref=bad, why="fake")])
    assert not res.valid


def test_wrong_subject_dropped(core):
    rec = _some_lb(core)
    bad = RecordKey(rec.domain, "000-S00-000", rec.seq)
    res = validate(core, [EvidenceItem(ref=bad, why="fake")])
    assert not res.valid


def test_failing_check_dropped(core):
    rec = _some_lb(core)
    res = validate(core, [EvidenceItem(ref=rec.key, why="claim false", check=lambda: False)])
    assert not res.valid and res.dropped[0][1] == "claim_not_supported_by_record"


def test_raising_check_dropped(core):
    rec = _some_lb(core)

    def boom():
        raise RuntimeError("x")

    res = validate(core, [EvidenceItem(ref=rec.key, why="raises", check=boom)])
    assert not res.valid and res.dropped[0][1].startswith("check_raised")


def test_doc_ref_valid_and_invalid(core):
    name = next(iter(core.documents))
    section = next(iter(core.documents[name].sections))
    good = EvidenceItem(ref=DocRefKey(name, section), why="doc")
    bad_section = EvidenceItem(ref=DocRefKey(name, "no-such-section-xyz"), why="doc")
    bad_doc = EvidenceItem(ref=DocRefKey("no-such-doc", section), why="doc")
    res = validate(core, [good, bad_section, bad_doc])
    assert len(res.valid) == 1 and len(res.dropped) == 2


def test_corrected_record_valid_after_correction_cut(data_dir):
    c = StudyGraphCore(data_dir)
    c.build()
    if not c.corrections:
        pytest.skip("no corrections in dataset")
    corr = c.corrections[0]
    c.build(corr.cut)
    key = RecordKey(corr.domain, corr.usubjid, corr.seq)
    rec = c.idx.get_key(key)
    assert rec is not None and rec.get(corr.field) == corr.new_value
    valid, dropped = validate_refs(c, [key])
    assert valid == [key] and not dropped
    # before the correction cut the record (if available) still carries the old value
    c.build(corr.cut - 1)
    rec2 = c.idx.get_key(key)
    if rec2 is not None:
        assert rec2.get(corr.field) == corr.old_value


def test_assemble_drops_finding_without_support(core):
    rec = _some_lb(core)
    f_ok = Finding(code="X", usubjid=rec.usubjid, site=rec.site, summary="ok",
                   evidence=[EvidenceItem(ref=rec.key, why="s", role="support")])
    f_bad = Finding(code="X", usubjid=rec.usubjid, site=rec.site, summary="bad",
                    evidence=[EvidenceItem(ref=RecordKey("LB", rec.usubjid, 10**6), why="s", role="support")])
    asm = assemble(core, [f_ok, f_bad])
    assert [f.summary for f in asm.kept] == ["ok"]
    assert len(asm.dropped_findings) == 1
    assert asm.refs == [rec.key]
    assert asm.evidence_dropped


def test_dedupe_sorted_order(core):
    lb = core.idx.by_domain["LB"][:3]
    refs = [lb[2].key, lb[0].key, DocRefKey("protocol_v1", "7"), lb[0].key, lb[1].key]
    out = dedupe_sorted(refs)
    assert len(out) == 4
    assert isinstance(out[-1], DocRefKey)
    assert [r.seq for r in out[:3]] == sorted(r.seq for r in out[:3])


def test_confidence_policy():
    assert confidence_for("finding") == 0.9
    assert confidence_for("finding", empty=True) == 0.85
    assert confidence_for("finding", insufficient=True) == 0.3
    assert confidence_for("finding", ambiguous=True) == 0.2
    assert confidence_for("finding", evidence_dropped=True) < confidence_for("finding")
    assert confidence_for("finding", flagged=True) < confidence_for("finding")
    assert confidence_for("finding", rules_missing=True) < confidence_for("finding")
    assert 0.05 <= confidence_for("count", evidence_dropped=True, flagged=True, rules_missing=True, extra_penalty=5) <= 0.95
