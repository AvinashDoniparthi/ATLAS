"""Deterministic clinical rules. Each ``find_*`` is pure over a built StudyGraphCore
and cached per build key via ``core.derived`` — a rebuild (new cut, new data,
new protocol version) invalidates everything automatically."""
from __future__ import annotations

from typing import Callable

from backend.graph.nodes import Finding
from backend.rules.common import rule_availability
from backend.rules.dosing import CODE_MISSING, CODE_WRONG, find_dosing_errors, find_missing_doses
from backend.rules.duplicates import CODE as CODE_DUP
from backend.rules.duplicates import find_duplicate_subjects
from backend.rules.eligibility import CODE as CODE_ELIG
from backend.rules.eligibility import find_eligibility_violations
from backend.rules.hys_law import CODE as CODE_HYS
from backend.rules.hys_law import find_hys_law
from backend.rules.prohibited_meds import CODE as CODE_PROHIB
from backend.rules.prohibited_meds import find_prohibited_meds
from backend.rules.serious_ae import CODE_PRE, CODE_SAE, find_ae_before_first_dose, find_serious_ae
from backend.rules.visit_windows import CODE as CODE_VISIT
from backend.rules.visit_windows import find_visit_window_deviations

ALL_RULES: dict[str, Callable[..., list[Finding]]] = {
    CODE_HYS: find_hys_law,
    CODE_SAE: find_serious_ae,
    CODE_PRE: find_ae_before_first_dose,
    CODE_WRONG: find_dosing_errors,
    CODE_MISSING: find_missing_doses,
    CODE_VISIT: find_visit_window_deviations,
    CODE_PROHIB: find_prohibited_meds,
    CODE_ELIG: find_eligibility_violations,
    CODE_DUP: find_duplicate_subjects,
}


def run_rule(core, code: str) -> list[Finding]:
    fn = ALL_RULES.get(code)
    if fn is None:
        raise KeyError(f"unknown rule {code}")
    return core.derived(f"rule:{code}", lambda: fn(core))


def run_all(core) -> dict[str, list[Finding]]:
    return {code: run_rule(core, code) for code in ALL_RULES}


__all__ = ["ALL_RULES", "run_rule", "run_all", "rule_availability"]
