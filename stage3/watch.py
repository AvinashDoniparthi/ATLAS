"""StudyWatch — Main Stage 3 surveillance engine and explainability interface."""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, List, Optional, Union

from stage2.crew import ReviewCrew
from stage3.adapters import IncrementalStudyGraph, WatchGatewayClient, WatchHubClient, make_crew
from stage3.budget import BudgetManager
from stage3.config import DEFAULT_PERIOD, WatchConfig
from stage3.decisions import DecisionStore
from stage3.explanations import build_explanation
from stage3.human_state import HumanState
from stage3.models import CutResult, Explanation, SurveillanceReport
from stage3.report import generate_surveillance_report, write_report_artifacts
from stage3.state import WatchState
from stage3.surveillance import run_surveillance_cut
from stage3.trace import TraceStore

log = logging.getLogger("stage3.watch")


class StudyWatch:
    """Surveils a clinical study across sequential cuts with incremental updates."""

    def __init__(
        self,
        data_dir: Union[str, Path],
        crew: Optional[ReviewCrew] = None,
        config: Optional[WatchConfig] = None,
    ):
        self.data_dir = Path(data_dir)
        self.config = config or WatchConfig()

        self.output_dir = self.config.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.state = WatchState(persistence_file=self.output_dir / "state.json")
        self.human_state = HumanState(config=self.config)
        self.budget_manager = BudgetManager(config=self.config)
        self.decision_store = DecisionStore()
        self.trace_store = TraceStore()

        # Restore persisted decisions and traces if available
        self.decision_store.load(self.output_dir / "decision_log.json")
        self.trace_store.load(self.output_dir / "trace.jsonl")

        self.last_report: Optional[SurveillanceReport] = None
        self.cut_summaries: List[CutResult] = []

        # Restore cut summaries from state cut history (latest run per cut)
        if self.state.cut_history:
            by_cut = {}
            for c in self.state.cut_history:
                try:
                    cr = CutResult(**c) if isinstance(c, dict) else c
                    by_cut[cr.cut] = cr
                except Exception:
                    pass
            self.cut_summaries = [by_cut[k] for k in sorted(by_cut.keys())]

        # Restore budget manager state from run_stats.json or cut summaries
        restored_budget = False
        stats_file = self.output_dir / "run_stats.json"
        if stats_file.is_file():
            try:
                with open(stats_file, "r", encoding="utf-8") as f:
                    s_data = json.load(f)
                b_sum = s_data.get("budget_summary", {})
                if b_sum and float(b_sum.get("spent_ms", 0.0)) > 0:
                    self.budget_manager.spent_ms = float(b_sum["spent_ms"])
                    self.budget_manager.total_ms = float(b_sum.get("total_ms", self.budget_manager.total_ms))
                    self.budget_manager.current_tier = b_sum.get("tier", "FULL")
                    restored_budget = True
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to restore budget from run_stats.json: %s", exc)

        if not restored_budget and self.cut_summaries:
            total_spent = sum(cr.budget_ms_used for cr in self.cut_summaries)
            if total_spent > 0:
                self.budget_manager.spent_ms = total_spent
                for cr in self.cut_summaries:
                    self.budget_manager.cut_durations[cr.cut] = cr.budget_ms_used
                self.budget_manager.current_tier = self.cut_summaries[-1].degradation_tier

        if crew is not None:
            self.crew = crew
            # Ensure graph is duck-typed for incremental updates if possible
            if not isinstance(self.crew.atlas.graph, IncrementalStudyGraph):
                existing = self.crew.atlas.graph
                inc_graph = IncrementalStudyGraph(str(self.data_dir), core=getattr(existing, "core", None))
                inc_graph.last_stats = dict(getattr(existing, "last_stats", {}) or {})
                self.crew.atlas.graph = inc_graph
            self.graph = self.crew.atlas.graph
            self.hub_client = getattr(self.crew, "hub_client", None)
            if not isinstance(self.hub_client, WatchHubClient):
                self.hub_client = WatchHubClient(
                    hub_url=getattr(self.crew, "hub_url", "local"),
                    human_response_delay_cuts=self.config.human_response_delay_cuts,
                )
                self.crew.hub_client = self.hub_client
            self.gateway_client = getattr(self.crew, "gateway_client", None)
            if not isinstance(self.gateway_client, WatchGatewayClient):
                self.gateway_client = WatchGatewayClient(gateway_url=getattr(self.crew, "gateway_url", "local"))
                self.crew.gateway_client = self.gateway_client
        else:
            self.crew, self.graph, self.hub_client, self.gateway_client = make_crew(
                data_dir=self.data_dir,
                persistence_file=self.output_dir / ".stage3_memory.json",
                human_response_delay_cuts=self.config.human_response_delay_cuts,
            )

        # cut_table / raw_domains / resolver are only populated by load(); the
        # first cut delta needs them before the graph is built.
        core = self.graph.core
        if core.loaded_signature is None:
            core.load()

    def run_period(
        self,
        cuts: Optional[Union[range, List[int]]] = None,
    ) -> SurveillanceReport:
        """Surveils the study across sequential cuts and produces final report."""
        target_cuts = list(cuts) if cuts is not None else list(self.config.cuts)
        log.info("Starting StudyWatch surveillance period over cuts: %s", target_cuts)

        for c in target_cuts:
            cut_res = run_surveillance_cut(
                cut=c,
                crew=self.crew,
                graph=self.graph,
                hub_client=self.hub_client,
                gateway_client=self.gateway_client,
                state=self.state,
                human_state=self.human_state,
                budget_manager=self.budget_manager,
                decision_store=self.decision_store,
                trace_store=self.trace_store,
                config=self.config,
            )
            self.cut_summaries.append(cut_res)

        # Compute summary metrics
        dec_counts = Counter(d.status for d in self.decision_store.all_decisions())
        total_findings = sum(cr.findings_count for cr in self.cut_summaries)
        serious_findings = sum(cr.serious_findings_count for cr in self.cut_summaries)
        queries_count = sum(cr.queries_count for cr in self.cut_summaries)

        protocol_transitions = []
        for i in range(1, len(self.cut_summaries)):
            prev, curr = self.cut_summaries[i - 1], self.cut_summaries[i]
            if curr.protocol_version != prev.protocol_version:
                protocol_transitions.append({
                    "cut": curr.cut,
                    "from_version": prev.protocol_version,
                    "to_version": curr.protocol_version,
                })

        report = generate_surveillance_report(
            cuts_evaluated=target_cuts,
            cut_summaries=self.cut_summaries,
            protocol_transitions=protocol_transitions,
            document_changes=[],
            decisions_summary=dict(dec_counts),
            quarantined_sites=sorted(self.state.quarantined_sites),
            untrusted_count=len(self.state.untrusted_lab_keys),
            queries_count=queries_count,
            total_findings=total_findings,
            serious_findings=serious_findings,
            budget_summary=self.budget_manager.state().model_dump(),
        )
        self.last_report = report

        # Write artifacts to disk
        self.decision_store.save(self.output_dir / "decision_log.json")
        self.trace_store.save(self.output_dir / "trace.jsonl")
        write_report_artifacts(report, self.output_dir)

        log.info("Surveillance period completed successfully across %d cuts", len(target_cuts))
        return report

    def explain(self, decision_id: str) -> Explanation:
        """Produces a trace-backed explanation for any decision."""
        return build_explanation(
            decision_id=decision_id,
            decision_store=self.decision_store,
            trace_store=self.trace_store,
            core=self.graph.core,
        )
