"""Typed laboratory / numeric value parsing.

``"<5"`` is censored (below detection), ``"ND"`` is not detected / not done,
``""`` is missing. None of them is numeric and none of them is ever zero.
``"12,4"`` (comma decimal, no dot) becomes 12.4; ``"1,234.5"`` is *not* silently
coerced because it is ambiguous.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

_RE_NUMERIC = re.compile(r"^\s*[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?\s*$")
_RE_COMMA_DECIMAL = re.compile(r"^\s*-?\d+,\d+\s*$")
_RE_CENSORED = re.compile(r"^\s*(<=|>=|<|>)\s*([-+]?\d+(?:[.,]\d+)?)\s*$")

_NOT_DETECTED_TOKENS = {
    "ND", "N/D", "NOT DONE", "NOTDONE", "NOT DETECTED", "NOTDETECTED",
    "BLQ", "NA", "N/A", "NOT_DONE", "NOT_DETECTED",
}


@dataclass(frozen=True)
class LabValue:
    raw: str
    kind: str  # "numeric" | "censored" | "not_detected" | "missing" | "non_numeric"
    value: Optional[float] = None
    operator: Optional[str] = None
    bound: Optional[float] = None
    note: Optional[str] = None

    @property
    def is_numeric(self) -> bool:
        return self.kind == "numeric" and self.value is not None


def _to_float(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_lab_value(raw: Any) -> LabValue:
    """Classify ``raw`` into a :class:`LabValue`. Never raises."""
    if raw is None:
        return LabValue(raw="", kind="missing")
    if isinstance(raw, bool):
        return LabValue(raw=str(raw), kind="non_numeric")
    if isinstance(raw, (int, float)):
        return LabValue(raw=str(raw), kind="numeric", value=float(raw))

    text = str(raw)
    stripped = text.strip()
    if not stripped:
        return LabValue(raw=text, kind="missing")

    if _RE_NUMERIC.match(stripped):
        val = _to_float(stripped)
        if val is not None:
            return LabValue(raw=text, kind="numeric", value=val)

    if _RE_COMMA_DECIMAL.match(stripped) and "." not in stripped:
        val = _to_float(stripped.replace(",", "."))
        if val is not None:
            return LabValue(raw=text, kind="numeric", value=val, note="comma_decimal")

    m = _RE_CENSORED.match(stripped)
    if m:
        bound = _to_float(m[2].replace(",", "."))
        return LabValue(raw=text, kind="censored", operator=m[1], bound=bound, note="censored_value_unknown")

    if stripped.upper() in _NOT_DETECTED_TOKENS:
        return LabValue(raw=text, kind="not_detected", note="not_detected_or_not_done")

    return LabValue(raw=text, kind="non_numeric")


def parse_number(raw: Any) -> Optional[float]:
    """Plain numeric parse for generic fields (AGE, EXDOSE, LOW/HIGH...). None on failure."""
    lv = parse_lab_value(raw)
    return lv.value if lv.is_numeric else None
