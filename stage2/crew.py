"""Stage 2 — MONITOR: Multi-Agent Clinical Trial Review Crew.

Orchestrates the six-node review pipeline:
  1. detect
  2. medical_review
  3. data_manager
  4. compliance
  5. human_gate
  6. execute
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from stage1.atlas import Atlas
from stage2.client import GatewayClient, HubClient
from stage2.memory import ReviewMemory
from stage2.nodes.compliance import compliance_node
from stage2.nodes.data_manager import data_manager_node
from stage2.nodes.detect import detect_node
from stage2.nodes.execute import execute_node
from stage2.nodes.human_gate import human_gate_node
from stage2.nodes.medical_review import medical_review_node
from stage2.schemas import ReviewReport, TraceEntry

log = logging.getLogger("stage2.crew")


class ReviewCrew:
    """Six-node multi-agent review crew with human-in-the-loop medical monitor."""

    def __init__(
        self,
        hub_url: Optional[str],
        gateway_url: Optional[str],
        team_key: Optional[str],
        atlas: Atlas,
        persistence_file: Optional[str | Path] = None,
        memory: Optional[ReviewMemory] = None,
    ):
        self.hub_url = hub_url or ""
        self.gateway_url = gateway_url or ""
        self.team_key = team_key or ""
        self.atlas = atlas

        if memory is not None:
            self.memory = memory
        else:
            self.memory = ReviewMemory(persistence_file=persistence_file or Path(".stage2_memory.json"))
        self.gateway_client = GatewayClient(gateway_url=self.gateway_url, team_key=self.team_key)
        self.hub_client = HubClient(hub_url=self.hub_url, team_key=self.team_key)

        self.cycle_count = len(self.memory.completed_cycles)
        self.reports: List[ReviewReport] = []

    def run_cycle(self, cut: int, protocol_version: int) -> ReviewReport:
        """Executes a single clinical review cycle across the 6 required nodes."""
        self.cycle_count += 1
        cycle = self.cycle_count
        trace: List[TraceEntry] = []

        log.info("Starting MONITOR review cycle %d (cut=%d, protocol_version=%d)", cycle, cut, protocol_version)

        # ------------------------------------------------------------------ #
        # Node 1: DETECT
        # ------------------------------------------------------------------ #
        findings, core = detect_node(self.atlas, cut, protocol_version, cycle, trace)

        # ------------------------------------------------------------------ #
        # Node 2: MEDICAL REVIEW
        # ------------------------------------------------------------------ #
        med_escalations, serious_findings, monitoring_only, dq_findings, comp_findings = medical_review_node(
            findings, core, self.memory, cut, cycle, trace
        )

        # ------------------------------------------------------------------ #
        # Node 3: DATA MANAGER
        # ------------------------------------------------------------------ #
        queries = data_manager_node(
            dq_findings, core, self.memory, self.gateway_client, cut, cycle, trace
        )

        # ------------------------------------------------------------------ #
        # Node 4: COMPLIANCE
        # ------------------------------------------------------------------ #
        deviations, site_flags, site_escalations = compliance_node(
            comp_findings, core, self.memory, protocol_version, cycle, trace
        )

        # ------------------------------------------------------------------ #
        # Node 5: HUMAN GATE
        # ------------------------------------------------------------------ #
        all_pending_escalations = med_escalations + site_escalations
        processed_escalations = human_gate_node(
            all_pending_escalations, core, self.memory, self.hub_client, cycle, trace
        )

        # ------------------------------------------------------------------ #
        # Node 6: EXECUTE
        # ------------------------------------------------------------------ #
        report = execute_node(
            cycle=cycle,
            cut=cut,
            protocol_version=protocol_version,
            findings=findings,
            serious_findings=serious_findings,
            monitoring_only=monitoring_only,
            queries=queries,
            escalations=processed_escalations,
            deviations=deviations,
            site_flags=site_flags,
            memory=self.memory,
            trace=trace,
        )

        self.reports.append(report)
        log.info("Cycle %d completed successfully (%d findings, %d escalations, %d queries)",
                 cycle, len(findings), len(processed_escalations), len(queries))
        return report

    def reset_memory(self) -> None:
        """Reset crew memory and cycle counters."""
        self.memory.reset()
        self.cycle_count = 0
        self.reports.clear()
