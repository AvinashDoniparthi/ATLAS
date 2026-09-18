"""Resolve which protocol version / lab manual is in force at a data cut."""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.ingestion.document_loader import Document, InstructionLike, load_documents, split_sentences
from backend.protocol.protocol_loader import DocRef, ProtocolRules
from backend.protocol.protocol_versions import ProtocolRegistry

log = logging.getLogger(__name__)


@dataclass
class CutInfo:
    cut: int
    protocol_version: int
    new_records: Optional[int]
    corrections: Optional[int]


def _to_int(v) -> Optional[int]:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return None


def load_cuts(data_dir: str | Path) -> list[CutInfo]:
    path = Path(data_dir) / "data" / "cuts.csv"
    cuts: list[CutInfo] = []
    if not path.is_file():
        log.warning("cuts.csv not found at %s", path)
        return cuts
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                cut, ver = _to_int(row.get("cut")), _to_int(row.get("protocol_version"))
                if cut is None or ver is None:
                    log.warning("skipping malformed cuts.csv row: %s", row)
                    continue
                cuts.append(CutInfo(cut=cut, protocol_version=ver,
                                    new_records=_to_int(row.get("new_records")),
                                    corrections=_to_int(row.get("corrections"))))
    except Exception as exc:  # noqa: BLE001
        log.warning("failed reading cuts.csv: %s", exc)
    cuts.sort(key=lambda c: c.cut)
    return cuts


def protocol_version_for_cut(cuts: list[CutInfo], cut: Optional[int]) -> Optional[int]:
    """Version of the highest listed cut ≤ ``cut``; None cut → latest listed;
    cut below the first listed → first listed version; no cuts → None."""
    if not cuts:
        return None
    ordered = sorted(cuts, key=lambda c: c.cut)
    if cut is None:
        return ordered[-1].protocol_version
    eligible = [c for c in ordered if c.cut <= cut]
    if not eligible:
        return ordered[0].protocol_version
    return eligible[-1].protocol_version


_UNIT_STATEMENT = re.compile(r"\b(reports?|reported|analys(?:ed|is)|analyz(?:ed|is)|measured)\b[^.]*\bin\b[^.]*?(?:\b[A-Za-z\u00b5\u03bc]+/[A-Za-z]+\b|%)", re.I)
_UNIT_FACT = re.compile(r"\b1\s*[A-Za-zµμ]+/[A-Za-z]+\s*(?:=|equals)\s*\d+(?:[.,]\d+)?\s*[A-Za-zµμ]+/[A-Za-z]+", re.I)


class RuleResolver:
    def __init__(self, data_dir: str | Path, documents: Optional[dict[str, Document]] = None):
        self.data_dir = Path(data_dir)
        self.all_documents: dict[str, Document] = documents if documents is not None else load_documents(self.data_dir)
        self.registry = ProtocolRegistry(self.all_documents)
        self.cuts: list[CutInfo] = load_cuts(self.data_dir)

    def version_for_cut(self, cut: Optional[int]) -> Optional[int]:
        version = protocol_version_for_cut(self.cuts, cut)
        if version is None:
            latest = self.registry.latest()
            version = latest.version if latest else None
        return version

    def rules_for_cut(self, cut: Optional[int]) -> Optional[ProtocolRules]:
        version = self.version_for_cut(cut)
        rules = self.registry.get(version)
        if rules is None and version is not None:
            # cuts.csv names a version we have no document for: use the closest lower one
            lower = [v for v in self.registry.versions() if v <= version]
            if lower:
                log.warning("no protocol document for version %s; using version %s", version, lower[-1])
                rules = self.registry.get(lower[-1])
        return rules

    def lab_manual_for_cut(self, cut: Optional[int]) -> Optional[Document]:
        return self.registry.lab_manual_for(self.version_for_cut(cut))

    def unit_facts(self, version: Optional[int]) -> list[tuple[str, DocRef]]:
        """Sentences in the applicable lab manual stating unit conversions/units."""
        manual = self.registry.lab_manual_for(version)
        if manual is None:
            return []
        out: list[tuple[str, DocRef]] = []
        seen: set[str] = set()
        for sec in manual.sections.values():
            if sec.key == "body":
                continue
            for sent in split_sentences(sec.text):
                if sent in seen:
                    continue
                if _UNIT_FACT.search(sent) or _UNIT_STATEMENT.search(sent):
                    seen.add(sent)
                    out.append((sent, DocRef(document=manual.name, section=sec.key, sentence=sent)))
        return out

    def instruction_like_sentences(self, version: Optional[int] = None) -> list[InstructionLike]:
        """All instruction-like sentences across documents (reported as facts only).
        If ``version`` is given, only the lab manual applicable to that version is
        included alongside every protocol/SAP document."""
        out: list[InstructionLike] = []
        applicable_manual = self.registry.lab_manual_for(version) if version is not None else None
        for doc in self.all_documents.values():
            if version is not None and doc.kind == "lab_manual" and applicable_manual is not None and doc.name != applicable_manual.name:
                continue
            out.extend(doc.instruction_like)
        return out
