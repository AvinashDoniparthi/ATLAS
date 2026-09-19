"""Stage 3 adapters connecting the incremental StudyGraph and ReviewCrew.

Provides:
  - IncrementalStudyGraph: Duck-typed StudyGraph subclass executing advance_to_cut() on progression.
  - WatchHubClient: HubClient implementing human response delay and documented decision vocabulary.
  - WatchGatewayClient: Cut-aware Gateway client.
  - make_crew: Helper creating a fully wired ReviewCrew with Stage 3 adapters.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from stage1.atlas import Atlas, StudyGraph
from stage2.client import GatewayClient, HubClient
from stage2.crew import ReviewCrew
from stage2.memory import ReviewMemory
from stage2.schemas import MedicalEscalation, SiteQuery

log = logging.getLogger("stage3.adapters")

# Documented code aliases for monitor_decisions lookup
CODE_ALIASES: Dict[str, str] = {
    "SERIOUS_AE": "SAE_UNESCALATED",
    "SITE_DOSING_ERROR": "DOSING_ERROR",
}


class IncrementalStudyGraph(StudyGraph):
    """Subclass of Stage 1 StudyGraph that uses advance_to_cut() for sequential cuts."""

    def __init__(self, data_dir: str, core: Optional[Any] = None):
        super().__init__(data_dir)
        if core is not None:
            # Share the crew's existing core so Atlas.router (built on the same
            # object) stays in sync instead of pointing at a stale graph.
            self.core = core

    def build(self, cut: Optional[int] = None, protocol_version: Optional[int] = None) -> dict:
        if (
            cut is not None
            and self.core.view is not None
            and self.core.view.effective_cut is not None
            and cut == self.core.view.effective_cut + 1
            and not self.core.data_changed()
        ):
            stats = self.core.advance_to_cut(cut, protocol_version)
            self.last_stats = stats.as_dict()
            return self.last_stats
        return super().build(cut, protocol_version)


class WatchHubClient(HubClient):
    """HubClient with configurable human monitor response delay and strict unlisted policy."""

    def __init__(
        self,
        hub_url: Optional[str] = None,
        team_key: Optional[str] = None,
        human_response_delay_cuts: Optional[int] = 1,
    ):
        super().__init__(hub_url=hub_url, team_key=team_key)
        self.current_cut: int = 1
        self.human_response_delay_cuts: Optional[int] = human_response_delay_cuts
        self.raised_cuts: Dict[str, int] = {}  # escalation_fingerprint -> cut raised

    def set_cut(self, cut: int) -> None:
        self.current_cut = cut

    def send_escalation(
        self,
        esc: MedicalEscalation,
        core: Any,
        resubmission: bool = False,
    ) -> Tuple[str, str]:
        # On resubmission after evidence clarification, approve
        if resubmission:
            return "APPROVED", "Clarification verified against source data; safety escalation approved."

        target = esc.site if (esc.code.startswith("SITE_") and esc.site) else (esc.usubjid or esc.site or "UNKNOWN")
        fp = f"{esc.code}|{target}".upper()

        if fp not in self.raised_cuts:
            self.raised_cuts[fp] = self.current_cut
        raised_at = self.raised_cuts[fp]

        # Delay policy check
        if self.human_response_delay_cuts is None:
            return "PENDING", f"Escalated at cut {raised_at}; awaiting human medical monitor adjudication."

        if self.current_cut < raised_at + self.human_response_delay_cuts:
            return "PENDING", f"Escalated at cut {raised_at}; awaiting medical monitor response (delay policy)."

        # Consult mock or remote endpoint
        if self.hub_url and self.hub_url not in ("local", "offline", "none"):
            try:
                dec, rsn = super().send_escalation(esc, core, resubmission=False)
                return dec, rsn
            except Exception:
                pass

        dec_obj = getattr(core, "monitor_decisions", None)
        if dec_obj and dec_obj.loaded:
            alias_code = CODE_ALIASES.get(esc.code, esc.code)
            candidates = [
                (esc.code, target),
                (alias_code, target),
            ]
            if esc.site and esc.site != target:
                candidates.append((esc.code, esc.site))
                candidates.append((alias_code, esc.site))

            for c_code, c_target in candidates:
                if not c_target:
                    continue
                reply = dec_obj.lookup(c_code, c_target)
                if reply and len(reply) >= 2:
                    return reply[0], reply[1]

        # Unlisted: unanswered != approved -> return PENDING
        return "PENDING", f"Escalation {esc.code} unlisted in monitor decisions; awaiting manual review."


class WatchGatewayClient(GatewayClient):
    """GatewayClient tracking dispatched site queries across cuts."""

    def __init__(self, gateway_url: Optional[str] = None, team_key: Optional[str] = None):
        super().__init__(gateway_url=gateway_url, team_key=team_key)
        self.dispatched_queries: list[dict] = []

    def send_query(self, query: SiteQuery, core: Any) -> Tuple[str, str]:
        status, text = super().send_query(query, core)
        self.dispatched_queries.append({
            "query_id": query.query_id,
            "usubjid": query.usubjid,
            "domain": query.domain,
            "cut": query.cut,
            "status": status,
            "reply": text,
        })
        return status, text


def make_crew(
    data_dir: str | Path,
    persistence_file: Optional[str | Path] = None,
    memory: Optional[ReviewMemory] = None,
    human_response_delay_cuts: Optional[int] = 1,
) -> Tuple[ReviewCrew, IncrementalStudyGraph, WatchHubClient, WatchGatewayClient]:
    """Factory creating an incremental StudyGraph, custom clients, and ReviewCrew."""
    graph = IncrementalStudyGraph(str(data_dir))
    atlas = Atlas(graph)

    hub_client = WatchHubClient(
        hub_url="local",
        team_key="stage3_team",
        human_response_delay_cuts=human_response_delay_cuts,
    )
    gateway_client = WatchGatewayClient(gateway_url="local", team_key="stage3_team")

    if memory is None:
        memory = ReviewMemory(persistence_file=persistence_file)

    crew = ReviewCrew(
        hub_url="local",
        gateway_url="local",
        team_key="stage3_team",
        atlas=atlas,
        persistence_file=persistence_file,
        memory=memory,
    )
    crew.hub_client = hub_client
    crew.gateway_client = gateway_client

    return crew, graph, hub_client, gateway_client
