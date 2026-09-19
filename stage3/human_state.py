"""Human medical monitor state machine and standing limits for Stage 3."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from starter.schemas import RecordRef
from stage2.schemas import AlternativeConsidered, MedicalEscalation
from stage3.clarify import answer_clarification
from stage3.config import WatchConfig
from stage3.models import Decision, HumanGateItem, WatchTraceEntry

log = logging.getLogger("stage3.human_state")


class HumanState:
    """Manages escalation lifecycles, monitor replies, and standing limits."""

    def __init__(self, config: WatchConfig):
        self.config = config
        self.items: Dict[str, HumanGateItem] = {}  # fingerprint -> HumanGateItem

    @staticmethod
    def escalation_fingerprint(code: str, target: str) -> str:
        return f"{code}|{target}".upper()

    def ingest_new_escalation(
        self,
        esc: MedicalEscalation,
        cut: int,
        core: Any,
        memory: Any,
        hub_client: Any,
    ) -> Tuple[Optional[Decision], Optional[WatchTraceEntry]]:
        """Registers a newly raised escalation at cut and applies initial decision."""
        target = esc.site if (esc.code.startswith("SITE_") and esc.site) else (esc.usubjid or esc.site or "UNKNOWN")
        fp = self.escalation_fingerprint(esc.code, target)

        if fp in self.items:
            # Already tracked
            return None, None

        item = HumanGateItem(
            escalation_id=esc.escalation_id,
            fingerprint=fp,
            code=esc.code,
            target=target,
            detected_at_cut=cut,
            escalated_at_cut=cut,
            submitted_cuts=[cut],
            decision=esc.status or "PENDING",
            reason=esc.monitor_reason or "",
            cuts_pending=0,
            action_taken=esc.action_taken,
        )
        self.items[fp] = item

        # If escalation came with a non-pending initial decision from crew
        if esc.status == "APPROVED":
            item.reply_cut = cut
            item.decision = "APPROVED"
            return self._build_decision_and_trace(item, cut, "APPROVED", esc.evidence, core)
        elif esc.status in ("REJECTED", "MONITORING"):
            item.reply_cut = cut
            item.decision = "REJECTED"
            return self._build_decision_and_trace(item, cut, "REJECTED", esc.evidence, core)
        elif esc.status in ("CLARIFICATION_REQUIRED", "CLARIFY"):
            item.decision = "CLARIFY"
            item.clarification_question = esc.clarification_requested or esc.monitor_reason
            return self._handle_clarification(item, cut, esc.evidence, core, memory, hub_client)

        # Default: PENDING
        item.decision = "PENDING"
        memory.record_escalation(esc)
        return self._build_decision_and_trace(item, cut, "PENDING", esc.evidence, core)

    def process_pending(
        self,
        cut: int,
        core: Any,
        memory: Any,
        hub_client: Any,
    ) -> Tuple[List[Decision], List[WatchTraceEntry]]:
        """Ages pending items, checks for delayed responses, and transitions to standing limits."""
        decisions: List[Decision] = []
        trace_entries: List[WatchTraceEntry] = []

        for fp, item in list(self.items.items()):
            if item.decision in ("APPROVED", "REJECTED", "STANDING_LIMITS"):
                continue

            if cut not in item.submitted_cuts:
                item.submitted_cuts.append(cut)

            # Re-check monitor response via hub_client
            dummy_esc = MedicalEscalation(
                escalation_id=item.escalation_id,
                code=item.code,
                usubjid=item.target if item.target.startswith("042-") else "",
                site=item.target if not item.target.startswith("042-") else core.site_of(item.target),
                summary=f"Follow-up on pending escalation {item.code}",
                status="PENDING",
                cycle=cut,
            )

            reply_dec, reply_rsn = hub_client.send_escalation(dummy_esc, core, resubmission=False)

            if reply_dec == "APPROVED":
                item.decision = "APPROVED"
                item.reply_cut = cut
                item.reason = reply_rsn
                item.action_taken = f"Action approved by medical monitor at cut {cut}."
                memory.update_escalation_decision(item.escalation_id, "APPROVED", reply_rsn)
                d, t = self._build_decision_and_trace(item, cut, "APPROVED", [], core)
                if d and t:
                    decisions.append(d)
                    trace_entries.append(t)

            elif reply_dec == "REJECTED":
                item.decision = "REJECTED"
                item.reply_cut = cut
                item.reason = reply_rsn
                item.action_taken = "Downgraded to routine monitoring."
                memory.update_escalation_decision(item.escalation_id, "REJECTED", reply_rsn)
                d, t = self._build_decision_and_trace(item, cut, "REJECTED", [], core)
                if d and t:
                    decisions.append(d)
                    trace_entries.append(t)

            elif reply_dec == "CLARIFY":
                item.clarification_question = reply_rsn
                d, t = self._handle_clarification(item, cut, [], core, memory, hub_client)
                if d and t:
                    decisions.append(d)
                    trace_entries.append(t)

            else:
                # Still PENDING
                item.cuts_pending += 1
                if item.cuts_pending >= self.config.standing_limit_cuts:
                    # Transition to STANDING_LIMITS
                    item.decision = "STANDING_LIMITS"
                    item.action_taken = (
                        f"Unanswered after {item.cuts_pending} cuts; transitioned to protocol standing safety limits."
                    )
                    memory.standing_limits[item.fingerprint] = item.model_dump()
                    memory.save()

                    dec_id = f"D-{cut}-LIMITS-{abs(hash(item.fingerprint)) % 1000000:06d}"
                    dec = Decision(
                        decision_id=dec_id,
                        cut=cut,
                        decision_type="STANDING_LIMITS",
                        target=item.target,
                        code=item.code,
                        status="STANDING_LIMITS",
                        reason=(
                            f"Escalation {item.code} for {item.target} pending for {item.cuts_pending} cuts "
                            f"(exceeded {self.config.standing_limit_cuts}-cut threshold). Transitioned to standing limits."
                        ),
                        evidence=[],
                        alternatives=[
                            AlternativeConsidered(
                                alternative="Assume silent clinical approval",
                                rejected_reason="Unanswered != approved; safety protocol requires standing limits when human monitor is unreachable.",
                            )
                        ],
                        timestamp=core.loaded_signature or "",
                        status_history=[{"cut": cut, "status": "STANDING_LIMITS", "cuts_pending": item.cuts_pending}],
                    )
                    decisions.append(dec)

                    trace_entries.append(
                        WatchTraceEntry(
                            timestamp=core.loaded_signature or "",
                            cycle=cut,
                            cut=cut,
                            trace_id=f"T-{cut}-LIMITS-{abs(hash(item.fingerprint)) % 1000000:06d}",
                            decision_id=dec_id,
                            node="human_gate",
                            action=f"STANDING_LIMITS: {item.code} {item.target} transitioned after {item.cuts_pending} unanswered cuts",
                            target=item.target,
                            evidence=[],
                            reason="Human monitor response timeout: holding actions governed by protocol standing safety limits.",
                            status="STANDING_LIMITS",
                        )
                    )

        return decisions, trace_entries

    def _handle_clarification(
        self,
        item: HumanGateItem,
        cut: int,
        evidence: List[RecordRef],
        core: Any,
        memory: Any,
        hub_client: Any,
    ) -> Tuple[Optional[Decision], Optional[WatchTraceEntry]]:
        q_text = item.clarification_question or "Clarification requested"
        solved = answer_clarification(core, item.code, item.target, q_text)

        if solved is not None:
            ans_text, cited_refs = solved
            item.clarification_answer = ans_text
            # Resubmit escalation with evidence
            dummy_esc = MedicalEscalation(
                escalation_id=item.escalation_id,
                code=item.code,
                usubjid=item.target if item.target.startswith("042-") else "",
                site=item.target if not item.target.startswith("042-") else core.site_of(item.target),
                summary=f"Resubmitted escalation: {ans_text}",
                evidence=cited_refs,
                status="RESUBMITTED",
                cycle=cut,
            )
            resub_dec, resub_rsn = hub_client.send_escalation(dummy_esc, core, resubmission=True)
            item.decision = "APPROVED"
            item.reply_cut = cut
            item.reason = resub_rsn
            item.action_taken = f"Approved after data verification: {ans_text}"
            memory.update_escalation_decision(item.escalation_id, "APPROVED", resub_rsn)
            return self._build_decision_and_trace(item, cut, "APPROVED", cited_refs, core)
        else:
            item.decision = "UNRESOLVED"
            item.action_taken = "Clarification question cannot be verified from trial dataset; escalation remains open."
            return self._build_decision_and_trace(item, cut, "UNRESOLVED", evidence, core)

    def _build_decision_and_trace(
        self,
        item: HumanGateItem,
        cut: int,
        status: str,
        evidence: List[RecordRef],
        core: Any,
    ) -> Tuple[Decision, WatchTraceEntry]:
        dec_id = f"D-{cut}-HUMAN-{abs(hash(item.fingerprint)) % 1000000:06d}"
        dec = Decision(
            decision_id=dec_id,
            cut=cut,
            decision_type="HUMAN_GATE",
            target=item.target,
            code=item.code,
            status=status,
            reason=item.reason or f"Medical monitor {status} adjudication for {item.code} {item.target}.",
            evidence=evidence,
            alternatives=[
                AlternativeConsidered(
                    alternative="Bypass human monitor adjudication",
                    rejected_reason="Protocol §7 mandates formal medical monitor adjudication for safety escalations.",
                )
            ],
            timestamp=core.loaded_signature or "",
            status_history=[{"cut": cut, "status": status}],
        )
        trace_entry = WatchTraceEntry(
            timestamp=core.loaded_signature or "",
            cycle=cut,
            cut=cut,
            trace_id=f"T-{cut}-HUMAN-{abs(hash(item.fingerprint)) % 1000000:06d}",
            decision_id=dec_id,
            node="human_gate",
            action=f"{item.code} {item.target} -> {status}",
            target=item.target,
            evidence=evidence,
            reason=item.reason or f"Medical monitor status transition to {status}.",
            status=status,
        )
        return dec, trace_entry
