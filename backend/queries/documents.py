"""Document questions: what the protocol / lab manual / SAP say (as evidence)."""
from __future__ import annotations

import re
from typing import Optional

from backend.graph.nodes import DocRefKey

TOPIC_RULES: dict[str, list[str]] = {
    "hys_law": ["hy", "liver", "hepat", "bilirubin", "transaminase"],
    "visit_window": ["window", "visit", "schedule", "deviation"],
    "prohibited": ["prohibit", "concomitant", "medication", "glucocorticoid", "sulfonylurea"],
    "sae_hosp": ["serious", "sae", "hospital", "aeshosp"],
    "dosing": ["dose", "dosing", "mg"],
    "age": ["age", "inclusion"],
    "hba1c": ["hba1c"],
    "creatinine": ["creatinine", "renal", "exclusion"],
    "hepatic": ["hepatic disease"],
    "pregnancy": ["pregnan"],
}


def instruction_like(core, version: Optional[int] = None) -> list[dict]:
    """Instruction-like sentences found in the documents — reported, never obeyed."""
    resolver = getattr(core, "resolver", None)
    if resolver is None:
        return []
    out = []
    for s in resolver.instruction_like_sentences(version):
        out.append({"document": s.document, "section": s.section, "sentence": s.sentence, "reason": s.reason,
                    "ref": DocRefKey(s.document, s.section)})
    return out


def sites_mentioned(sentence: str) -> list[str]:
    return sorted(set(re.findall(r"\bS\d{2}\b", sentence)))


def protocol_rule_text(core, topic: str) -> Optional[dict]:
    rules = getattr(core, "rules", None)
    if rules is None:
        return None
    low = topic.lower()
    best = None
    for name, words in TOPIC_RULES.items():
        if any(w in low for w in words):
            best = name
            break
    if best is None:
        return None
    ref = rules.refs.get(best)
    if ref is None:
        return None
    return {"rule": best, "value": rule_value(rules, best), "document": ref.document, "section": ref.section,
            "sentence": ref.sentence, "ref": DocRefKey(ref.document, ref.section), "version": rules.version}


RULE_FIELDS: dict[str, list[str]] = {
    "hys_law": ["hys_transaminase_multiple", "hys_bili_multiple", "hys_window_days"],
    "visit_window": ["visit_window_days"],
    "visit_schedule": ["visit_schedule"],
    "prohibited": ["prohibited_classes"],
    "sae_hosp": ["sae_hosp_flag_rule"],
    "sae": ["sae_criteria"],
    "dosing": ["expected_dose", "dose_unit"],
    "age": ["age_min", "age_max"],
    "hba1c": ["hba1c_min", "hba1c_max"],
    "creatinine": ["creatinine_max", "creatinine_unit"],
    "hepatic": ["hepatic_uln_multiple"],
    "pregnancy": ["pregnancy_excluded"],
}


def rule_value(rules, name: str):
    fields = RULE_FIELDS.get(name, [name])
    vals = {f: getattr(rules, f, None) for f in fields}
    if len(vals) == 1:
        return next(iter(vals.values()))
    return vals


def protocol_diff(core, a: int, b: int) -> dict:
    resolver = getattr(core, "resolver", None)
    if resolver is None:
        return {}
    return resolver.registry.diff(a, b)


def protocol_versions(core) -> list[int]:
    resolver = getattr(core, "resolver", None)
    return resolver.registry.versions() if resolver else []


def unit_facts(core) -> list[dict]:
    resolver = getattr(core, "resolver", None)
    if resolver is None:
        return []
    return [{"sentence": s, "ref": DocRefKey(r.document, r.section)} for s, r in resolver.unit_facts(core.protocol_version())]
