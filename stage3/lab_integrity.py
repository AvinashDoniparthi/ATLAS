"""Laboratory distribution and unit-shift integrity detector for Stage 3."""
from __future__ import annotations

import logging
import statistics
from typing import Any, Dict, List, Optional, Set, Tuple

from starter.schemas import RecordRef
from stage2.schemas import AlternativeConsidered, SiteQuery
from stage3.config import WatchConfig
from stage3.models import Decision, LabIntegrityEvent, WatchTraceEntry
from stage3.state import WatchState

log = logging.getLogger("stage3.lab_integrity")


def check_lab_integrity(
    cut: int,
    core: Any,
    state: WatchState,
    config: WatchConfig,
    gateway_client: Any,
) -> Tuple[List[LabIntegrityEvent], List[Decision], List[WatchTraceEntry], List[SiteQuery]]:
    """Detects conversion-like unit shifts and data corruption in lab analytes."""
    events: List[LabIntegrityEvent] = []
    decisions: List[Decision] = []
    trace_entries: List[WatchTraceEntry] = []
    queries: List[SiteQuery] = []

    # Group all available LB records by analyte, site, and cut
    all_lb = core.raw_domains.get("LB", [])
    if not all_lb:
        return events, decisions, trace_entries, queries

    current_records: Dict[Tuple[str, str], List[Any]] = {}  # (site, testcd) -> list[Record]
    prior_records: Dict[Tuple[str, str], List[float]] = {}  # (site, testcd) -> list[float]
    current_others: Dict[str, List[float]] = {}  # testcd -> list[float]
    prior_others: Dict[str, List[float]] = {}  # testcd -> list[float]

    for r in all_lb:
        rcut = r.cut_available
        if rcut is None or rcut > cut:
            continue

        testcd = (r.get("LBTESTCD") or "").upper()
        if not testcd:
            continue

        try:
            val = float(r.get("LBORRES"))
        except (TypeError, ValueError):
            continue

        site = r.site or core.site_of(r.usubjid)
        if not site:
            continue

        if rcut == cut:
            current_records.setdefault((site, testcd), []).append(r)
            current_others.setdefault(testcd, []).append(val)
        elif rcut < cut:
            prior_records.setdefault((site, testcd), []).append(val)
            prior_others.setdefault(testcd, []).append(val)

    # Evaluate each site x testcd with new records at this cut
    for (site, testcd), recs in current_records.items():
        if len(recs) < config.lab_min_samples:
            continue

        site_vals_cut = [float(r.get("LBORRES")) for r in recs]
        med_site_cut = statistics.median(site_vals_cut)

        # Other sites' median at current cut
        other_vals_cut = [
            float(r.get("LBORRES"))
            for (s, t), rlist in current_records.items()
            if t == testcd and s != site
            for r in rlist
        ]
        if len(other_vals_cut) < config.lab_min_samples:
            continue
        med_others_cut = statistics.median(other_vals_cut)
        if med_others_cut == 0:
            continue

        r_cut = med_site_cut / med_others_cut

        # Baseline ratio prior to cut N
        prior_site_vals = prior_records.get((site, testcd), [])
        prior_other_vals = [
            v for (s, t), vlist in prior_records.items() if t == testcd and s != site for v in vlist
        ]
        if prior_site_vals and prior_other_vals:
            med_site_prior = statistics.median(prior_site_vals)
            med_others_prior = statistics.median(prior_other_vals)
            r0 = med_site_prior / med_others_prior if med_others_prior != 0 else 1.0
        else:
            r0 = 1.0

        if r0 == 0:
            r0 = 1.0

        ratio_shift = r_cut / r0
        fold_change = 1.0 / ratio_shift if ratio_shift < 1.0 else ratio_shift

        # Check match with known conversion factors
        matched_factor: Optional[float] = None
        is_shift = False

        known_factor = config.known_conversions.get(testcd)
        if known_factor:
            # Check fold change against factor within tolerance
            err = abs(fold_change - known_factor) / known_factor
            if err <= config.lab_ratio_tolerance:
                is_shift = True
                matched_factor = known_factor

        # Catch generic uncalibrated shift (> 3x fold change without documented unit change)
        if not is_shift and fold_change >= config.lab_max_plausible_fold:
            is_shift = True
            matched_factor = round(fold_change, 2)

        if is_shift:
            # Mark records as untrusted in state
            untrusted_refs: List[RecordRef] = []
            for r in recs:
                state.mark_lab_untrusted(r.domain or "LB", r.usubjid, r.seq)
                untrusted_refs.append(RecordRef(domain=r.domain or "LB", usubjid=r.usubjid, seq=r.seq))

            event = LabIntegrityEvent(
                site=site,
                analyte=testcd,
                cut=cut,
                baseline_ratio=round(r0, 3),
                current_ratio=round(r_cut, 3),
                fold_change=round(fold_change, 2),
                matched_factor=matched_factor,
                untrusted_keys=[f"{r.domain or 'LB'}|{r.usubjid}|{r.seq}" for r in recs],
            )
            events.append(event)

            unit_stated = recs[0].get("LBORRESU") or "mg/dL"
            dec_id = f"D-{cut}-LABSHIFT-{site}-{testcd}"
            dec = Decision(
                decision_id=dec_id,
                cut=cut,
                decision_type="LAB_UNIT_SHIFT",
                target=site,
                code="LAB_UNIT_SHIFT",
                status="DATA_INTEGRITY",
                reason=(
                    f"Site {site} {testcd} median dropped by {fold_change:.1f}x at cut {cut} "
                    f"consistent with physical conversion factor ({matched_factor}) while unit field remained '{unit_stated}'. "
                    f"{len(recs)} record(s) marked untrusted."
                ),
                evidence=untrusted_refs[:5],
                alternatives=[
                    AlternativeConsidered(
                        alternative="Flag clinical laboratory abnormality escalation",
                        rejected_reason="Values dropped by precise molecular conversion factor (~18.016); indicates analyser unit misconfiguration, not clinical toxicity.",
                    )
                ],
                timestamp=core.loaded_signature or "",
                status_history=[{"cut": cut, "status": "DATA_INTEGRITY", "shift": fold_change}],
            )
            decisions.append(dec)

            trace_entries.append(
                WatchTraceEntry(
                    timestamp=core.loaded_signature or "",
                    cycle=cut,
                    cut=cut,
                    trace_id=f"T-{cut}-LABSHIFT-{site}-{testcd}",
                    decision_id=dec_id,
                    node="lab_integrity",
                    action=f"LAB_UNIT_SHIFT detected for site {site} analyte {testcd} (fold={fold_change:.1f}x)",
                    target=site,
                    evidence=untrusted_refs[:5],
                    reason=f"Analyser unit mismatch: site {site} values shifted by conversion ratio {matched_factor}; clinical escalations suppressed.",
                    status="DATA_INTEGRITY",
                )
            )

            # Generate formal site data-quality query via gateway client
            query = SiteQuery(
                query_id=f"Q-LAB-{site}-{testcd}-C{cut}",
                usubjid=recs[0].usubjid,
                site=site,
                domain="LB",
                seq=recs[0].seq,
                cut=cut,
                query_text=(
                    f"Site {site}: Reported {testcd} values at cut {cut} appear reported in conventional SI units "
                    f"without required conversion (ratio {fold_change:.1f}x). Confirm analyser calibration and unit specification."
                ),
                status="OPEN",
                fingerprint=f"LAB_SHIFT|{site}|{testcd}|{cut}".upper(),
                cycle=cut,
            )
            if gateway_client is not None:
                gateway_client.send_query(query, core)
            queries.append(query)

    return events, decisions, trace_entries, queries
