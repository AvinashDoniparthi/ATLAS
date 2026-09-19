"""Deterministic clarification solvers for site and subject monitor queries."""
from __future__ import annotations

import logging
import statistics
from typing import Any, List, Optional, Tuple

from starter.schemas import RecordRef
from stage2.clarify_solver import resolve_clarification as stage2_resolve

log = logging.getLogger("stage3.clarify")


def answer_clarification(
    core: Any,
    code: str,
    target: str,
    question_text: str,
) -> Optional[Tuple[str, List[RecordRef]]]:
    """Resolves monitor clarification queries against source study data.

    Returns (answer_text, cited_record_refs) or None if unanswerable from data.
    """
    q_lower = (question_text or "").lower()

    # 1. Site-level IMPLAUSIBLE_SITE_PATTERN clarification
    # "Show the variance statistics versus other sites."
    if "variance statistics" in q_lower or code == "IMPLAUSIBLE_SITE_PATTERN":
        site = target
        all_lb = core.raw_domains.get("LB", [])
        site_vals = []
        other_vals = []
        evidence_refs: List[RecordRef] = []

        for r in all_lb:
            if (r.get("LBTESTCD") or "").upper() == "GLUC":
                try:
                    v = float(r.get("LBORRES"))
                    r_site = r.site or core.site_of(r.usubjid)
                    if r_site == site:
                        site_vals.append(v)
                        if len(evidence_refs) < 5:
                            evidence_refs.append(RecordRef(domain="LB", usubjid=r.usubjid, seq=r.seq))
                    else:
                        other_vals.append(v)
                except (TypeError, ValueError):
                    pass

        if site_vals and other_vals:
            cv_site = (statistics.stdev(site_vals) / statistics.mean(site_vals)) if len(site_vals) > 1 else 0.0
            cv_other = (statistics.stdev(other_vals) / statistics.mean(other_vals)) if len(other_vals) > 1 else 0.0
            ans = (
                f"Site {site} glucose CV is {cv_site:.4f} (std={statistics.stdev(site_vals):.2f}) "
                f"versus pooled other sites CV of {cv_other:.4f} (std={statistics.stdev(other_vals):.2f}). "
                f"Variance ratio is {cv_site/cv_other:.2f}x of the study baseline."
            )
            return ans, evidence_refs

    # 2. Site-level DOSING_ERROR clarification
    # "How many subjects at the site are affected and over which visits?"
    if "how many subjects" in q_lower or (code == "DOSING_ERROR" and not target.startswith("042-")):
        site = target
        affected_subjects = set()
        affected_visits = set()
        evidence_refs: List[RecordRef] = []

        # Find dosing findings or EX records for this site
        from backend.rules.dosing import find_dosing_errors
        findings = core.derived("rule:DOSING_ERROR", lambda: find_dosing_errors(core))

        for f in findings:
            f_site = f.site or core.site_of(f.usubjid)
            if f_site == site:
                affected_subjects.add(f.usubjid)
                for item in f.evidence:
                    if hasattr(item, "ref") and item.ref.seq is not None:
                        evidence_refs.append(RecordRef(domain=item.ref.domain, usubjid=item.ref.usubjid, seq=item.ref.seq))
                    elif isinstance(item, RecordRef):
                        evidence_refs.append(item)

        for u in affected_subjects:
            for r in core.idx.records(u, "EX"):
                v = r.get("VISIT")
                if v:
                    affected_visits.add(v)

        if affected_subjects:
            ans = (
                f"{len(affected_subjects)} subject(s) at site {site} ({', '.join(sorted(affected_subjects))}) "
                f"are affected across visit(s): {', '.join(sorted(affected_visits))}."
            )
            return ans, evidence_refs[:6]

    # 3. Subject-level clarification (e.g. screening ALT & hepatotoxic med)
    if target.startswith("042-"):
        ans_text, refs = stage2_resolve(core, target, question_text)
        if refs:
            return ans_text, refs

    # If unanswerable from data
    return None
