"""Tolerant CSV ingestion.

* Discovers domain files from ``<data_dir>/data/*.csv`` (upper-case stems are
  clinical domains; ``reference_ranges``, ``corrections``, ``cuts`` are tables).
* Reads headers from the files themselves; nothing about column sets is assumed
  beyond ``USUBJID`` (join key) and the optional ``<DOMAIN>SEQ``,
  ``cut_available``, ``corrected_at_cut`` columns.
* A malformed row is recorded in the ``LoadReport`` and skipped; it never stops
  the build.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from backend.graph.nodes import Record
from backend.normalization.rows import normalize_row, to_int

log = logging.getLogger("atlas.ingest")

TABLE_FILES = {"reference_ranges", "corrections", "cuts"}


@dataclass
class LoadReport:
    path: str
    domain: str
    headers: list[str] = field(default_factory=list)
    rows_read: int = 0
    rows_kept: int = 0
    malformed: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    missing: bool = False
    error: Optional[str] = None

    def summary(self) -> dict:
        return {
            "domain": self.domain,
            "file": Path(self.path).name,
            "rows_read": self.rows_read,
            "rows_kept": self.rows_kept,
            "malformed": len(self.malformed),
            "duplicate_keys": len(self.duplicates),
            "missing": self.missing,
            "error": self.error,
        }


def discover_domain_files(data_dir: str | Path) -> dict[str, Path]:
    """Return {DOMAIN: path} for every upper-case-stem CSV in data/."""
    d = Path(data_dir) / "data"
    out: dict[str, Path] = {}
    if not d.exists():
        log.warning("data directory missing: %s", d)
        return out
    for p in sorted(d.glob("*.csv")):
        stem = p.stem
        if stem in TABLE_FILES or not stem.isupper():
            continue
        out[stem] = p
    return out


def read_csv_rows(path: str | Path) -> tuple[list[str], list[dict[str, str]], list[dict]]:
    """Read a CSV into (headers, rows, malformed). Never raises on bad rows."""
    path = Path(path)
    headers: list[str] = []
    rows: list[dict[str, str]] = []
    malformed: list[dict] = []
    if not path.exists():
        return headers, rows, malformed
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.reader(fh)
        try:
            headers = [h.strip() for h in next(reader)]
        except StopIteration:
            return headers, rows, malformed
        for line_no, raw in enumerate(reader, start=2):
            if not raw or all(not c.strip() for c in raw):
                continue
            if len(raw) != len(headers):
                # tolerate short rows by padding; long rows are recorded and truncated
                if len(raw) < len(headers):
                    raw = raw + [""] * (len(headers) - len(raw))
                    malformed.append({"line": line_no, "reason": "short_row_padded"})
                else:
                    malformed.append({"line": line_no, "reason": "extra_columns_truncated"})
                    raw = raw[: len(headers)]
            rows.append(normalize_row(dict(zip(headers, raw))))
    return headers, rows, malformed


def load_domain(domain: str, path: str | Path) -> tuple[list[Record], LoadReport]:
    """Load one clinical domain into Record objects."""
    report = LoadReport(path=str(path), domain=domain)
    p = Path(path)
    if not p.exists():
        report.missing = True
        log.warning("domain file missing: %s", p)
        return [], report
    try:
        headers, rows, malformed = read_csv_rows(p)
    except Exception as exc:  # noqa: BLE001 - never crash the build
        report.error = f"{type(exc).__name__}: {exc}"
        log.error("failed to read %s: %s", p, report.error)
        return [], report
    report.headers = headers
    report.malformed.extend(malformed)
    seq_col = f"{domain}SEQ"
    has_seq = seq_col in headers
    records: list[Record] = []
    seen: dict[tuple, int] = {}
    for i, row in enumerate(rows, start=2):
        report.rows_read += 1
        usubjid = (row.get("USUBJID") or "").strip()
        if not usubjid:
            report.malformed.append({"line": i, "reason": "missing USUBJID"})
            continue
        issues: list[str] = []
        seq: Optional[int] = None
        if has_seq:
            seq = to_int(row.get(seq_col))
            if seq is None:
                issues.append(f"missing_or_invalid_{seq_col}")
        cut_av = to_int(row.get("cut_available"))
        if "cut_available" in headers and cut_av is None:
            issues.append("missing_cut_available")
        corr_at = to_int(row.get("corrected_at_cut"))
        key = (usubjid, seq)
        if has_seq and seq is not None:
            if key in seen:
                report.duplicates.append({"line": i, "usubjid": usubjid, "seq": seq, "first_line": seen[key]})
                issues.append("duplicate_key")
            else:
                seen[key] = i
        rec = Record(
            domain=domain,
            usubjid=usubjid,
            seq=seq,
            fields=row,
            cut_available=cut_av,
            corrected_at_cut=corr_at,
            row_no=i,
            issues=issues,
        )
        records.append(rec)
        report.rows_kept += 1
    return records, report


def load_table(path: str | Path) -> tuple[list[dict[str, str]], LoadReport]:
    """Load a non-domain table (reference_ranges, corrections, cuts)."""
    p = Path(path)
    report = LoadReport(path=str(p), domain=p.stem)
    if not p.exists():
        report.missing = True
        return [], report
    try:
        headers, rows, malformed = read_csv_rows(p)
    except Exception as exc:  # noqa: BLE001
        report.error = f"{type(exc).__name__}: {exc}"
        return [], report
    report.headers = headers
    report.malformed = malformed
    report.rows_read = report.rows_kept = len(rows)
    return rows, report


def load_all_domains(data_dir: str | Path) -> tuple[dict[str, list[Record]], dict[str, LoadReport]]:
    domains: dict[str, list[Record]] = {}
    reports: dict[str, LoadReport] = {}
    for domain, path in discover_domain_files(data_dir).items():
        recs, rep = load_domain(domain, path)
        domains[domain] = recs
        reports[domain] = rep
        log.info("loaded %s: %d rows (%d malformed)", domain, rep.rows_kept, len(rep.malformed))
    return domains, reports


def iter_records(domains: dict[str, list[Record]]) -> Iterable[Record]:
    for recs in domains.values():
        yield from recs
