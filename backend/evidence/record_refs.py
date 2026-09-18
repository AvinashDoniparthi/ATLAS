"""Helpers for building and ordering evidence references."""
from __future__ import annotations

from typing import Iterable, Optional

from backend.graph.nodes import DocRefKey, Record, RecordKey


def rec_ref(record: Record) -> RecordKey:
    return record.key


def doc_ref(document: str, section: str) -> DocRefKey:
    return DocRefKey(document=document, section=section)


def _sort_key(ref: RecordKey | DocRefKey) -> tuple:
    if isinstance(ref, DocRefKey):
        return (1, ref.document, ref.section, "", 0)
    return (0, ref.usubjid, ref.domain, "", ref.seq if ref.seq is not None else -1)


def dedupe_sorted(refs: Iterable[RecordKey | DocRefKey]) -> list[RecordKey | DocRefKey]:
    """Deterministic, duplicate-free order: record refs by (usubjid, domain, seq), DOC refs last."""
    seen: set = set()
    out: list = []
    for r in refs:
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    out.sort(key=_sort_key)
    return out


def ref_tuple(ref: RecordKey | DocRefKey) -> tuple:
    if isinstance(ref, DocRefKey):
        return ("DOC", ref.document, ref.section)
    return ref.as_tuple()


def ref_dict(ref: RecordKey | DocRefKey) -> dict:
    if isinstance(ref, DocRefKey):
        return {"domain": "DOC", "document": ref.document, "section": ref.section}
    return {"domain": ref.domain, "usubjid": ref.usubjid, "seq": ref.seq}


def subject_of(ref: RecordKey | DocRefKey) -> Optional[str]:
    return ref.usubjid if isinstance(ref, RecordKey) else None
