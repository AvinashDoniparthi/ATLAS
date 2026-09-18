"""Missing-value detection shared by every loader and rule."""
from __future__ import annotations

from typing import Any, Optional

_MISSING_TOKENS = {"", "NA", "N/A", "NULL", "NONE", "."}


def is_missing(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip().upper() in _MISSING_TOKENS
    return False


def clean(v: Any) -> Optional[str]:
    """Stripped string, or ``None`` if the value counts as missing."""
    if is_missing(v):
        return None
    return str(v).strip()
