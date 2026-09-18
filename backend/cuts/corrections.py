"""corrections.csv: values that are re-issued at a later cut."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.ingestion.csv_loader import load_table
from backend.normalization.rows import to_int

log = logging.getLogger("atlas.cuts")


@dataclass(frozen=True)
class Correction:
    cut: int
    domain: str
    usubjid: str
    seq: Optional[int]
    field: str
    old_value: str
    new_value: str
    reason: str
    row_no: int

    @property
    def key(self) -> tuple[str, str, Optional[int]]:
        return (self.domain, self.usubjid, self.seq)


def load_corrections(data_dir: str | Path) -> tuple[list[Correction], list[dict]]:
    rows, report = load_table(Path(data_dir) / "data" / "corrections.csv")
    out: list[Correction] = []
    bad: list[dict] = list(report.malformed)
    for i, r in enumerate(rows, start=2):
        cut = to_int(r.get("cut"))
        domain = (r.get("domain") or "").strip().upper()
        usubjid = (r.get("usubjid") or r.get("USUBJID") or "").strip()
        fld = (r.get("field") or "").strip()
        if cut is None or not domain or not usubjid or not fld:
            bad.append({"line": i, "reason": "missing cut/domain/usubjid/field"})
            continue
        out.append(
            Correction(
                cut=cut,
                domain=domain,
                usubjid=usubjid,
                seq=to_int(r.get("seq")),
                field=fld,
                old_value=(r.get("old_value") or "").strip(),
                new_value=(r.get("new_value") or "").strip(),
                reason=(r.get("reason") or "").strip(),
                row_no=i,
            )
        )
    if bad:
        log.warning("corrections.csv: %d malformed rows skipped", len(bad))
    return out, bad


def corrections_in_force(corrections: list[Correction], cut: Optional[int]) -> dict[tuple, list[Correction]]:
    """Group corrections effective at ``cut`` by record key, ordered by cut (later wins)."""
    grouped: dict[tuple, list[Correction]] = {}
    for c in corrections:
        if cut is not None and c.cut > cut:
            continue
        grouped.setdefault(c.key, []).append(c)
    for lst in grouped.values():
        lst.sort(key=lambda c: (c.cut, c.row_no))
    return grouped
