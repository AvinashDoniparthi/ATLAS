"""
RECONSTRUCTED compatibility schemas — NOT the official organiser starter.

The official Problem 1 specification references ``starter/schemas.py`` but the
organiser file was not available in this workspace. This module reconstructs the
*smallest* schema that supports the contract the specification documents:

* ``from starter.schemas import Question, Answer``
* ``Atlas.answer(question: Question) -> Answer``
* Answer JSON keys shown in the worked example:
  ``question_id, answer, text, evidence, confidence, steps_used, tokens_used``
* Evidence items shown as ``RecordRef(domain="LB", usubjid="042-S07-001", seq=31)``
  and ``RecordRef(domain="DOC", document="lab-manual", section="units")``.

Everything not documented is an assumption; see
``docs/reconstructed_starter_assumptions.md``. Replace this file with the
organiser's version when it becomes available — the backend only touches these
classes through ``stage1/atlas.py``.
"""
from __future__ import annotations

from typing import List, Optional, Union

from pydantic import BaseModel, Field, model_validator

RECONSTRUCTED_STARTER = True


class RecordRef(BaseModel):
    """A citation of one study record (domain/usubjid/seq) or one document section."""

    domain: str
    usubjid: Optional[str] = None
    seq: Optional[int] = None
    document: Optional[str] = None
    section: Optional[str] = None

    @model_validator(mode="after")
    def _check_shape(self) -> "RecordRef":
        if self.domain == "DOC":
            if not self.document:
                raise ValueError("DOC RecordRef requires 'document'")
        else:
            if not self.usubjid:
                raise ValueError("data RecordRef requires 'usubjid'")
        return self


class Question(BaseModel):
    question_id: str
    text: str
    # Optional hint (count / lookup / finding / trap). The backend never relies on it.
    kind: Optional[str] = None


class Answer(BaseModel):
    question_id: str
    answer: Union[List[str], List[RecordRef], int, str, None] = None
    text: str = ""
    evidence: List[RecordRef] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    steps_used: int = 0
    tokens_used: int = 0
