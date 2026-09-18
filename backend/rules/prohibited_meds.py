"""Prohibited concomitant medications per the protocol version in force (CMCLAS-driven)."""
from __future__ import annotations

from backend.graph.nodes import Finding
from backend.protocol.protocol_loader import match_prohibited
from backend.rules.common import protocol_evidence, rec_evidence, rule_availability, rules_of, sort_findings

CODE = "PROHIBITED_MEDICATION"


def find_prohibited_meds(core) -> list[Finding]:
    ok, _ = rule_availability(core, CODE)
    if not ok:
        return []
    r = rules_of(core)
    prohibited = list(r.prohibited_classes)
    findings: list[Finding] = []
    for rec in core.idx.by_domain.get("CM", []):
        matched = match_prohibited(rec.get("CMCLAS"), rec.get("CMTRT"), prohibited)
        if matched is None:
            continue

        def check(rec=rec):
            rr = rules_of(core)
            return bool(rr and match_prohibited(rec.get("CMCLAS"), rec.get("CMTRT"), rr.prohibited_classes))

        ev = [rec_evidence(rec, f"CMCLAS {rec.get('CMCLAS')} matches prohibited class {matched}", check)]
        ev += protocol_evidence(core, "prohibited", "protocol prohibited medication list")
        findings.append(
            Finding(
                code=CODE,
                usubjid=rec.usubjid,
                site=core.site_of(rec.usubjid),
                summary=f"{rec.get('CMTRT')} ({rec.get('CMCLAS')}) is a prohibited {matched}",
                details={
                    "matched_class": matched,
                    "CMTRT": rec.get("CMTRT"),
                    "CMCLAS": rec.get("CMCLAS"),
                    "CMSTDTC": rec.get("CMSTDTC"),
                    "record": rec.key.as_tuple(),
                    "protocol_version": core.protocol_version(),
                },
                evidence=ev,
                confidence=0.95,
            )
        )
    return sort_findings(findings)
