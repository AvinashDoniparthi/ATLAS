"""Row-level helpers: whitespace hygiene without type coercion."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .missing import is_missing


def normalize_row(raw: Dict[Any, Any]) -> Dict[str, Any]:
    """Strip keys and string values; keep raw strings (no numeric coercion)."""
    out: Dict[str, Any] = {}
    if not raw:
        return out
    for k, v in raw.items():
        key = str(k).strip() if k is not None else ""
        if isinstance(v, str):
            out[key] = v.strip()
        else:
            out[key] = v
    return out


def to_int(v: Any) -> Optional[int]:
    """'5' -> 5, '5.0' -> 5, 5 -> 5; anything else -> None."""
    if is_missing(v):
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    if f != int(f):
        return None
    return int(f)


def to_upper(v: Any) -> Optional[str]:
    if is_missing(v):
        return None
    return str(v).strip().upper()
