"""Protocol amendment and version transition handling for Stage 3."""
from __future__ import annotations

import logging
from typing import Any, List, Optional, Set, Tuple

from starter.schemas import RecordRef
from stage3.models import WatchTraceEntry

log = logging.getLogger("stage3.amendment")

AMENDMENT_FIELD_TO_RULES: dict[str, list[str]] = {
    "visit_window_days": ["VISIT_WINDOW_DEVIATION"],
    "visit_schedule": ["VISIT_WINDOW_DEVIATION"],
    "prohibited_classes": ["PROHIBITED_MEDICATION"],
    "age_min": ["ELIGIBILITY_VIOLATION"],
    "age_max": ["ELIGIBILITY_VIOLATION"],
    "hba1c_min": ["ELIGIBILITY_VIOLATION"],
    "hba1c_max": ["ELIGIBILITY_VIOLATION"],
    "hepatic_uln_multiple": ["ELIGIBILITY_VIOLATION"],
    "creatinine_max": ["ELIGIBILITY_VIOLATION"],
    "creatinine_unit": ["ELIGIBILITY_VIOLATION"],
    "pregnancy_excluded": ["ELIGIBILITY_VIOLATION"],
    "sae_hosp_flag_rule": ["SERIOUS_AE"],
    "sae_criteria": ["SERIOUS_AE"],
    "hys_transaminase_multiple": ["HYS_LAW_CANDIDATE"],
    "hys_bili_multiple": ["HYS_LAW_CANDIDATE"],
    "hys_window_days": ["HYS_LAW_CANDIDATE"],
    "expected_dose": ["DOSING_ERROR", "MISSING_DOSE"],
    "dose_unit": ["DOSING_ERROR", "MISSING_DOSE"],
}


def check_amendment(
    cut: int,
    old_pv: Optional[int],
    new_pv: int,
    core: Any,
) -> Tuple[Set[str], List[WatchTraceEntry]]:
    """Identifies affected rules when protocol version changes and emits audit trace."""
    affected_rules: Set[str] = set()
    trace_entries: List[WatchTraceEntry] = []

    if old_pv is None or old_pv == new_pv:
        return affected_rules, trace_entries

    diff: dict = {}
    if core.resolver and hasattr(core.resolver, "registry"):
        diff = core.resolver.registry.diff(old_pv, new_pv)

    for field_name in diff:
        rules = AMENDMENT_FIELD_TO_RULES.get(field_name, [])
        for r in rules:
            affected_rules.add(r)

    # Per SAP requirement: amendment changes require re-evaluation of derived flags
    if not affected_rules:
        affected_rules.update(["VISIT_WINDOW_DEVIATION", "PROHIBITED_MEDICATION", "ELIGIBILITY_VIOLATION"])

    doc_refs = [
        RecordRef(domain="DOC", document=f"protocol_v{new_pv}", section="body"),
        RecordRef(domain="DOC", document="sap", section="body"),
    ]

    trace_entries.append(
        WatchTraceEntry(
            timestamp=core.loaded_signature or "",
            cycle=cut,
            cut=cut,
            trace_id=f"T-{cut}-AMEND-v{old_pv}-v{new_pv}",
            node="amendment",
            action=f"Protocol amendment transition v{old_pv} -> v{new_pv} at cut {cut}",
            target=f"v{new_pv}",
            evidence=doc_refs,
            reason=f"Protocol amended: recomputing affected rules {sorted(affected_rules)} per SAP §6",
            status="CONFIRMED",
        )
    )

    return affected_rules, trace_entries
