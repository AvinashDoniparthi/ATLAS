"""Unit canonicalisation and a deterministic conversion registry.

Conversions come from two sources: a small table of physically true factors and
statements discovered in study documents (``"1 µkat/L = 60 U/L"``). Every
conversion records its source so evidence can show the chain
``original value/unit -> factor -> normalised value/unit``.
The registry never guesses: an unknown pair returns ``None``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

_MICRO_CHARS = ("µ", "μ")  # µ (micro sign), μ (greek mu)


def canonical_unit(u: Any) -> str:
    """Lower-case, whitespace-free, micro-sign-normalised unit string ('' if missing)."""
    if u is None:
        return ""
    text = str(u).strip()
    if not text:
        return ""
    for ch in _MICRO_CHARS:
        text = text.replace(ch, "u")
    text = re.sub(r"\s+", "", text)
    return text.lower()


@dataclass(frozen=True)
class Conversion:
    value: float
    factor: float
    from_unit: str
    to_unit: str
    source: str  # "identity" | "builtin" | "doc:<name>"


# (test or None, from_unit, to_unit, factor)
_BUILTIN: List[Tuple[Optional[str], str, str, float]] = [
    (None, "ukat/L", "U/L", 60.0),
    ("BILI", "mg/dL", "umol/L", 17.104),
    ("CREAT", "mg/dL", "umol/L", 88.42),
    ("GLUC", "mg/dL", "mmol/L", 1.0 / 18.0182),
    # HBA1C % <-> mmol/mol is affine (NGSP/IFCC), not a pure factor: intentionally unsupported.
]

_RE_STATEMENT = re.compile(
    r"1\s*([A-Za-zµμ%/]+)\s*(?:=|equals|is equal to|is|→|->)\s*([0-9]+(?:[.,][0-9]+)?)\s*([A-Za-zµμ%/]+)",
    re.IGNORECASE,
)


class UnitRegistry:
    def __init__(self, with_builtin: bool = True) -> None:
        # key: (test_upper_or_None, from_canon, to_canon) -> (factor, source)
        self._table: Dict[Tuple[Optional[str], str, str], Tuple[float, str]] = {}
        if with_builtin:
            for test, f, t, factor in _BUILTIN:
                self.register(test, f, t, factor, "builtin")

    # ------------------------------------------------------------------ #
    def register(self, test: Optional[str], from_unit: Any, to_unit: Any, factor: float, source: str) -> None:
        f, t = canonical_unit(from_unit), canonical_unit(to_unit)
        if not f or not t or f == t:
            return
        try:
            factor = float(factor)
        except (TypeError, ValueError):
            return
        if factor == 0:
            return
        key_test = test.strip().upper() if isinstance(test, str) and test.strip() else None
        self._table[(key_test, f, t)] = (factor, source)
        self._table[(key_test, t, f)] = (1.0 / factor, source)

    def convert(self, test: Optional[str], value: float, from_unit: Any, to_unit: Any) -> Optional[Conversion]:
        f, t = canonical_unit(from_unit), canonical_unit(to_unit)
        if value is None or not f or not t:
            return None
        if f == t:
            return Conversion(value=float(value), factor=1.0, from_unit=f, to_unit=t, source="identity")
        key_test = test.strip().upper() if isinstance(test, str) and test.strip() else None
        hit = None
        if key_test is not None:
            hit = self._table.get((key_test, f, t))
        if hit is None:
            hit = self._table.get((None, f, t))
        if hit is None:
            return None
        factor, source = hit
        return Conversion(value=float(value) * factor, factor=factor, from_unit=f, to_unit=t, source=source)

    def learn_from_text(self, text: str, source: str) -> List[Tuple[Optional[str], str, str, float, str]]:
        """Find '1 <unit> = <n> <unit>' statements and register them as generic conversions."""
        learned: List[Tuple[Optional[str], str, str, float, str]] = []
        if not text:
            return learned
        for m in _RE_STATEMENT.finditer(text):
            f, num, t = m[1], m[2], m[3]
            try:
                factor = float(num.replace(",", "."))
            except ValueError:
                continue
            cf, ct = canonical_unit(f), canonical_unit(t)
            if not cf or not ct or cf == ct or factor == 0:
                continue
            self.register(None, cf, ct, factor, source)
            learned.append((None, cf, ct, factor, source))
        return learned

    def known_pairs(self) -> List[Tuple[Optional[str], str, str, float, str]]:
        return [(k[0], k[1], k[2], v[0], v[1]) for k, v in sorted(self._table.items(), key=lambda kv: (kv[0][0] or "", kv[0][1], kv[0][2]))]
