"""Cross-cycle persistent memory for the MONITOR review crew.

Implements the memory rules required by the specification:
  - Rule 1: Query deduplication (no duplicate queries on same record/problem)
  - Rule 2: Escalation deduplication (no repeated escalations)
  - Rule 3: Two-cycle subject flag (flagged in cycle N-1 + cycle N -> automatic escalation)
  - Rule 4: Recurring site problem accumulation
  - Rule 5: Same cut re-run -> 0 new queries, 0 new escalations
  - Open query tracking
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from stage2.schemas import MedicalEscalation, SiteQuery

log = logging.getLogger("stage2.memory")


class ReviewMemory:
    """Maintains state across review cycles with optional disk persistence."""

    def __init__(self, persistence_file: Optional[str | Path] = None):
        self.persistence_file = Path(persistence_file) if persistence_file else None
        self.queries: Dict[str, dict] = {}  # fingerprint -> query_dict
        self.escalations: Dict[str, dict] = {}  # fingerprint -> escalation_dict
        self.rejected_escalations: Set[str] = set()  # fingerprints of rejected escalations
        self.subject_cycles: Dict[str, List[int]] = {}  # usubjid -> list of cycles where flagged
        self.site_issue_counts: Dict[str, Dict[str, int]] = {}  # site -> {issue_type: count}
        self.open_query_ids: Set[str] = set()
        self.completed_cycles: List[int] = []
        self.completed_cuts: Set[int] = set()
        self.resolutions: Dict[str, dict] = {}
        self.standing_limits: Dict[str, dict] = {}
        self.clarifications: Dict[str, dict] = {}
        self.quarantined_sites: Set[str] = set()
        self.untrusted_labs: Set[tuple] = set()
        self.load()

    def reset(self) -> None:
        """Clear all memory state (used for isolated test runs)."""
        self.queries.clear()
        self.escalations.clear()
        self.rejected_escalations.clear()
        self.subject_cycles.clear()
        self.site_issue_counts.clear()
        self.open_query_ids.clear()
        self.completed_cycles.clear()
        self.completed_cuts.clear()
        self.resolutions.clear()
        self.standing_limits.clear()
        self.clarifications.clear()
        self.quarantined_sites.clear()
        self.untrusted_labs.clear()
        if self.persistence_file and self.persistence_file.exists():
            try:
                self.persistence_file.unlink()
            except OSError:
                pass

    # ---- Query Deduplication --------------------------------------------- #
    @staticmethod
    def query_fingerprint(usubjid: str, domain: str, seq: Optional[int], issue_type: str) -> str:
        s = "" if seq is None else str(seq)
        return f"{domain}|{usubjid}|{s}|{issue_type}".upper()

    def has_query(self, fingerprint: str) -> bool:
        return fingerprint in self.queries

    def record_query(self, query: SiteQuery) -> None:
        fp = query.fingerprint or self.query_fingerprint(query.usubjid, query.domain, query.seq, "DATA_QUALITY")
        self.queries[fp] = query.model_dump()
        if query.status == "OPEN":
            self.open_query_ids.add(query.query_id)
        elif query.query_id in self.open_query_ids:
            self.open_query_ids.remove(query.query_id)
        self.save()

    def update_query_status(self, query_id: str, status: str, reply: Optional[str] = None) -> None:
        for fp, q in self.queries.items():
            if q.get("query_id") == query_id:
                q["status"] = status
                if reply:
                    q["reply"] = reply
                if status != "OPEN" and query_id in self.open_query_ids:
                    self.open_query_ids.remove(query_id)
                self.save()
                break

    # ---- Escalation Deduplication ----------------------------------------- #
    @staticmethod
    def escalation_fingerprint(code: str, usubjid_or_site: str) -> str:
        return f"{code}|{usubjid_or_site}".upper()

    def has_escalation(self, fingerprint: str) -> bool:
        return fingerprint in self.escalations

    def is_rejected(self, fingerprint: str) -> bool:
        return fingerprint in self.rejected_escalations

    def record_escalation(self, esc: MedicalEscalation) -> None:
        target = esc.site if (esc.code.startswith("SITE_") and esc.site) else (esc.usubjid or esc.site or "UNKNOWN")
        fp = self.escalation_fingerprint(esc.code, target)
        self.escalations[fp] = esc.model_dump()
        if esc.status == "REJECTED" or esc.status == "MONITORING" or esc.monitor_decision == "REJECTED":
            self.rejected_escalations.add(fp)
        self.save()

    def get_escalation(self, escalation_id: str) -> Optional[MedicalEscalation]:
        for fp, e in self.escalations.items():
            if e.get("escalation_id") == escalation_id:
                return MedicalEscalation(**e)
        return None

    def update_escalation_decision(self, escalation_id: str, decision: str, reason: Optional[str] = None) -> None:
        for fp, e in self.escalations.items():
            if e.get("escalation_id") == escalation_id:
                e["monitor_decision"] = decision
                e["monitor_reason"] = reason
                if decision == "APPROVED":
                    e["status"] = "APPROVED"
                elif decision == "REJECTED":
                    e["status"] = "MONITORING"
                    self.rejected_escalations.add(fp)
                elif decision == "CLARIFY":
                    e["status"] = "CLARIFICATION_REQUIRED"
                    e["clarification_requested"] = reason
                self.save()
                break

    # ---- Two-Cycle Subject Flagging --------------------------------------- #
    def record_subject_flag(self, usubjid: str, cycle: int, cut: int) -> bool:
        """Records that usubjid had findings in `cycle` at `cut`.

        Returns True if the subject was flagged in a previous cycle and this is a NEW cut
        (Rule 3: subject flagged in cycle 1 + cycle 2 on progression -> automatic escalation;
         Rule 5: re-running the same cut creates 0 new escalations).
        """
        is_same_cut = cut in self.completed_cuts
        cycles = self.subject_cycles.setdefault(usubjid, [])
        was_flagged_prev = (cycle - 1) in cycles and not is_same_cut
        if cycle not in cycles:
            cycles.append(cycle)
        self.save()
        return was_flagged_prev

    # ---- Recurring Site Problems ------------------------------------------ #
    def record_site_issue(self, site: str, issue_type: str, count: int = 1) -> int:
        """Accumulate recurring site problem counts. Returns the total occurrences."""
        issues = self.site_issue_counts.setdefault(site, {})
        issues[issue_type] = issues.get(issue_type, 0) + count
        self.save()
        return issues[issue_type]

    # ---- Cycle Completion ------------------------------------------------- #
    def record_cycle_completed(self, cycle: int, cut: int) -> None:
        if cycle not in self.completed_cycles:
            self.completed_cycles.append(cycle)
        if cut not in self.completed_cuts:
            self.completed_cuts.add(cut)
        self.save()

    # ---- Persistence ------------------------------------------------------ #
    def save(self) -> None:
        if not self.persistence_file:
            return
        try:
            data = {
                "queries": self.queries,
                "escalations": self.escalations,
                "rejected_escalations": list(self.rejected_escalations),
                "subject_cycles": self.subject_cycles,
                "site_issue_counts": self.site_issue_counts,
                "open_query_ids": list(self.open_query_ids),
                "completed_cycles": self.completed_cycles,
                "completed_cuts": list(self.completed_cuts),
                "resolutions": self.resolutions,
                "standing_limits": self.standing_limits,
                "clarifications": self.clarifications,
                "quarantined_sites": list(self.quarantined_sites),
                "untrusted_labs": [list(k) for k in self.untrusted_labs],
            }
            self.persistence_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.persistence_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to persist memory: %s", exc)

    def load(self) -> None:
        if not self.persistence_file or not self.persistence_file.is_file():
            return
        try:
            with open(self.persistence_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.queries = data.get("queries", {})
            self.escalations = data.get("escalations", {})
            self.rejected_escalations = set(data.get("rejected_escalations", []))
            self.subject_cycles = data.get("subject_cycles", {})
            self.site_issue_counts = data.get("site_issue_counts", {})
            self.open_query_ids = set(data.get("open_query_ids", []))
            self.completed_cycles = data.get("completed_cycles", [])
            self.completed_cuts = set(data.get("completed_cuts", []))
            self.resolutions = data.get("resolutions", {})
            self.standing_limits = data.get("standing_limits", {})
            self.clarifications = data.get("clarifications", {})
            self.quarantined_sites = set(data.get("quarantined_sites", []))
            self.untrusted_labs = {tuple(k) for k in data.get("untrusted_labs", [])}
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to load persisted memory: %s", exc)
