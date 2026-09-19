"""WatchState — Cut-aware surveillance ledger and state tracking.

Maintains:
  - Stable finding ledger keyed by fingerprint (code|usubjid|site|domain|seq)
  - Quarantined sites (data preserved, findings isolated)
  - Untrusted lab record keys
  - Document content hash registry
  - Known sites & domains
  - Cut execution history
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger("stage3.state")


class WatchState:
    """State manager persisted across sequential surveillance cuts."""

    def __init__(self, persistence_file: Optional[str | Path] = None):
        self.persistence_file = Path(persistence_file) if persistence_file else None
        self.findings_ledger: Dict[str, dict] = {}  # fp -> finding dict
        self.quarantined_sites: Set[str] = set()
        self.untrusted_lab_keys: Set[tuple] = set()  # (domain, usubjid, seq)
        self.doc_hash_registry: Dict[str, str] = {}  # doc_name -> sha256
        self.active_protocol_version: Optional[int] = None
        self.active_lab_manual: Optional[str] = None
        self.known_domains: Set[str] = set()
        self.known_sites: Set[str] = set()
        self.cut_history: List[dict] = []
        self.load()

    @staticmethod
    def finding_fingerprint(code: str, usubjid: str, site: Optional[str], domain: str = "", seq: Optional[int] = None) -> str:
        s = "" if seq is None else str(seq)
        st = site or ""
        return f"{code}|{usubjid}|{st}|{domain}|{s}".upper()

    def record_finding(self, finding: dict, cut: int) -> tuple[bool, str]:
        """Record finding in ledger. Returns (is_new, fingerprint)."""
        code = finding.get("code", "")
        usubjid = finding.get("usubjid", "")
        site = finding.get("site", "")
        # Get first evidence domain/seq if available
        ev = finding.get("evidence", [])
        dom, seq = "", None
        if ev and hasattr(ev[0], "domain"):
            dom = ev[0].domain
            seq = ev[0].seq
        elif ev and isinstance(ev[0], dict):
            dom = ev[0].get("domain", "")
            seq = ev[0].get("seq")

        fp = self.finding_fingerprint(code, usubjid, site, dom, seq)
        is_new = fp not in self.findings_ledger

        if is_new:
            self.findings_ledger[fp] = {
                **finding,
                "fingerprint": fp,
                "first_detected_cut": cut,
                "last_seen_cut": cut,
                "status": "CONFIRMED",
                "evidence_keys": [
                    (getattr(e, "domain", ""), getattr(e, "usubjid", ""), getattr(e, "seq", None))
                    for e in ev
                ],
            }
        else:
            self.findings_ledger[fp]["last_seen_cut"] = cut
        return is_new, fp

    def quarantine_site(self, site: str) -> None:
        self.quarantined_sites.add(site.upper())

    def mark_lab_untrusted(self, domain: str, usubjid: str, seq: Optional[int]) -> None:
        self.untrusted_lab_keys.add((domain, usubjid, seq))

    def is_lab_untrusted(self, domain: str, usubjid: str, seq: Optional[int]) -> bool:
        return (domain, usubjid, seq) in self.untrusted_lab_keys

    def save(self) -> None:
        if not self.persistence_file:
            return
        try:
            self.persistence_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "findings_ledger": self.findings_ledger,
                "quarantined_sites": sorted(self.quarantined_sites),
                "untrusted_lab_keys": [list(k) for k in self.untrusted_lab_keys],
                "doc_hash_registry": self.doc_hash_registry,
                "active_protocol_version": self.active_protocol_version,
                "active_lab_manual": self.active_lab_manual,
                "known_domains": sorted(self.known_domains),
                "known_sites": sorted(self.known_sites),
                "cut_history": self.cut_history,
            }

            def _default(obj: Any) -> Any:
                if hasattr(obj, "model_dump"):
                    return obj.model_dump()
                if hasattr(obj, "as_dict"):
                    return obj.as_dict()
                if hasattr(obj, "__dict__"):
                    return obj.__dict__
                return str(obj)

            with open(self.persistence_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=_default)
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to save WatchState: %s", exc)

    def load(self) -> None:
        if not self.persistence_file or not self.persistence_file.is_file():
            return
        try:
            with open(self.persistence_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.findings_ledger = data.get("findings_ledger", {})
            self.quarantined_sites = set(data.get("quarantined_sites", []))
            self.untrusted_lab_keys = {tuple(k) for k in data.get("untrusted_lab_keys", [])}
            self.doc_hash_registry = data.get("doc_hash_registry", {})
            self.active_protocol_version = data.get("active_protocol_version")
            self.active_lab_manual = data.get("active_lab_manual")
            self.known_domains = set(data.get("known_domains", []))
            self.known_sites = set(data.get("known_sites", []))
            self.cut_history = data.get("cut_history", [])
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to load WatchState: %s", exc)
