"""Reconstruct the study as it was known at a given cut.

``CutView.build(raw_domains, corrections, cut)`` keeps records with
``cut_available <= cut`` (all records when ``cut`` is None) and applies every
correction whose cut is <= the selected cut. The raw records are never mutated:
each kept record is copied and corrections land in ``corrected_fields``.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Optional

from backend.cuts.corrections import Correction, corrections_in_force
from backend.graph.nodes import Record

log = logging.getLogger("atlas.cuts")


@dataclass
class CutView:
    cut: Optional[int]
    effective_cut: Optional[int]
    domains: dict[str, list[Record]] = field(default_factory=dict)
    excluded_by_cut: dict[str, int] = field(default_factory=dict)
    corrections_applied: int = 0
    corrections_pending: int = 0
    corrections_unmatched: list[dict] = field(default_factory=list)

    @property
    def record_count(self) -> int:
        return sum(len(v) for v in self.domains.values())


def max_cut_available(raw_domains: dict[str, list[Record]]) -> Optional[int]:
    m: Optional[int] = None
    for recs in raw_domains.values():
        for r in recs:
            if r.cut_available is not None and (m is None or r.cut_available > m):
                m = r.cut_available
    return m


def build_cut_view(
    raw_domains: dict[str, list[Record]],
    corrections: list[Correction],
    cut: Optional[int],
) -> CutView:
    effective = cut if cut is not None else max_cut_available(raw_domains)
    view = CutView(cut=cut, effective_cut=effective)
    in_force = corrections_in_force(corrections, cut)
    matched_keys: set[tuple] = set()

    for domain, recs in raw_domains.items():
        kept: list[Record] = []
        excluded = 0
        for r in recs:
            if cut is not None and r.cut_available is not None and r.cut_available > cut:
                excluded += 1
                continue
            rec = copy.copy(r)
            rec.fields = r.fields  # raw dict shared read-only; corrections go elsewhere
            rec.corrected_fields = {}
            rec.corrections = []
            rec.issues = list(r.issues)
            key = (rec.domain, rec.usubjid, rec.seq)
            if key in in_force:
                for c in in_force[key]:
                    if c.field not in rec.fields:
                        rec.issues.append(f"correction_field_unknown:{c.field}")
                    rec.corrected_fields[c.field] = c.new_value
                    rec.corrections.append(
                        {"cut": c.cut, "field": c.field, "old": c.old_value, "new": c.new_value, "reason": c.reason}
                    )
                    view.corrections_applied += 1
                matched_keys.add(key)
            kept.append(rec)
        view.domains[domain] = kept
        view.excluded_by_cut[domain] = excluded

    for key, lst in in_force.items():
        if key not in matched_keys:
            view.corrections_unmatched.append({"key": key, "count": len(lst)})
    view.corrections_pending = sum(1 for c in corrections if cut is not None and c.cut > cut)
    if view.corrections_unmatched:
        log.warning("%d corrections reference records not in this cut view", len(view.corrections_unmatched))
    return view
