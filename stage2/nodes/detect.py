"""Node 1 — DETECT

Runs the existing Stage 1 Atlas implementation against the specified cut and
protocol version to discover all CDISC findings while preserving exact evidence.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, List, Tuple

from stage1.atlas import Atlas, _ref_to_official
from stage2.schemas import TraceEntry
from backend.rules import run_all

log = logging.getLogger("stage2.detect")


def detect_node(
    atlas: Atlas,
    cut: int,
    protocol_version: int,
    cycle: int,
    trace: List[TraceEntry],
) -> Tuple[List[dict], Any]:
    """Executes Node 1: DETECT.

    Returns:
        (findings_list, core)
    """
    ts = datetime.datetime.now().isoformat()
    # 1. Build graph at the requested cut and protocol version
    atlas.graph.build(cut=cut, protocol_version=protocol_version)
    core = atlas.graph.core

    # 2. Run all deterministic rules over the built graph core
    rule_results = run_all(core)
    findings_list: List[dict] = []

    total_count = 0
    for code, findings in rule_results.items():
        for f in findings:
            total_count += 1
            # Convert evidence items to official RecordRef instances
            ev_refs = []
            for item in f.evidence:
                ref = _ref_to_official(item.ref, core)
                if ref is not None:
                    ev_refs.append(ref)

            # Determine finding code and severity
            subcode = f.details.get("subcode")
            finding_code = subcode if subcode == "SAE_MISCODED" else f.code

            severity = "MEDIUM"
            if f.code == "SERIOUS_AE" or finding_code == "SAE_MISCODED":
                severity = "CRITICAL" if (subcode == "SAE_MISCODED" or finding_code == "SAE_MISCODED") else "HIGH"
            elif f.code == "HYS_LAW_CANDIDATE":
                severity = "CRITICAL"
            elif f.code in ("DOSING_ERROR", "ELIGIBILITY_VIOLATION"):
                severity = "HIGH"

            # Preserved clinical rationale
            if finding_code == "SAE_MISCODED":
                rationale = f"{f.details.get('AETERM', 'Event')} has AESHOSP=Y but AESER=N"
            else:
                rationale = f.summary

            findings_list.append({
                "code": finding_code,
                "usubjid": f.usubjid,
                "site": f.site or core.site_of(f.usubjid),
                "summary": f.summary,
                "severity": severity,
                "rationale": rationale,
                "details": f.details,
                "evidence": ev_refs,
                "flags": list(f.flags),
                "confidence": getattr(f, "confidence", 0.9),
                "protocol_version": protocol_version,
                "cut": cut,
            })

    # 3. Live audit trace
    trace.append(
        TraceEntry(
            timestamp=ts,
            cycle=cycle,
            node="detect",
            action=f"{total_count} findings under protocol v{protocol_version} at cut {cut}",
            target=f"cut={cut}|pv={protocol_version}",
            reason="Exhaustive evaluation of CDISC rules against StudyGraph",
        )
    )

    return findings_list, core
