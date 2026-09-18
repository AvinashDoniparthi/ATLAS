"""Tolerant date parsing.

The practice study mixes ISO ``YYYY-MM-DD`` with ``DD-MON-YYYY``; unseen studies
may add more. Every observed/likely format is tried in order; anything that does
not parse is returned as ``ok=False`` with a reason and the raw value preserved.
Nothing here raises.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Any, Optional, Union

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

_RE_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_RE_ISO_DT = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2})(?::\d{1,2})?(?::\d{1,2})?.*$")
_RE_DMY_MON = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$")
_RE_DMY_MON_2Y = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{2})$")
_RE_YMD_SLASH = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")
_RE_DMY_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_RE_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_RE_DMY_MON_SPACE = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\s+(\d{4})$")


@dataclass(frozen=True)
class ParsedDate:
    raw: str
    date: Optional[datetime.date]
    fmt: Optional[str]
    ok: bool
    reason: Optional[str] = None


def _make(raw: str, y: int, m: int, d: int, fmt: str) -> ParsedDate:
    try:
        return ParsedDate(raw=raw, date=datetime.date(y, m, d), fmt=fmt, ok=True)
    except ValueError as exc:
        return ParsedDate(raw=raw, date=None, fmt=fmt, ok=False, reason=f"invalid_calendar_date: {exc}")


def parse_date(raw: Any) -> ParsedDate:
    """Parse ``raw`` into a :class:`ParsedDate`. Never raises."""
    if raw is None:
        return ParsedDate(raw="", date=None, fmt=None, ok=False, reason="missing")
    if isinstance(raw, datetime.datetime):
        return ParsedDate(raw=raw.isoformat(), date=raw.date(), fmt="datetime", ok=True)
    if isinstance(raw, datetime.date):
        return ParsedDate(raw=raw.isoformat(), date=raw, fmt="date", ok=True)

    text = str(raw).strip()
    if not text:
        return ParsedDate(raw=str(raw), date=None, fmt=None, ok=False, reason="missing")

    m = _RE_ISO.match(text)
    if m:
        return _make(text, int(m[1]), int(m[2]), int(m[3]), "ISO")

    m = _RE_ISO_DT.match(text)
    if m:
        return _make(text, int(m[1]), int(m[2]), int(m[3]), "ISO_DATETIME")

    m = _RE_DMY_MON.match(text)
    if m:
        mon = MONTHS.get(m[2].upper())
        if mon is None:
            return ParsedDate(raw=text, date=None, fmt="DD-MON-YYYY", ok=False, reason=f"unknown_month: {m[2]}")
        return _make(text, int(m[3]), mon, int(m[1]), "DD-MON-YYYY")

    m = _RE_DMY_MON_SPACE.match(text)
    if m:
        mon = MONTHS.get(m[2].upper())
        if mon is None:
            return ParsedDate(raw=text, date=None, fmt="DD MON YYYY", ok=False, reason=f"unknown_month: {m[2]}")
        return _make(text, int(m[3]), mon, int(m[1]), "DD MON YYYY")

    if _RE_DMY_MON_2Y.match(text):
        return ParsedDate(raw=text, date=None, fmt="DD-MON-YY", ok=False, reason="ambiguous_two_digit_year")

    m = _RE_YMD_SLASH.match(text)
    if m:
        return _make(text, int(m[1]), int(m[2]), int(m[3]), "YYYY/MM/DD")

    m = _RE_DMY_SLASH.match(text)
    if m:
        # Assumption (documented): slash dates with a trailing 4-digit year are day-first.
        return _make(text, int(m[3]), int(m[2]), int(m[1]), "DD/MM/YYYY")

    m = _RE_COMPACT.match(text)
    if m:
        return _make(text, int(m[1]), int(m[2]), int(m[3]), "YYYYMMDD")

    return ParsedDate(raw=text, date=None, fmt=None, ok=False, reason="unrecognised_format")


def _as_date(x: Union[ParsedDate, datetime.date, datetime.datetime, None]) -> Optional[datetime.date]:
    if x is None:
        return None
    if isinstance(x, ParsedDate):
        return x.date if x.ok else None
    if isinstance(x, datetime.datetime):
        return x.date()
    if isinstance(x, datetime.date):
        return x
    return None


def days_between(a: Union[ParsedDate, datetime.date, None], b: Union[ParsedDate, datetime.date, None]) -> Optional[int]:
    """Signed day difference ``b - a``; ``None`` if either side is unusable."""
    da, db = _as_date(a), _as_date(b)
    if da is None or db is None:
        return None
    return (db - da).days
