"""Run the documented public questions through the official facade and validate shape, evidence, timing."""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import pytest

from starter.schemas import Answer, Question, RecordRef
from stage1.atlas import Atlas, StudyGraph

QUESTIONS = Path(__file__).resolve().parent.parent / "starter" / "public_questions.json"


@pytest.fixture(scope="module")
def atlas(data_dir):
    g = StudyGraph(str(data_dir))
    g.build()
    return Atlas(g)


@pytest.fixture(scope="module")
def raw_index(data_dir):
    idx = {}
    for p in (Path(data_dir) / "data").glob("*.csv"):
        if not p.stem.isupper():
            continue
        seq_col = f"{p.stem}SEQ"
        keys = set()
        with open(p, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                s = row.get(seq_col)
                keys.add((row["USUBJID"], int(s) if s else None))
        idx[p.stem] = keys
    return idx


def _load_questions():
    doc = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    return [Question(**{k: v for k, v in q.items() if k in Question.model_fields}) for q in doc["questions"]]


@pytest.mark.parametrize("question", _load_questions(), ids=lambda q: q.question_id)
def test_public_question(atlas, raw_index, data_dir, question):
    t0 = time.perf_counter()
    ans = atlas.answer(question)
    elapsed = time.perf_counter() - t0
    assert elapsed < 120
    assert isinstance(ans, Answer)
    Answer.model_validate(ans.model_dump())
    assert ans.question_id == question.question_id
    assert 0.0 <= ans.confidence <= 1.0
    docs = {p.stem for p in (Path(data_dir) / "documents").glob("*")}
    refs = list(ans.evidence) + [a for a in (ans.answer if isinstance(ans.answer, list) else []) if isinstance(a, RecordRef)]
    for ref in refs:
        if ref.domain == "DOC":
            assert ref.document in docs
        else:
            assert (ref.usubjid, ref.seq) in raw_index[ref.domain], ref
    if question.kind == "trap":
        # documented trap: the honest answer may be empty; if empty there must be no evidence
        if ans.answer == []:
            assert ans.evidence == []
    if question.kind == "count":
        assert isinstance(ans.answer, int)
    if question.kind == "finding":
        assert isinstance(ans.answer, list) and all(isinstance(s, str) for s in ans.answer)
        if ans.answer:
            assert ans.evidence
    if question.kind == "lookup":
        assert isinstance(ans.answer, list) and all(isinstance(r, RecordRef) for r in ans.answer)
