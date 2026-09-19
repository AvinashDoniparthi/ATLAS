"""Global budget management and graceful degradation for Stage 3."""
from __future__ import annotations

import logging
import time
from typing import List, Optional, Tuple

from stage2.schemas import AlternativeConsidered
from stage3.config import WatchConfig
from stage3.models import BudgetState, Decision, WatchTraceEntry

log = logging.getLogger("stage3.budget")


class BudgetManager:
    """Manages global wall-clock execution budget and enforces graceful degradation."""

    def __init__(self, config: WatchConfig):
        self.config = config
        self.total_ms: float = max(config.budget_total_ms, 1.0)
        self.spent_ms: float = 0.0
        self.current_tier: str = "FULL"  # FULL | REDUCED | ESSENTIAL
        self.cut_start_time: float = 0.0
        self.cut_durations: dict[int, float] = {}
        self.degradation_events: List[dict] = []

    def start_cut(self, cut: int) -> Tuple[Optional[Decision], Optional[WatchTraceEntry]]:
        """Starts timing cut and checks if degradation tier changed."""
        self.cut_start_time = time.perf_counter()
        return self._evaluate_tier(cut)

    def end_cut(self, cut: int) -> float:
        """Ends timing for cut and accumulates spent budget."""
        duration_ms = (time.perf_counter() - self.cut_start_time) * 1000.0
        self.cut_durations[cut] = duration_ms
        self.spent_ms += duration_ms
        return duration_ms

    def record_usage(self, ms: float) -> None:
        """Manually record additional spent time if needed."""
        self.spent_ms += ms

    def remaining_ms(self) -> float:
        return max(0.0, self.total_ms - self.spent_ms)

    def fraction_used(self) -> float:
        return min(1.0, self.spent_ms / self.total_ms)

    def state(self) -> BudgetState:
        frac = self.fraction_used()
        return BudgetState(
            total_ms=round(self.total_ms, 2),
            spent_ms=round(self.spent_ms, 2),
            remaining_ms=round(self.remaining_ms(), 2),
            fraction_used=round(frac, 4),
            tier=self.current_tier,
        )

    def allow(self, work_class: str) -> bool:
        """Determines whether non-essential work is permitted under current tier."""
        work_upper = work_class.upper()
        # Essential clinical safety, audit trace, decisions, and detectors are never gated
        if work_upper in ("CRITICAL", "SAFETY", "TRACE", "DECISION", "HUMAN_GATE", "DETECTOR", "AMENDMENT", "CORRECTIONS"):
            return True

        if self.current_tier == "FULL":
            return True
        elif self.current_tier == "REDUCED":
            # Disallow expensive narrative generation or secondary statistical correlation
            return work_upper not in ("EXTENDED_NARRATIVE", "SECONDARY_ANALYSIS")
        else:  # ESSENTIAL
            # Block all optional work
            return False

    def _evaluate_tier(self, cut: int) -> Tuple[Optional[Decision], Optional[WatchTraceEntry]]:
        frac = self.fraction_used()
        new_tier = "FULL"
        if frac >= self.config.budget_tier_essential:
            new_tier = "ESSENTIAL"
        elif frac >= self.config.budget_tier_reduced:
            new_tier = "REDUCED"

        if new_tier != self.current_tier:
            old_tier = self.current_tier
            self.current_tier = new_tier
            event_info = {
                "cut": cut,
                "old_tier": old_tier,
                "new_tier": new_tier,
                "spent_ms": self.spent_ms,
                "fraction": frac,
            }
            self.degradation_events.append(event_info)

            dec_id = f"D-{cut}-DEGRADE-{new_tier}"
            dec = Decision(
                decision_id=dec_id,
                cut=cut,
                decision_type="DEGRADATION",
                target="system_budget",
                code=f"TIER_{new_tier}",
                status="CONFIRMED",
                reason=(
                    f"Surveillance budget utilization reached {frac*100:.1f}%. "
                    f"Degraded operating tier from {old_tier} to {new_tier}. Non-essential tasks gated."
                ),
                evidence=[],
                alternatives=[
                    AlternativeConsidered(
                        alternative="Halt surveillance due to budget exhaustion",
                        rejected_reason="Safety surveillance cannot be halted mid-study; system must degrade gracefully to essential clinical rules.",
                    )
                ],
                timestamp=str(time.time()),
                status_history=[{"cut": cut, "tier": new_tier, "spent_ms": self.spent_ms}],
            )

            trace_entry = WatchTraceEntry(
                timestamp=str(time.time()),
                cycle=cut,
                cut=cut,
                trace_id=f"T-{cut}-DEGRADE-{new_tier}",
                decision_id=dec_id,
                node="budget",
                action=f"budget_degradation: {old_tier} -> {new_tier} (spent={self.spent_ms:.1f}ms)",
                target="budget_manager",
                evidence=[],
                reason=f"Operating tier adjusted to {new_tier} to ensure full 12-cut completion within limits.",
                status="CONFIRMED",
            )
            return dec, trace_entry

        return None, None
