"""Dosing rules: wrong dose (EX vs protocol expectation per arm) and missing dose records."""
from __future__ import annotations

import re
from typing import Any, Optional

from backend.graph.nodes import EvidenceItem, Finding, Record
from backend.normalization.units import canonical_unit
from backend.normalization.values import parse_number
from backend.protocol.protocol_loader import normalize_visit_code
from backend.rules.common import arm_of, protocol_evidence, rec_evidence, rule_availability, rules_of, sort_findings

CODE_WRONG = "DOSING_ERROR"
CODE_MISSING = "MISSING_DOSE"
_PLACEBO_HINT = "PLACEBO"


def _placebo_like(token: str) -> bool:
    return _PLACEBO_HINT in token.upper()


def arm_key(rules, arm: Optional[str]) -> Optional[str]:
    """Map a subject's ARM label to a key of ``rules.expected_dose`` (generic, no study names)."""
    if not rules or not rules.expected_dose or not arm:
        return None
    keys = list(rules.expected_dose)
    a = arm.strip().upper()
    for k in keys:
        if k.upper() == a:
            return k
    tokens = set(re.split(r"[^A-Z0-9]+", a)) - {""}
    for k in keys:
        ku = k.upper()
        if ku in tokens or (len(ku) >= 3 and ku in a) or (len(a) >= 3 and a in ku):
            return k
    if _placebo_like(a):
        pl = [k for k in keys if _placebo_like(k)]
        return pl[0] if len(pl) == 1 else None
    non_pl = [k for k in keys if not _placebo_like(k)]
    return non_pl[0] if len(non_pl) == 1 else None


def expected_dose_for(core, usubjid: str) -> tuple[Optional[float], Optional[str]]:
    r = rules_of(core)
    key = arm_key(r, arm_of(core, usubjid))
    if key is None:
        return None, None
    return r.expected_dose.get(key), key


def _dose_mismatch(core, rec: Record) -> Optional[dict[str, Any]]:
    expected, key = expected_dose_for(core, rec.usubjid)
    if expected is None:
        return None
    actual = parse_number(rec.get("EXDOSE"))
    if actual is None:
        return None
    if abs(actual - expected) <= 1e-9:
        return None
    return {"record": rec.key.as_tuple(), "visit": rec.get("VISIT"), "actual": actual, "expected": expected, "arm_key": key}


def find_dosing_errors(core) -> list[Finding]:
    ok, _ = rule_availability(core, CODE_WRONG)
    if not ok:
        return []
    r = rules_of(core)
    dose_unit = canonical_unit(r.dose_unit) if r.dose_unit else ""
    findings: list[Finding] = []
    for usubjid in core.idx.subjects:
        recs = core.idx.records(usubjid, "EX")
        if not recs:
            continue
        wrong: list[dict[str, Any]] = []
        evidence: list[EvidenceItem] = []
        unit_flags: list[str] = []
        unparseable = 0
        for rec in recs:
            if parse_number(rec.get("EXDOSE")) is None and rec.get("EXDOSE") is not None:
                unparseable += 1
            mm = _dose_mismatch(core, rec)
            if mm is None:
                continue
            u = canonical_unit(rec.get("EXDOSU"))
            if dose_unit and u and u != dose_unit:
                unit_flags.append(f"{rec.key}: unit {rec.get('EXDOSU')} vs protocol {r.dose_unit}")
            wrong.append(mm)

            def check(rec=rec):
                return _dose_mismatch(core, rec) is not None

            evidence.append(rec_evidence(rec, f"administered {mm['actual']} != expected {mm['expected']}", check))
        if not wrong:
            continue
        evidence += protocol_evidence(core, "dosing", "protocol dosing requirement per arm")
        arm = arm_of(core, usubjid)
        findings.append(
            Finding(
                code=CODE_WRONG,
                usubjid=usubjid,
                site=core.site_of(usubjid),
                summary=f"{len(wrong)} dose record(s) differ from the expected {wrong[0]['expected']} {r.dose_unit or ''} for arm {arm}",
                details={
                    "subcode": "WRONG_DOSE",
                    "records": [w["record"] for w in wrong],
                    "mismatches": wrong,
                    "expected": wrong[0]["expected"],
                    "arm": arm,
                    "unit_flags": unit_flags,
                    "unparseable_doses": unparseable,
                },
                evidence=evidence,
                confidence=0.85 if unit_flags else 0.95,
                flags=["dose_unit_mismatch"] if unit_flags else [],
            )
        )
    return sort_findings(findings)


def dosing_visits(core) -> list[str]:
    """Visits at which dosing is recorded anywhere in the study, in schedule order."""
    r = rules_of(core)
    sched = {normalize_visit_code(k): v for k, v in (r.visit_schedule if r else {}).items()}
    seen: set[str] = set()
    for rec in core.idx.by_domain.get("EX", []):
        v = normalize_visit_code(rec.get("VISIT"))
        if v:
            seen.add(v)
    return sorted(seen, key=lambda v: (sched.get(v, 10**6), v))


def find_missing_doses(core) -> list[Finding]:
    ok, _ = rule_availability(core, CODE_MISSING)
    if not ok:
        return []
    visits = dosing_visits(core)
    order = {v: i for i, v in enumerate(visits)}
    findings: list[Finding] = []
    for usubjid in core.idx.subjects:
        recs = core.idx.records(usubjid, "EX")
        dm = core.dm(usubjid)
        if not recs:
            if dm is None:
                continue

            def check_no_ex(u=usubjid):
                return not core.idx.records(u, "EX")

            findings.append(
                Finding(
                    code=CODE_MISSING,
                    usubjid=usubjid,
                    site=core.site_of(usubjid),
                    summary="no exposure records at all",
                    details={"subcode": "NO_EXPOSURE", "missing_visits": list(visits), "expected_visits": list(visits)},
                    evidence=[EvidenceItem(ref=dm.key, why="enrolled subject (DM) with no EX rows", check=check_no_ex)],
                    confidence=0.85,
                )
            )
            continue
        by_visit: dict[str, Record] = {}
        for rec in recs:
            v = normalize_visit_code(rec.get("VISIT"))
            if v in order and v not in by_visit:
                by_visit[v] = rec
        if not by_visit:
            continue
        positions = sorted(order[v] for v in by_visit)
        first, last = positions[0], positions[-1]
        ds = core.idx.records(usubjid, "DS")
        completed = any((d.get("DSDECOD") or "").upper().startswith("COMPLET") for d in ds)
        upto = len(visits) - 1 if completed else last
        missing = [visits[i] for i in range(first, upto + 1) if visits[i] not in by_visit]
        if not missing:
            continue
        evidence: list[EvidenceItem] = []
        for m in missing:
            i = order[m]
            before = [v for v in by_visit if order[v] < i]
            after = [v for v in by_visit if order[v] > i]
            for v in ([max(before, key=order.get)] if before else []) + ([min(after, key=order.get)] if after else []):
                rec = by_visit[v]
                if any(e.ref == rec.key for e in evidence):
                    continue

                def check(rec=rec, m=m, u=usubjid):
                    have = {normalize_visit_code(x.get("VISIT")) for x in core.idx.records(u, "EX")}
                    return m not in have and normalize_visit_code(rec.get("VISIT")) in have

                evidence.append(rec_evidence(rec, f"dosing record bracketing the missing {m} visit", check, role="context"))
        for d in ds:
            def check_ds(d=d):
                return bool(core.idx.get("DS", d.usubjid, d.seq))
            evidence.append(EvidenceItem(ref=d.key, why="disposition shows study participation", check=check_ds, role="context"))
        findings.append(
            Finding(
                code=CODE_MISSING,
                usubjid=usubjid,
                site=core.site_of(usubjid),
                summary=f"no exposure record for {', '.join(missing)}",
                details={"subcode": "MISSING_DOSE", "missing_visits": missing, "recorded_visits": sorted(by_visit, key=order.get), "completed": completed},
                evidence=evidence,
                confidence=0.8,
            )
        )
    return sort_findings(findings)
