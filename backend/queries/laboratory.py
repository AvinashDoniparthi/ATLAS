"""Laboratory (LB) queries — always unit- and range-aware via ``core.lab()``."""
from __future__ import annotations

from typing import Optional

from backend.graph.nodes import EvidenceItem, Record
from backend.graph.reference_ranges import NormalisedLab


def lab_records(core, usubjid: str, testcd: Optional[str] = None) -> list[Record]:
    if testcd:
        return core.idx.tests_for(usubjid, "LB", testcd)
    return core.idx.records(usubjid, "LB")


def lab_chain(core, rec: Record) -> dict:
    nl: NormalisedLab = core.lab(rec)
    d = nl.chain()
    d["flag"] = nl.flag()
    d["visit"] = rec.get("VISIT")
    dt = core.idx.record_date(rec)
    d["date"] = dt.isoformat() if dt else rec.get("LBDTC")
    # value expressed in the central laboratory unit for display, when different
    central = core.ref_ranges.central_lab()
    if nl.comparable and central and nl.range and nl.range.lab.upper() != central:
        cands = core.ref_ranges.candidates(nl.testcd, central)
        if cands:
            conv = core.units.convert(nl.testcd, nl.value, nl.unit, cands[0].unit)
            if conv is not None:
                d["central_equivalent"] = {"value": conv.value, "unit": cands[0].unit_raw, "factor": conv.factor,
                                           "source": conv.source}
    return d


def lab_values(core, usubjid: str, testcd: Optional[str] = None, abnormal_only: bool = False) -> list[dict]:
    out = []
    for rec in lab_records(core, usubjid, testcd):
        ch = lab_chain(core, rec)
        if abnormal_only and ch["flag"] not in ("high", "low"):
            continue
        out.append(ch)
    return out


def abnormal_labs(core, testcd: str, multiple: Optional[float] = None, site: Optional[str] = None,
                  direction: str = "high") -> list[tuple[Record, NormalisedLab]]:
    """Records for ``testcd`` above ULN (or above ``multiple`` × ULN), optionally at one site."""
    out = []
    for rec in core.idx.by_test.get(("LB", testcd.upper()), []):
        if site and (core.site_of(rec.usubjid) or "").upper() != site.upper():
            continue
        nl = core.lab(rec)
        if not nl.comparable:
            continue
        if direction == "high":
            if multiple is not None:
                m = nl.multiple_of_uln()
                if m is None or m <= multiple:
                    continue
            elif nl.flag() != "high":
                continue
        elif direction == "low" and nl.flag() != "low":
            continue
        out.append((rec, nl))
    out.sort(key=lambda t: (t[0].usubjid, t[0].seq or 0))
    return out


def lab_evidence(core, rec: Record, why: str, role: str = "support") -> EvidenceItem:
    """Evidence item whose check re-verifies that the record is comparable and abnormal-high."""
    return EvidenceItem(ref=rec.key, why=why, role=role, check=lambda r=rec: core.lab(r).flag() == "high")
