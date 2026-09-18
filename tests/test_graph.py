"""StudyGraphCore / StudyGraph facade structure and index consistency."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.graph import edges as E
from backend.graph.edges import node_id
from backend.graph.nodes import site_from_usubjid
from backend.graph.study_graph import PRIMARY_IDENTITY_COLS, StudyGraphCore


@pytest.fixture(scope="module")
def core(data_dir: Path) -> StudyGraphCore:
    c = StudyGraphCore(data_dir)
    c.build()
    return c


def test_stats_consistency(core: StudyGraphCore):
    st = core.stats
    assert st.nodes == len(core.graph.nodes) == sum(st.node_types.values())
    assert st.edges == core.graph.edge_count == sum(st.edge_types.values())
    assert st.subjects == len(core.idx.subjects) > 0
    assert st.records == sum(st.domains.values()) == len(core.idx.by_ref) or st.records >= len(core.idx.by_ref)
    assert st.node_types[E.RECORD] == st.records
    assert st.node_types[E.SUBJECT] == st.subjects
    assert st.build_ms > 0
    assert st.effective_cut is not None and st.cut is None


def test_every_subject_enrolled_from_its_site(core: StudyGraphCore):
    for u in core.idx.subjects:
        site = core.idx.site_of(u)
        assert site, u
        sn = node_id(E.SUBJECT, u)
        preds = core.graph.predecessors(sn, E.ENROLLED)
        assert preds == [node_id(E.SITE, site)]
        assert site == site_from_usubjid(u)


def test_by_ref_resolves_and_matches_graph(core: StudyGraphCore):
    for key, rec in core.idx.by_ref.items():
        assert rec.key.as_tuple() == key
        assert core.exists(rec.key)
        assert core.graph.has(node_id(E.RECORD, rec.domain, rec.usubjid, rec.seq))
    assert core.record("LB", "no-such-subject", 1) is None


def test_lb_indexed_by_subject_test(core: StudyGraphCore):
    for rec in core.idx.by_domain["LB"]:
        t = (rec.get("LBTESTCD") or "").upper()
        assert rec in core.idx.tests_for(rec.usubjid, "LB", t)
        assert rec in core.idx.by_test[("LB", t)]
    for u in core.idx.subjects[:5]:
        for dom, recs in core.idx.by_subject[u].items():
            assert all(r.usubjid == u and r.domain == dom for r in recs)


def test_persons_and_duplicates(core: StudyGraphCore):
    st = core.stats
    assert len(core.persons) == st.unique_persons == st.subjects - st.duplicate_subjects
    assert set(core.person_of) == set(core.idx.subjects)
    dm_headers = core.load_reports["DM"].headers
    primary = [c for c in PRIMARY_IDENTITY_COLS if c in dm_headers]
    for pc in core.persons.values():
        assert pc.canonical in pc.usubjids
        if len(pc.usubjids) > 1:
            idents = {tuple((core.dm(u).get(c) or "").upper() for c in primary) for u in pc.usubjids}
            assert len(idents) == 1
            for u in pc.usubjids:
                assert core.is_duplicate_enrolment(u) == (u != pc.canonical)
    assert len(core.canonical_subjects()) == st.unique_persons


def test_visit_nodes(core: StudyGraphCore):
    u = core.idx.subjects[0]
    visits = core.graph.neighbours(node_id(E.SUBJECT, u), E.HAS_VISIT)
    assert visits
    for v in visits:
        assert core.graph.neighbours(v, E.AT_VISIT)


def test_lab_chain_and_cache(core: StudyGraphCore):
    rec = core.idx.by_domain["LB"][0]
    nl = core.lab(rec)
    assert core.lab(rec) is nl
    chain = nl.chain()
    assert chain["record"] == rec.key.as_tuple()
    assert chain["status"] in {"ok", "censored", "not_detected", "missing", "non_numeric", "no_range", "no_conversion"}


def test_rebuild_at_cut_clears_caches(core: StudyGraphCore):
    core.derived("x", lambda: 1)
    assert "x" in core._derived
    st = core.build(1)
    assert "x" not in core._derived
    assert st.cut == 1 and st.effective_cut == 1
    core.build()


# --------------------------------------------------------------- facade
def test_facade_build_stats(data_dir: Path):
    from stage1.atlas import StudyGraph

    g = StudyGraph(str(data_dir))
    stats = g.build()
    for key in ("nodes", "edges", "subjects", "build_ms", "ms", "cut", "protocol_version"):
        assert key in stats, key
    assert stats["nodes"] > 0 and stats["edges"] > 0 and stats["subjects"] > 0
    assert g.stats() == stats


def test_patient360_shape(data_dir: Path):
    pytest.importorskip("backend.queries.patient360")
    from stage1.atlas import StudyGraph

    g = StudyGraph(str(data_dir))
    g.build()
    u = g.core.idx.subjects[0]
    p = g.patient360(u)
    assert isinstance(p, dict)
    assert p.get("usubjid") == u
    assert p.get("found", True) is True
    missing = g.patient360("NOPE")
    assert isinstance(missing, dict)
    assert missing.get("found") is False
