"""Registry of parsed protocol versions and the lab manual applicable to each."""
from __future__ import annotations

import logging
from dataclasses import fields
from typing import Optional

from backend.ingestion.document_loader import Document
from backend.protocol.protocol_loader import ProtocolRules, parse_protocol

log = logging.getLogger(__name__)

_COMPARABLE = [
    "age_min", "age_max", "hba1c_min", "hba1c_max", "hepatic_uln_multiple",
    "creatinine_max", "creatinine_unit", "pregnancy_excluded", "visit_schedule",
    "visit_window_days", "prohibited_classes", "sae_hosp_flag_rule", "sae_criteria",
    "hys_transaminase_multiple", "hys_bili_multiple", "hys_window_days",
    "expected_dose", "dose_unit",
]


class ProtocolRegistry:
    def __init__(self, documents: dict[str, Document]):
        self._documents = documents or {}
        self._rules: dict[int, ProtocolRules] = {}
        self._lab_manuals: list[Document] = []
        for doc in self._documents.values():
            if doc.kind == "protocol":
                rules = parse_protocol(doc)
                if doc.version is None:
                    log.warning("protocol %s has no version; registered as version 0", doc.name)
                self._rules[rules.version] = rules
            elif doc.kind == "lab_manual":
                self._lab_manuals.append(doc)

    def versions(self) -> list[int]:
        return sorted(self._rules)

    def get(self, version: Optional[int]) -> Optional[ProtocolRules]:
        if version is None:
            return None
        return self._rules.get(int(version))

    def latest(self) -> Optional[ProtocolRules]:
        return self._rules[max(self._rules)] if self._rules else None

    def diff(self, v_a: int, v_b: int) -> dict[str, tuple]:
        a, b = self.get(v_a), self.get(v_b)
        if a is None or b is None:
            return {}
        out: dict[str, tuple] = {}
        for name in _COMPARABLE:
            va, vb = getattr(a, name), getattr(b, name)
            if isinstance(va, list) and isinstance(vb, list):
                if sorted(map(str, va)) != sorted(map(str, vb)):
                    out[name] = (va, vb)
            elif va != vb:
                out[name] = (va, vb)
        return out

    def lab_manuals(self) -> list[Document]:
        return list(self._lab_manuals)

    def lab_manual_for(self, version: Optional[int]) -> Optional[Document]:
        """Lab manual with the highest version ≤ requested; else the unversioned one;
        else the highest-versioned one available."""
        if not self._lab_manuals:
            return None
        versioned = [d for d in self._lab_manuals if d.version is not None]
        unversioned = [d for d in self._lab_manuals if d.version is None]
        if version is not None:
            eligible = [d for d in versioned if d.version <= int(version)]
            if eligible:
                return max(eligible, key=lambda d: d.version)
        if unversioned:
            return unversioned[0]
        return max(versioned, key=lambda d: d.version) if versioned else None

    @property
    def documents(self) -> dict[str, Document]:
        return self._documents

    @staticmethod
    def comparable_fields() -> list[str]:
        return [f.name for f in fields(ProtocolRules) if f.name in _COMPARABLE]
