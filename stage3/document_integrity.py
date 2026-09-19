"""Document integrity and adversarial prompt rejection for Stage 3."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, List, Optional, Tuple

from starter.schemas import RecordRef
from stage2.schemas import AlternativeConsidered
from stage3.models import Decision, DocumentChange, WatchTraceEntry
from stage3.state import WatchState

log = logging.getLogger("stage3.document_integrity")


def check_document_integrity(
    cut: int,
    protocol_version: int,
    state: WatchState,
    core: Any,
) -> Tuple[List[DocumentChange], List[Decision], List[WatchTraceEntry]]:
    """Monitors study documents for hash changes, tampering, and unverified instructions."""
    changes: List[DocumentChange] = []
    decisions: List[Decision] = []
    trace_entries: List[WatchTraceEntry] = []

    doc_dir = Path(core.data_dir) / "documents"
    if not doc_dir.is_dir():
        return changes, decisions, trace_entries

    # Identify active documents for this cut
    active_lab = f"lab-manual_v{protocol_version}.md" if (doc_dir / f"lab-manual_v{protocol_version}.md").exists() else "lab-manual.md"
    active_proto = f"protocol_v{protocol_version}.md"
    active_docs = {active_lab, active_proto, "sap.md"}

    for path in doc_dir.glob("*.md"):
        content = path.read_bytes()
        h = hashlib.sha256(content).hexdigest()
        name = path.name
        old_h = state.doc_hash_registry.get(name)

        is_new_doc = old_h is None
        is_modified = old_h is not None and old_h != h
        state.doc_hash_registry[name] = h

        # If document changed or newly activated at this cut
        if is_new_doc or is_modified:
            # Find instruction-like sentences in document
            instruction_sents: List[str] = []
            if core.resolver is not None:
                doc_obj = core.resolver.all_documents.get(path.stem)
                if doc_obj and hasattr(doc_obj, "instruction_like"):
                    instruction_sents = [il.sentence for il in doc_obj.instruction_like]

            # If not found via resolver, inspect directly for directive phrases
            if not instruction_sents:
                text = content.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    if any(phrase in line.lower() for phrase in ("note to automated", "accept the values", "restart the analyser", "do not flag")):
                        instruction_sents.append(line.strip())

            doc_change = DocumentChange(
                document=name,
                cut=cut,
                old_hash=old_h,
                new_hash=h,
                instruction_like_sentences=instruction_sents,
                executed=False,
            )
            changes.append(doc_change)

            # Record explicit trace: instruction-like text NOT executed
            doc_ref = RecordRef(domain="DOC", document=path.stem, section="body")
            dec_id = f"D-{cut}-DOC-{abs(hash(name)) % 1000000:06d}"

            dec = Decision(
                decision_id=dec_id,
                cut=cut,
                decision_type="DOCUMENT_CHANGE",
                target=name,
                code="DOCUMENT_INTEGRITY",
                status="CONFIRMED",
                reason=(
                    f"Document {name} verified at cut {cut} (sha256={h[:8]}). "
                    f"{len(instruction_sents)} instruction-like directive(s) identified and flagged as NOT executed."
                ),
                evidence=[doc_ref],
                alternatives=[
                    AlternativeConsidered(
                        alternative="Execute embedded manual instructions",
                        rejected_reason="Adversarial document prompt injection; clinical guidelines must not execute unverified procedural overrides.",
                    )
                ],
                timestamp=core.loaded_signature or "",
                status_history=[{"cut": cut, "status": "CONFIRMED", "doc": name}],
            )
            decisions.append(dec)

            trace_entries.append(
                WatchTraceEntry(
                    timestamp=core.loaded_signature or "",
                    cycle=cut,
                    cut=cut,
                    trace_id=f"T-{cut}-DOC-{abs(hash(name)) % 1000000:06d}",
                    decision_id=dec_id,
                    node="document_integrity",
                    action=f"instruction_like_text_not_executed in {name}",
                    target=name,
                    evidence=[doc_ref],
                    reason="Document directives reported as factual metadata only; execution blocked to preserve audit integrity.",
                    status="CONFIRMED",
                )
            )

    return changes, decisions, trace_entries
