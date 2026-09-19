"""Site regularity and implausible pattern anomaly detector for Stage 3."""
from __future__ import annotations

import logging
import statistics
from typing import Any, Dict, List, Optional, Set, Tuple

from starter.schemas import RecordRef
from stage2.schemas import AlternativeConsidered, MedicalEscalation
from stage3.config import WatchConfig
from stage3.models import Decision, SiteRisk, WatchTraceEntry
from stage3.state import WatchState

log = logging.getLogger("stage3.site_anomaly")


def check_site_anomalies(
    cut: int,
    core: Any,
    state: WatchState,
    config: WatchConfig,
) -> Tuple[List[SiteRisk], List[Decision], List[WatchTraceEntry], List[MedicalEscalation]]:
    """Identifies sites with implausibly low variance across multiple independent signals."""
    risks: List[SiteRisk] = []
    decisions: List[Decision] = []
    trace_entries: List[WatchTraceEntry] = []
    escalations: List[MedicalEscalation] = []

    # Check all sites present in the index up to this cut
    sites = sorted(core.idx.by_site.keys())

    # Collect numeric measurements per site across LB and VS
    site_measurements: Dict[str, Dict[str, List[float]]] = {}
    other_measurements: Dict[str, Dict[str, List[float]]] = {}

    for domain in ("LB", "VS"):
        for r in core.raw_domains.get(domain, []):
            rcut = r.cut_available
            if rcut is None or rcut > cut:
                continue
            site = r.site or core.site_of(r.usubjid)
            if not site:
                continue

            testcd = (r.get(f"{domain}TESTCD") or "").upper()
            if not testcd:
                continue
            val_col = f"{domain}ORRES"
            try:
                val = float(r.get(val_col))
            except (TypeError, ValueError):
                continue

            site_measurements.setdefault(site, {}).setdefault(testcd, []).append(val)
            for s in sites:
                if s != site:
                    other_measurements.setdefault(s, {}).setdefault(testcd, []).append(val)

    for site in sites:
        # Check if already quarantined
        if site in state.quarantined_sites:
            continue

        subj_count = len(core.idx.subjects_at(site))
        if subj_count < config.site_min_subjects:
            continue

        site_tests = site_measurements.get(site, {})
        total_recs = sum(len(v) for v in site_tests.values())
        if total_recs < config.site_min_records:
            continue

        regular_signals: List[str] = []

        for testcd, vals in site_tests.items():
            if len(vals) < 10:
                continue
            mean_site = statistics.mean(vals)
            if mean_site == 0:
                continue
            std_site = statistics.stdev(vals) if len(vals) > 1 else 0.0
            cv_site = std_site / abs(mean_site)

            # Compare against pooled other sites for this test
            pooled_other = other_measurements.get(site, {}).get(testcd, [])
            if len(pooled_other) < 20:
                continue
            mean_other = statistics.mean(pooled_other)
            if mean_other == 0:
                continue
            std_other = statistics.stdev(pooled_other)
            cv_other = std_other / abs(mean_other)

            # Check if site CV is suspiciously lower than study CV (< 20% of study CV)
            if cv_other > 0.05 and cv_site < (config.site_cv_ratio_threshold * cv_other):
                regular_signals.append(f"{testcd}_low_cv({cv_site:.3f} vs study {cv_other:.3f})")

        # Check visit date regularity
        visit_diffs: List[int] = []
        for u in core.idx.subjects_at(site):
            dates = [d for d, _ in core.idx.by_subject_date.get((u, "LB"), [])]
            if len(dates) >= 2:
                dates = sorted(dates)
                for i in range(1, len(dates)):
                    visit_diffs.append((dates[i] - dates[i - 1]).days)

        if len(visit_diffs) >= 10:
            std_spacing = statistics.stdev(visit_diffs)
            if std_spacing < 0.5:
                regular_signals.append(f"rigid_visit_spacing(std={std_spacing:.2f}d)")

        if len(regular_signals) >= config.site_min_regular_signals:
            state.quarantine_site(site)

            rationale = (
                f"Site {site} exhibited variance an order of magnitude below study variance across "
                f"{len(regular_signals)} independent signals ({', '.join(regular_signals)}). "
                f"Site quarantined to prevent study-wide statistical contamination; raw data preserved."
            )

            risk = SiteRisk(
                site=site,
                cv_score=round(len(regular_signals) / max(len(site_tests), 1), 3),
                regular_signals=regular_signals,
                quarantined=True,
                rationale=rationale,
            )
            risks.append(risk)

            # Gather sample evidence records from site
            ev_refs: List[RecordRef] = []
            for u in list(core.idx.subjects_at(site))[:3]:
                ev_refs.append(RecordRef(domain="DM", usubjid=u, seq=1))

            dec_id = f"D-{cut}-QUARANTINE-{site}"
            dec = Decision(
                decision_id=dec_id,
                cut=cut,
                decision_type="SITE_QUARANTINE",
                target=site,
                code="IMPLAUSIBLE_SITE_PATTERN",
                status="QUARANTINED",
                reason=rationale,
                evidence=ev_refs,
                alternatives=[
                    AlternativeConsidered(
                        alternative="Delete site data from analysis database",
                        rejected_reason="Good Clinical Practice (GCP) and 21 CFR Part 11 forbid data deletion; data must be quarantined and fully preserved for regulatory audit.",
                    )
                ],
                timestamp=core.loaded_signature or "",
                status_history=[{"cut": cut, "status": "QUARANTINED", "signals": regular_signals}],
            )
            decisions.append(dec)

            trace_entries.append(
                WatchTraceEntry(
                    timestamp=core.loaded_signature or "",
                    cycle=cut,
                    cut=cut,
                    trace_id=f"T-{cut}-QUARANTINE-{site}",
                    decision_id=dec_id,
                    node="site_anomaly",
                    action=f"IMPLAUSIBLE_SITE_PATTERN: quarantined site {site}",
                    target=site,
                    evidence=ev_refs,
                    reason=rationale,
                    status="QUARANTINED",
                )
            )

            # Create site-level escalation to medical monitor
            esc = MedicalEscalation(
                escalation_id=f"ESC-SITE-ANOMALY-{site}-C{cut}",
                code="IMPLAUSIBLE_SITE_PATTERN",
                usubjid="",
                site=site,
                severity="CRITICAL",
                summary=f"Site {site}: Systemic implausibly low clinical variability across {len(regular_signals)} parameters.",
                evidence=ev_refs,
                alternatives=[
                    AlternativeConsidered(
                        alternative="Disregard pattern as benign compliance",
                        rejected_reason="Cross-domain identical variance violates biological limits; indicates non-authentic reporting.",
                    )
                ],
                status="PENDING",
                cycle=cut,
            )
            escalations.append(esc)

    return risks, decisions, trace_entries, escalations
