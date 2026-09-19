"""Node 3 — DATA MANAGER

Handles data-quality findings by generating specific, record-cited, actionable,
and deduplicated site queries dispatched to the competition gateway (POST /queries)
or verified against responses/site_replies.json.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, List

from stage2.client import GatewayClient
from stage2.memory import ReviewMemory
from stage2.schemas import SiteQuery, TraceEntry

log = logging.getLogger("stage2.data_manager")


def data_manager_node(
    data_quality_findings: List[dict],
    core: Any,
    memory: ReviewMemory,
    client: GatewayClient,
    cut: int,
    cycle: int,
    trace: List[TraceEntry],
) -> List[SiteQuery]:
    """Executes Node 3: DATA MANAGER.

    Returns:
        queries_list: list of all queries (new and existing tracked open queries)
    """
    ts = datetime.datetime.now().isoformat()
    queries: List[SiteQuery] = []
    new_count = 0
    duplicate_count = 0

    for f in data_quality_findings:
        code = f["code"]
        u = f.get("usubjid") or ""
        site = f.get("site") or core.site_of(u)
        details = f.get("details") or {}
        evidence = f.get("evidence") or []

        # Find primary record key for citation
        domain = "AE" if code == "AE_BEFORE_FIRST_DOSE" else ("EX" if code == "MISSING_DOSE" else "DM")
        seq: int | None = None
        for r in evidence:
            if r.domain == domain and r.seq is not None:
                seq = r.seq
                break
        if seq is None and "record" in details:
            # Tuple (domain, usubjid, seq)
            rec_tup = details["record"]
            if len(rec_tup) >= 3:
                domain, _, seq = rec_tup[0], rec_tup[1], rec_tup[2]

        # Construct specific, actionable query text
        if code == "AE_BEFORE_FIRST_DOSE":
            term = details.get("AETERM") or f["summary"].split(" started ")[0]
            start = details.get("AESTDTC") or "unknown date"
            ref_date = details.get("first_dose_date") or "first dose"
            query_text = (
                f"AE '{term}' starts {start}, before first dose {ref_date}. "
                "Please verify the AE start date against source and correct or confirm."
            )
        elif code == "MISSING_DOSE":
            visit = details.get("visit") or "scheduled visit"
            query_text = (
                f"Missing exposure record at visit {visit} for subject {u}. "
                "Please verify drug administration against pharmacy logs and submit dose record or deviation."
            )
        elif code == "DUPLICATE_SUBJECT":
            canonical = details.get("canonical") or "existing record"
            query_text = (
                f"Subject {u} shares clinical demographic identity keys with {canonical}. "
                "Please verify subject identity documents against potential dual-enrollment."
            )
        else:
            query_text = f"Data anomaly identified on subject {u} ({f['summary']}). Please verify source data."

        # Check deduplication
        fp = memory.query_fingerprint(u, domain, seq, code)
        if memory.has_query(fp):
            duplicate_count += 1
            continue

        qid = f"QRY-{domain}-{u}-{seq or 1}-{cycle}"
        query = SiteQuery(
            query_id=qid,
            usubjid=u,
            site=site,
            domain=domain,
            seq=seq,
            cut=cut,
            query_text=query_text,
            status="OPEN",
            fingerprint=fp,
            cycle=cycle,
        )

        # Dispatch via gateway (or local fallback)
        status, reply = client.send_query(query, core)
        query.status = status
        query.reply = reply

        memory.record_query(query)
        queries.append(query)
        new_count += 1

        trace.append(
            TraceEntry(
                timestamp=ts,
                cycle=cycle,
                node="data_manager",
                action=f"Dispatched query {qid} on {domain}/{u}/{seq} ({status})",
                target=u,
                evidence=[r for r in evidence if r.domain == domain],
                reason=query_text,
            )
        )

    # Log node summary trace
    trace.append(
        TraceEntry(
            timestamp=ts,
            cycle=cycle,
            node="data_manager",
            action=f"{new_count} queries raised, {duplicate_count} duplicates skipped",
            target=f"cut={cut}",
            reason="Actionable, record-cited data queries with persistent fingerprint deduplication",
        )
    )

    return queries
