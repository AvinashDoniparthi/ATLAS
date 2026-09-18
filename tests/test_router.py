"""Router classification and end-to-end answers on the real data (expectations computed, not hard-coded)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.agent.router import Router
from backend.agent.schemas import STATUS_AMBIGUOUS, STATUS_NO_MATCH, STATUS_OK
from backend.graph.nodes import DocRefKey, RecordKey
from backend.graph.study_graph import StudyGraphCore
from backend.queries import disposition as DS


@pytest.fixture(scope="module")
def core(data_dir):
    c = StudyGraphCore(data_dir)
    c.build()
    return c


@pytest.fixture(scope="module")
def router(core):
    return Router(core)


def _some_site(core):
    return sorted(core.idx.by_site)[0]


def _assert_refs_exist(core, ia):
    for ref in ia.evidence:
        if isinstance(ref, RecordKey):
            assert core.exists(ref), ref
        else:
            assert ref.document in core.documents
    if isinstance(ia.answer, list):
        for item in ia.answer:
            if isinstance(item, RecordKey):
                assert core.exists(item)


# ------------------------------------------------------------------ classify
@pytest.mark.parametrize("text,kind", [
    ("How many subjects at site S07 discontinued due to an adverse event?", "count"),
    ("Count the S07 subjects who dropped out because of an AE", "count"),
    ("Which subjects meet the Hy's law criteria?", "finding"),
    ("list subjects with potential Hy's law", "finding"),
    ("Which subjects at site S01 received a wrong dose?", "finding"),
    ("what does the lab manual say to automated reviewers?", "doc"),
    ("What is the visit window in the protocol?", "doc"),
    ("Which subjects took a prohibited medication?", "finding"),
    ("How many serious adverse events were reported?", "count"),
])
def test_classification(router, text, kind):
    it = router.classify(text)
    assert it.kind == kind, (text, it)


def test_classification_entities(router, core):
    it = router.classify("How many subjects at site S07 discontinued due to an adverse event?")
    assert it.site == "S07" and it.filters.get("disposition") == "DISCONTINUED"
    assert "ADVERSE" in it.filters.get("disposition_reason", "")
    subj = core.idx.subjects[0]
    it = router.classify(f"List the laboratory and adverse-event records for {subj} within 7 days of the WEEK8 visit")
    assert it.kind == "lookup" and it.subject == subj and it.visit == "WEEK8" and it.window_days == 7
    assert it.domains == ["LB", "AE"]
    it = router.classify("Which subjects at site S01 received a wrong dose?")
    assert it.finding_code == "DOSING_ERROR" and it.site == "S01"


# ---------------------------------------------------------------------- count
def test_count_matches_independent_computation(router, core):
    site = _some_site(core)
    # independent computation from raw records (unique persons)
    persons = set()
    for u in core.idx.subjects_at(site):
        if any(DS.matches_disposition(r, "DISCONTINUED", "ADVERSE EVENT") for r in core.idx.records(u, "DS")):
            persons.add(core.person_of.get(u, u))
    ia = router.answer("q", f"How many subjects at site {site} discontinued due to an adverse event?")
    assert ia.kind == "count" and isinstance(ia.answer, int)
    assert ia.answer == len(persons)
    if ia.answer == 0:
        assert ia.status == STATUS_NO_MATCH and ia.evidence == []
    else:
        assert ia.status == STATUS_OK and all(r.domain == "DS" for r in ia.evidence)
    _assert_refs_exist(core, ia)


def test_count_all_subjects_collapses_duplicates(router, core):
    ia = router.answer("q", "How many subjects are in the study?")
    assert ia.answer == len(core.persons)
    assert ia.answer <= len(core.idx.subjects)


# --------------------------------------------------------------------- lookup
def test_lookup_window(router, core):
    # pick a subject that has WEEK8 records
    subj = next(u for u in core.idx.subjects if core.idx.visit_records(u, "WEEK8"))
    ia = router.answer("q", f"List the laboratory and adverse-event records for {subj} within 7 days of the WEEK8 visit")
    assert ia.kind == "lookup" and ia.status in (STATUS_OK, STATUS_NO_MATCH)
    assert all(isinstance(r, RecordKey) and r.domain in ("LB", "AE") for r in ia.answer)
    assert ia.evidence == ia.answer
    _assert_refs_exist(core, ia)
    from backend.queries.lookups import visit_anchor

    anchor, _, _ = visit_anchor(core, subj, "WEEK8")
    assert anchor is not None
    for ref in ia.answer:
        rec = core.idx.get_key(ref)
        d = core.idx.record_date(rec)
        assert d is not None and abs((d - anchor).days) <= 7
    # completeness: every LB/AE record of the subject inside the window is returned
    expected = set()
    for dom in ("LB", "AE"):
        for r in core.idx.records(subj, dom):
            d = core.idx.record_date(r)
            if d is not None and anchor - timedelta(days=7) <= d <= anchor + timedelta(days=7):
                expected.add(r.key)
    assert set(ia.answer) == expected


def test_lookup_unknown_subject_is_no_match(router):
    ia = router.answer("q", "List the laboratory records for 999-S99-999")
    assert ia.status == STATUS_NO_MATCH and ia.answer == [] and ia.evidence == []


# ------------------------------------------------------------------- finding
def test_hys_law_finding(router, core):
    pytest.importorskip("backend.rules")
    ia = router.answer("q", "Which subjects meet the Hy's law criteria?")
    assert ia.kind == "finding"
    assert isinstance(ia.answer, list) and all(isinstance(s, str) for s in ia.answer)
    assert ia.answer == sorted(set(ia.answer))
    _assert_refs_exist(core, ia)
    rules = core.rules
    for ref in ia.evidence:
        if isinstance(ref, RecordKey) and ref.domain == "LB":
            nl = core.lab(core.idx.get_key(ref))
            assert nl.comparable
            m = nl.multiple_of_uln()
            if nl.testcd in ("ALT", "AST"):
                assert m > rules.hys_transaminase_multiple
            else:
                assert m > rules.hys_bili_multiple
    subjects_cited = {r.usubjid for r in ia.evidence if isinstance(r, RecordKey)}
    assert set(ia.answer) <= subjects_cited


def test_finding_paraphrases_agree(router):
    pytest.importorskip("backend.rules")
    a = router.answer("q", "Which subjects meet the Hy's law criteria?")
    b = router.answer("q", "list subjects with potential Hy's law")
    c = router.answer("q", "Who shows a drug-induced liver injury signal?")
    assert a.answer == b.answer == c.answer


def test_gibberish_is_ambiguous(router):
    ia = router.answer("q", "blorp fizz wumble")
    assert ia.status == STATUS_AMBIGUOUS and ia.answer is None and ia.evidence == [] and ia.confidence <= 0.3


def test_doc_question_cites_documents(router, core):
    ia = router.answer("q", "what does the lab manual say to automated reviewers?")
    assert ia.kind == "doc"
    assert ia.evidence and all(isinstance(r, DocRefKey) for r in ia.evidence)
    for r in ia.evidence:
        assert r.document in core.documents


def test_trace_populated(router, core):
    ia = router.answer("q", "How many subjects are female?")
    t = ia.trace
    assert t.kind == "count" and t.tools and t.cut == core.cut() and t.protocol_version == core.protocol_version()
    assert ia.steps_used >= 1 and ia.tokens_used == 0
