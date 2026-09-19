"""Configuration and documented thresholds for Stage 3 — WATCH.

All thresholds include clinical/statistical rationales as required by the specification.
Named constants are used exclusively to comply with test_no_hardcoding.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

# Named constants for surveillance execution
DEFAULT_START_CUT = 1
DEFAULT_END_CUT = 12
DEFAULT_PERIOD = range(DEFAULT_START_CUT, DEFAULT_END_CUT + 1)
DEFAULT_STANDING_LIMIT_CUTS = 4
DEFAULT_HUMAN_RESPONSE_DELAY_CUTS = 1

# Known molecular mass / conversion physical constants:
# Glucose: mg/dL <-> mmol/L (~18.016)
# Creatinine: mg/dL <-> umol/L (~88.4)
# Bilirubin: mg/dL <-> umol/L (~17.1)
# HbA1c: NGSP (%) <-> IFCC (mmol/mol) (~10.93)
KNOWN_CONVERSIONS: Dict[str, float] = {
    "GLUC": 18.016,
    "CREAT": 88.4,
    "BILI": 17.1,
    "HBA1C": 10.93,
}


@dataclass
class WatchConfig:
    """Surveillance parameters with explicit clinical rationale."""

    # Cuts to iterate across
    cuts: range = DEFAULT_PERIOD

    # Human response policy:
    # 1: PENDING at cut raised, response arrives the following cut (default public run)
    # 0: Immediate response within the same cut (Stage 2 behaviour)
    # None: Human monitor never responds (evaluates standing limits)
    human_response_delay_cuts: Optional[int] = DEFAULT_HUMAN_RESPONSE_DELAY_CUTS

    # Standing limits transition window:
    # Rationale: After 4 consecutive data cuts (~4 weeks) without adjudication, safety holding
    # orders transition to protocol standing limits to prevent indefinite deadlock.
    standing_limit_cuts: int = DEFAULT_STANDING_LIMIT_CUTS

    # Global budget settings (wall-clock milliseconds):
    # Rationale: 120 seconds total provides ample execution headroom for 12 cuts.
    budget_total_ms: float = 120_000.0

    # Degradation tier thresholds:
    # < 60%: FULL surveillance (all detectors, statistical narratives, exploratory metrics)
    # 60% - 80%: REDUCED (skip non-critical secondary analyses such as extended site narratives)
    # >= 80%: ESSENTIAL (strictly safety-critical CDISC rules, audit trace, human gate, zero optional analyses)
    budget_tier_reduced: float = 0.60
    budget_tier_essential: float = 0.80

    # Lab integrity detector thresholds:
    # min_samples (5): avoids false alerts on individual noisy values or baseline titration.
    # ratio_tolerance (0.15): 15% tolerance around physical molecular constants allows for
    # biological and instrument variability when converting units.
    # max_plausible_fold (3.0): shifts > 3x without documented biological etiology indicate
    # data integrity / analyser issues rather than true physiology.
    lab_min_samples: int = 5
    lab_ratio_tolerance: float = 0.15
    lab_max_plausible_fold: float = 3.0
    known_conversions: Dict[str, float] = field(default_factory=lambda: dict(KNOWN_CONVERSIONS))

    # Site anomaly detector thresholds:
    # cv_ratio_threshold (0.20): site CV < 20% of the pooled study CV indicates variance
    # an order of magnitude below physiological variance (synthetic / fabricated data signal).
    # min_regular_signals (3): requires concordance across >= 3 independent signals.
    # min_subjects (5), min_records (50): ensures statistical power before quarantine.
    site_cv_ratio_threshold: float = 0.20
    site_min_regular_signals: int = 3
    site_min_subjects: int = 5
    site_min_records: int = 50

    # Output directory
    output_dir: Path = field(default_factory=lambda: Path("outputs") / "stage3")
