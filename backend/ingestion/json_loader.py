"""Loaders for responses/site_replies.json and responses/monitor_decisions.json.

Exposed as evidence/context lookups only (Problem 1). No MONITOR workflow.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("atlas.ingest")


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        log.warning("json file missing: %s", path)
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        log.error("failed to read %s: %s", path, exc)
        return None


@dataclass
class SiteReplies:
    replies: dict[str, list[Any]] = field(default_factory=dict)
    default: list[Any] = field(default_factory=list)
    loaded: bool = False

    @staticmethod
    def key(domain: str, usubjid: str, seq: Optional[int]) -> str:
        return f"{domain}|{usubjid}|{'' if seq is None else seq}"

    def lookup(self, domain: str, usubjid: str, seq: Optional[int]) -> tuple[list[Any], bool]:
        """Return (reply, exact_hit). Falls back to _default on a miss."""
        k = self.key(domain, usubjid, seq)
        if k in self.replies:
            return self.replies[k], True
        return list(self.default), False

    def keys_for_subject(self, usubjid: str) -> list[str]:
        return [k for k in self.replies if k.split("|")[1:2] == [usubjid]]


@dataclass
class MonitorDecisions:
    decisions: dict[str, list[Any]] = field(default_factory=dict)
    loaded: bool = False

    def lookup(self, code: str, usubjid_or_site: str) -> Optional[list[Any]]:
        return self.decisions.get(f"{code}|{usubjid_or_site}")

    def codes(self) -> list[str]:
        return sorted({k.split("|")[0] for k in self.decisions})


def load_site_replies(data_dir: str | Path) -> SiteReplies:
    data = _read_json(Path(data_dir) / "responses" / "site_replies.json")
    if not data:
        return SiteReplies()
    return SiteReplies(
        replies={k: v for k, v in (data.get("replies") or {}).items()},
        default=list(data.get("_default") or []),
        loaded=True,
    )


def load_monitor_decisions(data_dir: str | Path) -> MonitorDecisions:
    data = _read_json(Path(data_dir) / "responses" / "monitor_decisions.json")
    if not data:
        return MonitorDecisions()
    return MonitorDecisions(decisions={k: v for k, v in (data.get("decisions") or {}).items()}, loaded=True)
