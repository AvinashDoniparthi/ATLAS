"""Potential Hy's law screen — deterministic, unit-aware, protocol-parameterised.

Rule (read from the protocol in force): a transaminase (ALT/AST) > N x ULN and
total bilirubin > M x ULN within W days. N, M and W come from
``core.rules``. Values are normalised against the laboratory-specific reference
range through ``core.lab()``; censored ("<5"), not-detected, missing and
unparseable-date records never qualify and are counted in ``details.skipped``.

Instruction-like sentences in the lab manual (e.g. "exclude site X") are never
applied here — they are evidence, not rules.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.graph.nodes import Finding, Record
from backend.graph.reference_ranges import NormalisedLab
from backend.rules.common import (
    BASELINE_CODE,
    BILIRUBIN_TESTS,
    SCREENING_CODE,
    TRANSAMINASE_TESTS,
    lab_manual_units_ref,
    protocol_evidence,
    rec_evidence,
    rule_availability,
    rules_of,
    sort_findings,
    visit_code,
)
from backend.graph.nodes import EvidenceItem

CODE = "HYS_LAW_CANDIDATE"


def _central_display(core, nl: NormalisedLab) -> Optional[dict[str, Any]]:
    """Value expressed in the central laboratory's unit for the same test, when different."""
    central = core.ref_ranges.central_lab()
    if central is None or nl.range is None or nl.value is None:
        return None
    cands = core.ref_ranges.candidates(nl.testcd, central)
    if not cands:
        return None
    target = cands[0]
    if target.unit == nl.unit:
        return None
    conv = core.units.convert(nl.testcd, nl.value, nl.unit, target.unit)
    if conv is None:
        return None
    return {
        "value": conv.value,
        "unit": target.unit_raw,
        "factor": conv.factor,
        "source": conv.source,
        "central_uln": target.high,
        "central_multiple": (conv.value / target.high) if target.high else None,
    }


def _lab_detail(core, nl: NormalisedLab) -> dict[str, Any]:
    d = {
        "key": nl.record.key.as_tuple(),
        "test": nl.testcd,
        "visit": nl.record.get("VISIT"),
        "date": str(core.idx.record_date(nl.record)),
        "value": nl.value,
        "unit": nl.range.unit_raw if nl.range else nl.unit,
        "original_value": nl.parsed.raw,
        "original_unit": nl.orig_unit,
        "lab": nl.lab,
        "uln": nl.uln,
        "multiple_of_uln": nl.multiple_of_uln(),
    }
    if nl.conversion:
        d["conversion"] = {"factor": nl.conversion.factor, "from": nl.conversion.from_unit,
                           "to": nl.conversion.to_unit, "source": nl.conversion.source}
    disp = _central_display(core, nl)
    if disp:
        d["converted_display"] = disp
    if nl.record.corrections:
        d["corrections"] = list(nl.record.corrections)
    return d


def find_hys_law(core) -> list[Finding]:
    ok, _ = rule_availability(core, CODE)
    if not ok:
        return []
    r = rules_of(core)
    t_mult: float = r.hys_transaminase_multiple
    b_mult: float = r.hys_bili_multiple
    window: int = r.hys_window_days
    idx = core.idx
    findings: list[Finding] = []

    for usubjid in idx.subjects:
        skipped: dict[str, int] = {}
        tx: list[NormalisedLab] = []
        bili: list[NormalisedLab] = []
        baseline_tx: list[NormalisedLab] = []

        for rec in idx.records(usubjid, "LB"):
            test = (rec.get("LBTESTCD") or "").upper()
            if test not in TRANSAMINASE_TESTS and test not in BILIRUBIN_TESTS:
                continue
            nl = core.lab(rec)
            if not nl.comparable:
                skipped[nl.status] = skipped.get(nl.status, 0) + 1
                continue
            if idx.record_date(rec) is None:
                skipped["unparseable_date"] = skipped.get("unparseable_date", 0) + 1
                continue
            mult = nl.multiple_of_uln()
            if mult is None:
                skipped["no_uln"] = skipped.get("no_uln", 0) + 1
                continue
            if test in TRANSAMINASE_TESTS:
                if visit_code(rec) in (SCREENING_CODE, BASELINE_CODE) and mult > t_mult:
                    baseline_tx.append(nl)
                if mult > t_mult:
                    tx.append(nl)
            elif mult > b_mult:
                bili.append(nl)

        if not tx or not bili:
            continue

        pairs: list[dict[str, Any]] = []
        support: list[EvidenceItem] = []
        cited: set[tuple] = set()
        conversion_used = False
        for t in tx:
            td = idx.record_date(t.record)
            for b in bili:
                bd = idx.record_date(b.record)
                days = abs((td - bd).days)
                if days > window:
                    continue
                pairs.append({"transaminase": _lab_detail(core, t), "bilirubin": _lab_detail(core, b), "days_apart": days})
                for nl, mult in ((t, t_mult), (b, b_mult)):
                    k = nl.record.key.as_tuple()
                    if k in cited:
                        continue
                    cited.add(k)
                    if nl.conversion is not None or (nl.range is not None and not nl.range.is_central):
                        conversion_used = True  # non-central unit/range: cite the lab manual unit statement
                    support.append(_support(core, nl.record, nl.testcd, mult))
        if not pairs:
            continue

        flags: list[str] = []
        if baseline_tx:
            flags.append("baseline_elevated")
        if any("cholesta" in (m.get("MHTERM") or "").lower() for m in idx.records(usubjid, "MH")):
            flags.append("cholestasis_history")

        evidence = list(support)
        evidence += protocol_evidence(core, "hys_law", "protocol Hy's law definition (multiples and window)")
        if conversion_used:
            ref = lab_manual_units_ref(core)
            if ref is not None:
                evidence.append(EvidenceItem(ref=ref, why="laboratory unit statement (site-specific units / conversion)", check=lambda: True, role="context"))
        first = pairs[0]
        summary = (
            f"{first['transaminase']['test']} {first['transaminase']['multiple_of_uln']:.2f}x ULN and bilirubin "
            f"{first['bilirubin']['multiple_of_uln']:.2f}x ULN {first['days_apart']} day(s) apart"
            f" ({first['transaminase']['visit']})"
        )
        findings.append(
            Finding(
                code=CODE,
                usubjid=usubjid,
                site=core.site_of(usubjid),
                summary=summary,
                details={
                    "pairs": pairs,
                    "transaminase_multiple": t_mult,
                    "bilirubin_multiple": b_mult,
                    "window_days": window,
                    "skipped": skipped,
                    "flags": list(flags),
                    "protocol_version": core.protocol_version(),
                },
                evidence=evidence,
                confidence=0.7 if flags else 0.95,
                status="uncertain" if flags else "confirmed",
                flags=flags,
            )
        )
    return sort_findings(findings)


def _support(core, rec: Record, test: str, mult: float) -> EvidenceItem:
    def check(rec=rec, mult=mult):
        nl = core.lab(rec)
        m = nl.multiple_of_uln()
        return bool(nl.comparable and m is not None and m > mult)

    return rec_evidence(rec, f"{test} > {mult} x ULN", check, role="support")
