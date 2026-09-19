"""DecisionStore maintaining all clinical and operational surveillance decisions."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from stage3.models import Decision

log = logging.getLogger("stage3.decisions")


class DecisionStore:
    """Repository of structured surveillance decisions."""

    def __init__(self):
        self.decisions: Dict[str, Decision] = {}  # decision_id -> Decision
        self.by_cut: Dict[int, List[Decision]] = {}
        self.by_target: Dict[str, List[Decision]] = {}

    def add(self, dec: Decision) -> Decision:
        if dec.decision_id in self.decisions:
            # Update existing decision status if transition
            existing = self.decisions[dec.decision_id]
            if existing.status != dec.status:
                existing.status_history.append({"cut": dec.cut, "old_status": existing.status, "new_status": dec.status})
                existing.status = dec.status
                existing.reason = dec.reason
            return existing

        self.decisions[dec.decision_id] = dec
        self.by_cut.setdefault(dec.cut, []).append(dec)
        if dec.target:
            self.by_target.setdefault(dec.target, []).append(dec)
        return dec

    def extend(self, decisions: List[Decision]) -> None:
        for d in decisions:
            self.add(d)

    def get(self, decision_id: str) -> Optional[Decision]:
        return self.decisions.get(decision_id)

    def all_decisions(self) -> List[Decision]:
        return list(self.decisions.values())

    def filter(
        self,
        cut: Optional[int] = None,
        status: Optional[str] = None,
        code: Optional[str] = None,
        target: Optional[str] = None,
    ) -> List[Decision]:
        res = list(self.decisions.values())
        if cut is not None:
            res = [d for d in res if d.cut == cut]
        if status is not None:
            res = [d for d in res if d.status == status]
        if code is not None:
            res = [d for d in res if d.code == code]
        if target is not None:
            res = [d for d in res if d.target == target]
        return res

    def save(self, filepath: Path) -> None:
        """Persists decisions as JSON array."""
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            data = [d.model_dump() for d in self.decisions.values()]
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to save decisions: %s", exc)
