"""Serious adverse events (protocol-derived) and AEs starting before first dose."""
from __future__ import annotations

from backend.graph.nodes import EvidenceItem, Finding
from backend.normalization.dates import parse_date
from backend.rules.common import first_dose_date, protocol_evidence, rec_evidence, rules_of, sort_findings

CODE_SAE = "SERIOUS_AE"
CODE_PRE = "AE_BEFORE_FIRST_DOSE"


def _yes(v) -> bool:
    return (v or "").strip().upper() in ("Y", "YES", "TRUE", "1")


def is_serious(core, rec) -> tuple[bool, str]:
    """(serious?, subcode). AESER=Y is always serious; AESHOSP=Y is serious under the protocol rule."""
    r = rules_of(core)
    hosp_rule = bool(r and r.sae_hosp_flag_rule)
    ser = _yes(rec.get("AESER"))
    hosp = _yes(rec.get("AESHOSP"))
    if ser:
        return True, "SAE_CODED"
    if hosp and hosp_rule:
        return True, "SAE_MISCODED"
    return False, ""


def find_serious_ae(core) -> list[Finding]:
    findings: list[Finding] = []
    for rec in core.idx.by_domain.get("AE", []):
        serious, sub = is_serious(core, rec)
        if not serious:
            continue
        why = "AESER=Y (site-coded serious)" if sub == "SAE_CODED" else "AESHOSP=Y makes the event serious regardless of AESER"

        def check(rec=rec):
            return is_serious(core, rec)[0]

        ev = [rec_evidence(rec, why, check)]
        ev += protocol_evidence(core, "sae_hosp" if sub == "SAE_MISCODED" else "sae", "protocol seriousness definition")
        findings.append(
            Finding(
                code=CODE_SAE,
                usubjid=rec.usubjid,
                site=core.site_of(rec.usubjid),
                summary=f"{rec.get('AETERM')} ({rec.get('AESEV')}) — {sub.replace('_', ' ').lower()}",
                details={
                    "subcode": sub,
                    "AETERM": rec.get("AETERM"),
                    "AESEV": rec.get("AESEV"),
                    "AESER": rec.get("AESER"),
                    "AESHOSP": rec.get("AESHOSP"),
                    "AESTDTC": rec.get("AESTDTC"),
                    "record": rec.key.as_tuple(),
                },
                evidence=ev,
                confidence=0.95,
            )
        )
    return sort_findings(findings)


def find_ae_before_first_dose(core) -> list[Finding]:
    findings: list[Finding] = []
    for rec in core.idx.by_domain.get("AE", []):
        start = parse_date(rec.get("AESTDTC"))
        if not start.ok or start.date is None:
            continue
        ref_date, ref_rec = first_dose_date(core, rec.usubjid)
        if ref_date is None or ref_rec is None:
            continue
        if start.date >= ref_date:
            continue

        def check(rec=rec, ref_date=ref_date):
            d = parse_date(rec.get("AESTDTC"))
            return bool(d.ok and d.date and d.date < ref_date)

        def check_ref(ref_rec=ref_rec, ref_date=ref_date):
            d, _ = first_dose_date(core, ref_rec.usubjid)
            return d == ref_date

        findings.append(
            Finding(
                code=CODE_PRE,
                usubjid=rec.usubjid,
                site=core.site_of(rec.usubjid),
                summary=f"{rec.get('AETERM')} started {start.date} before first dose {ref_date}",
                details={
                    "AESTDTC": rec.get("AESTDTC"),
                    "first_dose_date": str(ref_date),
                    "days_before": (ref_date - start.date).days,
                    "record": rec.key.as_tuple(),
                },
                evidence=[
                    rec_evidence(rec, "AE start date precedes first dose", check),
                    EvidenceItem(ref=ref_rec.key, why="reference start date (RFSTDTC)", check=check_ref, role="context"),
                ],
                confidence=0.9,
            )
        )
    return sort_findings(findings)
