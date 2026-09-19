"""Correction processing and superseded finding retraction for Stage 3."""
from __future__ import annotations

import hashlib
import logging
from typing import Any, List, Optional, Set, Tuple

from starter.schemas import RecordRef
from stage2.schemas import AlternativeConsidered
from stage3.models import Decision, WatchTraceEntry
from stage3.state import WatchState

log = logging.getLogger("stage3.corrections")


def process_corrections(
    cut: int,
    corrections: list,
    state: WatchState,
    current_findings: List[dict],
    core: Any,
) -> Tuple[List[Decision], List[WatchTraceEntry]]:
    """Evaluates findings affected by corrections and retracts/resolves superseded findings."""
    decisions: List[Decision] = []
    trace_entries: List[WatchTraceEntry] = []

    if not corrections:
        return decisions, trace_entries

    # Set of corrected record keys (domain, usubjid, seq)
    corrected_keys: Set[Tuple[str, str, Optional[int]]] = {
        (c.domain, c.usubjid, c.seq) for c in corrections
    }

    def _ref_from_key(key: tuple) -> Optional[RecordRef]:
        """Rebuild a RecordRef from a ledger evidence key.

        Keys are (domain, usubjid, seq[, document, section]); DOC keys need a
        document, data keys need a usubjid — anything else cannot be cited and
        is skipped rather than fabricated.
        """
        key = tuple(key) + (None,) * (5 - len(key))
        d, u, s, doc, sec = key[:5]
        try:
            if d == "DOC":
                return RecordRef(domain=d, document=doc, section=sec) if doc else None
            return RecordRef(domain=d, usubjid=u, seq=s) if u else None
        except Exception:  # noqa: BLE001 - never crash a cut over one bad citation
            return None

    def _stable_id(prefix: str, fp: str) -> str:
        return f"{prefix}-{cut}-RETRACT-{hashlib.sha1(fp.encode('utf-8')).hexdigest()[:8]}"

    # Set of current finding fingerprints
    current_fps: Set[str] = set()
    for f in current_findings:
        ev = f.get("evidence", [])
        dom, seq = "", None
        if ev and hasattr(ev[0], "domain"):
            dom = ev[0].domain
            seq = ev[0].seq
        elif ev and isinstance(ev[0], dict):
            dom = ev[0].get("domain", "")
            seq = ev[0].get("seq")
        fp = WatchState.finding_fingerprint(
            f.get("code", ""), f.get("usubjid", ""), f.get("site", ""), dom, seq
        )
        current_fps.add(fp)

    # Check previously active findings whose evidence touched corrected records
    for fp, ledger_item in state.findings_ledger.items():
        if ledger_item.get("status") not in ("CONFIRMED", "PENDING"):
            continue

        item_ev_keys = [tuple(k) for k in ledger_item.get("evidence_keys", [])]
        touches_correction = any(tuple(k[:3]) in corrected_keys for k in item_ev_keys)

        if touches_correction and fp not in current_fps:
            # Finding is no longer present after correction -> retract/resolve
            ledger_item["status"] = "RESOLVED"
            ledger_item["resolved_at_cut"] = cut
            ledger_item["resolution_reason"] = "Superseded by laboratory data correction re-issue."

            dec_id = _stable_id("D", fp)
            ev_refs = [r for r in (_ref_from_key(k) for k in item_ev_keys) if r is not None]

            dec = Decision(
                decision_id=dec_id,
                cut=cut,
                decision_type="CORRECTION_RETRACT",
                target=ledger_item.get("usubjid", "") or ledger_item.get("site", "study"),
                code=ledger_item.get("code", ""),
                status="RESOLVED",
                reason=f"Finding {ledger_item.get('code')} retracted following source record correction re-issue at cut {cut}.",
                evidence=ev_refs,
                alternatives=[
                    AlternativeConsidered(
                        alternative="Maintain uncorrected finding",
                        rejected_reason="Source data was formally re-issued by central laboratory; historical finding invalidated.",
                    )
                ],
                timestamp=core.loaded_signature or "",
                status_history=[{"cut": cut, "status": "RESOLVED", "reason": "corrected"}],
            )
            decisions.append(dec)

            trace_entries.append(
                WatchTraceEntry(
                    timestamp=core.loaded_signature or "",
                    cycle=cut,
                    cut=cut,
                    trace_id=_stable_id("T", fp),
                    decision_id=dec_id,
                    node="corrections",
                    action=f"finding_retracted_by_correction: {ledger_item.get('code')} for {ledger_item.get('usubjid')}",
                    target=ledger_item.get("usubjid") or ledger_item.get("site"),
                    evidence=ev_refs,
                    reason="Laboratory record correction resolved previously flagged criterion.",
                    status="RESOLVED",
                )
            )

    return decisions, trace_entries
