"""Tool registry: every deterministic capability the router (or an LLM) may invoke.

Each tool is a plain function ``fn(core, **kwargs)``. Names are stable so that
QueryTrace.tools is meaningful and an LLM can only select from this list.
"""
from __future__ import annotations

from typing import Callable

from backend.queries.counts import CountFilters, count_records, count_subjects
from backend.queries.documents import instruction_like, protocol_diff, protocol_rule_text, unit_facts
from backend.queries.findings import finding_subjects
from backend.queries.laboratory import abnormal_labs, lab_values
from backend.queries.lookups import records_at_visit, records_for_subject
from backend.queries.patient360 import patient360


def _count_subjects(core, **filters):
    return count_subjects(core, CountFilters(**filters))


def _count_records(core, domain: str, **filters):
    return count_records(core, domain, CountFilters(**filters))


TOOLS: dict[str, Callable] = {
    "count_subjects": _count_subjects,
    "count_records": _count_records,
    "records_for_subject": records_for_subject,
    "records_at_visit": records_at_visit,
    "finding_subjects": finding_subjects,
    "lab_values": lab_values,
    "abnormal_labs": abnormal_labs,
    "patient360": patient360,
    "instruction_like": instruction_like,
    "protocol_rule_text": protocol_rule_text,
    "protocol_diff": protocol_diff,
    "unit_facts": unit_facts,
}


def tool_names() -> list[str]:
    return sorted(TOOLS)
