"""Shared helpers for rule tests: real-data cores and a tiny synthetic core."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Optional

from backend.graph.indexes import Indexes
from backend.graph.nodes import Record
from backend.graph.reference_ranges import ReferenceRange, ReferenceRangeIndex, normalise_lab
from backend.graph.study_graph import StudyGraphCore
from backend.normalization.units import UnitRegistry
from backend.protocol.protocol_loader import ProtocolRules

logging.getLogger("atlas").setLevel(logging.ERROR)


def real_core(data_dir, cut: Optional[int] = None) -> StudyGraphCore:
    core = StudyGraphCore(str(data_dir))
    core.build(cut)
    return core


def check_all_evidence(core, findings) -> None:
    """Every cited record exists in the cut view and every check() re-verifies."""
    for f in findings:
        assert f.evidence, f"{f.code} {f.usubjid} has no evidence"
        for e in f.evidence:
            if e.is_doc:
                assert e.ref.document in core.documents, e.ref
            else:
                assert core.exists(e.ref), (f.code, f.usubjid, e.ref)
            if e.check is not None:
                assert e.check() is True, (f.code, f.usubjid, e.ref, e.why)


def synthetic_core(records: dict[str, list[dict]], rules: ProtocolRules, ranges: list[tuple] = ()):
    """Build a minimal core-like object from raw row dicts (per domain).

    ``ranges`` items: (testcd, unit, low, high, lab).
    """
    domains: dict[str, list[Record]] = {}
    headers: dict[str, list[str]] = {}
    for dom, rows in records.items():
        recs = []
        for i, row in enumerate(rows, start=2):
            seq_col = f"{dom}SEQ"
            seq = int(row[seq_col]) if seq_col in row else None
            recs.append(Record(domain=dom, usubjid=row["USUBJID"], seq=seq, fields=dict(row), cut_available=1,
                               corrected_at_cut=None, row_no=i))
        domains[dom] = recs
        headers[dom] = list(rows[0].keys()) if rows else ["USUBJID"]
    idx = Indexes()
    for dom, recs in domains.items():
        idx.add_domain(dom, recs, headers[dom])
    idx.finalize()
    rr = ReferenceRangeIndex()
    for i, (t, u, lo, hi, lab) in enumerate(ranges, start=2):
        rr.add(ReferenceRange(testcd=t, unit=u.lower(), unit_raw=u, low=lo, high=hi, lab=lab, row_no=i))
    rr.loaded = bool(ranges)
    units = UnitRegistry()
    cache: dict = {}

    def lab(rec):
        k = rec.key.as_tuple()
        if k not in cache:
            cache[k] = normalise_lab(rec, idx.site_of(rec.usubjid), rr, units)
        return cache[k]

    def dm(u):
        r = idx.records(u, "DM")
        return r[0] if r else None

    derived: dict = {}
    core = SimpleNamespace(
        idx=idx, rules=rules, ref_ranges=rr, units=units, lab=lab, dm=dm,
        site_of=lambda u: idx.site_of(u), exists=lambda k: k.as_tuple() in idx.by_ref,
        protocol_version=lambda: rules.version, cut=lambda: None, resolver=None, documents={},
        persons={}, person_of={}, duplicate_pairs=[], load_reports={},
        derived=lambda name, fn: derived.setdefault(name, fn()),
    )
    return core


def basic_rules(**over) -> ProtocolRules:
    r = ProtocolRules(version=9, document="protocol_v9")
    r.hys_transaminase_multiple = 3.0
    r.hys_bili_multiple = 2.0
    r.hys_window_days = 14
    r.sae_hosp_flag_rule = True
    r.expected_dose = {"DRUG": 10.0, "PLACEBO": 0.0}
    r.dose_unit = "mg"
    r.visit_schedule = {"SCREENING": -14, "BASELINE": 0, "WEEK2": 14, "WEEK4": 28}
    r.visit_window_days = 3
    r.prohibited_classes = ["SYSTEMIC_GLUCOCORTICOID"]
    r.age_min, r.age_max = 18.0, 75.0
    for k, v in over.items():
        setattr(r, k, v)
    return r
