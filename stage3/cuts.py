"""Cut delta computation and cut table iteration for Stage 3."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from backend.cuts.corrections import Correction
from backend.graph.nodes import Record, site_from_usubjid
from stage3.state import WatchState


@dataclass
class CutDelta:
    """Represents the incremental data and metadata change at a single cut."""
    cut: int
    protocol_version: int
    pv_changed: bool
    new_records_count: int
    new_records_by_domain: Dict[str, int] = field(default_factory=dict)
    new_records: Dict[str, List[Record]] = field(default_factory=dict)
    new_sites: Set[str] = field(default_factory=set)
    new_domains: Set[str] = field(default_factory=set)
    corrections_count: int = 0
    corrections: List[Correction] = field(default_factory=list)


def compute_cut_delta(core: Any, cut: int, state: WatchState) -> CutDelta:
    """Computes the incremental additions and transitions arriving at cut."""
    # 1. Determine protocol version
    if core.resolver is not None:
        pv = core.resolver.version_for_cut(cut)
    else:
        from backend.cuts.version_manager import protocol_version_for_cut
        pv = protocol_version_for_cut(core.cut_table, cut)
    if pv is None:
        pv = 1

    pv_changed = (state.active_protocol_version is not None and pv != state.active_protocol_version)

    # 2. Extract new records
    new_records: Dict[str, List[Record]] = {}
    new_records_by_domain: Dict[str, int] = {}
    total_new = 0
    sites_in_cut: Set[str] = set()
    domains_in_cut: Set[str] = set()

    for domain, recs in core.raw_domains.items():
        cut_recs = [r for r in recs if r.cut_available == cut]
        if cut_recs:
            new_records[domain] = cut_recs
            new_records_by_domain[domain] = len(cut_recs)
            total_new += len(cut_recs)
            domains_in_cut.add(domain)
            for r in cut_recs:
                s = r.site or site_from_usubjid(r.usubjid)
                if s:
                    sites_in_cut.add(s)

    new_sites = sites_in_cut - state.known_sites
    new_domains = domains_in_cut - state.known_domains

    # 3. Corrections landing at cut
    corr_at_cut = [c for c in core.corrections if c.cut == cut]

    return CutDelta(
        cut=cut,
        protocol_version=pv,
        pv_changed=pv_changed,
        new_records_count=total_new,
        new_records_by_domain=new_records_by_domain,
        new_records=new_records,
        new_sites=new_sites,
        new_domains=new_domains,
        corrections_count=len(corr_at_cut),
        corrections=corr_at_cut,
    )
