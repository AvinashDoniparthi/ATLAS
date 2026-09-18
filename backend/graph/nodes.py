"""Core domain model shared by every backend layer.

Nothing in here knows about the starter schemas; ``stage1/atlas.py`` converts
these objects to the official ``Answer``/``RecordRef`` at the boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class RecordKey:
    """The evidence identity: (domain, USUBJID, <DOMAIN>SEQ)."""

    domain: str
    usubjid: str
    seq: Optional[int]

    def as_tuple(self) -> tuple[str, str, Optional[int]]:
        return (self.domain, self.usubjid, self.seq)

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.domain}/{self.usubjid}/{self.seq}"


@dataclass
class Record:
    """One row of one clinical domain, with raw values preserved.

    ``fields`` holds the values as loaded (strings). ``corrected_fields`` holds
    the values after cut-aware corrections; ``get()`` prefers the corrected value.
    """

    domain: str
    usubjid: str
    seq: Optional[int]
    fields: dict[str, str]
    cut_available: Optional[int]
    corrected_at_cut: Optional[int]
    row_no: int
    issues: list[str] = field(default_factory=list)
    corrected_fields: dict[str, str] = field(default_factory=dict)
    corrections: list[dict[str, Any]] = field(default_factory=list)

    @property
    def key(self) -> RecordKey:
        return RecordKey(self.domain, self.usubjid, self.seq)

    @property
    def site(self) -> Optional[str]:
        return site_from_usubjid(self.usubjid, self.fields.get("SITEID"))

    def get(self, name: str, default: Optional[str] = None) -> Optional[str]:
        if name in self.corrected_fields:
            return self.corrected_fields[name]
        v = self.fields.get(name)
        if v is None or v == "":
            return default
        return v

    def raw(self, name: str) -> Optional[str]:
        return self.fields.get(name)

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.fields)
        d.update(self.corrected_fields)
        d["_domain"] = self.domain
        d["_seq"] = self.seq
        d["_cut_available"] = self.cut_available
        d["_corrected"] = bool(self.corrections)
        if self.issues:
            d["_issues"] = list(self.issues)
        return d


def site_from_usubjid(usubjid: Optional[str], explicit: Optional[str] = None) -> Optional[str]:
    """Site id from an explicit SITEID column, else from the middle token of USUBJID."""
    if explicit:
        return explicit.strip()
    if not usubjid:
        return None
    parts = usubjid.split("-")
    if len(parts) >= 3:
        return parts[1]
    return None


@dataclass(frozen=True)
class DocRefKey:
    """Citation of a document section."""

    document: str
    section: str


@dataclass
class EvidenceItem:
    """A citation plus a re-checkable justification.

    ``check`` is a zero-argument callable returning True when the cited record
    still supports the claim; the evidence validator re-evaluates it before an
    answer is emitted so a stale or wrong citation is dropped, never returned.
    """

    ref: RecordKey | DocRefKey
    why: str
    check: Optional[Callable[[], bool]] = None
    role: str = "support"  # support | context | rule

    @property
    def is_doc(self) -> bool:
        return isinstance(self.ref, DocRefKey)


@dataclass
class Finding:
    code: str
    usubjid: Optional[str]
    site: Optional[str]
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    evidence: list[EvidenceItem] = field(default_factory=list)
    confidence: float = 0.9
    status: str = "confirmed"  # confirmed | uncertain
    flags: list[str] = field(default_factory=list)

    def record_refs(self) -> list[RecordKey]:
        return [e.ref for e in self.evidence if isinstance(e.ref, RecordKey)]
