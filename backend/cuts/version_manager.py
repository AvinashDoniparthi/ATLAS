"""cut -> protocol version resolution (from cuts.csv) and build identity."""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.ingestion.csv_loader import load_table
from backend.normalization.rows import to_int

log = logging.getLogger("atlas.cuts")


@dataclass(frozen=True)
class CutRow:
    cut: int
    protocol_version: Optional[int]
    new_records: Optional[int]
    corrections: Optional[int]


def load_cut_table(data_dir: str | Path) -> list[CutRow]:
    rows, _ = load_table(Path(data_dir) / "data" / "cuts.csv")
    out: list[CutRow] = []
    for r in rows:
        cut = to_int(r.get("cut"))
        if cut is None:
            continue
        out.append(
            CutRow(
                cut=cut,
                protocol_version=to_int(r.get("protocol_version")),
                new_records=to_int(r.get("new_records")),
                corrections=to_int(r.get("corrections")),
            )
        )
    out.sort(key=lambda c: c.cut)
    return out


def protocol_version_for_cut(cuts: list[CutRow], cut: Optional[int]) -> Optional[int]:
    """Version of the highest listed cut <= ``cut``; None cut -> latest listed."""
    listed = [c for c in cuts if c.protocol_version is not None]
    if not listed:
        return None
    if cut is None:
        return listed[-1].protocol_version
    eligible = [c for c in listed if c.cut <= cut]
    if not eligible:
        return listed[0].protocol_version
    return eligible[-1].protocol_version


def data_signature(data_dir: str | Path) -> str:
    """Cheap fingerprint of every file under data/, documents/, responses/.

    Uses size + mtime, so a changed dataset (the mid-stage announcement) is
    detected without re-reading content.
    """
    root = Path(data_dir)
    h = hashlib.sha1()
    for sub in ("data", "documents", "responses"):
        d = root / sub
        if not d.exists():
            continue
        for p in sorted(d.rglob("*")):
            if p.is_file():
                st = p.stat()
                h.update(f"{p.relative_to(root)}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


@dataclass(frozen=True)
class BuildKey:
    """Identity of one built graph; every derived cache is keyed on this."""

    data_signature: str
    cut: Optional[int]
    protocol_version: Optional[int]
    corrections_applied: int

    def short(self) -> str:
        return f"{self.data_signature[:10]}|cut={self.cut}|pv={self.protocol_version}|corr={self.corrections_applied}"
