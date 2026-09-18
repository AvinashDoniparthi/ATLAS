"""Duplicate-subject findings from the graph's person clusters (one person enrolled twice)."""
from __future__ import annotations

from backend.graph.nodes import EvidenceItem, Finding
from backend.rules.common import sort_findings

CODE = "DUPLICATE_SUBJECT"


def find_duplicate_subjects(core) -> list[Finding]:
    findings: list[Finding] = []
    for pc in core.persons.values():
        if len(pc.usubjids) < 2:
            continue
        evidence: list[EvidenceItem] = []
        for key in pc.evidence:
            def check(key=key, pid=pc.person_id):
                rec = core.idx.get_key(key)
                return rec is not None and core.person_of.get(rec.usubjid) == pid
            evidence.append(EvidenceItem(ref=key, why=f"DM row matching on {', '.join(pc.matched_on)}", check=check))
        findings.append(
            Finding(
                code=CODE,
                usubjid=pc.canonical,
                site=core.site_of(pc.canonical),
                summary=f"same person enrolled as {', '.join(pc.usubjids)} (matched on {', '.join(pc.matched_on)})",
                details={
                    "usubjids": list(pc.usubjids),
                    "canonical": pc.canonical,
                    "sites": [core.site_of(u) for u in pc.usubjids],
                    "matched_on": list(pc.matched_on),
                },
                evidence=evidence,
                confidence=0.85,
            )
        )
    return sort_findings(findings)
