"""Unit tests for the StudyGraph Relationship Query Layer and Explorer."""
from __future__ import annotations

from pathlib import Path
import pytest

from stage1.atlas import StudyGraph
from backend.graph.study_graph import StudyGraphCore
from backend.queries.relationships import get_related_subjects


@pytest.fixture(scope="module")
def graph(data_dir):
    g = StudyGraph(str(data_dir))
    g.ensure_built()
    return g


def test_relationships_empty_entity(graph):
    res = graph.related_subjects("medication", "")
    assert res["count"] == 0
    assert res["subjects"] == []
    assert "No connected patients found" in res["message"]


def test_relationships_unknown_entity(graph):
    res = graph.related_subjects("medication", "NON_EXISTENT_DRUG_XYZ_12345")
    assert res["count"] == 0
    assert res["subjects"] == []
    assert "No connected patients found" in res["message"]


def test_relationships_lab(graph):
    # Search for an actual test like ALT or AST or BILI
    res = graph.related_subjects("lab", "ALT")
    assert res["entity_type"] == "lab"
    assert res["entity_id"] == "ALT"
    assert res["count"] > 0
    assert len(res["subjects"]) == res["count"]
    first = res["subjects"][0]
    assert "usubjid" in first
    assert "site" in first
    assert "relationship" in first
    assert "summary" in first


def test_relationships_visit(graph):
    # Pick a visit seen in graph
    visits = list(graph.core.idx.visits_seen)
    if visits:
        v = visits[0]
        res = graph.related_subjects("visit", v)
        assert res["count"] > 0
        assert res["subjects"][0]["relationship"] == "Visit attendance"


def test_relationships_conmed(graph):
    # Find any conmed from CM domain if present
    cm_records = graph.core.idx.by_domain.get("CM", [])
    if cm_records:
        med = cm_records[0].get("CMTRT")
        if med:
            res = graph.related_subjects("medication", med)
            assert res["count"] >= 1
            assert any(s["usubjid"] == cm_records[0].usubjid for s in res["subjects"])


def test_relationships_adverse_event(graph):
    # Find any AE from AE domain if present
    ae_records = graph.core.idx.by_domain.get("AE", [])
    if ae_records:
        term = ae_records[0].get("AETERM")
        if term:
            res = graph.related_subjects("adverse_event", term)
            assert res["count"] >= 1
            assert any(s["usubjid"] == ae_records[0].usubjid for s in res["subjects"])


def test_relationships_exposure(graph):
    ex_records = graph.core.idx.by_domain.get("EX", [])
    if ex_records:
        trt = ex_records[0].get("EXTRT")
        if trt:
            res = graph.related_subjects("dosing", trt)
            assert res["count"] >= 1
            assert any(s["usubjid"] == ex_records[0].usubjid for s in res["subjects"])


def test_relationships_medical_history(graph):
    mh_records = graph.core.idx.by_domain.get("MH", [])
    if mh_records:
        term = mh_records[0].get("MHTERM")
        if term:
            res = graph.related_subjects("medical_history", term)
            assert res["count"] >= 1
            assert any(s["usubjid"] == mh_records[0].usubjid for s in res["subjects"])


def test_relationships_disposition(graph):
    ds_records = graph.core.idx.by_domain.get("DS", [])
    if ds_records:
        term = ds_records[0].get("DSDECOD") or ds_records[0].get("DSTERM")
        if term:
            res = graph.related_subjects("disposition", term)
            assert res["count"] >= 1
            assert any(s["usubjid"] == ds_records[0].usubjid for s in res["subjects"])


def test_relationships_findings(graph):
    res = graph.related_subjects("finding", "HYS_LAW_CANDIDATE")
    assert res["entity_type"] == "finding"
    assert isinstance(res["subjects"], list)
