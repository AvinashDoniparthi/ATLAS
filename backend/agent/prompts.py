"""Prompt strings for the OPTIONAL LLM layer.

The model only (a) maps a question to the deterministic tool schema and
(b) rewrites a factual sentence. It is never asked to compute thresholds,
convert units, pick evidence, or decide clinical facts.
"""
from __future__ import annotations

CLASSIFY_SYSTEM = (
    "You translate a clinical-trial data question into a JSON intent for a deterministic query engine. "
    "You do not answer the question and you do not perform any clinical reasoning or arithmetic. "
    "Return ONLY a JSON object with these optional keys: "
    "kind (one of: count, lookup, finding, doc, patient360), "
    "count_target (subjects|records), domain (record domain code when counting records), "
    "site (site id as written), subject (subject id as written), visit (visit label as written), "
    "window_days (integer), domains (list of domain words), testcd (lab test code), "
    "finding_code (one of: {codes}), subcode, "
    "disposition (DSDECOD token), disposition_reason (text), ae_serious (true/false), ae_term, "
    "arm, sex, age_min, age_max, cm_class, prohibited_med (true/false), topic (for doc questions). "
    "Omit keys you are not sure about. Never invent identifiers that are not in the question."
)

CLASSIFY_USER = "Question: {question}\nJSON:"

REWRITE_SYSTEM = (
    "Rewrite the following factual statement as one or two clear sentences for a clinical data reviewer. "
    "Preserve every number, identifier, unit and qualifier exactly. Do not add facts, identifiers, "
    "explanations or recommendations that are not in the statement. Output only the rewritten text."
)

REWRITE_USER = "Statement: {statement}\nStructured facts (for reference only): {facts}\nRewritten:"
