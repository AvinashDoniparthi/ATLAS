"""Reference-range engine and unit-aware laboratory normalisation.

``reference_ranges.csv`` is authoritative. Ranges are indexed by
(test code, laboratory); the laboratory for a record is the subject's site when
a site-specific range exists for that test, otherwise the central laboratory.
A value is compared only after it has been expressed in the range's unit; if no
conversion or no range exists the result is ``unknown`` — never a silent normal.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from backend.graph.nodes import Record
from backend.ingestion.csv_loader import load_table
from backend.normalization.units import Conversion, UnitRegistry, canonical_unit
from backend.normalization.values import LabValue, parse_lab_value, parse_number

log = logging.getLogger("atlas.labs")

CENTRAL_LAB_TOKENS = {"CENTRAL", "CENTRAL LAB", "CENTRAL_LAB", "CENTRALLAB"}


@dataclass(frozen=True)
class ReferenceRange:
    testcd: str
    unit: str  # canonical
    unit_raw: str
    low: Optional[float]
    high: Optional[float]
    lab: str
    row_no: int

    @property
    def is_central(self) -> bool:
        return self.lab.upper() in CENTRAL_LAB_TOKENS

    def as_dict(self) -> dict[str, Any]:
        return {"LBTESTCD": self.testcd, "UNIT": self.unit_raw, "LOW": self.low, "HIGH": self.high, "LAB": self.lab}


@dataclass
class ReferenceRangeIndex:
    ranges: list[ReferenceRange] = field(default_factory=list)
    by_test_lab: dict[tuple[str, str], list[ReferenceRange]] = field(default_factory=dict)
    labs: set[str] = field(default_factory=set)
    loaded: bool = False

    def add(self, rr: ReferenceRange) -> None:
        self.ranges.append(rr)
        self.by_test_lab.setdefault((rr.testcd, rr.lab.upper()), []).append(rr)
        self.labs.add(rr.lab.upper())

    def central_lab(self) -> Optional[str]:
        for lab in self.labs:
            if lab in CENTRAL_LAB_TOKENS:
                return lab
        return None

    def lab_for(self, testcd: str, site: Optional[str]) -> Optional[str]:
        """Site-specific laboratory if a range exists for (test, site), else central."""
        t = testcd.upper()
        if site and (t, site.upper()) in self.by_test_lab:
            return site.upper()
        c = self.central_lab()
        if c and (t, c) in self.by_test_lab:
            return c
        return None

    def candidates(self, testcd: str, lab: str) -> list[ReferenceRange]:
        return self.by_test_lab.get((testcd.upper(), lab.upper()), [])

    def tests(self) -> list[str]:
        return sorted({r.testcd for r in self.ranges})


def load_reference_ranges(data_dir: str | Path) -> ReferenceRangeIndex:
    idx = ReferenceRangeIndex()
    rows, report = load_table(Path(data_dir) / "data" / "reference_ranges.csv")
    if report.missing:
        log.warning("reference_ranges.csv missing — abnormality checks will be 'unknown'")
        return idx
    idx.loaded = True
    for i, r in enumerate(rows, start=2):
        testcd = (r.get("LBTESTCD") or r.get("TESTCD") or "").strip().upper()
        unit_raw = (r.get("UNIT") or r.get("LBORRESU") or "").strip()
        lab = (r.get("LAB") or "").strip() or "CENTRAL"
        if not testcd:
            continue
        idx.add(
            ReferenceRange(
                testcd=testcd,
                unit=canonical_unit(unit_raw),
                unit_raw=unit_raw,
                low=parse_number(r.get("LOW")),
                high=parse_number(r.get("HIGH")),
                lab=lab,
                row_no=i,
            )
        )
    return idx


@dataclass
class NormalisedLab:
    """Full evidence chain for one laboratory value."""

    record: Record
    testcd: str
    parsed: LabValue
    orig_unit: str
    lab: Optional[str]
    range: Optional[ReferenceRange]
    value: Optional[float]  # in range unit (or orig unit when no range)
    unit: str
    conversion: Optional[Conversion]
    status: str  # ok | censored | not_detected | missing | non_numeric | no_range | no_conversion
    notes: list[str] = field(default_factory=list)

    @property
    def comparable(self) -> bool:
        return self.status == "ok" and self.value is not None and self.range is not None

    @property
    def uln(self) -> Optional[float]:
        return self.range.high if self.range else None

    @property
    def lln(self) -> Optional[float]:
        return self.range.low if self.range else None

    def multiple_of_uln(self) -> Optional[float]:
        if not self.comparable or not self.uln:
            return None
        return self.value / self.uln

    def flag(self) -> str:
        """'high' | 'low' | 'normal' | 'unknown'."""
        if not self.comparable:
            return "unknown"
        if self.uln is not None and self.value > self.uln:
            return "high"
        if self.lln is not None and self.value < self.lln:
            return "low"
        return "normal"

    def chain(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "record": self.record.key.as_tuple(),
            "test": self.testcd,
            "original_value": self.parsed.raw,
            "original_unit": self.orig_unit,
            "parsed_kind": self.parsed.kind,
            "lab": self.lab,
            "status": self.status,
        }
        if self.conversion:
            d["conversion"] = {
                "factor": self.conversion.factor,
                "from": self.conversion.from_unit,
                "to": self.conversion.to_unit,
                "source": self.conversion.source,
            }
        d["normalized_value"] = self.value
        d["normalized_unit"] = self.unit
        if self.range:
            d["reference_range"] = self.range.as_dict()
            d["uln"] = self.uln
            d["multiple_of_uln"] = self.multiple_of_uln()
        if self.record.corrections:
            d["corrections"] = list(self.record.corrections)
        if self.notes:
            d["notes"] = list(self.notes)
        return d


def normalise_lab(
    record: Record,
    site: Optional[str],
    ranges: ReferenceRangeIndex,
    units: UnitRegistry,
    test_col: str = "LBTESTCD",
    value_col: str = "LBORRES",
    unit_col: str = "LBORRESU",
) -> NormalisedLab:
    testcd = (record.get(test_col) or "").upper()
    parsed = parse_lab_value(record.get(value_col))
    orig_unit_raw = record.get(unit_col) or ""
    orig_unit = canonical_unit(orig_unit_raw)
    lab = ranges.lab_for(testcd, site) if testcd else None
    notes: list[str] = []
    rng: Optional[ReferenceRange] = None
    conversion: Optional[Conversion] = None
    value: Optional[float] = None
    unit = orig_unit

    if parsed.kind != "numeric":
        return NormalisedLab(record, testcd, parsed, orig_unit_raw, lab, None, None, orig_unit, None, parsed.kind, notes)
    if parsed.note:
        notes.append(parsed.note)
    value = parsed.value

    if lab is None:
        return NormalisedLab(record, testcd, parsed, orig_unit_raw, None, None, value, orig_unit, None, "no_range", notes)

    cands = ranges.candidates(testcd, lab)
    same_unit = [c for c in cands if c.unit == orig_unit]
    if same_unit:
        rng = same_unit[0]
        conversion = None
    else:
        for c in cands:
            conv = units.convert(testcd, value, orig_unit, c.unit) if orig_unit else None
            if conv is not None:
                rng = c
                conversion = conv
                value = conv.value
                unit = c.unit
                break
        if rng is None:
            if not orig_unit:
                notes.append("unit_missing")
            return NormalisedLab(record, testcd, parsed, orig_unit_raw, lab, None, parsed.value, orig_unit, None, "no_conversion", notes)
    return NormalisedLab(record, testcd, parsed, orig_unit_raw, lab, rng, value, unit, conversion, "ok", notes)
