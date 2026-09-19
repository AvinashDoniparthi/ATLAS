"""Append-only audit trace store and ingestion for Stage 3."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from stage2.schemas import TraceEntry
from stage3.models import WatchTraceEntry

log = logging.getLogger("stage3.trace")


class TraceStore:
    """Central append-only audit trace repository."""

    def __init__(self):
        self.entries: List[WatchTraceEntry] = []
        self.by_decision_id: Dict[str, List[WatchTraceEntry]] = {}
        self.by_cut: Dict[int, List[WatchTraceEntry]] = {}
        self._seq = 0

    def append(self, entry: WatchTraceEntry) -> WatchTraceEntry:
        self._seq += 1
        entry.sequence = self._seq
        if not entry.trace_id:
            entry.trace_id = f"T-C{entry.cut}-{self._seq:05d}"
        self.entries.append(entry)

        if entry.decision_id:
            self.by_decision_id.setdefault(entry.decision_id, []).append(entry)
        self.by_cut.setdefault(entry.cut, []).append(entry)
        return entry

    def extend(self, entries: List[WatchTraceEntry]) -> None:
        for e in entries:
            self.append(e)

    def ingest_crew_trace(self, crew_trace: List[TraceEntry], cut: int) -> None:
        """Ingests Stage 2 ReviewCrew trace entries into the unified Stage 3 trace."""
        for te in crew_trace:
            wte = WatchTraceEntry(
                timestamp=te.timestamp,
                cycle=te.cycle,
                cut=cut,
                node=te.node,
                action=te.action,
                target=te.target,
                evidence=te.evidence,
                reason=te.reason,
                sequence=0,
                trace_id="",
            )
            self.append(wte)

    def for_decision(self, decision_id: str) -> List[WatchTraceEntry]:
        return list(self.by_decision_id.get(decision_id, []))

    def for_cut(self, cut: int) -> List[WatchTraceEntry]:
        return list(self.by_cut.get(cut, []))

    def all_entries(self) -> List[WatchTraceEntry]:
        return list(self.entries)

    def save(self, filepath: Path) -> None:
        """Persists the trace store as JSONL."""
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, "w", encoding="utf-8") as f:
                for e in self.entries:
                    f.write(json.dumps(e.model_dump()) + "\n")
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to save trace: %s", exc)
