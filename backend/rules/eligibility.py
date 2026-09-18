"""Eligibility checks against the inclusion/exclusion criteria of the protocol version in force."""
from __future__ import annotations

from typing import Optional

from backend.graph.nodes import EvidenceItem, Finding, Record
from backend.normalization.units import canonical_unit
from backend.normalization.values import parse_number
from backend.rules.common import (
    CREATININE_TESTS,
    HBA1C_TESTS,
    SCREENING_CODE,
    TRANSAMINASE_TESTS,
    protocol_evidence,
    rec_evidence,
    rule_availability,
    rules_of,
    sort_findings,
    visit_code,
)

CODE = "ELIGIBILITY_VIOLATION"


def _screening_labs(core, usubjid: str, tests: tuple[str, ...]) -> list[Record]:
    out = []
    for t in tests:
        for rec in core.idx.tests_for(usubjid, "LB", t):
            if visit_code(rec) == SCREENING_CODE:
                out.append(rec)
    return out


def _mk(core, usubjid, sub, summary, details, evidence, conf=0.9) -> Finding:
    details = dict(details, subcode=sub, protocol_version=core.protocol_version())
    return Finding(code=CODE, usubjid=usubjid, site=core.site_of(usubjid), summary=summary, details=details, evidence=evidence, confidence=conf)


def find_eligibility_violations(core) -> list[Finding]:
    ok, _ = rule_availability(core, CODE)
    if not ok:
        return []
    r = rules_of(core)
    findings: list[Finding] = []

    for usubjid in core.idx.subjects:
        dm = core.dm(usubjid)
        if dm is None:
            continue

        # --- age ---------------------------------------------------------- #
        if r.age_min is not None or r.age_max is not None:
            age = parse_number(dm.get("AGE"))
            if age is not None and ((r.age_min is not None and age < r.age_min) or (r.age_max is not None and age > r.age_max)):
                def chk(dm=dm):
                    a = parse_number(dm.get("AGE"))
                    return a is not None and ((r.age_min is not None and a < r.age_min) or (r.age_max is not None and a > r.age_max))
                findings.append(_mk(core, usubjid, "AGE_OUT_OF_RANGE", f"age {age:g} outside {r.age_min:g}-{r.age_max:g}",
                                    {"age": age, "min": r.age_min, "max": r.age_max},
                                    [rec_evidence(dm, "DM.AGE outside protocol range", chk)] + protocol_evidence(core, "age", "protocol age criterion")))

        # --- HbA1c -------------------------------------------------------- #
        if r.hba1c_min is not None or r.hba1c_max is not None:
            src: Optional[Record] = None
            val: Optional[float] = None
            if "SCR_HBA1C" in dm.fields and dm.get("SCR_HBA1C") is not None:
                val, src = parse_number(dm.get("SCR_HBA1C")), dm
            else:
                labs = _screening_labs(core, usubjid, HBA1C_TESTS)
                if labs:
                    nl = core.lab(labs[0])
                    if nl.parsed.is_numeric:
                        val, src = nl.parsed.value, labs[0]
            if val is not None and src is not None and ((r.hba1c_min is not None and val < r.hba1c_min) or (r.hba1c_max is not None and val > r.hba1c_max)):
                def chk_h(src=src, v=val):
                    return (parse_number(src.get("SCR_HBA1C")) if src.domain == "DM" else core.lab(src).parsed.value) == v
                findings.append(_mk(core, usubjid, "HBA1C_OUT_OF_RANGE", f"screening HbA1c {val:g} outside {r.hba1c_min:g}-{r.hba1c_max:g}",
                                    {"hba1c": val, "min": r.hba1c_min, "max": r.hba1c_max, "source": src.key.as_tuple()},
                                    [rec_evidence(src, "screening HbA1c outside protocol range", chk_h)] + protocol_evidence(core, "hba1c", "protocol HbA1c criterion")))

        # --- hepatic ------------------------------------------------------ #
        if r.hepatic_uln_multiple is not None:
            for rec in _screening_labs(core, usubjid, TRANSAMINASE_TESTS):
                nl = core.lab(rec)
                m = nl.multiple_of_uln()
                if nl.comparable and m is not None and m > r.hepatic_uln_multiple:
                    def chk_l(rec=rec):
                        x = core.lab(rec).multiple_of_uln()
                        return x is not None and x > r.hepatic_uln_multiple
                    findings.append(_mk(core, usubjid, "HEPATIC_SCREENING", f"screening {nl.testcd} {m:.2f}x ULN > {r.hepatic_uln_multiple:g}x",
                                        {"test": nl.testcd, "multiple_of_uln": m, "chain": nl.chain()},
                                        [rec_evidence(rec, f"screening {nl.testcd} > {r.hepatic_uln_multiple:g} x ULN", chk_l)] + protocol_evidence(core, "hepatic", "protocol hepatic exclusion")))

        # --- creatinine (only if the version defines it) ------------------ #
        if r.creatinine_max is not None:
            target_u = canonical_unit(r.creatinine_unit) if r.creatinine_unit else ""
            for rec in _screening_labs(core, usubjid, CREATININE_TESTS):
                nl = core.lab(rec)
                if not nl.parsed.is_numeric:
                    continue
                val, unit = nl.parsed.value, canonical_unit(rec.get("LBORRESU"))
                if target_u and unit and unit != target_u:
                    conv = core.units.convert(nl.testcd, val, unit, target_u)
                    if conv is None:
                        continue  # cannot compare safely
                    val = conv.value
                if val > r.creatinine_max:
                    def chk_c(rec=rec, cap=r.creatinine_max, tu=target_u):
                        n = core.lab(rec)
                        if not n.parsed.is_numeric:
                            return False
                        v, u = n.parsed.value, canonical_unit(rec.get("LBORRESU"))
                        if tu and u and u != tu:
                            c = core.units.convert(n.testcd, v, u, tu)
                            if c is None:
                                return False
                            v = c.value
                        return v > cap
                    findings.append(_mk(core, usubjid, "CREATININE_SCREENING", f"screening creatinine {val:g} > {r.creatinine_max:g} {r.creatinine_unit or ''}",
                                        {"value": val, "max": r.creatinine_max, "unit": r.creatinine_unit},
                                        [rec_evidence(rec, f"screening creatinine > {r.creatinine_max:g}", chk_c)] + protocol_evidence(core, "creatinine", "protocol renal exclusion")))

        # --- pregnancy ---------------------------------------------------- #
        if r.pregnancy_excluded:
            for dom, col in (("MH", "MHTERM"), ("AE", "AETERM")):
                for rec in core.idx.records(usubjid, dom):
                    if "pregnan" in (rec.get(col) or "").lower():
                        def chk_p(rec=rec, col=col):
                            return "pregnan" in (rec.get(col) or "").lower()
                        findings.append(_mk(core, usubjid, "PREGNANCY", f"{dom} term '{rec.get(col)}' indicates pregnancy",
                                            {"term": rec.get(col), "record": rec.key.as_tuple()},
                                            [rec_evidence(rec, "pregnancy term recorded", chk_p)] + protocol_evidence(core, "pregnancy", "protocol pregnancy exclusion"), conf=0.7))
    return sort_findings(findings)
